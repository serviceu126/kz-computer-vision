"""
Сервис сменных заданий упаковки.

Здесь мы держим выбранный план в памяти процесса.
Это сознательное решение для MVP: выбор не влияет на бизнес-логику упаковки
и используется только для отображения в UI, поэтому временная потеря выбора
после перезапуска сервиса допустима.
"""

from __future__ import annotations

from typing import Dict


_selected_plan_by_shift: Dict[int, int] = {}


def select_plan(shift_id: int, plan_id: int) -> None:
    """
    Запоминаем выбранный план для конкретной смены.

    Почему так:
    - оператор может работать по сменному заданию или без него;
    - выбор плана влияет только на UI (подсказка следующего SKU).
    """
    if shift_id <= 0 or plan_id <= 0:
        return
    _selected_plan_by_shift[shift_id] = plan_id


def get_selected_plan_id(shift_id: int) -> int:
    """
    Возвращает ID выбранного плана для смены.

    Если план не выбран, возвращаем 0 — UI покажет режим работы без списка.
    """
    return int(_selected_plan_by_shift.get(shift_id, 0))
