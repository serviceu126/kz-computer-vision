import importlib
import importlib.util
import os

from core.voice_piper import PiperVoice
from core.voice_xtts import XttsVoice


def _apply_ruaccent(text: str) -> str:
    if os.getenv("KZ_TTS_RUACCENT", "0") != "1":
        return text

    if not importlib.util.find_spec("ruaccent"):
        return text

    module = importlib.import_module("ruaccent")
    accent_class = getattr(module, "RUAccent", None)
    if accent_class:
        return accent_class().accentuate(text)

    accent_fn = getattr(module, "accentuate", None)
    if accent_fn:
        return accent_fn(text)
    return text


def _select_voice():
    engine = os.getenv("KZ_TTS_ENGINE", "piper").lower()
    if engine == "xtts":
        xtts = XttsVoice()
        if xtts.is_available():
            return xtts
    return PiperVoice("irina/medium")


_voice = _select_voice()


def say(text: str):
    """Обёртка: во всём проекте использовать только её."""
    text = _apply_ruaccent(text)
    _voice.say(text)
