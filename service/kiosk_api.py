from pathlib import Path
from typing import List, Optional, Literal
import re
import json
import time
import sqlite3

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from openpyxl import Workbook
import csv
import io

from core.logic import engine, KioskUIState
from core.storage import (
    add_event,
    get_conn,
    create_shift_plan,
    get_active_shift_id,
    get_shift_plan,
    list_shift_plans,
    get_kiosk_setting,
    get_kiosk_settings,
    set_kiosk_setting,
    get_master_session,
    set_master_session,
    clear_master_session,
    update_master_last_active,
    list_sku_catalog,
    create_sku_catalog_item,
    update_sku_catalog_item,
    get_report_rows,
)
from services.packaging import (
    advance_phase,
    apply_event,
    complete_current_step,
    compute_pack_ui_flags,
    get_active_session as get_pack_active_session,
    get_latest_session as get_pack_latest_session,
    get_plan_for_session,
    get_state as get_pack_state,
    get_steps_state,
    start_session as start_pack_session,
    EVENT_CLOSE_BOX,
    EVENT_PRINT_LABEL,
    EVENT_TABLE_EMPTY,
    PackagingTransitionError,
)
from services.timers import record_timer_state, record_heartbeat
from services import shift_plans


BASE_DIR = Path(__file__).resolve().parent.parent
KIOSK_DIR = BASE_DIR / "web" / "kiosk"
INDEX_FILE = KIOSK_DIR / "index.html"


class OverlaySlot(BaseModel):
    id: int
    x: float
    y: float
    w: float
    h: float
    status: Literal["pending", "current", "done"]
    title: Optional[str] = ""


class Step(BaseModel):
    index: int
    title: str
    status: Literal["pending", "current", "done", "error"]
    meta: Optional[str] = ""


class Event(BaseModel):
    ts_epoch: float
    time: str
    text: str
    level: Literal["info", "warning", "error"] = "info"


class KioskState(BaseModel):
    worker_name: str
    shift_label: str
    worker_stats: Optional[str]
    session_count_today: int

    # Смена/команда
    shift_active: bool = False
    active_workers: List[dict] = []

    bed_title: str
    bed_sku: str
    bed_details: Optional[str]

    status: str
    worker_state: str

    started_at_epoch: Optional[float] = None

    work_seconds: int
    idle_seconds: int
    timer_state: Optional[str] = None
    work_minutes: int = 0
    idle_minutes: int = 0
    heartbeat_age_sec: Optional[int] = None

    last_pack_seconds: int
    best_pack_seconds: int
    avg_pack_seconds: int

    instruction_main: str
    instruction_sub: str
    instruction_extra: str

    current_step_index: int
    total_steps: int
    completed_steps: int
    error_steps: int
    steps: List[Step]

    events: List[Event]

    camera_stream_url: str="http://127.0.0.1:8080/stream"
    overlay_slots: List[OverlaySlot]

    # Режим мастера (супервайзер).
    # Нужен только для UI, чтобы подсветить, кто имеет право на ручные действия.
    master_mode: bool = False
    master_id: Optional[str] = None


class StartSessionRequest(BaseModel):
    worker_id: Optional[str] = None
    worker_name: Optional[str] = None
    shift_label: Optional[str] = "Смена не выбрана"
    sku: Optional[str] = None


class FinishSessionRequest(BaseModel):
    status: Optional[str] = "done"


class ShiftWorkerRequest(BaseModel):
    worker_id: str
    work_center: Optional[str] = None  # УПАКОВКА / УКОМПЛЕКТОВКА


class ShiftStartRequest(BaseModel):
    # Явная схема для старта смены.
    # Нужна, чтобы API возвращал shift_id (идентификатор открытой смены).
    worker_id: str
    work_center: str


class ShiftEndRequest(BaseModel):
    worker_id: str
    work_centers: Optional[List[str]] = None  # если не задано — закрыть все


class TimerStateRequest(BaseModel):
    state: Literal["work", "idle"]
    reason: Optional[str] = None


class TimerHeartbeatRequest(BaseModel):
    source: Optional[str] = "kiosk"


