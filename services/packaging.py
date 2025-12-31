import time

from core import storage

STATE_STARTED = "started"
STATE_BOX_CLOSED = "box_closed"
STATE_LABEL_PRINTED = "label_printed"
STATE_TABLE_EMPTY = "table_empty"

EVENT_START = "START"
EVENT_CLOSE_BOX = "BOX_CLOSED"
EVENT_PRINT_LABEL = "PRINT_LABEL"
EVENT_TABLE_EMPTY = "TABLE_EMPTY"

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


def get_state() -> dict:
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
    state = session["state"] if session else None
    allowed = _ALLOWED_TRANSITIONS.get(state, set())
    return {
        "can_start_sku": EVENT_START in allowed,
        "can_mark_table_empty": EVENT_TABLE_EMPTY in allowed,
        "can_close_box": EVENT_CLOSE_BOX in allowed,
        "can_print_label": EVENT_PRINT_LABEL in allowed,
    }


def get_active_session() -> dict | None:
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
    }


def get_latest_session() -> dict | None:
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
    }


def start_session(sku: str) -> dict:
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
    session_id = storage.create_pack_session(sku=sku, ts=now, state=STATE_STARTED)
    storage.add_pack_event(
        session_id=session_id,
        event_type=EVENT_START,
        ts=now,
        sku=sku,
    )
    return {"session_id": session_id, "sku": sku, "state": STATE_STARTED}


def apply_event(event_type: str, sku: str | None = None) -> dict:
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
