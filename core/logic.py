import time
from datetime import datetime
import threading
from dataclasses import dataclass, field
from typing import List, Literal, Optional

from core.session import PackSession
from core.storage import (
    init_db,
    save_session,
    start_worker_shift,
    end_worker_shift,
    get_active_shifts,
    get_latest_active_shift_id,
    count_sessions_since,
<<<<<<< HEAD
=======
    add_event,
>>>>>>> main
)
from services.timers import compute_work_idle_seconds, get_heartbeat_age_sec
from core.voice import say
from core.beds_catalog import get_bed_info
# from core.detector import Detector  # подключим, когда будем работать с видео


# ────────────────────────
# DTO для фронта (UI state)
# ────────────────────────

@dataclass
class OverlaySlotDTO:
    id: int
    x: float
    y: float
    w: float
    h: float
    status: Literal["pending", "current", "done"]
    title: str = ""


@dataclass
class StepDTO:
    index: int
    title: str
    status: Literal["pending", "current", "done", "error"]
    meta: str = ""


@dataclass
class EventDTO:
    ts_epoch: float
    time: str
    text: str
    level: Literal["info", "warning", "error"] = "info"


@dataclass
class KioskUIState:
    worker_name: str
    shift_label: str
    worker_stats: str
    session_count_today: int

    # Смена/команда (для кнопок и модалки)
    shift_active: bool
    active_workers: List[dict]


    bed_title: str
    bed_sku: str
    bed_details: str

    status: Literal["idle", "running", "error", "done"]
    worker_state: Literal["working", "idle"]

    started_at_epoch: Optional[float]
    work_seconds: int
    idle_seconds: int
    # Таймерные поля на основе событий:
    # timer_state: текущее состояние таймера (work/idle/None)
    # work_minutes/idle_minutes: округление вниз до минут
    # heartbeat_age_sec: возраст последнего heartbeat (секунды) или None
    timer_state: Optional[str]
    work_minutes: int
    idle_minutes: int
    heartbeat_age_sec: Optional[int]

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
    steps: List[StepDTO] = field(default_factory=list)

    events: List[EventDTO] = field(default_factory=list)

    camera_stream_url: str = ""
    overlay_slots: List[OverlaySlotDTO] = field(default_factory=list)


# ────────────────────────
# KioskEngine
# ────────────────────────


class KioskEngine:
    """
    Один экземпляр на киоск (на рабочее место).
    Управляет текущей сессией упаковки и возвращает состояние для фронта.
    """

    TOTAL_STEPS = 6
    STEP_DURATION = 15.0  # секунд на один шаг
    TARGET_PACK_TIME = TOTAL_STEPS * STEP_DURATION

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # инициализируем базу
        init_db()

        # текущая сессия
        self._session: Optional[PackSession] = None
        self._session_start_ts: Optional[float] = None

        # временная "заглушка" по SKU (потом заменим на реальные данные из БД)
        self._last_pack_per_sku = {}  # sku -> last_sec
        self._best_pack_per_sku = {}
        self._avg_pack_per_sku = {}

        # статический поток камеры (потом вынесём в конфиг)
        self.camera_stream_url = "http://127.0.0.1:8080/stream"

        # кэш активных смен (для быстрого состояния UI)
        self._active_shifts_cache: list[dict] = []

    
    
    def _normalize_scan(self, s: Optional[str]) -> str:
        if not s:
            return ""
        s = s.strip()
        # раскладка RU -> EN для сканера-клавиатуры
        s = s.replace("Ю", ".").replace("ю", ".").replace("Б", ",").replace("б", ",")
        return s

    def _get_day_start_ts(self, now: float) -> float:
        local_now = time.localtime(now)
        day_start = time.struct_time((
            local_now.tm_year,
            local_now.tm_mon,
            local_now.tm_mday,
            0,
            0,
            0,
            local_now.tm_wday,
            local_now.tm_yday,
            local_now.tm_isdst,
        ))
        return time.mktime(day_start)

    def set_worker(self, worker_id: str, worker_name: Optional[str], shift_label: Optional[str]) -> None:
        with self._lock:
            wid = self._normalize_scan(worker_id)
            self._current_worker_name = worker_name or wid or "—"
            self._current_shift_label = shift_label or "Смена не выбрана"

    # ─── смены/РЦ ───

    def add_worker_to_shift(self, worker_id: str, work_center: str) -> int:
        """Открывает смену сотрудника на выбранном РЦ (УПАКОВКА/УКОМПЛЕКТОВКА)."""
        wid = self._normalize_scan(worker_id)
        wc = (work_center or "").strip().upper()
        if not wid or not wc:
            return 0