class PackStartRequest(BaseModel):
    sku: str


class ShiftPlanUploadRequest(BaseModel):
    name: Optional[str] = None
    text: Optional[str] = None
    items: Optional[List[str]] = None


class ShiftPlanSelectRequest(BaseModel):
    plan_id: int


class MasterLoginRequest(BaseModel):
    qr_text: str


class KioskSettingsRequest(BaseModel):
    operator_can_reorder: Optional[bool] = None
    operator_can_edit_qty: Optional[bool] = None
    operator_can_add_sku_to_shift: Optional[bool] = None
    operator_can_remove_sku_from_shift: Optional[bool] = None
    operator_can_manual_mode: Optional[bool] = None
    master_session_timeout_min: Optional[int] = None


class MasterLogoutRequest(BaseModel):
    reason: Optional[str] = "manual"


class SkuCreateRequest(BaseModel):
    sku_code: str
    name: str
    model_code: str
    width_cm: int
    fabric_code: str
    color_code: str
    is_active: Optional[bool] = True


class SkuUpdateRequest(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None


class ReportSaveRequest(BaseModel):
    report_type: str
    date_from: str
    date_to: str
    format: Literal["csv", "xlsx"]


def update_master_activity():
    # Фиксируем время последнего действия мастера в базе.
    # Так таймаут считается устойчиво, даже после перезапуска сервиса.
    update_master_last_active(int(time.time()))


def ensure_master_session_alive():
    """
    Проверяем, истёк ли таймаут мастер-сессии.

    Почему делаем это на уровне API:
    - фронт регулярно опрашивает /api/kiosk/state;
    - так мы автоматически выключаем режим при бездействии,
      даже если оператор ничего не нажимал.
    """
    session = get_master_session()
    if not session.get("enabled"):
        return
    timeout_min = get_kiosk_setting("master_session_timeout_min", 15)
    timeout_min = max(1, min(int(timeout_min or 15), 240))
    last_activity = session.get("last_active_ts") or 0
    if (time.time() - last_activity) > (timeout_min * 60):
        master_id = session.get("master_id")
        if master_id:
            add_event(
                event_type="master_logout",
                ts=time.time(),
                payload_json=json.dumps(
                    {"master_id": master_id, "reason": "timeout"},
                    ensure_ascii=False,
                ),
                shift_id=get_active_shift_id(),
            )
        clear_master_session()


def ensure_master_mode() -> dict:
    """
    Проверяем, что мастер-режим активен.

    Это единая точка контроля, чтобы не дублировать проверки
    по всем endpoint-ам каталога.
    """
    ensure_master_session_alive()
    session = get_master_session()
    if not session.get("enabled"):
        raise HTTPException(status_code=403, detail="Доступно только в мастер-режиме.")
    return session


def validate_report_params(report_type: str, date_from: str, date_to: str) -> None:
    """
    Проверяем параметры отчёта, чтобы backend не падал на неверных датах.
    """
    if report_type not in {"employees", "sku", "shifts"}:
        raise HTTPException(status_code=400, detail="Неизвестный тип отчёта.")
    try:
        time.strptime(date_from, "%Y-%m-%d")
        time.strptime(date_to, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте YYYY-MM-DD.") from exc


def build_report_headers(report_type: str) -> list[str]:
    # Подбираем заголовки столбцов под каждый тип отчёта.
    if report_type == "employees":
        return ["worker_id", "packed_count", "worktime_sec", "downtime_sec"]
    if report_type == "sku":
        return ["sku", "packed_count"]
    return ["shift_id", "worker_id", "start_time", "finish_time", "packed_count"]


def build_report_csv(rows: list[dict], headers: list[str]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([row.get(col, "") for col in headers])
    return output.getvalue().encode("utf-8")


def build_report_xlsx(rows: list[dict], headers: list[str]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append([row.get(col, "") for col in headers])
    # Простейшая настройка ширины колонок, чтобы текст не слипался.
    for idx, header in enumerate(headers, start=1):
        ws.column_dimensions[chr(64 + idx)].width = max(12, len(header) + 2)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def find_usb_mounts() -> list[Path]:
    """
    Ищем смонтированные USB-носители в типичных папках Linux.
    """
    roots = [Path("/media"), Path("/run/media")]
    mounts: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.glob("*/*"):
            if path.is_dir():
                mounts.append(path)
    return mounts


def build_usb_report_path(base_dir: Path, filename: str) -> Path:
    """
    Формируем путь и проверяем, что он остаётся внутри USB директории.
    """
    target = (base_dir / filename).resolve()
    if base_dir.resolve() not in target.parents and target != base_dir.resolve():
        raise HTTPException(status_code=400, detail="Некорректный путь файла.")
    return target


app = FastAPI(title="KZ Kiosk API")

app.mount(
    "/static",
    StaticFiles(directory=KIOSK_DIR),
    name="kiosk_static",
)


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(INDEX_FILE)


@app.get("/api/kiosk/state", response_model=KioskState)
async def get_state():
    ensure_master_session_alive()
    ui: KioskUIState = engine.get_ui_state()
    session = get_master_session()
    master_id = session.get("master_id") if session.get("enabled") else None
    return KioskState(
        worker_name=ui.worker_name,
        shift_label=ui.shift_label,
        worker_stats=ui.worker_stats,
        session_count_today=ui.session_count_today,
        shift_active=ui.shift_active,
        active_workers=ui.active_workers,
        bed_title=ui.bed_title,
        bed_sku=ui.bed_sku,
        bed_details=ui.bed_details,
        status=ui.status,
        worker_state=ui.worker_state,
        started_at_epoch=ui.started_at_epoch or 0.0,
        work_seconds=ui.work_seconds,
        idle_seconds=ui.idle_seconds,
        timer_state=ui.timer_state,
        work_minutes=ui.work_minutes,
        idle_minutes=ui.idle_minutes,
        heartbeat_age_sec=ui.heartbeat_age_sec,
        last_pack_seconds=ui.last_pack_seconds,
        best_pack_seconds=ui.best_pack_seconds,
        avg_pack_seconds=ui.avg_pack_seconds,
        instruction_main=ui.instruction_main,
        instruction_sub=ui.instruction_sub,
        instruction_extra=ui.instruction_extra,
        current_step_index=ui.current_step_index,
        total_steps=ui.total_steps,
        completed_steps=ui.completed_steps,
        error_steps=ui.error_steps,
        steps=[
            Step(
                index=s.index,
                title=s.title,
                status=s.status,
                meta=s.meta,
            )
            for s in ui.steps
        ],
        events=[
            Event(
                ts_epoch=e.ts_epoch,
                time=e.time,
                text=e.text,
                level=e.level,
            )
            for e in ui.events
        ],
        camera_stream_url=ui.camera_stream_url,
        overlay_slots=[
            OverlaySlot(
                id=o.id,
                x=o.x,
                y=o.y,
                w=o.w,
                h=o.h,
                status=o.status,
                title=o.title,
            )
            for o in ui.overlay_slots
        ],
        master_mode=bool(master_id),
        master_id=master_id,
    )


@app.post("/api/kiosk/master/login")
async def master_login(payload: MasterLoginRequest):
    """
    Вход в режим мастера по QR-коду.

    Формат:
    - буква M и 8 цифр (например, M13540876).
    Почему так:
    - формат легко распознаётся сканером;
    - мы быстро валидируем его без внешних сервисов.
    """
    qr_text = (payload.qr_text or "").strip()
    match = re.fullmatch(r"M(\d{8})", qr_text)
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Неверный QR мастера. Ожидается формат M######## (например, M13540876).",
        )
    master_id = match.group(1)
    set_master_session(master_id=master_id, last_active_ts=int(time.time()))
    add_event(
        event_type="master_login",
        ts=time.time(),
        payload_json=json.dumps({"master_id": master_id}, ensure_ascii=False),
        shift_id=get_active_shift_id(),
    )
    return {"status": "ok", "master_id": master_id}


@app.post("/api/kiosk/master/logout")
async def master_logout(payload: MasterLogoutRequest):
    """
    Выход из режима мастера.

    Мы просто очищаем master_id, чтобы UI вернулся к обычному режиму.
    """
    session = get_master_session()
    master_id = session.get("master_id") if session.get("enabled") else None
    reason = payload.reason or "manual"
    if master_id:
        add_event(
            event_type="master_logout",
            ts=time.time(),
            payload_json=json.dumps(
                {"master_id": master_id, "reason": reason},
                ensure_ascii=False,
            ),
            shift_id=get_active_shift_id(),
        )
    clear_master_session()
    return {"status": "ok", "reason": reason}


@app.post("/api/kiosk/session/start")
async def start_session(payload: StartSessionRequest):
    worker_id = payload.worker_id or ""
    sku = payload.sku or ""

    # 1) обновляем контекст (можно сканировать по отдельности)
    if worker_id:
        engine.set_worker(worker_id=worker_id, worker_name=payload.worker_name, shift_label=payload.shift_label)
    if sku:
        engine.set_bed(product_code=sku)

    # 2) стартуем только когда есть И сотрудник И кровать
    ui = engine.get_ui_state()
    ready_worker = (ui.worker_name and ui.worker_name != "—")
    ready_bed = (ui.bed_sku and ui.bed_sku != "—")

    if ready_worker and ready_bed and ui.status == "idle":
        engine.start_session(
            worker_id=worker_id or ui.worker_name,
            worker_name=payload.worker_name or ui.worker_name,
            product_code=sku or ui.bed_sku,
            shift_label=payload.shift_label or ui.shift_label,
        )

    return {"status": "ok"}


@app.post("/api/kiosk/session/finish")
async def finish_session(payload: FinishSessionRequest):
    engine.finish_session(status=payload.status or "done")
    return {"status": "ok"}


@app.post("/api/kiosk/shift/add")
async def shift_add(payload: ShiftWorkerRequest):
    engine.add_worker_to_shift(worker_id=payload.worker_id, work_center=payload.work_center or "")
    return {"status": "ok"}


@app.post("/api/kiosk/shift/start")
async def shift_start(payload: ShiftStartRequest):
    # Новый эндпоинт старта смены.
    # Возвращаем shift_id, чтобы фронт/интеграции могли связать события со сменой.
    shift_id = engine.add_worker_to_shift(worker_id=payload.worker_id, work_center=payload.work_center)
    return {"status": "ok", "shift_id": shift_id}


@app.post("/api/kiosk/shift/end")
async def shift_end(payload: ShiftEndRequest):
    closed = engine.close_worker_shift(worker_id=payload.worker_id, work_centers=payload.work_centers)
    return {"status": "ok", "closed": closed}


@app.post("/api/kiosk/timer/state")
async def timer_state(payload: TimerStateRequest):
    # Смена состояния таймера work/idle.
    # Что делаем: ищем активную сессию и её shift_id.
    # Если смена не активна — возвращаем 409.
    shift_id, worker_id = engine.get_active_session_shift_context()
    if not shift_id:
        raise HTTPException(
            status_code=409,
            detail="Нет активной смены для текущей упаковочной сессии.",
        )

    # Проверяем, что смена ещё активна в БД.
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT is_active FROM worker_shifts WHERE id=?", [shift_id])
    row = cur.fetchone()
    conn.close()
    if not row or int(row["is_active"]) != 1:
        raise HTTPException(
            status_code=409,
            detail="Смена уже закрыта, таймер не может менять состояние.",
        )

    created = record_timer_state(
        shift_id=shift_id,
        session_id=None,
        state=payload.state,
        reason=payload.reason,
        ts=time.time(),
        worker_id=worker_id,
    )
    return {"status": "ok", "created": created}


@app.post("/api/kiosk/timer/heartbeat")
async def timer_heartbeat(payload: TimerHeartbeatRequest):
    # Heartbeat-сигнал от киоска.
    # Что делаем: записываем HEARTBEAT для активной смены.
    # Зачем: используется в auto-idle расчёте (без добавления новых событий состояния).
    shift_id, worker_id = engine.get_active_session_shift_context()
    if not shift_id:
        raise HTTPException(
            status_code=409,
            detail="Нет активной смены для текущей упаковочной сессии.",
        )

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT is_active FROM worker_shifts WHERE id=?", [shift_id])
    row = cur.fetchone()
    conn.close()
    if not row or int(row["is_active"]) != 1:
        raise HTTPException(
            status_code=409,
            detail="Смена уже закрыта, heartbeat не записывается.",
        )

    record_heartbeat(
        shift_id=shift_id,
        session_id=None,
        ts=time.time(),
        worker_id=worker_id,
        source=payload.source,
    )
    return {"status": "ok"}


@app.post("/api/kiosk/pack/start")
async def pack_start(payload: PackStartRequest):
    """
    Старт упаковочной сессии по SKU.

    Важно:
    - Бизнес-правила проверяются в сервисе packaging.
    - Здесь мы только транслируем ошибки в HTTP 409.
    """
    try:
        state = start_pack_session(payload.sku)
    except PackagingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "state": state}


@app.post("/api/kiosk/pack/table-empty")
async def pack_table_empty():
    """
    Подтверждает, что стол пустой.

    Это обязательный gate перед стартом следующего SKU.
    Мы не меняем FSM напрямую, а вызываем сервисный слой.
    """
    try:
        state = apply_event(EVENT_TABLE_EMPTY)
    except PackagingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "state": state}


@app.post("/api/kiosk/pack/close-box")
async def pack_close_box():
    """
    Фиксирует закрытие коробки.

    Разрешено только из состояния STARTED.
    """
    try:
        state = apply_event(EVENT_CLOSE_BOX)
    except PackagingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "state": state}


