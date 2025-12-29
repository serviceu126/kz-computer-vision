from pathlib import Path
from typing import List, Optional, Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.logic import engine, KioskUIState

BASE_DIR = Path(__file__).resolve().parent.parent
KIOSK_DIR = BASE_DIR / "web" / "kiosk"
INDEX_FILE = KIOSK_DIR / "index.html"


class OverlaySlot(BaseModel):
    id: int
    x: float
    y: float
    w: float
    h: float
    status: Literal["pending", "current", "done"]
    title: Optional[str] = ""


class Step(BaseModel):
    index: int
    title: str
    status: Literal["pending", "current", "done", "error"]
    meta: Optional[str] = ""


class Event(BaseModel):
    ts_epoch: float
    time: str
    text: str
    level: Literal["info", "warning", "error"] = "info"


class KioskState(BaseModel):
    worker_name: str
    shift_label: str
    worker_stats: Optional[str]

    bed_title: str
    bed_sku: str
    bed_details: Optional[str]

    status: str
    worker_state: str

    started_at_epoch: Optional[float] = None

    work_seconds: int
    idle_seconds: int

    last_pack_seconds: int
    best_pack_seconds: int
    avg_pack_seconds: int

    instruction_main: str
    instruction_sub: str
    instruction_extra: str

    current_step_index: int
    total_steps: int
    completed_steps: int
    error_steps: int
    steps: List[Step]

    events: List[Event]

    camera_stream_url: str="http://127.0.0.1:8080/stream"
    overlay_slots: List[OverlaySlot]


class StartSessionRequest(BaseModel):
    worker_id: Optional[str] = None
    worker_name: Optional[str] = None
    shift_label: Optional[str] = "Смена не выбрана"
    sku: Optional[str] = None


class FinishSessionRequest(BaseModel):
    status: Optional[str] = "done"


app = FastAPI(title="KZ Kiosk API")

app.mount(
    "/static",
    StaticFiles(directory=KIOSK_DIR),
    name="kiosk_static",
)


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse(INDEX_FILE)


@app.get("/api/kiosk/state", response_model=KioskState)
async def get_state():
    ui: KioskUIState = engine.get_ui_state()
    return KioskState(
        worker_name=ui.worker_name,
        shift_label=ui.shift_label,
        worker_stats=ui.worker_stats,
        bed_title=ui.bed_title,
        bed_sku=ui.bed_sku,
        bed_details=ui.bed_details,
        status=ui.status,
        worker_state=ui.worker_state,
        started_at_epoch=ui.started_at_epoch or 0.0,
        work_seconds=ui.work_seconds,
        idle_seconds=ui.idle_seconds,
        last_pack_seconds=ui.last_pack_seconds,
        best_pack_seconds=ui.best_pack_seconds,
        avg_pack_seconds=ui.avg_pack_seconds,
        instruction_main=ui.instruction_main,
        instruction_sub=ui.instruction_sub,
        instruction_extra=ui.instruction_extra,
        current_step_index=ui.current_step_index,
        total_steps=ui.total_steps,
        completed_steps=ui.completed_steps,
        error_steps=ui.error_steps,
        steps=[
            Step(
                index=s.index,
                title=s.title,
                status=s.status,
                meta=s.meta,
            )
            for s in ui.steps
        ],
        events=[
            Event(
                ts_epoch=e.ts_epoch,
                time=e.time,
                text=e.text,
                level=e.level,
            )
            for e in ui.events
        ],
        camera_stream_url=ui.camera_stream_url,
        overlay_slots=[
            OverlaySlot(
                id=o.id,
                x=o.x,
                y=o.y,
                w=o.w,
                h=o.h,
                status=o.status,
                title=o.title,
            )
            for o in ui.overlay_slots
        ],
    )


@app.post("/api/kiosk/session/start")
async def start_session(payload: StartSessionRequest):
    worker_id = payload.worker_id or ""
    sku = payload.sku or ""

    # 1) обновляем контекст (можно сканировать по отдельности)
    if worker_id:
        engine.set_worker(worker_id=worker_id, worker_name=payload.worker_name, shift_label=payload.shift_label)
    if sku:
        engine.set_bed(product_code=sku)

    # 2) стартуем только когда есть И сотрудник И кровать
    ui = engine.get_ui_state()
    ready_worker = (ui.worker_name and ui.worker_name != "—")
    ready_bed = (ui.bed_sku and ui.bed_sku != "—")

    if ready_worker and ready_bed and ui.status == "idle":
        engine.start_session(
            worker_id=worker_id or ui.worker_name,
            worker_name=payload.worker_name or ui.worker_name,
            product_code=sku or ui.bed_sku,
            shift_label=payload.shift_label or ui.shift_label,
        )

    return {"status": "ok"}



@app.post("/api/kiosk/session/finish")
async def finish_session(payload: FinishSessionRequest):
    engine.finish_session(status=payload.status or "done")
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "service.kiosk_api:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
