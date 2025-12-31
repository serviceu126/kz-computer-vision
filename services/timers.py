import json
from datetime import datetime

from core.storage import add_event, get_conn

<<<<<<< HEAD
# Разрешённые типы событий таймера.
# ВАЖНО: новых колонок не добавляем — используем только новые значения event_type.
WORK_STARTED = "WORK_STARTED"
IDLE_STARTED = "IDLE_STARTED"
=======
# Типы событий таймера.
WORK_STARTED = "WORK_STARTED"
IDLE_STARTED = "IDLE_STARTED"
HEARTBEAT = "HEARTBEAT"
>>>>>>> main


def _get_shift_info(shift_id: int) -> dict | None:
    """
<<<<<<< HEAD
    Получаем информацию о смене по shift_id.
    - Что делаем: читаем is_active и end_time из worker_shifts.
    - Зачем: чтобы корректно закрывать "хвост" интервала
      (до end_time если смена завершена).
=======
    Получаем информацию о смене.
    - Нужно, чтобы понять, закрыта смена или нет,
      и корректно закрыть "хвост" интервала.
>>>>>>> main
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
<<<<<<< HEAD
    Читаем события таймера (WORK_STARTED/IDLE_STARTED) по смене.
    - Что делаем: сортируем по ts ASC для последовательного расчёта интервалов.
    - Зачем: корректно вычислить work/idle, двигаясь от события к событию.
=======
    Читаем события WORK_STARTED/IDLE_STARTED для смены.
    - Сортируем по ts ASC, чтобы считать интервалы последовательно.
>>>>>>> main
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


<<<<<<< HEAD
=======
def _get_last_heartbeat_ts(shift_id: int) -> float | None:
    """
    Получаем последний heartbeat по смене.
    - Нужен для авто-перехода в idle при отсутствии сигналов.
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


>>>>>>> main
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
<<<<<<< HEAD
    Запись события таймера с идемпотентностью.
    - Что делаем: если последнее событие смены уже соответствует state,
      то ничего не пишем (идемпотентность).
    - Зачем: повторные вызовы /api/kiosk/timer/state не должны дублировать события.
    - Возвращает True, если событие записано; False — если дубликат.
=======
    Идемпотентная запись состояния таймера.
    - Если последнее событие уже такое же, не пишем дубликат.
    - Возвращаем True, если событие записано; False — если пропущено.
>>>>>>> main
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


<<<<<<< HEAD
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
=======
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
    - Суммируем интервалы между WORK_STARTED/IDLE_STARTED.
    - "Хвост" закрываем до end_time (если смена закрыта) или до now_dt.
    - Auto-idle: если последний heartbeat слишком старый,
      принудительно считаем текущее состояние как idle (без записи событий).
>>>>>>> main
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
<<<<<<< HEAD
            # Защита от некорректного порядка или одинаковых ts.
=======
            # Защита от некорректного порядка событий.
>>>>>>> main
            continue
        if _state_for_event_type(event["type"]) == "work":
            work_seconds += end_ts - start_ts
        else:
            idle_seconds += end_ts - start_ts

    current_state = _state_for_event_type(events[-1]["type"])
<<<<<<< HEAD
    return int(work_seconds), int(idle_seconds), current_state
=======

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
>>>>>>> main