@app.post("/api/kiosk/pack/print-label")
async def pack_print_label():
    """
    Фиксирует печать этикетки.

    Разрешено только после BOX_CLOSED.
    """
    try:
        state = apply_event(EVENT_PRINT_LABEL)
    except PackagingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "state": state}


@app.get("/api/kiosk/pack/state")
async def pack_state():
    """
    Возвращает компактное состояние упаковки.
    Это вспомогательный endpoint, без расширенных флагов UI.
    """
    return {"status": "ok", "state": get_pack_state()}


@app.get("/api/kiosk/pack/ui-state")
async def pack_ui_state():
    """
    Расширенное состояние упаковки для UI.

    Важно:
    - active_session показывает текущий SKU (если есть).
    - pack_state/flags вычисляются из FSM, чтобы фронт не дублировал правила.
    """
    active_session = get_pack_active_session()
    session_for_flags = active_session or get_pack_latest_session()
    flags = compute_pack_ui_flags(session_for_flags)
    return {
        "active_session": active_session,
        "pack_state": session_for_flags["state"] if session_for_flags else None,
        **flags,
    }


@app.post("/api/kiosk/pack/plan/upload")
async def pack_plan_upload(payload: ShiftPlanUploadRequest):
    """
    Загружает сменное задание (список SKU) для активной смены.

    Мы принимаем либо текст со строками, либо JSON-список,
    чтобы оператор мог быстро вставить список без лишнего формата.
    """
    shift_id = get_active_shift_id()
    if not shift_id:
        raise HTTPException(status_code=409, detail="Нет активной смены для загрузки плана.")

    # Проверяем право редактирования количества/очереди.
    # Если мастер запретил редактирование, оператор не должен менять список.
    if get_kiosk_setting("operator_can_edit_qty", 1) == 0:
        raise HTTPException(
            status_code=403,
            detail="Редактирование списка запрещено настройками мастера.",
        )

    raw_items: List[str] = []
    if payload.items:
        raw_items = payload.items
    elif payload.text:
        raw_items = payload.text.splitlines()

    items = [item.strip() for item in raw_items if item and item.strip()]
    if not items:
        raise HTTPException(status_code=400, detail="Список SKU пуст.")

    name = (payload.name or "Сменное задание").strip()
    plan_id = create_shift_plan(
        shift_id=shift_id,
        name=name,
        created_at=time.time(),
        items_json=json.dumps(items, ensure_ascii=False),
    )
    return {"status": "ok", "id": plan_id, "name": name, "count": len(items)}


