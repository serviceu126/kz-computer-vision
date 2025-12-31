import sqlite3
import time
from pathlib import Path

DB = Path("storage/kz_pack.db")
DB.parent.mkdir(exist_ok=True)


def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        worker_id TEXT,
        product_code TEXT,
        start_time REAL,
        finish_time REAL,
        worktime_sec REAL,
        downtime_sec REAL,
        status TEXT,
        shift_id INTEGER
    )
    """)

    # ВАЖНО: добавляем связь с "сменой" (shift_id) безопасно.
    # Таблица sessions могла быть создана ранее без этого поля.
    # Поэтому делаем проверку структуры и добавляем колонку только при отсутствии.
    cur.execute("PRAGMA table_info(sessions)")
    session_columns = [row["name"] for row in cur.fetchall()]
    if "shift_id" not in session_columns:
        # shift_id может быть NULL, если активной смены нет.
        # Это важно для запуска сессии без заранее открытой смены.
        cur.execute("ALTER TABLE sessions ADD COLUMN shift_id INTEGER")

    # Учёт смен и рабочих центров (РЦ).
    # Миграция: добавляем shift_id в sessions, если его ещё нет.
    # Это поле остаётся NULL для старых записей и случаев без активной смены,
    # чтобы сохранить обратную совместимость и не ломать существующие данные.
    cur.execute("PRAGMA table_info(sessions)")
    session_columns = [row["name"] for row in (cur.fetchall() or [])]
    if "shift_id" not in session_columns:
        cur.execute("ALTER TABLE sessions ADD COLUMN shift_id INTEGER")

    # Учёт смен и рабочих центров (РЦ)
    # Один сотрудник может одновременно быть активен на нескольких РЦ.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS worker_shifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        worker_id TEXT NOT NULL,
        work_center TEXT NOT NULL,
        start_time REAL NOT NULL,
        end_time REAL,
        is_active INTEGER NOT NULL DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_worker_shifts_active
    ON worker_shifts(worker_id, is_active)
    """)


    # Таблица событий (events) — минимальный журнал таймеров work/idle.
    # Зачем нужна: хранит смену состояния таймера, чтобы позже посчитать
    # рабочее/простой время по событиям, а не по "тикерам".
    # Важно: CREATE TABLE IF NOT EXISTS — безопасная миграция без ломки существующих БД.

    # Минимальная таблица событий (events).
    # Зачем: хранит факты смены состояний таймера и heartbeat,
    # чтобы считать work/idle по событиям, а не по "тикерам".
    # CREATE TABLE IF NOT EXISTS безопасен для существующих БД.

    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        type TEXT NOT NULL,
        payload_json TEXT,
        shift_id INTEGER,
        session_id INTEGER,
        worker_id TEXT
                """)

    # Минимальная событийная модель (events).
    # Зачем: даёт единый журнал ключевых фактов (старт/финиш/упаковка),
    # чтобы потом строить отчёты без усложнения таблиц сессий и без ломки истории.
    # Для метрик это важно тем, что packed_count можно получать простым COUNT по событиям.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY,
        ts REAL,
        type TEXT,
        payload_json TEXT,
        shift_id INTEGER NULL,
        session_id INTEGER NULL,
        worker_id TEXT NULL


    )
    """)

    conn.commit()
    conn.close()


