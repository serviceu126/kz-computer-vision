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