@app.get("/api/kiosk/pack/plan/list")
async def pack_plan_list():
    """
    Возвращает список сменных заданий для активной смены.
    Выбранный план отмечаем отдельно, чтобы UI мог показать текущий выбор.
    """
    shift_id = get_active_shift_id()
    if not shift_id:
        raise HTTPException(status_code=409, detail="Нет активной смены.")

    plans = []
    for row in list_shift_plans(shift_id):
        items = json.loads(row["items_json"] or "[]")
        plans.append(
            {
                "id": int(row["id"]),
                "name": row["name"],
                "count": len(items),
            }
        )

    selected_id = shift_plans.get_selected_plan_id(shift_id)
    selected_plan = None
    if selected_id:
        row = get_shift_plan(selected_id)
        if row and int(row["shift_id"]) == int(shift_id):
            items = json.loads(row["items_json"] or "[]")
            selected_plan = {
                "id": int(row["id"]),
                "name": row["name"],
                "count": len(items),
                "items": items,
            }

    return {"status": "ok", "plans": plans, "selected_id": selected_id, "selected_plan": selected_plan}


@app.post("/api/kiosk/pack/plan/select")
async def pack_plan_select(payload: ShiftPlanSelectRequest):
    """
    Выбирает активный план для текущей смены.
    Это влияет только на подсказки в UI и не меняет логику упаковки.
    """
    shift_id = get_active_shift_id()
    if not shift_id:
        raise HTTPException(status_code=409, detail="Нет активной смены.")

    row = get_shift_plan(payload.plan_id)
    if not row or int(row["shift_id"]) != int(shift_id):
        raise HTTPException(status_code=404, detail="План не найден для активной смены.")

    shift_plans.select_plan(shift_id, payload.plan_id)
    items = json.loads(row["items_json"] or "[]")
    return {
        "status": "ok",
        "selected_plan": {
            "id": int(row["id"]),
            "name": row["name"],
            "count": len(items),
            "items": items,
        },
    }


