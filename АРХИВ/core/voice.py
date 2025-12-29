# core/voice.py
from core.voice_piper import PiperVoice

# здесь выбираем дефолтный голос под производство
_voice = PiperVoice("irina/medium")  # можно "ruslan/medium" или "dmitri/medium"


def say(text: str):
    """Обёртка: во всём проекте использовать только её."""
    _voice.say(text)
