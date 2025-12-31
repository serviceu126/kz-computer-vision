import json
from datetime import datetime

from core.storage import add_event, get_conn

# Типы событий таймера.
WORK_STARTED = "WORK_STARTED"
IDLE_STARTED = "IDLE_STARTED"
HEARTBEAT = "HEARTBEAT"


def _get_shift_info(shift_id: int) -> dict | None:
    """
    Получаем информацию о смене.
    - Нужно, чтобы понять, закрыта смена или нет,
      и корректно закрыть "хвост" интервала.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id, is_active, end_time
           FROM worker_shifts
           WHERE id=?""",
        [shift_id],
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "is_active": int(row["is_active"]),
        "end_time": row["end_time"],
    }


def _get_timer_events(shift_id: int) -> list[dict]:
    """
    Читаем события WORK_STARTED/IDLE_STARTED для смены.
    - Источник истины: таблица events.
    - Сортируем по ts ASC, чтобы считать интервалы последовательно.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT ts, type
           FROM events
           WHERE shift_id=? AND type IN (?, ?)
           ORDER BY ts ASC, id ASC""",
        [shift_id, WORK_STARTED, IDLE_STARTED],
    )
    rows = cur.fetchall() or []
    conn.close()
    return [{"ts": float(r["ts"]), "type": r["type"]} for r in rows]


def _get_last_heartbeat_ts(shift_id: int) -> float | None:
    """
    Получаем последний heartbeat по смене.
    - Используется ТОЛЬКО вычислительно для auto-idle,
      без записи событий состояния work/idle.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT ts
           FROM events
           WHERE shift_id=? AND type=?
           ORDER BY ts DESC, id DESC
           LIMIT 1""",
        [shift_id, HEARTBEAT],
    )
    row = cur.fetchone()
    conn.close()
    return float(row["ts"]) if row else None


def _event_type_for_state(state: str) -> str:
    return WORK_STARTED if state == "work" else IDLE_STARTED


def _state_for_event_type(event_type: str) -> str:
    return "work" if event_type == WORK_STARTED else "idle"


def record_timer_state(
    shift_id: int,
    session_id: int | None,
    state: str,
    reason: str | None,
    ts: float,
    worker_id: str | None = None,
) -> bool:
    """
    Идемпотентная запись состояния таймера.
    - Если последнее событие уже такое же, не пишем дубликат.
    - Возвращаем True, если событие записано; False — если пропущено.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT type
           FROM events
           WHERE shift_id=? AND type IN (?, ?)
           ORDER BY ts DESC, id DESC
           LIMIT 1""",
        [shift_id, WORK_STARTED, IDLE_STARTED],
    )
    row = cur.fetchone()
    conn.close()

    if row and _state_for_event_type(row["type"]) == state:
        return False

    payload = json.dumps({"reason": reason} if reason else {}, ensure_ascii=False)
    add_event(
        event_type=_event_type_for_state(state),
        ts=ts,
        payload_json=payload,
        shift_id=shift_id,
        session_id=session_id,
        worker_id=worker_id,
    )
    return True


def record_heartbeat(
    shift_id: int,
    session_id: int | None,
    ts: float,
    worker_id: str | None = None,
    source: str | None = None,
) -> int:
    """
    Запись heartbeat-события.
    - Зачем: heartbeat нужен для авто-idle логики (если сигналов нет долго).
    - Событие HEARTBEAT не меняет work/idle напрямую, оно только влияет
      на вычисление текущего состояния.
    """
    payload = json.dumps({"source": source} if source else {}, ensure_ascii=False)
    return add_event(
        event_type=HEARTBEAT,
        ts=ts,
        payload_json=payload,
        shift_id=shift_id,
        session_id=session_id,
        worker_id=worker_id,
    )


def compute_work_idle_seconds(
    shift_id: int,
    now_dt: datetime,
    idle_timeout_sec: int = 90,
) -> tuple[int, int, str | None]:
    """
    Считаем work/idle по событиям смены.
    - Источник истины: events (WORK_STARTED/IDLE_STARTED).
    - Суммируем интервалы между событиями.
    - "Хвост" закрываем до end_time (если смена закрыта) или до now_dt.
    - Auto-idle: если heartbeat слишком старый, вычислительно считаем
      текущее состояние как idle (без записи новых событий).
    """
    if not shift_id:
        return 0, 0, None

    shift_info = _get_shift_info(shift_id)
    if not shift_info:
        return 0, 0, None

    events = _get_timer_events(shift_id)
    if not events:
        return 0, 0, None

    now_ts = now_dt.timestamp()
    if shift_info["is_active"] == 0 and shift_info["end_time"]:
        tail_end = min(float(shift_info["end_time"]), now_ts)
    else:
        tail_end = now_ts

    work_seconds = 0.0
    idle_seconds = 0.0

    for idx, event in enumerate(events):
        start_ts = event["ts"]
        end_ts = events[idx + 1]["ts"] if idx + 1 < len(events) else tail_end
        if end_ts <= start_ts:
            # Защита от некорректного порядка событий.
            continue
        if _state_for_event_type(event["type"]) == "work":
            work_seconds += end_ts - start_ts
        else:
            idle_seconds += end_ts - start_ts

    current_state = _state_for_event_type(events[-1]["type"])

    last_heartbeat_ts = _get_last_heartbeat_ts(shift_id)
    if last_heartbeat_ts is not None:
        if now_ts - last_heartbeat_ts > idle_timeout_sec:
            # ВАЖНО: состояние вычислительное, событий не добавляем.
            current_state = "idle"

    return int(work_seconds), int(idle_seconds), current_state


def get_heartbeat_age_sec(shift_id: int, now_dt: datetime) -> int | None:
    """
    Возвращает возраст последнего heartbeat.
    - Если heartbeat не было — возвращаем None.
    """
    last_heartbeat_ts = _get_last_heartbeat_ts(shift_id)
    if last_heartbeat_ts is None:
        return None
    now_ts = now_dt.timestamp()
    age = now_ts - last_heartbeat_ts
    return int(age) if age >= 0 else 0