@app.get("/api/kiosk/settings")
async def get_kiosk_settings_api():
    """
    Возвращает настройки киоска.

    Мы отдаём фиксированный набор ключей,
    чтобы UI мог стабильно строить интерфейс.
    """
    ensure_master_session_alive()
    settings = get_kiosk_settings(
        [
            "operator_can_reorder",
            "operator_can_edit_qty",
            "operator_can_add_sku_to_shift",
            "operator_can_remove_sku_from_shift",
            "operator_can_manual_mode",
            "master_session_timeout_min",
        ]
    )
    session = get_master_session()
    master_id = session.get("master_id") if session.get("enabled") else None
    return {
        "status": "ok",
        "settings": {
            "operator_can_reorder": bool(settings.get("operator_can_reorder", 1)),
            "operator_can_edit_qty": bool(settings.get("operator_can_edit_qty", 1)),
            "operator_can_add_sku_to_shift": bool(settings.get("operator_can_add_sku_to_shift", 1)),
            "operator_can_remove_sku_from_shift": bool(settings.get("operator_can_remove_sku_from_shift", 1)),
            "operator_can_manual_mode": bool(settings.get("operator_can_manual_mode", 1)),
            "master_session_timeout_min": int(settings.get("master_session_timeout_min", 15)),
        },
        "master_mode": bool(master_id),
        "master_id": master_id,
    }


