import json
from datetime import datetime

from core.storage import add_event, get_conn

# Разрешённые типы событий таймера.
# ВАЖНО: новых колонок не добавляем — используем только новые значения event_type.
WORK_STARTED = "WORK_STARTED"
IDLE_STARTED = "IDLE_STARTED"


def _get_shift_info(shift_id: int) -> dict | None:
    """
    Получаем информацию о смене по shift_id.
    - Что делаем: читаем is_active и end_time из worker_shifts.
    - Зачем: чтобы корректно закрывать "хвост" интервала
      (до end_time если смена завершена).
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
    Читаем события таймера (WORK_STARTED/IDLE_STARTED) по смене.
    - Что делаем: сортируем по ts ASC для последовательного расчёта интервалов.
    - Зачем: корректно вычислить work/idle, двигаясь от события к событию.
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
    Запись события таймера с идемпотентностью.
    - Что делаем: если последнее событие смены уже соответствует state,
      то ничего не пишем (идемпотентность).
    - Зачем: повторные вызовы /api/kiosk/timer/state не должны дублировать события.
    - Возвращает True, если событие записано; False — если дубликат.
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


def compute_work_idle_seconds(
    shift_id: int,
    now_dt: datetime,
) -> tuple[int, int, str | None]:
    """
    Вычисляет work/idle в секундах на основе событий смены.
    - Что делаем: берём последовательность WORK_STARTED/IDLE_STARTED,
      складываем интервалы между событиями.
    - Хвост интервала:
        * до end_time, если смена закрыта;
        * до now_dt, если смена активна.
    - Возвращаем: (work_seconds, idle_seconds, current_state).
    - Ограничение: без HEARTBEAT не можем определить "пробелы" без событий,
      поэтому считаем только интервалы между сменами состояния.
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
            # Защита от некорректного порядка или одинаковых ts.
            continue
        if _state_for_event_type(event["type"]) == "work":
            work_seconds += end_ts - start_ts
        else:
            idle_seconds += end_ts - start_ts

    current_state = _state_for_event_type(events[-1]["type"])
    return int(work_seconds), int(idle_seconds), current_state
