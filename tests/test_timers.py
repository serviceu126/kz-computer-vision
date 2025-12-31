from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from core import storage
from core.logic import engine
from core.session import PackSession
from service.kiosk_api import app
from services.timers import (
    WORK_STARTED,
    IDLE_STARTED,
    compute_work_idle_seconds,
)


def _setup_db(tmp_path, monkeypatch):
    # Перенаправляем базу на временный файл,
    # чтобы тесты были изолированы и не трогали рабочие данные.
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


def test_compute_work_idle_switching(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _insert_shift(shift_id=1, is_active=1)

    # Инвариант: work/idle считаем по интервалам между событиями.
    # Сценарий: 10 сек работы → 20 сек простоя → 20 сек работы.
    t0 = 1000.0
    _insert_event(1, t0, WORK_STARTED)
    _insert_event(1, t0 + 10, IDLE_STARTED)
    _insert_event(1, t0 + 30, WORK_STARTED)

    now_dt = datetime.fromtimestamp(t0 + 50, tz=timezone.utc)
    work_sec, idle_sec, state = compute_work_idle_seconds(1, now_dt)

    assert work_sec == 30
    assert idle_sec == 20
    assert state == "work"


def test_timer_state_idempotent(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _insert_shift(shift_id=1, is_active=1)

    # Подготавливаем активную сессию в памяти,
    # чтобы эндпоинт мог найти shift_id.
    sess = PackSession(worker_id="W1", product_code="SKU1")
    sess.shift_id = 1
    engine._session = sess

    client = TestClient(app)
    try:
        # Инвариант: повторное состояние не должно писать второе событие.
        res1 = client.post("/api/kiosk/timer/state", json={"state": "work"})
        res2 = client.post("/api/kiosk/timer/state", json={"state": "work"})
    finally:
        engine._session = None

    assert res1.status_code == 200
    assert res2.status_code == 200

    conn = storage.get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS cnt FROM events WHERE shift_id=1")
    count = cur.fetchone()["cnt"]
    conn.close()
    assert count == 1


def test_tail_closed_on_shift_end(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    _insert_shift(shift_id=1, is_active=0, end_time=25.0)
    _insert_event(1, 0.0, WORK_STARTED)

    # Инвариант: "хвост" закрывается на end_time, даже если now_dt больше.
    now_dt = datetime.fromtimestamp(100.0, tz=timezone.utc)
    work_sec, idle_sec, state = compute_work_idle_seconds(1, now_dt)

    assert work_sec == 25
    assert idle_sec == 0
    assert state == "work"
