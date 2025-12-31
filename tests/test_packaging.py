import pytest
from fastapi.testclient import TestClient

from core import storage
from service.kiosk_api import app


def _setup_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_packaging.db"
    monkeypatch.setattr(storage, "DB", db_path)
    storage.DB.parent.mkdir(exist_ok=True)
    storage.init_db()


def test_packaging_happy_path(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    client = TestClient(app)

    res_start = client.post("/api/kiosk/pack/start", json={"sku": "SKU-1"})
    assert res_start.status_code == 200

    res_close = client.post("/api/kiosk/pack/close-box")
    assert res_close.status_code == 200

    res_print = client.post("/api/kiosk/pack/print-label")
    assert res_print.status_code == 200


def test_packaging_print_before_close_forbidden(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    client = TestClient(app)

    client.post("/api/kiosk/pack/start", json={"sku": "SKU-2"})
    res_print = client.post("/api/kiosk/pack/print-label")
    assert res_print.status_code == 409


def test_packaging_start_before_table_empty_forbidden(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    client = TestClient(app)

    res_start = client.post("/api/kiosk/pack/start", json={"sku": "SKU-3"})
    assert res_start.status_code == 200

    res_start_again = client.post("/api/kiosk/pack/start", json={"sku": "SKU-4"})
    assert res_start_again.status_code == 409


def test_packaging_ui_state_flags(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    client = TestClient(app)

    res_idle = client.get("/api/kiosk/pack/ui-state")
    assert res_idle.status_code == 200
    payload_idle = res_idle.json()
    assert payload_idle["active_session"] is None
    assert payload_idle["can_start_sku"] is True
    assert payload_idle["can_close_box"] is False
    assert payload_idle["can_print_label"] is False
    assert payload_idle["can_mark_table_empty"] is False

    client.post("/api/kiosk/pack/start", json={"sku": "SKU-5"})
    res_started = client.get("/api/kiosk/pack/ui-state")
    payload_started = res_started.json()
    assert payload_started["can_start_sku"] is False

    client.post("/api/kiosk/pack/close-box")
    res_closed = client.get("/api/kiosk/pack/ui-state")
    payload_closed = res_closed.json()
    assert payload_closed["can_print_label"] is True

    client.post("/api/kiosk/pack/print-label")
    res_labeled = client.get("/api/kiosk/pack/ui-state")
    payload_labeled = res_labeled.json()
    assert payload_labeled["can_start_sku"] is False
    assert payload_labeled["can_mark_table_empty"] is True

    client.post("/api/kiosk/pack/table-empty")
    res_empty = client.get("/api/kiosk/pack/ui-state")
    payload_empty = res_empty.json()
    assert payload_empty["can_start_sku"] is True


def test_packaging_table_empty_gate_for_next_sku(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    client = TestClient(app)

    res_start = client.post("/api/kiosk/pack/start", json={"sku": "SKU-6"})
    assert res_start.status_code == 200

    res_close = client.post("/api/kiosk/pack/close-box")
    assert res_close.status_code == 200

    res_print = client.post("/api/kiosk/pack/print-label")
    assert res_print.status_code == 200

    res_start_next = client.post("/api/kiosk/pack/start", json={"sku": "SKU-7"})
    assert res_start_next.status_code == 409

    res_table_empty = client.post("/api/kiosk/pack/table-empty")
    assert res_table_empty.status_code == 200

    res_start_ok = client.post("/api/kiosk/pack/start", json={"sku": "SKU-7"})
    assert res_start_ok.status_code == 200


def test_packaging_steps_happy_path(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    client = TestClient(app)

    res_start = client.post("/api/kiosk/pack/start", json={"sku": "SKU-1"})
    assert res_start.status_code == 200

    res_plan = client.get("/api/kiosk/pack/plan")
    assert res_plan.status_code == 200
    steps = res_plan.json()["steps"]
    layout_steps = [step for step in steps if step["phase"] == "LAYOUT"]
    packing_steps = [step for step in steps if step["phase"] == "PACKING"]

    for _ in layout_steps:
        res_complete = client.post("/api/kiosk/pack/step/complete")
        assert res_complete.status_code == 200

    res_phase = client.post("/api/kiosk/pack/phase/next")
    assert res_phase.status_code == 200

    for _ in packing_steps:
        res_complete = client.post("/api/kiosk/pack/step/complete")
        assert res_complete.status_code == 200

    res_close = client.post("/api/kiosk/pack/close-box")
    assert res_close.status_code == 200

    res_print = client.post("/api/kiosk/pack/print-label")
    assert res_print.status_code == 200

    res_table = client.post("/api/kiosk/pack/table-empty")
    assert res_table.status_code == 200


def test_packaging_phase_next_requires_layout_complete(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    client = TestClient(app)

    res_start = client.post("/api/kiosk/pack/start", json={"sku": "SKU-1"})
    assert res_start.status_code == 200

    res_phase = client.post("/api/kiosk/pack/phase/next")
    assert res_phase.status_code == 409


def test_packaging_complete_step_without_session(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    client = TestClient(app)

    res_complete = client.post("/api/kiosk/pack/step/complete")
    assert res_complete.status_code == 409
