#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Инструмент для ручного задания ROI мышкой.

Как пользоваться:
- Открывается окно с изображением (кадр/скриншот).
- ЛКМ: поставить точку.
- Нужно 2 клика на один прямоугольник: (левый верхний) -> (правый нижний).
- После 2 кликов прямоугольник фиксируется и подписывается именем ROI.
- Клавиши:
  1 / 2 — выбрать имя зоны (work_table / pack_zone)
  n — переключить имя (по кругу)
  u — отменить последний прямоугольник
  s — сохранить roi.json и выйти
  q — выйти без сохранения
"""

import argparse
import json
import os
from typing import Dict, List, Tuple

import cv2

NAMES = ["work_table", "pack_zone"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, help="Путь к изображению (кадр/скриншот)")
    ap.add_argument("--out", default="roi.json", help="Куда сохранить roi.json")
    args = ap.parse_args()

    img = cv2.imread(args.image)
    if img is None:
        raise SystemExit(f"Не удалось прочитать изображение: {args.image}")

    h, w = img.shape[:2]
    base = img.copy()

    rois: Dict[str, Dict[str, int]] = {}
    current_name_idx = 0
    clicks: List[Tuple[int, int]] = []

    def redraw():
        view = base.copy()

        # рисуем уже созданные прямоугольники
        for name, r in rois.items():
            cv2.rectangle(view, (r["x1"], r["y1"]), (r["x2"], r["y2"]), (0, 255, 0), 2)
            cv2.putText(view, name, (r["x1"], max(20, r["y1"] - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)

        # подсказки
        name = NAMES[current_name_idx]
        cv2.putText(view, f"IMAGE {w}x{h} | ROI: {name} | clicks: {len(clicks)}",
                    (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(view, "Keys: 1/2 select ROI | n next | u undo | s save | q quit",
                    (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)

        # если есть 1 клик — показываем точку
        if len(clicks) == 1:
            cv2.circle(view, clicks[0], 5, (0, 0, 255), -1)

        cv2.imshow("ROI DRAW", view)

    def on_mouse(event, x, y, flags, param):
        nonlocal clicks, rois
        if event == cv2.EVENT_LBUTTONDOWN:
            clicks.append((x, y))
            if len(clicks) == 2:
                (x1, y1), (x2, y2) = clicks
                # нормализуем
                x1, x2 = sorted([x1, x2])
                y1, y2 = sorted([y1, y2])
                name = NAMES[current_name_idx]
                rois[name] = {"x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2)}
                clicks = []
            redraw()

    cv2.namedWindow("ROI DRAW", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("ROI DRAW", on_mouse)

    redraw()
    while True:
        key = cv2.waitKey(30) & 0xFF
        if key == ord("q"):
            break
        if key == ord("s"):
            out = os.path.abspath(args.out)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(rois, f, ensure_ascii=False, indent=2)
            print(f"Saved: {out}")
            break
        if key == ord("u"):
            # удалить последнюю зону (по текущему имени)
            name = NAMES[current_name_idx]
            rois.pop(name, None)
            clicks = []
            redraw()
        if key == ord("n"):
            current_name_idx = (current_name_idx + 1) % len(NAMES)
            clicks = []
            redraw()
        if key == ord("1"):
            current_name_idx = 0
            clicks = []
            redraw()
        if key == ord("2"):
            current_name_idx = 1
            clicks = []
            redraw()

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