<<<<<<< HEAD
        # ВАЖНО: start_worker_shift возвращает shift_id.
        # Это нужно для API /api/kiosk/shift/start, чтобы отдать идентификатор смены.
=======

        # Запускаем смену и получаем её ID,
        # чтобы при необходимости вернуть его в API.
>>>>>>> main
        shift_id = start_worker_shift(wid, wc)
        self._active_shifts_cache = get_active_shifts()

        # для верхней карточки показываем «текущего» сотрудника, если ещё не задан
        if getattr(self, "_current_worker_name", "—") in ("—", ""):
            self._current_worker_name = wid
        return shift_id

    def close_worker_shift(self, worker_id: str, work_centers: Optional[list[str]] = None) -> int:
        wid = self._normalize_scan(worker_id)
        if not wid:
            return 0
        n = end_worker_shift(wid, work_centers=work_centers)
        self._active_shifts_cache = get_active_shifts()
        return n

    def get_active_session_shift_context(self) -> tuple[int | None, str | None]:
        """
        Возвращаем (shift_id, worker_id) для активной упаковочной сессии.
        - Это нужно для эндпоинтов таймера (state/heartbeat),
          чтобы корректно привязать событие к смене.
        """
        with self._lock:
            if not self._session:
                return None, None
            return getattr(self._session, "shift_id", None), self._session.worker_id

    def set_bed(self, product_code: str) -> None:
        code = self._normalize_scan(product_code)
        if not code:
            return
        info = get_bed_info(code)
        bed_title = info.title if info else f"Кровать {code}"
        bed_details = info.details if info else "Размер — | Цвет — | Вид —"
        with self._lock:
            self._current_bed_sku = code
            self._current_bed_title = bed_title
            self._current_bed_details = bed_details



   
    # ─── управление сессией ───

    def start_session(
        self,
        worker_id: str,
        worker_name: Optional[str] = None,
        product_code: Optional[str] = None,
        sku: Optional[str] = None,
        bed_title: str = "",
        bed_details: str = "",
        shift_label: str = "Смена не выбрана",
    ) -> None:
        """
        Старт новой сессии упаковки.
        (потом повесим на скан QR)
        """
        # product_code — внутренний ключ. Иногда фронт присылает sku.
        code = self._normalize_scan(product_code or sku)
        if not code:
            return


        # Пытаемся найти активную смену сотрудника.
        # Это нужно, чтобы автоматически привязать упаковочную сессию к смене.
        wid = self._normalize_scan(worker_id)
        shift_id = get_latest_active_shift_id(wid) if wid else None

        info = get_bed_info(code)
        if info:
            bed_title = info.title
            bed_details = info.details
        else:
            # если в каталоге нет — хотя бы показываем SKU
            if not bed_title:
                bed_title = "Кровать " + code
            if not bed_details:
                bed_details = "Размер — | Цвет — | Вид —"
        # ВАЖНО: смена может быть не открыта.
        # В этом случае shift_id будет None, и это допустимо для таблицы sessions.
        shift_id = get_latest_active_shift_id(worker_id)
        with self._lock:
            self._session = PackSession(
                worker_id=worker_id,
                product_code=code,
                start_time=time.time(),
                status="running",
            )
<<<<<<< HEAD
            # Привязываем сессию к активной смене (если она есть).
            # Мы не добавляем поле в PackSession, а используем динамический атрибут,
            # чтобы не менять отдельный файл core/session.py.
