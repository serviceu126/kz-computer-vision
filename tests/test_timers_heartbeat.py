from datetime import datetime, timezone

from core import storage
from services.timers import (
    WORK_STARTED,
    HEARTBEAT,
    compute_work_idle_seconds,
)


def _setup_db(tmp_path, monkeypatch):
    # Изолируем БД для тестов, чтобы не трогать рабочие данные.
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(storage, "DB", db_path)
    storage.DB.parent.mkdir(exist_ok=True)
    storage.init_db()


def _insert_shift(shift_id: int, is_active: int, end_time: float | None = None):
    conn = storage.get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO worker_shifts(id, worker_id, work_center, start_time, end_time, is_active)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [shift_id, "W1", "УПАКОВКА", 0.0, end_time, is_active],
    )
    conn.commit()
    conn.close()


def _insert_event(shift_id: int, ts: float, event_type: str):
    conn = storage.get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO events(ts, type, payload_json, shift_id, session_id, worker_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [ts, event_type, "", shift_id, None, "W1"],
    )
    conn.commit()
    conn.close()


def test_auto_idle_on_stale_heartbeat(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _insert_shift(shift_id=1, is_active=1)

    # Инвариант: если heartbeat слишком старый, текущий state -> idle.
    t0 = 1000.0
    _insert_event(1, t0, WORK_STARTED)
    _insert_event(1, t0, HEARTBEAT)

    now_dt = datetime.fromtimestamp(t0 + 200, tz=timezone.utc)
    work_sec, idle_sec, state = compute_work_idle_seconds(
        1,
        now_dt,
        idle_timeout_sec=90,
    )

    assert work_sec == 200
    assert idle_sec == 0
    assert state == "idle"


def test_fresh_heartbeat_keeps_state(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _insert_shift(shift_id=1, is_active=1)

    # Инвариант: свежий heartbeat не переопределяет состояние.
    t0 = 1000.0
    _insert_event(1, t0, WORK_STARTED)
    _insert_event(1, t0 + 10, HEARTBEAT)

    now_dt = datetime.fromtimestamp(t0 + 50, tz=timezone.utc)
    work_sec, idle_sec, state = compute_work_idle_seconds(
        1,
        now_dt,
        idle_timeout_sec=90,
    )

    assert work_sec == 50
    assert idle_sec == 0
    assert state == "work"


def test_no_heartbeat_keeps_previous_behavior(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _insert_shift(shift_id=1, is_active=1)

    # Инвариант: при отсутствии heartbeat состояние вычисляется только по событиям.
    t0 = 1000.0
    _insert_event(1, t0, WORK_STARTED)

    now_dt = datetime.fromtimestamp(t0 + 30, tz=timezone.utc)
    work_sec, idle_sec, state = compute_work_idle_seconds(
        1,
        now_dt,
        idle_timeout_sec=90,
    )

    assert work_sec == 30
    assert idle_sec == 0
    assert state == "work"
