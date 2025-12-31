from __future__ import annotations

import os
from pathlib import Path


class XttsVoice:
    """
    Заглушка XTTS v2.

    Реальная реализация будет подключать модель и воспроизводить звук.
    Здесь оставляем проверку доступности и интерфейс say(text).
    """

    def __init__(self, model_dir: str | None = None):
        self.model_dir = model_dir or os.getenv("KZ_TTS_XTTS_MODEL_DIR", "")

    def is_available(self) -> bool:
        if not self.model_dir:
            return False
        return Path(self.model_dir).exists()

    def say(self, text: str) -> None:
        if not self.is_available():
            raise RuntimeError("XTTS не доступен: модель не найдена.")
        if not text:
            return
        print(f"[XTTS] (stub) сказать: {text}")
