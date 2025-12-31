import time
import json

from core import storage

STATE_STARTED = "started"
STATE_BOX_CLOSED = "box_closed"
STATE_LABEL_PRINTED = "label_printed"
STATE_TABLE_EMPTY = "table_empty"

PHASE_LAYOUT = "LAYOUT"
PHASE_PACKING = "PACKING"

EVENT_START = "START"
EVENT_CLOSE_BOX = "BOX_CLOSED"
EVENT_PRINT_LABEL = "PRINT_LABEL"
EVENT_TABLE_EMPTY = "TABLE_EMPTY"
EVENT_STEP_COMPLETED = "STEP_COMPLETED"
EVENT_PHASE_CHANGED = "PHASE_CHANGED"

_EVENT_TO_STATE = {
    EVENT_START: STATE_STARTED,
    EVENT_CLOSE_BOX: STATE_BOX_CLOSED,
    EVENT_PRINT_LABEL: STATE_LABEL_PRINTED,
    EVENT_TABLE_EMPTY: STATE_TABLE_EMPTY,
}

_ALLOWED_TRANSITIONS = {
    None: {EVENT_START},
    STATE_TABLE_EMPTY: {EVENT_START},
    STATE_STARTED: {EVENT_CLOSE_BOX, EVENT_TABLE_EMPTY},
    STATE_BOX_CLOSED: {EVENT_PRINT_LABEL, EVENT_TABLE_EMPTY},
    STATE_LABEL_PRINTED: {EVENT_TABLE_EMPTY},
}


class PackagingTransitionError(ValueError):
    pass


def _get_layout_stub(sku: str) -> list[dict]:
    """
    Возвращает учебный (stub) план выкладки по SKU.

    Почему так:
    - Реальный каталог SKU будет подключён позднее.
    - Нам нужна предсказуемая структура шагов, чтобы отладить workflow.
    - Структура содержит slot и part_code, чтобы фронт мог подсветить цель.
    """
    catalog = {
        "SKU-1": [
            {"slot": "A1", "part_code": "PART-1"},
            {"slot": "A2", "part_code": "PART-2"},
            {"slot": "B1", "part_code": "PART-3"},
        ],
        "SKU-2": [
            {"slot": "A1", "part_code": "PART-4"},
            {"slot": "A2", "part_code": "PART-5"},
        ],
    }
    return catalog.get(sku, [{"slot": "A1", "part_code": "PART-DEFAULT"}])


def _build_plan(sku: str) -> dict:
    """
    Строит полный план упаковки: сначала LAYOUT, затем PACKING.

    Инвариант:
    - Шаги PACKING идут в обратном порядке относительно LAYOUT.
    - Это важно, потому что физическая упаковка часто идёт "сверху вниз",
      в зеркальном порядке относительно выкладки.
    """
    layout = _get_layout_stub(sku)
    packing = list(reversed(layout))
    layout_steps = [
        {
            "step_id": f"layout-{idx}",
            "phase": PHASE_LAYOUT,
            "index": idx,
            "slot": step["slot"],
            "part_code": step["part_code"],
        }
        for idx, step in enumerate(layout)
    ]
    packing_steps = [
        {
            "step_id": f"packing-{idx}",
            "phase": PHASE_PACKING,
            "index": idx,
            "slot": step["slot"],
            "part_code": step["part_code"],
        }
        for idx, step in enumerate(packing)
    ]
    return {"layout": layout_steps, "packing": packing_steps, "all": layout_steps + packing_steps}


def verify_step(step: dict, frame=None) -> str:
    """
    Заглушка валидации шага для будущего CV.

    Зачем:
    - Позволяет включить интерфейс проверки, не внедряя модель сейчас.
    - Возвращаем "unknown", чтобы не блокировать поток.
    - В будущем сюда можно подать frame и вернуть ok/fail.
    """
    return "unknown"


def get_state() -> dict:
    """
    Возвращает минимальное состояние упаковки для UI/логики.

    Если активной сессии нет, отдаём последнюю, чтобы UI мог показать
    "последнее состояние" и правильно рассчитать gate.
    """
    active = storage.get_active_pack_session()
    if active:
        return {
            "session_id": int(active["id"]),
            "sku": active["sku"],
            "state": active["state"],
        }
    latest = storage.get_latest_pack_session()
    if latest:
        return {
            "session_id": int(latest["id"]),
            "sku": latest["sku"],
            "state": latest["state"],
        }
    return {"session_id": None, "sku": None, "state": None}