@app.post("/api/kiosk/settings")
async def set_kiosk_settings_api(payload: KioskSettingsRequest):
    """
    Сохраняет настройки киоска.

    Важно:
    - менять настройки может только мастер (QR уже отсканирован);
    - изменения сразу сохраняются в SQLite и переживают перезапуск сервиса.
    """
    ensure_master_session_alive()
    session = get_master_session()
    if not session.get("enabled"):
        raise HTTPException(status_code=403, detail="Настройки доступны только мастеру.")

    changed_keys: list[str] = []
    if payload.operator_can_reorder is not None:
        set_kiosk_setting("operator_can_reorder", int(payload.operator_can_reorder))
        changed_keys.append("operator_can_reorder")
    if payload.operator_can_edit_qty is not None:
        set_kiosk_setting("operator_can_edit_qty", int(payload.operator_can_edit_qty))
        changed_keys.append("operator_can_edit_qty")
    if payload.operator_can_add_sku_to_shift is not None:
        set_kiosk_setting(
            "operator_can_add_sku_to_shift", int(payload.operator_can_add_sku_to_shift)
        )
        changed_keys.append("operator_can_add_sku_to_shift")
    if payload.operator_can_remove_sku_from_shift is not None:
        set_kiosk_setting(
            "operator_can_remove_sku_from_shift", int(payload.operator_can_remove_sku_from_shift)
        )
        changed_keys.append("operator_can_remove_sku_from_shift")
    if payload.operator_can_manual_mode is not None:
        set_kiosk_setting("operator_can_manual_mode", int(payload.operator_can_manual_mode))
        changed_keys.append("operator_can_manual_mode")
    if payload.master_session_timeout_min is not None:
        timeout = int(payload.master_session_timeout_min)
        if timeout < 1 or timeout > 240:
            raise HTTPException(
                status_code=400,
                detail="Таймаут мастера должен быть в диапазоне 1..240 минут.",
            )
        set_kiosk_setting("master_session_timeout_min", timeout)
        changed_keys.append("master_session_timeout_min")

    update_master_activity()
    if changed_keys:
        add_event(
            event_type="settings_change",
            ts=time.time(),
            payload_json=json.dumps(
                {"master_id": session.get("master_id"), "keys": changed_keys},
                ensure_ascii=False,
            ),
            shift_id=get_active_shift_id(),
        )
    settings = get_kiosk_settings(
        [
            "operator_can_reorder",
            "operator_can_edit_qty",
            "operator_can_add_sku_to_shift",
            "operator_can_remove_sku_from_shift",
            "operator_can_manual_mode",
            "master_session_timeout_min",
        ]
    )
    return {
        "status": "ok",
        "settings": {
            "operator_can_reorder": bool(settings.get("operator_can_reorder", 1)),
            "operator_can_edit_qty": bool(settings.get("operator_can_edit_qty", 1)),
            "operator_can_add_sku_to_shift": bool(settings.get("operator_can_add_sku_to_shift", 1)),
            "operator_can_remove_sku_from_shift": bool(settings.get("operator_can_remove_sku_from_shift", 1)),
            "operator_can_manual_mode": bool(settings.get("operator_can_manual_mode", 1)),
            "master_session_timeout_min": int(settings.get("master_session_timeout_min", 15)),
        },
        "master_mode": True,
        "master_id": session.get("master_id"),
    }


