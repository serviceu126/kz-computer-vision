from pathlib import Path
from typing import List, Optional, Literal
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.logic import engine, KioskUIState
from core.storage import get_conn
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
    ui: KioskUIState = engine.get_ui_state()
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
    )


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
    try:
        state = start_pack_session(payload.sku)
    except PackagingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "state": state}


@app.post("/api/kiosk/pack/table-empty")
async def pack_table_empty():
    try:
        state = apply_event(EVENT_TABLE_EMPTY)
    except PackagingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "state": state}


@app.post("/api/kiosk/pack/close-box")
async def pack_close_box():
    try:
        state = apply_event(EVENT_CLOSE_BOX)
    except PackagingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "state": state}


@app.post("/api/kiosk/pack/print-label")
async def pack_print_label():
    try:
        state = apply_event(EVENT_PRINT_LABEL)
    except PackagingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "state": state}


@app.get("/api/kiosk/pack/state")
async def pack_state():
    return {"status": "ok", "state": get_pack_state()}


@app.get("/api/kiosk/pack/ui-state")
async def pack_ui_state():
    active_session = get_pack_active_session()
    session_for_flags = active_session or get_pack_latest_session()
    flags = compute_pack_ui_flags(session_for_flags)
    return {
        "active_session": active_session,
        "pack_state": session_for_flags["state"] if session_for_flags else None,
        **flags,
    }


@app.get("/api/kiosk/pack/plan")
async def pack_plan():
    active_session = get_pack_active_session()
    if not active_session:
        raise HTTPException(status_code=409, detail="Нет активной упаковочной сессии.")
    return {"status": "ok", "steps": get_plan_for_session(active_session)}


@app.get("/api/kiosk/pack/steps/state")
async def pack_steps_state():
    active_session = get_pack_active_session()
    if not active_session:
        raise HTTPException(status_code=409, detail="Нет активной упаковочной сессии.")
    return {"status": "ok", **get_steps_state(active_session)}


@app.post("/api/kiosk/pack/step/complete")
async def pack_step_complete():
    try:
        result = complete_current_step()
    except PackagingTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", **result}


@app.post("/api/kiosk/pack/phase/next")
async def pack_phase_next():
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