=======
            # Сохраняем shift_id прямо в объекте сессии,
            # чтобы при записи в БД сохранить привязку к смене.
>>>>>>> main
            self._session.shift_id = shift_id
            self._session_start_ts = self._session.start_time

            # простая голосовая подсказка
            say(f"Начинаем упаковку: {bed_title}")

            # FIXME: здесь потом можно инициализировать список шагов по SKU
            self._current_worker_name = (worker_name or worker_id)
            self._current_shift_label = shift_label
            self._current_bed_sku = code
            self._current_bed_title = bed_title
            self._current_bed_details = bed_details

    def finish_session(self, status: str = "done") -> None:
        with self._lock:
            if not self._session:
                return

            self._session.finish(status=status)
            session_id = save_session(self._session)

            # Если упаковка завершилась успешно, фиксируем событие,
            # чтобы packed_count считался по events (без ручных счётчиков).
            if status == "done":
                add_event(
                    type="PACKED_CONFIRMED",
                    ts=self._session.finish_time or time.time(),
                    shift_id=getattr(self._session, "shift_id", None),
                    session_id=session_id or None,
                    worker_id=self._session.worker_id,
                )

            total_sec = int(self._session.worktime_sec + self._session.downtime_sec)
            sku = self._session.product_code

            self._last_pack_per_sku[sku] = total_sec

            best = self._best_pack_per_sku.get(sku)
            if best is None or total_sec < best:
                self._best_pack_per_sku[sku] = total_sec

            # простая "средняя" по 2 значениям (для примера)
            avg_prev = self._avg_pack_per_sku.get(sku)
            if avg_prev is None:
                self._avg_pack_per_sku[sku] = total_sec
            else:
                self._avg_pack_per_sku[sku] = int((avg_prev + total_sec) / 2)

            say("Упаковка завершена")

            self._session = None
            self._session_start_ts = None

    def _finish_session_locked(self, status: str = "done") -> None:
        #ВНИМАНИЕ: эту функцию вызываем ТОЛЬКО тогда, когда self._lock УЖЕ взят!

         #   Почему так:
         #   - get_ui_state() уже работает внутри "with self._lock:"
         #     - а finish_session() снова пытается взять self._lock
         #    - это может привести к зависанию (deadlock)

         #    Поэтому делаем "внутреннюю" версию завершения сессии без повторного lock.
    
        if not self._session:
            return  # если сессии нет — нечего завершать

        # 1) фиксируем завершение сессии
        self._session.finish(status=status)

        # 2) сохраняем в базу
        session_id = save_session(self._session)

        # Если упаковка завершилась успешно, пишем событие в events.
        # Это нужно для корректного packed_count в отчётах по смене.
        if status == "done":
            add_event(
                type="PACKED_CONFIRMED",
                ts=self._session.finish_time or time.time(),
                shift_id=getattr(self._session, "shift_id", None),
                session_id=session_id or None,
                worker_id=self._session.worker_id,
            )

        # 3) считаем время (работа+простой), чтобы обновить "последнее / лучшее / среднее"
        total_sec = int(self._session.worktime_sec + self._session.downtime_sec)
        sku = self._session.product_code

        # "Последняя упаковка"
        self._last_pack_per_sku[sku] = total_sec

        # "Лучшее время"
        best = self._best_pack_per_sku.get(sku)
        if best is None or total_sec < best:
            self._best_pack_per_sku[sku] = total_sec

        # "Среднее время" — пока простая формула (потом можно по-настоящему)
        avg_prev = self._avg_pack_per_sku.get(sku)
        if avg_prev is None:
            self._avg_pack_per_sku[sku] = total_sec
        else:
            self._avg_pack_per_sku[sku] = int((avg_prev + total_sec) / 2)

        # голос
        say("Упаковка завершена")

        # 4) очищаем текущую сессию в памяти (важно!)
        self._session = None
        self._session_start_ts = None


    # ─── внутренняя "симуляция" шагов ───

    def _build_steps_and_slots(self, now: float):
        """
        Пока что симуляция, завязанная на время.
        Позже сюда встраиваем результаты YOLO + правила проверки.
        """
        if not self._session_start_ts:
            # нет активной сессии — шаги пустые
            return 0, 0, [], []

        elapsed = now - self._session_start_ts

        raw_step_index = int(elapsed // self.STEP_DURATION)
        current_step_index = min(max(raw_step_index + 1, 0), self.TOTAL_STEPS)
        completed_steps = min(max(raw_step_index, 0), self.TOTAL_STEPS)

        # шаги
        steps: List[StepDTO] = []
        for i in range(self.TOTAL_STEPS):
            if i < completed_steps:
                st = "done"
            elif i == current_step_index - 1:
                st = "current"
            else:
                st = "pending"

            steps.append(
                StepDTO(
                    index=i,
                    title=f"Положите деталь {i + 1} в подсвеченную зону",
                    status=st,
                    meta="Проверьте ориентацию детали.",
                )
            )

        # слоты
        slots: List[OverlaySlotDTO] = []
        for i in range(self.TOTAL_STEPS):
            if i < completed_steps:
                st = "done"
            elif i == current_step_index - 1:
                st = "current"
            else:
                st = "pending"

            slots.append(
                OverlaySlotDTO(
                    id=i,
                    x=0.06 + 0.15 * i,
                    y=0.18 if i % 2 == 0 else 0.38,
                    w=0.12,
                    h=0.18,
                    status=st,
                    title=f"Деталь {i + 1}",
                )
            )

        return current_step_index, completed_steps, steps, slots

    def _build_events(self, now: float, completed_steps: int) -> List[EventDTO]:
        events: List[EventDTO] = []

        if self._session_start_ts:
            t0 = self._session_start_ts
            events.append(
                EventDTO(
                    ts_epoch=t0,
                    time=time.strftime("%H:%M", time.localtime(t0)),
                    text="Сессия упаковки запущена",
                    level="info",
                )
            )

            for i in range(completed_steps):
                ts = t0 + (i + 1) * self.STEP_DURATION
                if ts > now:
                    continue
                events.append(
                    EventDTO(
                        ts_epoch=ts,
                        time=time.strftime("%H:%M", time.localtime(ts)),
                        text=f"Деталь {i + 1} успешно уложена",
                        level="info",
                    )
                )

        events_sorted = sorted(events, key=lambda e: e.ts_epoch, reverse=True)
        return events_sorted[:6]

    # ─── публичное состояние для фронта ───

    def get_ui_state(self) -> KioskUIState:
        with self._lock:
            now = time.time()
            today_start = self._get_day_start_ts(now)
            session_count_today = count_sessions_since(today_start)

            # актуализируем список активных смен для UI
            try:
                self._active_shifts_cache = get_active_shifts()
            except Exception:
                # если БД временно недоступна — показываем то, что было
                self._active_shifts_cache = getattr(self, "_active_shifts_cache", [])

            shift_active = len(self._active_shifts_cache) > 0
            # Для расчёта таймера используем shift_id активной сессии,
            # либо первую активную смену из списка (минимальный fallback).
            shift_id_for_timer = None
            if self._session and getattr(self._session, "shift_id", None):
                shift_id_for_timer = self._session.shift_id
            elif self._active_shifts_cache:
                shift_id_for_timer = self._active_shifts_cache[0].get("shift_id")

            work_seconds = 0
            idle_seconds = 0
            timer_state = None
            heartbeat_age_sec = None
            if shift_id_for_timer:
                work_seconds, idle_seconds, timer_state = compute_work_idle_seconds(
                    shift_id_for_timer,
                    datetime.utcnow(),
                )
                heartbeat_age_sec = get_heartbeat_age_sec(
                    shift_id_for_timer,
                    datetime.utcnow(),
                )
            work_minutes = int(work_seconds // 60)
            idle_minutes = int(idle_seconds // 60)

            # нет активной сессии — показываем ожидание
            if not self._session:
                return KioskUIState(
                    worker_name=getattr(self, "_current_worker_name", "—"),
                    shift_label=getattr(self, "_current_shift_label", "Смена не выбрана"),
                    worker_stats=f"Сегодня: {session_count_today} кроватей, простоев 0 мин",
                    session_count_today=session_count_today,
                    shift_active=shift_active,
                    active_workers=self._active_shifts_cache,
                    bed_title=getattr(self, "_current_bed_title", "Кровать не выбрана"),
                    bed_sku=getattr(self, "_current_bed_sku", "—"),
                    bed_details=getattr(self, "_current_bed_details", "Размер — | Цвет — | Вид —"),
                    status="idle",
                    worker_state="idle",
                    started_at_epoch=None,
                    work_seconds=work_seconds,
                    idle_seconds=idle_seconds,
                    timer_state=timer_state,
                    work_minutes=work_minutes,
                    idle_minutes=idle_minutes,
                    heartbeat_age_sec=heartbeat_age_sec,
                    last_pack_seconds=self._last_pack_per_sku.get(getattr(self, "_current_bed_sku", "—"), 0),
                    best_pack_seconds=self._best_pack_per_sku.get(getattr(self, "_current_bed_sku", "—"), 0),
                    avg_pack_seconds=self._avg_pack_per_sku.get(getattr(self, "_current_bed_sku", "—"), 0),
                    instruction_main="Ожидание начала упаковки…",
                    instruction_sub="Просканируйте QR-код кровати и сотрудника для старта.",
                    instruction_extra="Голосовые подсказки повторяют текст.",
                    current_step_index=0,
                    total_steps=self.TOTAL_STEPS,
                    completed_steps=0,
                    error_steps=0,
                    steps=[],
                    events=[],
                    camera_stream_url=self.camera_stream_url,
                    overlay_slots=[],
                )

            # есть активная сессия
            sess = self._session
            # На каждом запросе /state безопасно обновляем таймеры.
            # Это устраняет "зависания" и отрицательные/скачущие значения.
            sess._update_timers(now, idle_threshold=5.0)
            status = sess.status
            work_sec = int(sess.worktime_sec)
            idle_sec = int(sess.downtime_sec)

            current_step_index, completed_steps, steps, slots = self._build_steps_and_slots(now)
            events = self._build_events(now, completed_steps)

                        # ─────────────────────────────────────────────────────────────
            # АВТО-ЗАВЕРШЕНИЕ СЕССИИ В ЭМУЛЯЦИИ
            #
            # Сейчас шаги "идут по времени": 6 шагов * 15 секунд.
            # Когда completed_steps дошёл до 6 — значит, все детали уложены.
            #
            # Нам нужно завершить сессию, чтобы:
            # - остановился таймер
            # - записалась статистика
            # - статус стал "done"
            #
            # ВАЖНО:
            # Мы НЕ вызываем finish_session(), потому что она снова берёт lock.
            # Вместо этого вызываем _finish_session_locked(), потому что lock уже взят.
            # ─────────────────────────────────────────────────────────────
            if status == "running" and completed_steps >= self.TOTAL_STEPS:
                # Добавим событие в ленту (приятно для UI)
                # (события у нас строятся отдельно, но это объяснение логики)
                # Завершаем сессию:
                self._finish_session_locked(status="done")

                # После завершения self._session стал None.
                # Значит нужно вернуть UI в "idle/done".
                # Проще всего — вернуть state как будто "нет активной сессии".
                return KioskUIState(
                    worker_name=getattr(self, "_current_worker_name", "—"),
                    shift_label=getattr(self, "_current_shift_label", "Смена не выбрана"),
                    worker_stats=f"Сегодня: {session_count_today} кроватей, простоев 0 мин",
                    session_count_today=session_count_today,
                    shift_active=shift_active,
                    active_workers=self._active_shifts_cache,
                    bed_title=getattr(self, "_current_bed_title", "Кровать не выбрана"),
                    bed_sku=getattr(self, "_current_bed_sku", "—"),
                    bed_details=getattr(self, "_current_bed_details", "Размер — | Цвет — | Вид —"),
                    status="done",                 # важно: показываем "Готово"
                    worker_state="idle",
                    started_at_epoch=None,          # таймер обнуляется
                    work_seconds=work_sec,          # оставляем посчитанное
                    idle_seconds=idle_sec,
                    timer_state=timer_state,
                    work_minutes=work_minutes,
                    idle_minutes=idle_minutes,
                    heartbeat_age_sec=heartbeat_age_sec,
                    last_pack_seconds=self._last_pack_per_sku.get(getattr(self, "_current_bed_sku", "—"), 0),
                    best_pack_seconds=self._best_pack_per_sku.get(getattr(self, "_current_bed_sku", "—"), 0),
                    avg_pack_seconds=self._avg_pack_per_sku.get(getattr(self, "_current_bed_sku", "—"), 0),
                    instruction_main="Комплект готов. Закройте коробку.",
                    instruction_sub="Можно сканировать следующую кровать, если стол пустой.",
                    instruction_extra="Этикетка печатается после определения 'коробка закрыта'.",
                    current_step_index=self.TOTAL_STEPS,
                    total_steps=self.TOTAL_STEPS,
                    completed_steps=self.TOTAL_STEPS,
                    error_steps=0,
                    steps=steps,
                    events=events,
                    camera_stream_url=self.camera_stream_url,
                    overlay_slots=slots,
                )


<<<<<<< HEAD
            work_sec = int(sess.worktime_sec)
            idle_sec = int(sess.downtime_sec)

            # пока грубо: считаем всю длительность как "работу"
            if status == "running":
                work_sec += int(elapsed)

            # Если есть события таймера, используем их как источник истины.
            # Это обеспечивает расчёт work/idle на основе событий.
            if timer_state is not None or work_seconds or idle_seconds:
                work_sec = work_seconds
                idle_sec = idle_seconds

=======
>>>>>>> main
            sku = sess.product_code
            last_pack = self._last_pack_per_sku.get(sku, 0)
            best_pack = self._best_pack_per_sku.get(sku, 0)
            avg_pack = self._avg_pack_per_sku.get(sku, 0)

            return KioskUIState(
                worker_name=getattr(self, "_current_worker_name", "—"),
                shift_label=getattr(self, "_current_shift_label", "Смена не выбрана"),
                worker_stats=f"Сегодня: {session_count_today} кроватей, простоев {idle_sec // 60} мин",
                session_count_today=session_count_today,
                shift_active=shift_active,
                active_workers=self._active_shifts_cache,
                bed_title=getattr(self, "_current_bed_title", "Кровать не выбрана"),
                bed_sku=sess.product_code,
                bed_details=getattr(self, "_current_bed_details", "Размер — | Цвет — | Вид —"),
                status=status,
                worker_state="working" if status == "running" else "idle",
                started_at_epoch=sess.start_time,
                work_seconds=work_sec,
                idle_seconds=idle_sec,
                timer_state=timer_state,
                work_minutes=work_minutes,
                idle_minutes=idle_minutes,
                heartbeat_age_sec=heartbeat_age_sec,
                last_pack_seconds=last_pack,
                best_pack_seconds=best_pack,
                avg_pack_seconds=avg_pack,
                instruction_main=(
                    "Комплект готов. Закройте коробку."
                    if status == "done"
                    else f"Шаг {current_step_index}: уложите следующую деталь"
                ),
                instruction_sub=(
                    "Проверьте, что все детали уложены и коробка закрыта."
                    if status == "done"
                    else "Положите деталь в подсвеченный слот на столе."
                ),
                instruction_extra="Следите за подсветкой и голосовыми подсказками.",
                current_step_index=current_step_index,
                total_steps=self.TOTAL_STEPS,
                completed_steps=completed_steps,
                error_steps=0,
                steps=steps,
                events=events,
                camera_stream_url=self.camera_stream_url,
                overlay_slots=slots,
            )


# Глобальный экземпляр для киоска
engine = KioskEngine()