@app.get("/api/kiosk/sku")
async def sku_list(
    q: Optional[str] = Query(None, description="Поиск по SKU/названию/модели"),
    include_inactive: bool = Query(False, description="Показывать неактивные SKU"),
):
    """
    Возвращает список SKU из каталога.

    По умолчанию отдаём только активные позиции,
    чтобы UI не захламлялся архивом.
    """
    ensure_master_mode()
    items = list_sku_catalog(search=q, include_inactive=include_inactive)
    return {"status": "ok", "items": items}


@app.post("/api/kiosk/sku")
async def sku_create(payload: SkuCreateRequest):
    """
    Создаёт SKU в каталоге (только мастер).
    """
    ensure_master_mode()
    sku_code = payload.sku_code.strip()
    name = payload.name.strip()
    if not sku_code or not name:
        raise HTTPException(status_code=400, detail="SKU и имя не должны быть пустыми.")
    try:
        sku_id = create_sku_catalog_item(
            sku_code=sku_code,
            name=name,
            model_code=payload.model_code.strip(),
            width_cm=int(payload.width_cm),
            fabric_code=payload.fabric_code.strip(),
            color_code=payload.color_code.strip(),
            is_active=1 if payload.is_active else 0,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="SKU с таким кодом уже существует.")
    return {"status": "ok", "id": sku_id}


