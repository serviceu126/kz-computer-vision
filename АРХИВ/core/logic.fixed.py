import time
import threading
from datetime import date
from dataclasses import dataclass, field
from typing import List, Literal, Optional

from core.session import PackSession
from core.storage import init_db, save_session
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

    bed_title: str
    bed_sku: str
    bed_details: str

    status: Literal["idle", "running", "error", "done"]
    worker_state: Literal["working", "idle"]

    started_at_epoch: float
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

        # ==============================
        # Данные, которые постоянно выводятся в интерфейсе
        # ==============================
        # Сотрудник / смена
        self._current_worker_id: str = ""
        self._current_worker_name: str = "—"
        self._current_shift_label: str = "Смена не выбрана"

        # Текущая кровать
        self._current_bed_title: str = "Кровать не выбрана"
        self._current_bed_sku: str = "—"
        self._current_bed_details: str = "Размер — | Цвет — | Вид —"

        # ==============================
        # Счётчики "сегодня" (обновляются по ходу работы)
        # ==============================
        self._stats_day: date = date.today()          # какая дата сейчас учтена
        self._beds_packed_today: int = 0              # сколько кроватей упаковано за день
        self._idle_today_seconds: float = 0.0         # суммарный простой (сек) за день
        self._idle_started_ts: Optional[float] = None # когда начался текущий простой

        # Последнее время упаковки (показываем после завершения, пока ждём следующую кровать)
        self._last_pack_seconds: int = 0

        # статический поток камеры (потом вынесём в конфиг)
        self.camera_stream_url = "http://127.0.0.1:8080/stream"

    # ─── сервисные функции ───

    def _rollover_day_if_needed(self) -> None:
        """Если наступил новый день — обнуляем дневные счётчики."""
        today = date.today()
        if today != self._stats_day:
            self._stats_day = today
            self._beds_packed_today = 0
            self._idle_today_seconds = 0.0
            self._idle_started_ts = None
            self._last_pack_seconds = 0

    def _ensure_idle_running(self, now: float) -> None:
        """Если сейчас нет активной сессии, запускаем счётчик простоя."""
        if self._session is None and self._current_worker_name != "—":
            if self._idle_started_ts is None:
                self._idle_started_ts = now

    def _stop_idle_and_add(self, now: float) -> None:
        """Останавливает простой и добавляет его длительность в сумму."""
        if self._idle_started_ts is not None:
            self._idle_today_seconds += max(0.0, now - self._idle_started_ts)
            self._idle_started_ts = None

    # ─── управление сессией ───

    def start_session(
        self,
        worker_id: str,
        worker_name: str,
        product_code: str,
        bed_title: str,
        bed_details: str,
        shift_label: str = "Смена не выбрана",
    ) -> None:
        """
        Старт новой сессии упаковки.
        (потом повесим на скан QR)
        """
        # На всякий случай: если наступил новый день — обнуляем дневную статистику.
        self._rollover_day_if_needed()

        info = get_bed_info(product_code)
        if info:
            bed_title = info.title
            bed_details = info.details
        with self._lock:
            # Если до этого мы стояли в ожидании — считаем простой до момента старта.
            if self._idle_started_ts is not None:
                self._idle_today_sec += max(0.0, time.time() - self._idle_started_ts)
                self._idle_started_ts = None

            self._session = PackSession(
                worker_id=worker_id,
                product_code=product_code,
                start_time=time.time(),
                status="running",
            )
            self._session_start_ts = self._session.start_time

            # простая голосовая подсказка
            say(f"Начинаем упаковку: {bed_title}")

            # FIXME: здесь потом можно инициализировать список шагов по SKU
            now = time.time()

            # Если до этого была пауза (ожидание новой кровати), то закрываем её.
            # Это нужно, чтобы "Простой" перестал расти в момент начала следующей упаковки.
            if self._idle_started_ts is not None:
                self._idle_today_seconds += max(0.0, now - self._idle_started_ts)
                self._idle_started_ts = None

            # Регистрируем сотрудника/смену и текущую кровать.
            self._current_worker_id = worker_id
            self._current_worker_name = worker_name
            self._current_shift_label = shift_label
            self._current_bed_title = bed_title
            self._current_bed_details = bed_details
            self._current_bed_sku = product_code

            # В момент старта новой сессии «последнее время упаковки» обнуляем,
            # чтобы в UI показывалось время текущей сессии.
            self._last_pack_seconds = 0

    def finish_session(self, status: str = "done") -> None:
        with self._lock:
            if not self._session:
                return

            self._session.finish(status=status)
            save_session(self._session)

            # На всякий случай: если наступил новый день — обнуляем дневную статистику.
            # (finish может прилететь уже после полуночи).
            self._rollover_day_if_needed()

            total_sec = int(self._session.worktime_sec + self._session.downtime_sec)
            sku = self._session.product_code

            # "Последняя упаковка" для UI: после завершения сессии время должно остановиться.
            self._last_pack_seconds = total_sec

            # Дневная статистика: считаем только успешные упаковки.
            if status == "done":
                self._beds_packed_today += 1

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

            # Сбрасываем текущую кровать и переводим систему в ожидание следующего скана.
            self._session = None
            self._current_bed_title = "Кровать не выбрана"
            self._current_bed_details = "Размер — | Цвет — | Вид —"
            self._current_bed_sku = ""

            # После завершения упаковки начинается простой (ожидание новой кровати).
            # Простой считаем даже если работник ничего не делает, чтобы видеть паузы между сессиями.
            self._idle_started_ts = time.time()
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
            # Если наступил новый день — сбрасываем дневные счётчики.
            self._rollover_day_if_needed()

            now = time.time()

            # ==============================
            # 1) Нет активной сессии — режим ожидания
            # ==============================
            if not self._session:
                # В ожидании считаем простой, если сотрудник уже зарегистрирован.
                if self._is_worker_registered() and self._idle_started_ts is None:
                    self._idle_started_ts = now

                idle_total_sec = self._get_idle_today_seconds(now)

                # В "Время упаковки" показываем последнее завершённое время (до старта новой кровати).
                # Это даёт эффект: "упаковка остановилась", а "простой пошёл".
                work_sec = int(self._last_pack_seconds or 0)
                idle_sec = int(idle_total_sec)

                # "Сегодня: ..." — считаем упакованные кровати и суммарный простой.
                worker_stats = f"Сегодня: {self._beds_packed_today} кроватей, простоев {idle_sec // 60} мин"

                # Статистика "по текущей кровати" в ожидании не привязана к SKU, поэтому показываем 0.
                last_pack = 0
                best_pack = 0
                avg_pack = 0

                return KioskUIState(
                    worker_name=getattr(self, "_current_worker_name", "—"),
                    shift_label=getattr(self, "_current_shift_label", "Смена не выбрана"),
                    worker_stats=worker_stats,
                    bed_title=getattr(self, "_current_bed_title", "Кровать не выбрана"),
                    bed_sku=getattr(self, "_current_bed_sku", "—") or "—",
                    bed_details=getattr(self, "_current_bed_details", "Размер — | Цвет — | Вид —"),
                    status="idle",
                    worker_state="idle",
                    started_at_epoch=now,
                    work_seconds=work_sec,
                    idle_seconds=idle_sec,
                    last_pack_seconds=last_pack,
                    best_pack_seconds=best_pack,
                    avg_pack_seconds=avg_pack,
                    instruction_main="Ожидание сканирования кровати…",
                    instruction_sub="Просканируйте QR-код кровати для старта новой упаковки.",
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

            # ==============================
            # 2) Есть активная сессия
            # ==============================
            sess = self._session
            elapsed = now - sess.start_time

            # Автозавершение: как только дошли до планового времени — фиксируем "done",
            # сохраняем сессию, увеличиваем дневной счётчик, очищаем "Текущую кровать" и
            # переводим киоск в ожидание следующего сканирования.
            if sess.status == "running" and elapsed >= self.TARGET_PACK_TIME:
                # 1) Финализируем сессию
                sess.finish(status="done")
                save_session(sess)

                # 2) Обновляем дневные счётчики
                self._beds_packed_today += 1
                self._last_pack_seconds = int(elapsed)

                # 3) Переводим в простой
                self._idle_started_ts = now

                # 4) Очищаем "текущую кровать" и сбрасываем активную сессию
                self._clear_current_bed()
                self._session = None

                # Возвращаем UI как в режиме ожидания (последнее время упаковки + растущий простой)
                # Повторный вызов не приведёт к рекурсии, т.к. self._session уже None.
                return self.get_ui_state()

            # Строим шаги/слоты/события
            current_step_index, completed_steps, steps, slots = self._build_steps_and_slots(now)
            events = self._build_events(now, completed_steps)

            # Внутри активной сессии пока считаем всю длительность как "работу".
            work_sec = int(elapsed)
            idle_sec = int(sess.downtime_sec)

            # Статистика по SKU (последняя/лучшая/средняя) — для текущей кровати
            sku = sess.product_code
            last_pack = self._last_pack_per_sku.get(sku, 0)
            best_pack = self._best_pack_per_sku.get(sku, 0)
            avg_pack = self._avg_pack_per_sku.get(sku, 0)

            # "Сегодня: ..." — суммарный простой считается вне сессий
            idle_total_sec = self._get_idle_today_seconds(now)
            worker_stats = f"Сегодня: {self._beds_packed_today} кроватей, простоев {int(idle_total_sec) // 60} мин"

            return KioskUIState(
                worker_name=getattr(self, "_current_worker_name", "—"),
                shift_label=getattr(self, "_current_shift_label", "Смена не выбрана"),
                worker_stats=worker_stats,
                bed_title=getattr(self, "_current_bed_title", "Кровать не выбрана"),
                bed_sku=sess.product_code,
                bed_details=getattr(self, "_current_bed_details", "Размер — | Цвет — | Вид —"),
                status=sess.status,
                worker_state="working",
                started_at_epoch=sess.start_time,
                work_seconds=work_sec,
                idle_seconds=idle_sec,
                last_pack_seconds=last_pack,
                best_pack_seconds=best_pack,
                avg_pack_seconds=avg_pack,
                instruction_main=f"Шаг {current_step_index}: уложите следующую деталь",
                instruction_sub="Положите деталь в подсвеченный слот на столе.",
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
