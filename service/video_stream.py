import os
import subprocess
import time
import threading
from typing import Iterator

# Учительская подсказка: берём RTSP из окружения, иначе используем текущий дефолт.
try:
    from service import mjpeg_server
    DEFAULT_RTSP_URL = getattr(mjpeg_server, "RTSP_URL", "")
except Exception:
    DEFAULT_RTSP_URL = ""

RTSP_URL = os.getenv("KZ_RTSP_URL", DEFAULT_RTSP_URL)

_status_lock = threading.Lock()
_status = {
    "status": "idle",
    "last_frame_ts": None,
    "last_error": None,
    "restarts": 0,
}


def _set_status(**updates) -> None:
    with _status_lock:
        _status.update(updates)


def get_status() -> dict:
    with _status_lock:
        return dict(_status)


def _start_ffmpeg() -> subprocess.Popen:
    cmd = [
        "ffmpeg",
        "-loglevel", "error",
        "-rtsp_transport", "tcp",
        "-i", RTSP_URL,
        "-vf", "fps=15,scale=2560:-1",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "-q:v", "5",
        "-",
    ]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=0)


def mjpeg_stream() -> Iterator[bytes]:
    """
    Учительская подсказка: поток MJPEG с автопереподключением.
    Перезапускаем ffmpeg, если RTSP падает или stdout обрывается.
    """
    if not RTSP_URL:
        _set_status(status="error", last_error="RTSP URL не задан", last_frame_ts=None)
        return

    while True:
        proc = _start_ffmpeg()
        _set_status(status="running", last_error=None)
        try:
            while True:
                header = proc.stdout.read(2)
                if not header:
                    raise RuntimeError("Поток RTSP завершился")
                if header != b"\xff\xd8":
                    continue

                jpg = bytearray(header)
                while True:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        raise RuntimeError("Поток RTSP завершился")
                    jpg.extend(chunk)
                    end_pos = jpg.find(b"\xff\xd9")
                    if end_pos != -1:
                        frame = bytes(jpg[: end_pos + 2])
                        _set_status(last_frame_ts=time.time())
                        yield (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" +
                            frame +
                            b"\r\n"
                        )
                        break
        except GeneratorExit:
            break
        except Exception as exc:
            _set_status(status="restarting", last_error=str(exc))
            with _status_lock:
                _status["restarts"] += 1
            time.sleep(1)
        finally:
            if proc and proc.poll() is None:
                proc.kill()
    _set_status(status="stopped")
