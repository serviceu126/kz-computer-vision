# core/tests/test_voice_piper.py
from core.voice_piper import PiperVoice
import time

print("== Старт теста Piper TTS ==")

v = PiperVoice("irina/medium")   # можно ruslan/medium или dmitri/medium

v.say("Тест новой голосовой системы. Проверка связи.")
time.sleep(1)
v.say("Положите первую деталь комплекта на стол.")
time.sleep(1)
v.say("Если деталь распознана верно, переходите к следующей.")

print("== Тест завершён ==")
