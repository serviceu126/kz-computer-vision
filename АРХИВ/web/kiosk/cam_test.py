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