@app.put("/api/kiosk/sku/{sku_id}")
async def sku_update(sku_id: int, payload: SkuUpdateRequest):
    """
    Редактирует SKU (только имя и активность), только мастер.
    """
    ensure_master_mode()
    update_sku_catalog_item(
        sku_id=sku_id,
        name=payload.name.strip() if payload.name is not None else None,
        is_active=1 if payload.is_active else (0 if payload.is_active is False else None),
    )
    return {"status": "ok"}


@app.get("/api/kiosk/reports/preview")
async def report_preview(
    report_type: str = Query(..., alias="type"),
    date_from: str = Query(...),
    date_to: str = Query(...),
):
    """
    Возвращает первые 50 строк отчёта для предпросмотра.
    """
    ensure_master_mode()
    validate_report_params(report_type, date_from, date_to)
    rows = get_report_rows(report_type, date_from, date_to)[:50]
    return {"status": "ok", "rows": rows}


@app.get("/api/kiosk/reports/export")
async def report_export(
    report_type: str = Query(..., alias="type"),
    date_from: str = Query(...),
    date_to: str = Query(...),
    format: Literal["csv", "xlsx"] = Query("csv"),
):
    """
    Экспортирует отчёт в CSV/XLSX и отдаёт файл на скачивание.
    """
    ensure_master_mode()
    validate_report_params(report_type, date_from, date_to)
    rows = get_report_rows(report_type, date_from, date_to)
    headers = build_report_headers(report_type)
    if format == "xlsx":
        content = build_report_xlsx(rows, headers)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        content = build_report_csv(rows, headers)
        media_type = "text/csv"
    filename = f"report_{report_type}_{date_from}_{date_to}.{format}"
    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/kiosk/reports/save_to_usb")
async def report_save_to_usb(payload: ReportSaveRequest):
    """
    Сохраняет отчёт на USB-носитель.
    """
    ensure_master_mode()
    validate_report_params(payload.report_type, payload.date_from, payload.date_to)
    mounts = find_usb_mounts()
    if not mounts:
        raise HTTPException(status_code=404, detail="USB not found")
    target_dir = mounts[0]
    timestamp = int(time.time())
    filename = (
        f"report_{payload.report_type}_{payload.date_from}_{payload.date_to}_{timestamp}."
        f"{payload.format}"
    )
    target_path = build_usb_report_path(target_dir, filename)
    rows = get_report_rows(payload.report_type, payload.date_from, payload.date_to)
    headers = build_report_headers(payload.report_type)
    if payload.format == "xlsx":
        content = build_report_xlsx(rows, headers)
    else:
        content = build_report_csv(rows, headers)
    target_path.write_bytes(content)
    return {"status": "ok", "path": str(target_path)}


@app.get("/api/kiosk/pack/plan")
async def pack_plan():
    """
    Возвращает план шагов для активного SKU.

    План формируется сервисом и нужен UI для отображения прогресса.
    """
    active_session = get_pack_active_session()
    if not active_session:
        raise HTTPException(status_code=409, detail="Нет активной упаковочной сессии.")
    return {"status": "ok", "steps": get_plan_for_session(active_session)}


@app.get("/api/kiosk/pack/steps/state")
async def pack_steps_state():
    """
    Возвращает состояние шагов (фаза, индекс, текущий шаг).
    Это основная точка синхронизации UI и backend.
    """
    active_session = get_pack_active_session()
    if not active_session:
        raise HTTPException(status_code=409, detail="Нет активной упаковочной сессии.")
    return {"status": "ok", **get_steps_state(active_session)}


@app.post("/api/kiosk/pack/step/complete")
async def pack_step_complete():
    """
    Завершает текущий шаг упаковки.

    Логика проверки и запись события живёт в сервисе,
    здесь только транслируем результат.
    """
    try:
        result = complete_current_step()
    except PackagingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", **result}


@app.post("/api/kiosk/pack/phase/next")
async def pack_phase_next():
    """
    Переводит процесс из LAYOUT в PACKING.

    Нельзя перейти, если есть незавершённые шаги LAYOUT.
    """
    try:
        result = advance_phase()
    except PackagingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", **result}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "service.kiosk_api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
