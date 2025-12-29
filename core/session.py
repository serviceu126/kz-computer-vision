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


    def event_activity(self):
        """Работник положил деталь / произошло действие"""
        now = time.time()
        self.worktime_sec += now - self.last_activity
        self.last_activity = now


    def event_idle_tick(self, idle_threshold=5):
        """Вызывается периодически — фиксируем простой"""
        now = time.time()
        idle = now - self.last_activity

        if idle >= idle_threshold:
            self.downtime_sec += idle
            self.last_activity = now


    def finish(self, status: str):
        self.finish_time = time.time()
        self.status = status
