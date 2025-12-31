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