def compute_pack_ui_flags(session: dict | None) -> dict:
    """
    Вычисляет UI-флаги строго из FSM.

    Принцип:
    - Фронт не должен дублировать правила переходов.
    - Мы берём разрешённые события для текущего состояния и превращаем
      их в понятные кнопкам флаги (can_*).
    """
    state = session["state"] if session else None
    allowed = _ALLOWED_TRANSITIONS.get(state, set())
    return {
        "can_start_sku": EVENT_START in allowed,
        "can_mark_table_empty": EVENT_TABLE_EMPTY in allowed,
        "can_close_box": EVENT_CLOSE_BOX in allowed,
        "can_print_label": EVENT_PRINT_LABEL in allowed,
    }


def get_active_session() -> dict | None:
    """
    Возвращает активную упаковочную сессию в удобном формате.

    Это данные, которые UI показывает пользователю: SKU, состояние,
    текущая фаза и индекс шага.
    """
    active = storage.get_active_pack_session()
    if not active:
        return None
    return {
        "id": int(active["id"]),
        "shift_id": active["shift_id"],
        "worker_id": active["worker_id"],
        "sku": active["sku"],
        "state": active["state"],
        "start_time": active["start_time"],
        "end_time": active["end_time"],
        "phase": active["phase"],
        "current_step_index": active["current_step_index"],
        "total_steps": active["total_steps"],
    }


def get_latest_session() -> dict | None:
    """
    Возвращает последнюю сессию (даже если она уже завершена).

    Зачем:
    - UI-флаги и gate должны учитывать последнюю упаковку.
    - Например, start следующего SKU разрешён только после TABLE_EMPTY.
    """
    latest = storage.get_latest_pack_session()
    if not latest:
        return None
    return {
        "id": int(latest["id"]),
        "shift_id": latest["shift_id"],
        "worker_id": latest["worker_id"],
        "sku": latest["sku"],
        "state": latest["state"],
        "start_time": latest["start_time"],
        "end_time": latest["end_time"],
        "phase": latest["phase"],
        "current_step_index": latest["current_step_index"],
        "total_steps": latest["total_steps"],
    }


def get_plan_for_session(session: dict) -> list[dict]:
    """
    Возвращает полный план шагов для текущей сессии.

    Мы не храним шаги в БД, а строим их из SKU,
    чтобы оставить схему гибкой на раннем этапе.
    """
    return _build_plan(session["sku"])["all"]


def get_steps_state(session: dict) -> dict:
    """
    Возвращает состояние шагов для UI:
    - фаза (LAYOUT или PACKING),
    - индекс текущего шага,
    - число шагов в фазе,
    - подробности текущего шага.

    Важно: UI не должен вычислять эти вещи сам.
    """
    plan = _build_plan(session["sku"])
    phase = session["phase"] or PHASE_LAYOUT
    steps = plan["layout"] if phase == PHASE_LAYOUT else plan["packing"]
    total_steps = len(steps)
    current_step_index = session["current_step_index"] or 0
    current_step = steps[current_step_index] if current_step_index < total_steps else None
    return {
        "phase": phase,
        "current_step_index": current_step_index,
        "total_steps": total_steps,
        "current_step": current_step,
    }


def start_session(sku: str) -> dict:
    """
    Стартует упаковочную сессию для SKU.

    Правила:
    - SKU обязателен.
    - Нельзя стартовать, если есть активная сессия.
    - Нельзя стартовать, если предыдущий SKU не подтвердил TABLE_EMPTY.
    - Старт автоматически устанавливает фазу LAYOUT и шаг 0.
    """
    sku = (sku or "").strip()
    if not sku:
        raise PackagingTransitionError("SKU обязателен для старта упаковки.")

    active = storage.get_active_pack_session()
    if active:
        raise PackagingTransitionError(
            "Нельзя начать новый SKU: завершите текущую упаковку или зафиксируйте TABLE_EMPTY."
        )

    latest = storage.get_latest_pack_session()
    if latest and latest["state"] != STATE_TABLE_EMPTY:
        raise PackagingTransitionError(
            "Стол должен быть пустым перед стартом следующего SKU."
        )

    now = time.time()
    plan = _build_plan(sku)
    session_id = storage.create_pack_session(
        sku=sku,
        ts=now,
        state=STATE_STARTED,
        phase=PHASE_LAYOUT,
        current_step_index=0,
        total_steps=len(plan["layout"]),
    )
    storage.add_pack_event(
        session_id=session_id,
        event_type=EVENT_START,
        ts=now,
        sku=sku,
    )
    return {"session_id": session_id, "sku": sku, "state": STATE_STARTED}


