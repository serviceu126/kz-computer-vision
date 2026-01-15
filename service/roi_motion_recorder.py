# -*- coding: utf-8 -*-
"""
Запись видео ТОЛЬКО при движении внутри заданных ROI.

Зачем:
- не снимать «всё подряд»
- получать короткие клипы со сценами работы

Как работает (простыми словами):
- берём кадр из RTSP
- вырезаем по ROI (work_table и pack_zone)
- считаем "сколько изменилось пикселей" относительно предыдущего кадра
- если изменений много -> старт записи
- если изменений мало N секунд -> стоп записи
"""

import argparse
import json
import os
import time
from datetime import datetime

import cv2
import numpy as np


def load_rois(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        rois = json.load(f)
    for name, r in rois.items():
        for k in ("x1", "y1", "x2", "y2"):
            if k not in r:
                raise ValueError(f"ROI '{name}' не содержит ключ '{k}'")
    return rois


def crop(frame, r):
    return frame[r["y1"]:r["y2"], r["x1"]:r["x2"]]


def motion_score(prev_gray, gray, blur_ksize=9, thresh=25) -> int:
    diff = cv2.absdiff(prev_gray, gray)
    diff = cv2.GaussianBlur(diff, (blur_ksize, blur_ksize), 0)
    _, bw = cv2.threshold(diff, thresh, 255, cv2.THRESH_BINARY)
    return int(np.count_nonzero(bw))


def make_writer(path: str, w: int, h: int, fps: float):
    """
    Пишем AVI с MJPEG.
    Почему так:
    - MJPEG намного меньше портит мелкие детали, чем mp4v из OpenCV
    - для обучения/разметки это удобнее
    Минус: файлы больше.
    """
    # Меняем расширение на .avi, чтобы контейнер соответствовал кодеку
    if path.lower().endswith(".mp4"):
        path = path[:-4] + ".avi"

    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    return cv2.VideoWriter(path, fourcc, fps, (w, h))



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rtsp", required=True, help="RTSP URL камеры")
    ap.add_argument("--roi", default="roi.json", help="Путь к roi.json")
    ap.add_argument("--outdir", default="~/kz_recordings", help="Куда сохранять клипы")
    ap.add_argument("--fps", type=float, default=15.0, help="FPS для записи (обычно 10-15 достаточно)")
    ap.add_argument("--min_record_sec", type=float, default=3.0, help="Минимальная длительность клипа")
    ap.add_argument("--stop_after_silence_sec", type=float, default=2.0, help="Стоп, если тишина N секунд")
    ap.add_argument("--start_thresh", type=int, default=6000, help="Порог старта (пикселей движения)")
    ap.add_argument("--stop_thresh", type=int, default=2500, help="Порог тишины для остановки")
    args = ap.parse_args()

    outdir = os.path.expanduser(args.outdir)
    os.makedirs(outdir, exist_ok=True)

    rois = load_rois(args.roi)

    cap = cv2.VideoCapture(args.rtsp)
    if not cap.isOpened():
        raise RuntimeError("Не удалось открыть RTSP. Проверь URL/доступ/сеть.")

    # иногда помогает уменьшить буфер для RTSP
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
    except Exception:
        pass

    prev_roi_gray = {name: None for name in rois.keys()}

    recording = False
    writers = {}
    record_start_ts = 0.0
    last_motion_ts = 0.0

    print("Запись по движению запущена. Ctrl+C для остановки.")
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            time.sleep(0.2)
            continue

        # ограничиваем частоту кадров для обработки/записи
        time.sleep(max(0.0, (1.0 / args.fps) - 0.001))

        motion_sum = 0
        for name, r in rois.items():
            roi = crop(frame, r)
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            if prev_roi_gray[name] is not None:
                motion_sum += motion_score(prev_roi_gray[name], gray)
            prev_roi_gray[name] = gray

        now = time.time()

        # старт записи
        if not recording and motion_sum >= args.start_thresh:
            recording = True
            record_start_ts = now
            last_motion_ts = now

            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            writers = {}
            for name, r in rois.items():
                w = r["x2"] - r["x1"]
                h = r["y2"] - r["y1"]
                path = os.path.join(outdir, f"{stamp}_{name}.mp4")
                writers[name] = make_writer(path, w, h, args.fps)

            print(f"[REC START] motion={motion_sum}")

        # если пишем — пишем в файлы
        if recording:
            if motion_sum >= args.stop_thresh:
                last_motion_ts = now

            for name, r in rois.items():
                writers[name].write(crop(frame, r))

            # стоп записи
            if (now - last_motion_ts) >= args.stop_after_silence_sec and (now - record_start_ts) >= args.min_record_sec:
                for w in writers.values():
                    w.release()
                writers = {}
                recording = False
                print(f"[REC STOP] motion={motion_sum}")


if __name__ == "__main__":
    main()
