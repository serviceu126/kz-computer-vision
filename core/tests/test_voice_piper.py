"""
Проверка голосового движка Piper.
ВАЖНО: в CI обычно нет PortAudio, поэтому тест пропускается,
если нет необходимых зависимостей.
"""

import pytest

try:
    import sounddevice  # noqa: F401
except OSError:
    pytest.skip("PortAudio недоступен для sounddevice", allow_module_level=True)

from core.voice_piper import PiperVoice


@pytest.mark.skip(reason="Аудио-тест предназначен для ручного запуска")
def test_piper_voice_manual():
    # Этот тест оставлен как документированный ручной сценарий.
    # В автоматическом прогоне он пропускается, чтобы не требовать аудио-устройств.
    v = PiperVoice("irina/medium")  # можно ruslan/medium или dmitri/medium
    v.say("Тест новой голосовой системы. Проверка связи.")