def apply_event(event_type: str, sku: str | None = None) -> dict:
    """
    Применяет событие FSM (закрытие коробки, печать этикетки, TABLE_EMPTY).

    Это единственная точка, где мы меняем состояние FSM по событию,
    поэтому здесь выполняется строгая проверка разрешённых переходов.
    """
    active = storage.get_active_pack_session()
    session = active or storage.get_latest_pack_session()
    if not session:
        raise PackagingTransitionError("Нет активной упаковочной сессии.")

    current_state = session["state"]
    allowed = _ALLOWED_TRANSITIONS.get(current_state, set())
    if event_type not in allowed:
        raise PackagingTransitionError(
            f"Событие {event_type} недоступно из состояния {current_state or 'none'}."
        )

    next_state = _EVENT_TO_STATE[event_type]
    now = time.time()
    storage.add_pack_event(
        session_id=int(session["id"]),
        event_type=event_type,
        ts=now,
        sku=sku or session["sku"],
    )

    end_time = now if next_state == STATE_TABLE_EMPTY else None
    storage.update_pack_session_state(
        session_id=int(session["id"]),
        state=next_state,
        end_time=end_time,
    )

    return {"session_id": int(session["id"]), "sku": session["sku"], "state": next_state}


def complete_current_step() -> dict:
    """
    Завершает текущий шаг в активной фазе.

    Важные инварианты:
    - Нельзя завершать шаги без активной сессии.
    - Нельзя выйти за границы списка шагов.
    - На каждый шаг пишется событие STEP_COMPLETED с подробным payload.
    """
    active = storage.get_active_pack_session()
    if not active:
        raise PackagingTransitionError("Нет активной упаковочной сессии.")

    session = {
        "id": int(active["id"]),
        "sku": active["sku"],
        "phase": active["phase"] or PHASE_LAYOUT,
        "current_step_index": active["current_step_index"] or 0,
    }
    plan = _build_plan(session["sku"])
    steps = plan["layout"] if session["phase"] == PHASE_LAYOUT else plan["packing"]
    total_steps = len(steps)
    if session["current_step_index"] >= total_steps:
        raise PackagingTransitionError("Все шаги текущей фазы уже выполнены.")

    step = steps[session["current_step_index"]]
    verify_result = verify_step(step)
    payload = {
        "step_id": step["step_id"],
        "phase": step["phase"],
        "slot": step["slot"],
        "part_code": step["part_code"],
        "verify_result": verify_result,
    }
    now = time.time()
    storage.add_pack_event(
        session_id=session["id"],
        event_type=EVENT_STEP_COMPLETED,
        ts=now,
        payload_json=json.dumps(payload, ensure_ascii=False),
        sku=session["sku"],
    )
    storage.update_pack_session_progress(
        session_id=session["id"],
        phase=session["phase"],
        current_step_index=session["current_step_index"] + 1,
        total_steps=total_steps,
    )
    return {"session_id": session["id"], "step": step, "phase": session["phase"]}


def advance_phase() -> dict:
    """
    Переводит фазу с LAYOUT на PACKING.

    Инвариант:
    - Перейти можно только после завершения всех шагов LAYOUT.
    - При переходе пишется событие PHASE_CHANGED.
    """
    active = storage.get_active_pack_session()
    if not active:
        raise PackagingTransitionError("Нет активной упаковочной сессии.")

    phase = active["phase"] or PHASE_LAYOUT
    if phase != PHASE_LAYOUT:
        raise PackagingTransitionError("Перейти к PACKING можно только из LAYOUT.")

    plan = _build_plan(active["sku"])
    layout_steps = plan["layout"]
    current_step_index = active["current_step_index"] or 0
    if current_step_index < len(layout_steps):
        raise PackagingTransitionError("Сначала завершите все шаги LAYOUT.")

    now = time.time()
    payload = {"from": PHASE_LAYOUT, "to": PHASE_PACKING}
    storage.add_pack_event(
        session_id=int(active["id"]),
        event_type=EVENT_PHASE_CHANGED,
        ts=now,
        payload_json=json.dumps(payload, ensure_ascii=False),
        sku=active["sku"],
    )
    packing_steps = plan["packing"]
    storage.update_pack_session_progress(
        session_id=int(active["id"]),
        phase=PHASE_PACKING,
        current_step_index=0,
        total_steps=len(packing_steps),
    )
    return {"session_id": int(active["id"]), "phase": PHASE_PACKING}
