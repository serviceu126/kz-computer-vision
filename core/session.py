from dataclasses import dataclass, field
import time
from typing import Optional


@dataclass
class PackSession:
    worker_id: Optional[str]
    product_code: str   # модель/размер/цвет
    start_time: float = field(default_factory=time.time)

    finish_time: Optional[float] = None
    status: Optional[str] = None   # ok / fail
    downtime_sec: float = 0
    worktime_sec: float = 0

    last_activity: float = field(default_factory=time.time)
    # last_metrics_ts — это "последняя учтённая точка времени".
    # Мы используем её, чтобы на каждом запросе /state добавлять только дельту
    # и не допускать скачков/отрицательных значений в work/idle.
    last_metrics_ts: float = field(default_factory=time.time)


    def _update_timers(self, now: float, idle_threshold: float = 5.0) -> None:
        """
        Безопасное накопление work/idle.
        - Что делаем: считаем дельту с последнего учёта и относим её либо к работе,
          либо к простоям в зависимости от last_activity и порога idle_threshold.
        - Зачем: метрики должны расти монотонно и не "зависать" при отсутствии
          внешних вызовов event_activity/event_idle_tick.
        - Как влияет на метрики: work_seconds/idle_seconds обновляются при каждом
          чтении /api/kiosk/state (через engine.get_ui_state()).
        - Тестирование (curl):
          1) GET /api/kiosk/state несколько раз с паузой
          2) убедиться, что work_seconds/idle_seconds растут и не прыгают назад
        """
        if now is None:
            return
        delta = now - self.last_metrics_ts
        if delta <= 0:
            return

        idle_time = now - self.last_activity
        if idle_time >= idle_threshold:
            self.downtime_sec += delta
        else:
            self.worktime_sec += delta

        self.last_metrics_ts = now


    def event_activity(self):
        """Работник положил деталь / произошло действие"""
        now = time.time()
        # Перед фиксацией активности сначала учтём прошедшее время,
        # чтобы корректно распределить его между work/idle.
        self._update_timers(now)
        self.last_activity = now


    def event_idle_tick(self, idle_threshold=5):
        """Вызывается периодически — фиксируем простой"""
        now = time.time()
        # Вместо отдельной логики — используем единый расчёт,
        # чтобы не было отрицательных/скачущих значений.
        self._update_timers(now, idle_threshold=idle_threshold)


    def finish(self, status: str):
        now = time.time()
        # Перед завершением фиксируем время, чтобы в БД попали точные метрики.
        self._update_timers(now)
        self.finish_time = now
        self.status = status