def save_session(session) -> int:
    conn = get_conn()
    cur = conn.cursor()

    # shift_id может быть не задан (нет активной смены или старая логика).
    # Тогда сохраняем NULL, чтобы не ломать аналитику по историческим данным.
    shift_id = getattr(session, "shift_id", None)

    cur.execute("""
    INSERT INTO sessions
    (worker_id, product_code, start_time, finish_time,
     worktime_sec, downtime_sec, status, shift_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        session.worker_id,
        session.product_code,
        session.start_time,
        session.finish_time,
        session.worktime_sec,
        session.downtime_sec,
        session.status,
        getattr(session, "shift_id", None),
        shift_id,
    ])

    session_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(session_id or 0)


def add_event(
    event_type: str,
    ts: float,
    payload_json: str = "",
    shift_id: int | None = None,
    session_id: int | None = None,
    worker_id: str | None = None,
) -> int:
    """

    Добавляет событие в таблицу events.
    - Что делает: пишет запись с типом события и временем (ts).
    - Зачем: события нужны для вычисления work/idle на основе смены состояния,
      а не на основе частых heartbeat-тикеров.
    - Как использовать: вызывать при смене состояния таймера (WORK_STARTED/IDLE_STARTED).

    Добавляем событие в events.
    - Что делаем: записываем тип события и время (ts).
    - Зачем: события нужны для вычисления work/idle и heartbeat-авто-idle.
    - Как использовать: вызовы из /api/kiosk/timer/state и /api/kiosk/timer/heartbeat.

    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO events(ts, type, payload_json, shift_id, session_id, worker_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [ts, event_type, payload_json or "", shift_id, session_id, worker_id],
    )
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(event_id or 0)


def add_event(
    event_type: str,
    ts: float,
    payload_json: str = "",
    shift_id: int | None = None,
    session_id: int | None = None,
    worker_id: str | None = None,
) -> int:
    """
    Добавляем событие в events.
    - Что делаем: записываем тип события и время (ts).
    - Зачем: события нужны для вычисления work/idle и heartbeat-авто-idle.
    - Как использовать: вызовы из /api/kiosk/timer/state и /api/kiosk/timer/heartbeat.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO events(ts, type, payload_json, shift_id, session_id, worker_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [ts, event_type, payload_json or "", shift_id, session_id, worker_id],
    )
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(event_id or 0)


def start_worker_shift(worker_id: str, work_center: str) -> int:
    """Открывает смену сотрудника на указанном РЦ и возвращает shift_id."""
    """Открывает смену сотрудника на указанном РЦ. Возвращает ID новой смены."""
    worker_id = (worker_id or "").strip()
    work_center = (work_center or "").strip().upper()
    if not worker_id or not work_center:
        return 0

    conn = get_conn()
    cur = conn.cursor()

    # Закрываем предыдущую активную смену на этом РЦ (если есть).
    # Это важно: у сотрудника может быть только одна активная смена на одном РЦ.
    now = time.time()

    # Закрываем предыдущую активную смену на этом же РЦ (если была),
    # чтобы не допустить несколько пересекающихся смен в одной зоне.
    cur.execute(
        """UPDATE worker_shifts
           SET end_time=?, is_active=0
           WHERE worker_id=? AND work_center=? AND is_active=1""",
        [time.time(), worker_id, work_center],
    )

    # Создаём новую смену и возвращаем её идентификатор.
    # Открываем новую смену и возвращаем её ID,
    # чтобы можно было привязать к ней сессию упаковки.
    cur.execute(
        """INSERT INTO worker_shifts(worker_id, work_center, start_time, end_time, is_active)
           VALUES (?, ?, ?, NULL, 1)""",
        [worker_id, work_center, now],
    )
    shift_id = int(cur.lastrowid or 0)
    conn.commit()
    conn.close()
    return shift_id
    shift_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(shift_id or 0)


def end_worker_shift(worker_id: str, work_centers: list[str] | None = None) -> int:
    """Закрывает активные смены сотрудника. Возвращает количество закрытых записей."""
    worker_id = (worker_id or "").strip()
    if not worker_id:
        return 0
    conn = get_conn()
    cur = conn.cursor()

    now = time.time()
    # Если передан список РЦ — закрываем только их,
    # иначе закрываем все активные смены сотрудника.
    if work_centers:
        # Закрываем смены только по указанным РЦ.
        centers = [c.strip().upper() for c in work_centers if c and c.strip()]
        if not centers:
            conn.close()
            return 0
        q_marks = ",".join(["?"] * len(centers))
        cur.execute(
            f"""UPDATE worker_shifts
                SET end_time=?, is_active=0
                WHERE worker_id=? AND is_active=1 AND work_center IN ({q_marks})""",
            [now, worker_id, *centers],
        )
    else:
        # Закрываем все активные смены сотрудника.
        cur.execute(
            """UPDATE worker_shifts
                SET end_time=?, is_active=0
                WHERE worker_id=? AND is_active=1""",
            [now, worker_id],
        )

    changed = cur.rowcount or 0
    conn.commit()
    conn.close()
    return int(changed)


def get_active_shifts() -> list[dict]:
    """Список активных смен: [{worker_id, work_center, start_time, shift_id}]."""
    """Список активных смен: [{shift_id, worker_id, work_center, start_time}]."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id, worker_id, work_center, start_time
           FROM worker_shifts
           WHERE is_active=1
           ORDER BY start_time ASC"""
    )
    rows = cur.fetchall() or []
    conn.close()
    return [
        {
            "shift_id": r["id"],
            "worker_id": r["worker_id"],
            "work_center": r["work_center"],
            "start_time": r["start_time"],
            "shift_id": r["id"],
        }
        for r in rows
    ]


def get_latest_active_shift_id(worker_id: str) -> int | None:
    # Возвращаем ID самой свежей активной смены сотрудника,
    # чтобы автоматически привязать новую упаковочную сессию к смене.
    worker_id = (worker_id or "").strip()
    if not worker_id:
        return None
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id
           FROM worker_shifts
           WHERE worker_id=? AND is_active=1
           ORDER BY start_time DESC, id DESC
           LIMIT 1""",
        [worker_id],
    )
    row = cur.fetchone()
    conn.close()
    return int(row["id"]) if row else None


