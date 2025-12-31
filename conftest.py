"""
Глобальные настройки pytest.
Здесь отключаем сбор архивных файлов, чтобы не тянуть устаревшие тесты.
"""

from __future__ import annotations

from pathlib import Path


def pytest_ignore_collect(collection_path: Path, config) -> bool:
    # Пропускаем архивные директории с устаревшими файлами,
    # чтобы избежать дублирования тестов и ошибок коллекции.
    return "АРХИВ" in str(collection_path)
