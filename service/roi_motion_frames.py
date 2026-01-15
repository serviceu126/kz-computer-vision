#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сохранение КАДРОВ (а не видео) только при движении внутри ROI.

Почему это лучше для качества:
- PNG = без потерь (идеально для мелких деталей, наклеек, текстуры)
- потом из кадров можно собрать видео или сразу размечать

Как работает:
- читаем RTSP
- вырезаем ROI из roi.json
- считаем движение (разница по пикселям)
- если движение есть -> "режим записи"
- в режиме записи сохраняем кадры с заданной частотой (save_fps)
- если тишина N секунд -> выключаем режим записи
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


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def save_frame_png(path: str, img) -> None:
    """
    PNG без потерь.
    compression=0 -> максимальное качество и скорость, но файлы больше.
    """
    cv2.imwrite(path, img, [cv2.IMWRITE_PNG_COMPRESSION, 0])


def save_frame_jpg(path: str, img, quality: int) -> None:
    """
    JPEG с очень высоким качеством (98-100).
    """
    cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rtsp", required=True, help="RTSP URL камеры")
    ap.add_argument("--roi", default="roi.json", help="Путь к roi.json")
    ap.add_argument("--outdir", default="~/kz_frames", help="Куда сохранять кадры")
    ap.add_argument("--process_fps", type=float, default=15.0, help="Как часто обрабатывать кадры (нагрузка CPU)")
    ap.add_argument("--save_fps", type=float, default=5.0, help="Как часто сохранять кадры в режиме записи")
    ap.add_argument("--format", choices=["png", "jpg"], default="png", help="Формат сохранения (png=лучшее качество)")
    ap.add_argument("--jpg_quality", type=int, default=98, help="Качество JPEG (если format=jpg)")
    ap.add_argument("--min_record_sec", type=float, default=3.0, help="Минимальная длительность сцены")
    ap.add_argument("--stop_after_silence_sec", type=float, default=2.0, help="Стоп, если тишина N секунд")
    ap.add_argument("--start_thresh", type=int, default=6000, help="Порог старта (пикселей движения)")
    ap.add_argument("--stop_thresh", type=int, default=2500, help="Порог тишины для остановки")
    args = ap.parse_args()

    outdir = os.path.expanduser(args.outdir)
    ensure_dir(outdir)

    rois = load_rois(args.roi)

    cap = cv2.VideoCapture(args.rtsp)
    if not cap.isOpened():
        raise RuntimeError("Не удалось открыть RTSP. Проверь URL/доступ/сеть.")

    # иногда помогает уменьшить буфер у RTSP
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
    except Exception:
        pass

    prev_roi_gray = {name: None for name in rois.keys()}

    recording = False
    record_start_ts = 0.0
    last_motion_ts = 0.0
    last_save_ts = 0.0

    # папки текущей сцены
    scene_dir = None
    frame_idx = 0
    scene_id = 0


    process_dt = 1.0 / max(1e-6, args.process_fps)
    save_dt = 1.0 / max(1e-6, args.save_fps)

    print("Сохранение кадров по движению запущено. Ctrl+C для остановки.")
    print(f"outdir={outdir} format={args.format} save_fps={args.save_fps}")

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            time.sleep(0.2)
            continue

        time.sleep(max(0.0, process_dt - 0.001))

        motion_sum = 0
        for name, r in rois.items():
            roi_img = crop(frame, r)
            gray = cv2.cvtColor(roi_img, cv2.COLOR_BGR2GRAY)
            if prev_roi_gray[name] is not None:
                motion_sum += motion_score(prev_roi_gray[name], gray)
            prev_roi_gray[name] = gray

        now = time.time()

        # старт "сцены"
        if not recording and motion_sum >= args.start_thresh:
            recording = True
            record_start_ts = now
            last_motion_ts = now
            last_save_ts = 0.0
            frame_idx = 0

            scene_id += 1
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

                # Папка на день, чтобы не плодить тысячи папок
            day_dir = os.path.join(outdir, datetime.now().strftime("%Y-%m-%d"))
            ensure_dir(day_dir)

            # Внутри — две папки по зонам
            for name in rois.keys():
                ensure_dir(os.path.join(day_dir, name))

            scene_dir = day_dir
            print(f"[REC START] scene_id={scene_id} motion={motion_sum} -> {scene_dir}")

        if recording:
            if motion_sum >= args.stop_thresh:
                last_motion_ts = now

            # сохраняем кадры не чаще save_fps
            if (now - last_save_ts) >= save_dt:
                                  # сохраняем ПОЛНЫЙ кадр, а не ROI
                    frame_idx += 1
                    ext = args.format           
                    fname = f"s{scene_id:03d}_{stamp[11:]}_f{frame_idx:06d}.{ext}"

                    fpath = os.path.join(scene_dir, fname)

                    if args.format == "png":
                        save_frame_png(fpath, frame)
                    else:
                        save_frame_jpg(fpath, frame, args.jpg_quality)

                    last_save_ts = now


            # стоп "сцены"
            if (now - last_motion_ts) >= args.stop_after_silence_sec and (now - record_start_ts) >= args.min_record_sec:
                recording = False
                print(f"[REC STOP] motion={motion_sum} saved_frames={frame_idx} -> {scene_dir}")
                scene_dir = None


if __name__ == "__main__":
    main()