def get_worker_active_centers(worker_id: str) -> list[str]:
    worker_id = (worker_id or "").strip()
    if not worker_id:
        return []
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT work_center FROM worker_shifts
           WHERE worker_id=? AND is_active=1
           ORDER BY work_center ASC""",
        [worker_id],
    )
    rows = cur.fetchall() or []
    conn.close()
    return [r["work_center"] for r in rows]


def get_latest_active_shift_id(worker_id: str) -> int | None:
    # Возвращаем самую "свежую" активную смену сотрудника.
    # Это нужно для привязки упаковочной сессии к конкретной смене.
    worker_id = (worker_id or "").strip()
    if not worker_id:
        return None
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id FROM worker_shifts
           WHERE worker_id=? AND is_active=1
           ORDER BY start_time DESC
           LIMIT 1""",
        [worker_id],
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return int(row["id"])


def count_sessions_since(start_time: float, worker_id: str | None = None) -> int:
    worker_id = (worker_id or "").strip()
    conn = get_conn()
    cur = conn.cursor()
    if worker_id:
        cur.execute(
            """SELECT COUNT(*) AS cnt FROM sessions
               WHERE start_time >= ? AND worker_id = ?""",
            [start_time, worker_id],
        )
    else:
        cur.execute(
            """SELECT COUNT(*) AS cnt FROM sessions
               WHERE start_time >= ?""",
            [start_time],
        )
    row = cur.fetchone()
    conn.close()
    return int(row["cnt"] if row else 0)


def add_event(
    type: str,
    ts: float,
    payload_json: str = "",
    shift_id: int | None = None,
    session_id: int | None = None,
    worker_id: str | None = None,
) -> int:
    
    """Добавляем событие в events.
    - Что делаем: пишем строку в events с типом и временем.
    - Зачем: фиксируем факт (например PACKED_CONFIRMED), чтобы потом считать метрики
      через COUNT/агрегации, а не вручную пересчитывать сессии.
    - Как влияет на метрики: packed_count = COUNT(type='PACKED_CONFIRMED').
    - Тестирование (curl):
     # 1) POST /api/kiosk/session/finish {"status":"done"}
     # 2) GET  /api/kiosk/report/shift?shift_id=...
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute( """INSERT INTO events(ts, type, payload_json, shift_id, session_id, worker_id),VALUES (?, ?, ?, ?, ?, ?)""",
        [ts, type, payload_json or "", shift_id, session_id, worker_id],
    )
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(event_id or 0)


def get_shift_report(shift_id: int) -> dict:
    """
    Минимальный отчёт по смене.
    - Что делаем: считаем packed_count через events и суммируем времена из sessions.
    - Зачем: нужен быстрый источник метрик для API /report.
    - Как влияет на метрики: packed_count растёт по событиям PACKED_CONFIRMED.
    - Тестирование (curl):
      1) Запустить смену и упаковать комплект со status=done.
      2) GET /api/kiosk/report/shift?shift_id=...
    """
    if not shift_id:
        return {
            "shift_id": 0,
            "packed_count": 0,
            "worktime_sec": 0,
            "downtime_sec": 0,
            "per_worker": {},
        }

    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """SELECT COUNT(*) AS cnt
           FROM events
           WHERE shift_id=? AND type='PACKED_CONFIRMED'""",
        [shift_id],
    )
    row = cur.fetchone()
    packed_count = int(row["cnt"] if row else 0)

    cur.execute(
        """SELECT
               COALESCE(SUM(worktime_sec), 0) AS worktime_sec,
               COALESCE(SUM(downtime_sec), 0) AS downtime_sec
           FROM sessions
           WHERE shift_id=?""",
        [shift_id],
    )
    totals = cur.fetchone()
    worktime_sec = int((totals["worktime_sec"] if totals else 0) or 0)
    downtime_sec = int((totals["downtime_sec"] if totals else 0) or 0)

    cur.execute(
        """SELECT worker_id,
                  COALESCE(SUM(worktime_sec), 0) AS worktime_sec,
                  COALESCE(SUM(downtime_sec), 0) AS downtime_sec
           FROM sessions
           WHERE shift_id=?
           GROUP BY worker_id""",
        [shift_id],
    )
    per_worker_rows = cur.fetchall() or []

    cur.execute(
        """SELECT worker_id, COUNT(*) AS cnt
           FROM events
           WHERE shift_id=? AND type='PACKED_CONFIRMED'
           GROUP BY worker_id""",
        [shift_id],
    )
    packed_per_worker_rows = cur.fetchall() or []

    conn.close()

    per_worker = {}
    for row in per_worker_rows:
        wid = row["worker_id"] or ""
        per_worker[wid] = {
            "packed_count": 0,
            "worktime_sec": int(row["worktime_sec"] or 0),
            "downtime_sec": int(row["downtime_sec"] or 0),
        }

    for row in packed_per_worker_rows:
        wid = row["worker_id"] or ""
        per_worker.setdefault(wid, {"packed_count": 0, "worktime_sec": 0, "downtime_sec": 0})
        per_worker[wid]["packed_count"] = int(row["cnt"] or 0)

    return {
        "shift_id": int(shift_id),
        "packed_count": packed_count,
        "worktime_sec": worktime_sec,
        "downtime_sec": downtime_sec,
        "per_worker": per_worker,
    }
