import subprocess
import threading
import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

#RTSP_URL = "rtsp://admin:QsD4Fv%216@192.168.31.64:554/Streaming/Channels/102" #Урезанный поток
RTSP_URL = "rtsp://admin:QsD4Fv%216@192.168.31.64:554/Streaming/Channels/101" #Основной поток



app = FastAPI()

latest_frame = None
frame_lock = threading.Lock()


def get_latest_frame():
    global latest_frame
    with frame_lock:
        if latest_frame is None:
            return None
        return latest_frame.copy()


def mjpeg_generator():
    """
    Берём RTSP через ffmpeg, получаем JPEG-кадры через stdout (image2pipe),
    и отдаём их браузеру как multipart/x-mixed-replace.
    Параллельно декодируем JPEG в numpy и сохраняем latest_frame.
    """
    cmd = [
        "ffmpeg",
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", RTSP_URL,
        "-vf", "fps=15,scale=2560:-1",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "-q:v", "5",
        "-"
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)

    try:
        while True:
            # Ищем начало JPEG: FF D8
            b = proc.stdout.read(2)
            if not b:
                break
            if b != b"\xff\xd8":
                continue

            jpg = bytearray(b)

            # Дочитываем JPEG до конца: FF D9
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    return
                jpg.extend(chunk)

                end_pos = jpg.find(b"\xff\xd9")
                if end_pos != -1:
                    frame = bytes(jpg[:end_pos + 2])

                    # 1) сохраняем кадр как numpy (BGR)
                    img = cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if img is not None:
                        global latest_frame
                        with frame_lock:
                            latest_frame = img

                    # 2) отдаём в браузер как MJPEG
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" +
                        frame +
                        b"\r\n"
                    )
                    break
    finally:
        proc.kill()


@app.get("/stream")
def stream():
    return StreamingResponse(
        mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )
@app.get("/debug/frame_info")
def frame_info():
    f = get_latest_frame()
    if f is None:
        return {"frame": None}
    return {
        "frame": "ok",
        "shape": f.shape
    }
