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
        status TEXT
    )
    """)

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

    conn.commit()
    conn.close()


def save_session(session):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO sessions
    (worker_id, product_code, start_time, finish_time,
     worktime_sec, downtime_sec, status)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [
        session.worker_id,
        session.product_code,
        session.start_time,
        session.finish_time,
        session.worktime_sec,
        session.downtime_sec,
        session.status
    ])

    conn.commit()
    conn.close()


def start_worker_shift(worker_id: str, work_center: str) -> None:
    """Открывает смену сотрудника на указанном РЦ. Если уже открыта — ничего не делает."""
    worker_id = (worker_id or "").strip()
    work_center = (work_center or "").strip().upper()
    if not worker_id or not work_center:
        return

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id FROM worker_shifts
           WHERE worker_id=? AND work_center=? AND is_active=1
           LIMIT 1""",
        [worker_id, work_center],
    )
    row = cur.fetchone()
    if row:
        conn.close()
        return

    cur.execute(
        """INSERT INTO worker_shifts(worker_id, work_center, start_time, end_time, is_active)
           VALUES (?, ?, ?, NULL, 1)""",
        [worker_id, work_center, time.time()],
    )
    conn.commit()
    conn.close()


def end_worker_shift(worker_id: str, work_centers: list[str] | None = None) -> int:
    """Закрывает активные смены сотрудника. Возвращает количество закрытых записей."""
    worker_id = (worker_id or "").strip()
    if not worker_id:
        return 0
    conn = get_conn()
    cur = conn.cursor()

    now = time.time()
    if work_centers:
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
    """Список активных смен: [{worker_id, work_center, start_time}]."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT worker_id, work_center, start_time
           FROM worker_shifts
           WHERE is_active=1
           ORDER BY start_time ASC"""
    )
    rows = cur.fetchall() or []
    conn.close()
    return [
        {
            "worker_id": r["worker_id"],
            "work_center": r["work_center"],
            "start_time": r["start_time"],
        }
        for r in rows
    ]


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
