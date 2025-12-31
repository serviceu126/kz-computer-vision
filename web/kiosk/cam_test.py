"""
Скрипт ручной проверки RTSP-потока.
ВАЖНО: это не pytest-тест. При импорте в pytest — сразу пропускаем,
потому что в CI нет камеры и системных библиотек для OpenCV.
"""

if __name__ != "__main__":
    # pytest будет импортировать *_test.py модули.
    # Чтобы не падать на импорте cv2 и не зависеть от камеры — пропускаем.
    import pytest

    pytest.skip("RTSP/OpenCV скрипт пропущен в тестах", allow_module_level=True)

import cv2

url = "rtsp://admin:QsD4Fv%216@192.168.31.64:554/Streaming/Channels/102"

cap = cv2.VideoCapture(url)

if not cap.isOpened():
    print("❌ Не удалось открыть поток")
    exit()

while True:
    ok, frame = cap.read()
    if not ok:
        print("⚠️ Кадр не получен")
        break

    cv2.imshow("CAM 102", frame)

    if cv2.waitKey(1) == 27:  # ESC
        break

cap.release()
cv2.destroyAllWindows()
