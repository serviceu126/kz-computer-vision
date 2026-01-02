import sqlite3
import time
import json
from pathlib import Path

DB = Path("storage/kz_pack.db")
DB.parent.mkdir(exist_ok=True)


def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        worker_id TEXT,
        product_code TEXT,
        start_time REAL,
        finish_time REAL,
        worktime_sec REAL,
        downtime_sec REAL,
        status TEXT,
        shift_id INTEGER
    )
    """)

    # ВАЖНО: добавляем связь с "сменой" (shift_id) безопасно.
    # Таблица sessions могла быть создана ранее без этого поля.
    # Поэтому делаем проверку структуры и добавляем колонку только при отсутствии.
    cur.execute("PRAGMA table_info(sessions)")
    session_columns = [row["name"] for row in cur.fetchall()]
    if "shift_id" not in session_columns:
        # shift_id может быть NULL, если активной смены нет.
        # Это важно для запуска сессии без заранее открытой смены.
        cur.execute("ALTER TABLE sessions ADD COLUMN shift_id INTEGER")

    # Учёт смен и рабочих центров (РЦ).
    # Миграция: добавляем shift_id в sessions, если его ещё нет.
    # Это поле остаётся NULL для старых записей и случаев без активной смены,
    # чтобы сохранить обратную совместимость и не ломать существующие данные.
    cur.execute("PRAGMA table_info(sessions)")
    session_columns = [row["name"] for row in (cur.fetchall() or [])]
    if "shift_id" not in session_columns:
        cur.execute("ALTER TABLE sessions ADD COLUMN shift_id INTEGER")

    # Учёт смен и рабочих центров (РЦ)
    # Один сотрудник может одновременно быть активен на нескольких РЦ.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS worker_shifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        worker_id TEXT NOT NULL,
        work_center TEXT NOT NULL,
        start_time REAL NOT NULL,
        end_time REAL,
        is_active INTEGER NOT NULL DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_worker_shifts_active
    ON worker_shifts(worker_id, is_active)
    """)

    # Минимальная таблица событий (events).
    # Зачем: хранит факты смены состояний таймера и heartbeat,
    # чтобы считать work/idle по событиям, а не по "тикерам".
    # CREATE TABLE IF NOT EXISTS безопасен для существующих БД.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        type TEXT NOT NULL,
        payload_json TEXT,
        shift_id INTEGER,
        session_id INTEGER,
        worker_id TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS pack_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT NOT NULL,
        start_time REAL NOT NULL,
        end_time REAL,
        state TEXT NOT NULL,
        shift_id INTEGER,
        worker_id TEXT,
        phase TEXT,
        current_step_index INTEGER,
        total_steps INTEGER
    )
    """)

    cur.execute("PRAGMA table_info(pack_sessions)")
    pack_columns = [row["name"] for row in cur.fetchall()]
    # Ниже — безопасная миграция: если база была создана ранее,
    # мы добавляем недостающие колонки без изменения существующих данных.
    # Это важно, чтобы не ломать рабочие станции при обновлении.
    if "shift_id" not in pack_columns:
        cur.execute("ALTER TABLE pack_sessions ADD COLUMN shift_id INTEGER")
    if "worker_id" not in pack_columns:
        cur.execute("ALTER TABLE pack_sessions ADD COLUMN worker_id TEXT")
    if "phase" not in pack_columns:
        cur.execute("ALTER TABLE pack_sessions ADD COLUMN phase TEXT")
    if "current_step_index" not in pack_columns:
        cur.execute("ALTER TABLE pack_sessions ADD COLUMN current_step_index INTEGER")
    if "total_steps" not in pack_columns:
        cur.execute("ALTER TABLE pack_sessions ADD COLUMN total_steps INTEGER")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS pack_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        type TEXT NOT NULL,
        payload_json TEXT,
        session_id INTEGER NOT NULL,
        sku TEXT
    )
    """)

    # Таблица сменных заданий для упаковки.
    # Мы сохраняем список SKU одной строкой JSON,
    # чтобы не плодить дополнительные таблицы на раннем этапе.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS shift_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shift_id INTEGER NOT NULL,
        created_at REAL NOT NULL,
        name TEXT NOT NULL,
        items_json TEXT NOT NULL
    )
    """)
    # Учительская ремарка:
    # добавляем флаг активности без жёстких миграций, чтобы старые базы не ломались.
    cur.execute("PRAGMA table_info(shift_plans)")
    shift_plan_columns = [row["name"] for row in cur.fetchall()]
    if "is_active" not in shift_plan_columns:
        cur.execute("ALTER TABLE shift_plans ADD COLUMN is_active INTEGER NOT NULL DEFAULT 0")

    # Таблица позиций сменного задания:
    # храним каждую строку отдельно, чтобы UI мог сортировать по позиции.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS shift_plan_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id INTEGER NOT NULL,
        sku_code TEXT NOT NULL,
        qty INTEGER NOT NULL,
        position INTEGER NOT NULL
    )
    """)

    # Таблица настроек киоска.
    # Храним простые флаги (0/1), чтобы быстро управлять правами оператора.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS kiosk_settings (
        key TEXT PRIMARY KEY,
        value INTEGER NOT NULL
    )
    """)

    # Таблица мастер-сессии киоска.
    # Держим одну строку (id=1), чтобы хранить master_id и таймштамп активности.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS kiosk_master_session (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        master_id TEXT,
        last_active_ts INTEGER,
        enabled INTEGER NOT NULL
    )
    """)

    # Дефолтные настройки киоска: если записей нет, добавляем их.
    # Это нужно, чтобы UI всегда получал ожидаемые значения.
    cur.execute(
        "INSERT OR IGNORE INTO kiosk_settings(key, value) VALUES (?, ?)",
        ["operator_can_reorder", 1],
    )
    cur.execute(
        "INSERT OR IGNORE INTO kiosk_settings(key, value) VALUES (?, ?)",
        ["operator_can_edit_qty", 1],
    )
    cur.execute(
        "INSERT OR IGNORE INTO kiosk_settings(key, value) VALUES (?, ?)",
        ["operator_can_add_sku_to_shift", 1],
    )
    cur.execute(
        "INSERT OR IGNORE INTO kiosk_settings(key, value) VALUES (?, ?)",
        ["operator_can_remove_sku_from_shift", 1],
    )
    cur.execute(
        "INSERT OR IGNORE INTO kiosk_settings(key, value) VALUES (?, ?)",
        ["operator_can_manual_mode", 1],
    )
    # Учительская ремарка: по умолчанию оператор НЕ может импортировать план с флешки.
    cur.execute(
        "INSERT OR IGNORE INTO kiosk_settings(key, value) VALUES (?, ?)",
        ["allow_operator_shift_plan_import", 0],
    )
    cur.execute(
        "INSERT OR IGNORE INTO kiosk_settings(key, value) VALUES (?, ?)",
        ["master_session_timeout_min", 15],
    )

    # Таблица каталога SKU для мастер-режима.
    # Она нужна для базового справочника, который мастер пополняет вручную.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sku_catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku_code TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        model_code TEXT NOT NULL,
        width_cm INTEGER NOT NULL,
        fabric_code TEXT NOT NULL,
        color_code TEXT NOT NULL,
        is_active INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """)

    # Таблица очереди (сменного задания) для упаковки.
    # Мы храним по одной строке на SKU и обновляем количество,
    # чтобы очередь была компактной и удобной для оператора.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS queue_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku_code TEXT NOT NULL UNIQUE,
        qty INTEGER NOT NULL,
        position INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """)

    # Создаём дефолтную строку мастер-сессии.
    # Это упрощает обновления: всегда есть одна запись id=1.
    cur.execute(
        "INSERT OR IGNORE INTO kiosk_master_session(id, master_id, last_active_ts, enabled) "
        "VALUES (1, NULL, NULL, 0)"
    )

    conn.commit()
    conn.close()


def get_kiosk_setting(key: str, default: int = 0) -> int:
    # Читаем настройку по ключу.
    # Если записи нет, возвращаем безопасный дефолт.
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM kiosk_settings WHERE key=?", [key])
    row = cur.fetchone()
    conn.close()
    if not row:
        return int(default)
    return int(row["value"] or 0)


def set_kiosk_setting(key: str, value: int) -> None:
    # Записываем настройку (0/1) по ключу.
    # Используем INSERT OR REPLACE, чтобы обновлять без сложных проверок.
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO kiosk_settings(key, value) VALUES (?, ?)",
        [key, int(value)],
    )
    conn.commit()
    conn.close()


def get_kiosk_settings(keys: list[str]) -> dict[str, int]:
    # Массовое чтение настроек.
    # Это ускоряет UI-запросы и упрощает обработку.
    conn = get_conn()
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in keys)
    cur.execute(
        f"SELECT key, value FROM kiosk_settings WHERE key IN ({placeholders})",
        keys,
    )
    rows = cur.fetchall()
    conn.close()
    return {row["key"]: int(row["value"] or 0) for row in (rows or [])}


def get_master_session() -> dict[str, int | str | None]:
    """
    Читает текущую мастер-сессию из БД.

    Возвращаем словарь с полями:
    - enabled: 0/1
    - master_id: строка или None
    - last_active_ts: unix time (int) или None
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT master_id, last_active_ts, enabled FROM kiosk_master_session WHERE id=1"
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return {"enabled": 0, "master_id": None, "last_active_ts": None}
    return {
        "enabled": int(row["enabled"] or 0),
        "master_id": row["master_id"],
        "last_active_ts": row["last_active_ts"],
    }


def set_master_session(master_id: str, last_active_ts: int) -> None:
    """
    Включает мастер-режим и фиксирует активность.

    Мы пишем всегда в строку id=1, чтобы не усложнять логику.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """UPDATE kiosk_master_session
           SET master_id=?, last_active_ts=?, enabled=1
           WHERE id=1""",
        [master_id, int(last_active_ts)],
    )
    conn.commit()
    conn.close()


def clear_master_session() -> None:
    """
    Отключает мастер-режим.

    Мы очищаем master_id и таймштамп, чтобы UI видел пустое состояние.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """UPDATE kiosk_master_session
           SET master_id=NULL, last_active_ts=NULL, enabled=0
           WHERE id=1"""
    )
    conn.commit()
    conn.close()


def update_master_last_active(last_active_ts: int) -> None:
    """
    Обновляет время последней активности мастера.

    Этот метод вызываем при любых мастер-действиях,
    чтобы таймаут отсчитывался корректно.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """UPDATE kiosk_master_session
           SET last_active_ts=?
           WHERE id=1 AND enabled=1""",
        [int(last_active_ts)],
    )
    conn.commit()
    conn.close()


def list_sku_catalog(search: str | None = None, include_inactive: bool = False) -> list[dict]:
    """
    Возвращает список SKU из каталога.

    По умолчанию показываем только активные позиции,
    чтобы оператор не видел архивные записи.
    """
    conn = get_conn()
    cur = conn.cursor()
    params: list = []
    where = []
    if not include_inactive:
        where.append("is_active = 1")
    if search:
        where.append("(sku_code LIKE ? OR name LIKE ? OR model_code LIKE ?)")
        needle = f"%{search.strip()}%"
        params.extend([needle, needle, needle])
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    cur.execute(
        f"""SELECT id, sku_code, name, model_code, width_cm, fabric_code, color_code,
                is_active, created_at, updated_at
           FROM sku_catalog
           {where_sql}
           ORDER BY updated_at DESC, id DESC""",
        params,
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in (rows or [])]


def create_sku_catalog_item(
    sku_code: str,
    name: str,
    model_code: str,
    width_cm: int,
    fabric_code: str,
    color_code: str,
    is_active: int = 1,
) -> int:
    """
    Создаёт новую запись SKU в каталоге.

    Мы пишем timestamps в секундах, чтобы можно было сортировать и фильтровать.
    """
    ts = int(time.time())
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO sku_catalog(
               sku_code, name, model_code, width_cm, fabric_code, color_code,
               is_active, created_at, updated_at
           )
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            sku_code,
            name,
            model_code,
            int(width_cm),
            fabric_code,
            color_code,
            int(is_active),
            ts,
            ts,
        ],
    )
    sku_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(sku_id or 0)


def update_sku_catalog_item(
    sku_id: int,
    name: str | None = None,
    is_active: int | None = None,
) -> None:
    """
    Обновляет поля name / is_active у SKU.

    Другие поля не трогаем, чтобы не ломать код SKU.
    """
    fields = []
    params: list = []
    if name is not None:
        fields.append("name=?")
        params.append(name)
    if is_active is not None:
        fields.append("is_active=?")
        params.append(int(is_active))
    if not fields:
        return
    fields.append("updated_at=?")
    params.append(int(time.time()))
    params.append(int(sku_id))
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"UPDATE sku_catalog SET {', '.join(fields)} WHERE id=?",
        params,
    )
    conn.commit()
    conn.close()


def get_active_sku_codes() -> set[str]:
    """
    Возвращает множество активных SKU из каталога.

    Используем set для быстрых проверок при импорте CSV.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT sku_code FROM sku_catalog WHERE is_active=1")
    rows = cur.fetchall()
    conn.close()
    return {row["sku_code"] for row in (rows or [])}


def get_sku_catalog_validation_data() -> tuple[set[str], bool]:
    """
    Возвращает активные SKU и флаг наличия каталога.

    Это нужно для импорта CSV: если каталог пуст,
    мы не блокируем новые SKU, а если каталог есть —
    требуем только активные коды.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT sku_code, is_active FROM sku_catalog")
    rows = cur.fetchall() or []
    conn.close()
    if not rows:
        return set(), False
    active = {
        row["sku_code"]
        for row in rows
        if int(row["is_active"] or 0) == 1
    }
    return active, True


def get_report_rows(report_type: str, date_from: str, date_to: str) -> list[dict]:
    """
    Формирует строки отчёта по типу и диапазону дат.

    Мы возвращаем простые словари, чтобы API мог легко
    строить CSV/XLSX без дополнительной обработки.
    """
    conn = get_conn()
    cur = conn.cursor()

    # Даты приходят строками YYYY-MM-DD, превращаем их в unix time (секунды).
    # Это простой и понятный формат для SQL-фильтров по времени.
    start_ts = int(time.mktime(time.strptime(date_from, "%Y-%m-%d")))
    end_ts = int(time.mktime(time.strptime(date_to, "%Y-%m-%d"))) + 86399

    if report_type == "employees":
        cur.execute(
            """SELECT worker_id,
                      COUNT(*) AS packed_count,
                      COALESCE(SUM(worktime_sec), 0) AS worktime_sec,
                      COALESCE(SUM(downtime_sec), 0) AS downtime_sec
               FROM sessions
               WHERE start_time BETWEEN ? AND ?
               GROUP BY worker_id
               ORDER BY packed_count DESC""",
            [start_ts, end_ts],
        )
        rows = cur.fetchall() or []
        conn.close()
        return [dict(row) for row in rows]

    if report_type == "sku":
        cur.execute(
            """SELECT product_code AS sku,
                      COUNT(*) AS packed_count
               FROM sessions
               WHERE start_time BETWEEN ? AND ?
               GROUP BY product_code
               ORDER BY packed_count DESC""",
            [start_ts, end_ts],
        )
        rows = cur.fetchall() or []
        conn.close()
        return [dict(row) for row in rows]

    # Отчёт по сменам: даём по каждой смене краткую сводку.
    cur.execute(
        """SELECT shift_id,
                  worker_id,
                  MIN(start_time) AS start_time,
                  MAX(finish_time) AS finish_time,
                  COUNT(*) AS packed_count
           FROM sessions
           WHERE start_time BETWEEN ? AND ?
           GROUP BY shift_id, worker_id
           ORDER BY start_time DESC""",
        [start_ts, end_ts],
    )
    rows = cur.fetchall() or []
    conn.close()
    return [dict(row) for row in rows]


def list_queue_items() -> list[dict]:
    """
    Возвращает очередь SKU в порядке position.

    Добавляем display_name из каталога, если он есть,
    чтобы UI мог показать "человеческое" название.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT q.id,
                  q.sku_code,
                  q.qty,
                  q.position,
                  q.created_at,
                  q.updated_at,
                  c.name AS display_name
           FROM queue_items q
           LEFT JOIN sku_catalog c ON c.sku_code = q.sku_code
           ORDER BY q.position ASC"""
    )
    rows = cur.fetchall() or []
    conn.close()
    return [dict(row) for row in rows]


def add_or_update_queue_item(sku_code: str, qty: int) -> int:
    """
    Добавляет SKU в очередь или увеличивает количество.

    Мы нормализуем очередь: один SKU = одна строка.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, qty FROM queue_items WHERE sku_code=?", [sku_code])
    row = cur.fetchone()
    ts = int(time.time())
    if row:
        new_qty = int(row["qty"] or 0) + int(qty)
        cur.execute(
            """UPDATE queue_items
               SET qty=?, updated_at=?
               WHERE id=?""",
            [new_qty, ts, int(row["id"])],
        )
        conn.commit()
        conn.close()
        return int(row["id"])

    cur.execute("SELECT COALESCE(MAX(position), 0) AS max_pos FROM queue_items")
    max_pos_row = cur.fetchone()
    next_pos = int(max_pos_row["max_pos"] if max_pos_row else 0) + 1
    cur.execute(
        """INSERT INTO queue_items(sku_code, qty, position, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        [sku_code, int(qty), next_pos, ts, ts],
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(new_id or 0)


def update_queue_qty(item_id: int, qty: int) -> None:
    """
    Обновляет количество SKU в очереди.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE queue_items SET qty=?, updated_at=? WHERE id=?",
        [int(qty), int(time.time()), int(item_id)],
    )
    conn.commit()
    conn.close()


def remove_queue_item(item_id: int) -> None:
    """
    Удаляет позицию из очереди.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM queue_items WHERE id=?", [int(item_id)])
    conn.commit()
    conn.close()


def reorder_queue_items(item_ids: list[int]) -> None:
    """
    Перезаписываем позиции очереди в порядке списка.
    """
    conn = get_conn()
    cur = conn.cursor()
    for idx, item_id in enumerate(item_ids, start=1):
        cur.execute(
            "UPDATE queue_items SET position=?, updated_at=? WHERE id=?",
            [idx, int(time.time()), int(item_id)],
        )
    conn.commit()
    conn.close()


def replace_queue_items(items: list[dict]) -> None:
    """
    Полностью заменяем очередь SKU в транзакции.

    Важно:
    - если что-то пошло не так, мы откатываем изменения;
    - позиции пересчитываются по порядку items.
    """
    conn = get_conn()
    cur = conn.cursor()
    ts = int(time.time())
    try:
        cur.execute("BEGIN")
        # Сначала очищаем очередь, чтобы не оставлять "старые" позиции.
        cur.execute("DELETE FROM queue_items")
        for idx, item in enumerate(items, start=1):
            cur.execute(
                """INSERT INTO queue_items(sku_code, qty, position, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    item["sku_code"],
                    int(item["qty"]),
                    idx,
                    ts,
                    ts,
                ],
            )
        conn.commit()
    except Exception:
        # Любая ошибка — откат, чтобы очередь оставалась в прежнем состоянии.
        conn.rollback()
        raise
    finally:
        conn.close()


def save_session(session) -> int:
    conn = get_conn()
    cur = conn.cursor()

    # shift_id может быть не задан (нет активной смены или старая логика).
    # Тогда сохраняем NULL, чтобы не ломать аналитику по историческим данным.
    shift_id = getattr(session, "shift_id", None)

    cur.execute("""
    INSERT INTO sessions
    (worker_id, product_code, start_time, finish_time,
     worktime_sec, downtime_sec, status, shift_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        session.worker_id,
        session.product_code,
        session.start_time,
        session.finish_time,
        session.worktime_sec,
        session.downtime_sec,
        session.status,
        getattr(session, "shift_id", None),
        shift_id,
    ])

    session_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(session_id or 0)


def add_event(
    event_type: str,
    ts: float,
    payload_json: str = "",
    shift_id: int | None = None,
    session_id: int | None = None,
    worker_id: str | None = None,
) -> int:
    """

    Добавляет событие в таблицу events.
    - Что делает: пишет запись с типом события и временем (ts).
    - Зачем: события нужны для вычисления work/idle на основе смены состояния,
      а не на основе частых heartbeat-тикеров.
    - Как использовать: вызывать при смене состояния таймера (WORK_STARTED/IDLE_STARTED).

    Добавляем событие в events.
    - Что делаем: записываем тип события и время (ts).
    - Зачем: события нужны для вычисления work/idle и heartbeat-авто-idle.
    - Как использовать: вызовы из /api/kiosk/timer/state и /api/kiosk/timer/heartbeat.

    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO events(ts, type, payload_json, shift_id, session_id, worker_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [ts, event_type, payload_json or "", shift_id, session_id, worker_id],
    )
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(event_id or 0)


def create_pack_session(
    sku: str,
    ts: float,
    state: str,
    shift_id: int | None = None,
    worker_id: str | None = None,
    phase: str | None = None,
    current_step_index: int | None = None,
    total_steps: int | None = None,
) -> int:
    # Здесь мы сохраняем старт упаковки в БД.
    # Важно фиксировать phase/current_step_index/total_steps сразу,
    # чтобы UI мог корректно показывать прогресс даже после перезапуска сервиса.
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO pack_sessions(
               sku, start_time, end_time, state, shift_id, worker_id,
               phase, current_step_index, total_steps
           )
           VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
        [sku, ts, state, shift_id, worker_id, phase, current_step_index, total_steps],
    )
    session_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(session_id or 0)


def update_pack_session_state(session_id: int, state: str, end_time: float | None = None) -> None:
    # Обновляет состояние FSM упаковки.
    # end_time записываем только при TABLE_EMPTY, чтобы зафиксировать завершение SKU.
    conn = get_conn()
    cur = conn.cursor()
    if end_time is None:
        cur.execute(
            """UPDATE pack_sessions
               SET state=?
               WHERE id=?""",
            [state, session_id],
        )
    else:
        cur.execute(
            """UPDATE pack_sessions
               SET state=?, end_time=?
               WHERE id=?""",
            [state, end_time, session_id],
        )
    conn.commit()
    conn.close()


def update_pack_session_progress(
    session_id: int,
    phase: str,
    current_step_index: int,
    total_steps: int,
) -> None:
    # Обновляет прогресс шагов.
    # Это отдельная функция, чтобы логически отделить FSM-состояние от workflow-шагов.
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """UPDATE pack_sessions
           SET phase=?, current_step_index=?, total_steps=?
           WHERE id=?""",
        [phase, current_step_index, total_steps, session_id],
    )
    conn.commit()
    conn.close()


def create_shift_plan(shift_id: int, name: str, created_at: float, items_json: str) -> int:
    # Создаём сменное задание для активной смены.
    # Храним список SKU в items_json, чтобы сохранять порядок и не терять данные.
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO shift_plans(shift_id, created_at, name, items_json)
           VALUES (?, ?, ?, ?)""",
        [shift_id, created_at, name, items_json],
    )
    plan_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(plan_id or 0)


def list_shift_plans(shift_id: int) -> list[sqlite3.Row]:
    # Возвращаем все планы для указанной смены,
    # чтобы UI мог показать оператору доступные варианты.
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, created_at, items_json FROM shift_plans WHERE shift_id=? ORDER BY id DESC",
        [shift_id],
    )
    rows = cur.fetchall()
    conn.close()
    return list(rows or [])


def get_shift_plan(plan_id: int) -> sqlite3.Row | None:
    # Точный доступ к плану по ID нужен для выбора активного плана.
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM shift_plans WHERE id=?", [plan_id])
    row = cur.fetchone()
    conn.close()
    return row


def create_shift_plan_with_items(
    shift_id: int,
    name: str,
    created_at: float,
    items: list[dict],
) -> int:
    """
    Создаёт новый сменный план и делает его активным.

    Объяснение по-учительски:
    - сначала деактивируем старые планы, чтобы активным был только один;
    - сохраняем и JSON-версию (для обратной совместимости), и таблицу items;
    - порядок строк фиксируем через position.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE shift_plans SET is_active=0 WHERE is_active=1")

    items_json = json.dumps(
        [{"sku_code": item["sku_code"], "qty": item["qty"]} for item in items],
        ensure_ascii=False,
    )
    cur.execute(
        """INSERT INTO shift_plans(shift_id, created_at, name, items_json, is_active)
           VALUES (?, ?, ?, ?, 1)""",
        [int(shift_id), float(created_at), name, items_json],
    )
    plan_id = int(cur.lastrowid or 0)

    if plan_id and items:
        rows = [
            (plan_id, item["sku_code"], int(item["qty"]), index)
            for index, item in enumerate(items)
        ]
        cur.executemany(
            """INSERT INTO shift_plan_items(plan_id, sku_code, qty, position)
               VALUES (?, ?, ?, ?)""",
            rows,
        )

    conn.commit()
    conn.close()
    return plan_id


def get_active_shift_plan() -> dict | None:
    """
    Возвращает активный сменный план вместе с позициями.

    Учительская ремарка:
    - храним план в двух местах, но читаем приоритетно из таблицы items;
    - если items пуст, всё равно возвращаем шапку плана.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, created_at, shift_id FROM shift_plans WHERE is_active=1 ORDER BY created_at DESC LIMIT 1"
    )
    plan_row = cur.fetchone()
    if not plan_row:
        conn.close()
        return None

    cur.execute(
        """SELECT sku_code, qty, position
           FROM shift_plan_items
           WHERE plan_id=?
           ORDER BY position ASC""",
        [int(plan_row["id"])],
    )
    items = [dict(row) for row in cur.fetchall() or []]
    conn.close()
    return {
        "id": int(plan_row["id"]),
        "name": plan_row["name"],
        "created_at": plan_row["created_at"],
        "shift_id": plan_row["shift_id"],
        "items": items,
    }


def set_active_shift_plan(plan_id: int) -> None:
    """
    Делает план активным и выключает остальные.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE shift_plans SET is_active=0 WHERE is_active=1")
    cur.execute("UPDATE shift_plans SET is_active=1 WHERE id=?", [int(plan_id)])
    conn.commit()
    conn.close()


def clear_active_shift_plan() -> None:
    """
    Снимаем активность с текущего плана.

    Почему так:
    - план остаётся в истории, но UI больше не считает его активным.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE shift_plans SET is_active=0 WHERE is_active=1")
    conn.commit()
    conn.close()


def get_sku_catalog_map(sku_codes: list[str]) -> dict:
    """
    Возвращает словарь sku_code -> запись из каталога.

    Учительская подсказка:
    - делаем один запрос IN (...), чтобы не бегать по базе циклом;
    - если список пуст, сразу отдаём пустой словарь.
    """
    if not sku_codes:
        return {}
    conn = get_conn()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(sku_codes))
    cur.execute(
        f"""SELECT sku_code, name, is_active
            FROM sku_catalog
            WHERE sku_code IN ({placeholders})""",
        sku_codes,
    )
    rows = cur.fetchall()
    conn.close()
    return {row["sku_code"]: dict(row) for row in (rows or [])}


def get_active_shift_id() -> int:
    # Ищем самую свежую активную смену.
    # Это нужно, чтобы привязывать сменное задание к правильной смене.
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM worker_shifts WHERE is_active=1 ORDER BY start_time DESC LIMIT 1"
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return 0
    return int(row["id"] or 0)


def add_pack_event(
    session_id: int,
    event_type: str,
    ts: float,
    payload_json: str = "",
    sku: str | None = None,
) -> int:
    # Сохраняем событие упаковки.
    # payload_json хранит подробности шага или перехода, чтобы не менять схему БД.
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO pack_events(ts, type, payload_json, session_id, sku)
           VALUES (?, ?, ?, ?, ?)""",
        [ts, event_type, payload_json or "", session_id, sku],
    )
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(event_id or 0)


def get_pack_session(session_id: int) -> sqlite3.Row | None:
    # Точное чтение сессии по ID — используется в отладке и сервисных сценариях.
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM pack_sessions WHERE id=?", [session_id])
    row = cur.fetchone()
    conn.close()
    return row


def get_latest_pack_session() -> sqlite3.Row | None:
    # Берём последнюю сессию по id, чтобы восстановить контекст после перезапуска.
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM pack_sessions ORDER BY id DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    return row


def get_active_pack_session() -> sqlite3.Row | None:
    # Активной считаем сессию в состояниях, где процесс ещё не завершён полностью.
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT * FROM pack_sessions
           WHERE state IN ('started', 'box_closed')
           ORDER BY id DESC LIMIT 1"""
    )
    row = cur.fetchone()
    conn.close()
    return row


def start_worker_shift(worker_id: str, work_center: str) -> int:
    """Открывает смену сотрудника на указанном РЦ и возвращает shift_id."""
    """Открывает смену сотрудника на указанном РЦ. Возвращает ID новой смены."""
    worker_id = (worker_id or "").strip()
    work_center = (work_center or "").strip().upper()
    if not worker_id or not work_center:
        return 0

    conn = get_conn()
    cur = conn.cursor()

    # Закрываем предыдущую активную смену на этом РЦ (если есть).
    # Это важно: у сотрудника может быть только одна активная смена на одном РЦ.
    now = time.time()

    # Закрываем предыдущую активную смену на этом же РЦ (если была),
    # чтобы не допустить несколько пересекающихся смен в одной зоне.
    cur.execute(
        """UPDATE worker_shifts
           SET end_time=?, is_active=0
           WHERE worker_id=? AND work_center=? AND is_active=1""",
        [time.time(), worker_id, work_center],
    )

    # Создаём новую смену и возвращаем её идентификатор.
    # Открываем новую смену и возвращаем её ID,
    # чтобы можно было привязать к ней сессию упаковки.
    cur.execute(
        """INSERT INTO worker_shifts(worker_id, work_center, start_time, end_time, is_active)
           VALUES (?, ?, ?, NULL, 1)""",
        [worker_id, work_center, now],
    )
    shift_id = int(cur.lastrowid or 0)
    conn.commit()
    conn.close()
    return shift_id
    shift_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(shift_id or 0)


def end_worker_shift(worker_id: str, work_centers: list[str] | None = None) -> int:
    """Закрывает активные смены сотрудника. Возвращает количество закрытых записей."""
    worker_id = (worker_id or "").strip()
    if not worker_id:
        return 0
    conn = get_conn()
    cur = conn.cursor()

    now = time.time()
    # Если передан список РЦ — закрываем только их,
    # иначе закрываем все активные смены сотрудника.
    if work_centers:
        # Закрываем смены только по указанным РЦ.
        centers = [c.strip().upper() for c in work_centers if c and c.strip()]
        if not centers:
            conn.close()
            return 0
        q_marks = ",".join(["?"] * len(centers))
        cur.execute(
            f"""UPDATE worker_shifts
                SET end_time=?, is_active=0
                WHERE worker_id=? AND is_active=1 AND work_center IN ({q_marks})""",
            [now, worker_id, *centers],
        )
    else:
        # Закрываем все активные смены сотрудника.
        cur.execute(
            """UPDATE worker_shifts
                SET end_time=?, is_active=0
                WHERE worker_id=? AND is_active=1""",
            [now, worker_id],
        )

    changed = cur.rowcount or 0
    conn.commit()
    conn.close()
    return int(changed)


def get_active_shifts() -> list[dict]:
    """Список активных смен: [{worker_id, work_center, start_time, shift_id}]."""
    """Список активных смен: [{shift_id, worker_id, work_center, start_time}]."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id, worker_id, work_center, start_time
           FROM worker_shifts
           WHERE is_active=1
           ORDER BY start_time ASC"""
    )
    rows = cur.fetchall() or []
    conn.close()
    return [
        {
            "shift_id": r["id"],
            "worker_id": r["worker_id"],
            "work_center": r["work_center"],
            "start_time": r["start_time"],
            "shift_id": r["id"],
        }
        for r in rows
    ]


def get_latest_active_shift_id(worker_id: str) -> int | None:
    # Возвращаем ID самой свежей активной смены сотрудника,
    # чтобы автоматически привязать новую упаковочную сессию к смене.
    worker_id = (worker_id or "").strip()
    if not worker_id:
        return None
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id
           FROM worker_shifts
           WHERE worker_id=? AND is_active=1
           ORDER BY start_time DESC, id DESC
           LIMIT 1""",
        [worker_id],
    )
    row = cur.fetchone()
    conn.close()
    return int(row["id"]) if row else None


def get_worker_active_centers(worker_id: str) -> list[str]:
    worker_id = (worker_id or "").strip()
    if not worker_id:
        return []
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT work_center FROM worker_shifts
           WHERE worker_id=? AND is_active=1
           ORDER BY work_center ASC""",
        [worker_id],
    )
    rows = cur.fetchall() or []
    conn.close()
    return [r["work_center"] for r in rows]


def get_latest_active_shift_id(worker_id: str) -> int | None:
    # Возвращаем самую "свежую" активную смену сотрудника.
    # Это нужно для привязки упаковочной сессии к конкретной смене.
    worker_id = (worker_id or "").strip()
    if not worker_id:
        return None
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """SELECT id FROM worker_shifts
           WHERE worker_id=? AND is_active=1
           ORDER BY start_time DESC
           LIMIT 1""",
        [worker_id],
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return int(row["id"])


def count_sessions_since(start_time: float, worker_id: str | None = None) -> int:
    worker_id = (worker_id or "").strip()
    conn = get_conn()
    cur = conn.cursor()
    if worker_id:
        cur.execute(
            """SELECT COUNT(*) AS cnt FROM sessions
               WHERE start_time >= ? AND worker_id = ?""",
            [start_time, worker_id],
        )
    else:
        cur.execute(
            """SELECT COUNT(*) AS cnt FROM sessions
               WHERE start_time >= ?""",
            [start_time],
        )
    row = cur.fetchone()
    conn.close()
    return int(row["cnt"] if row else 0)



def get_shift_report(shift_id: int) -> dict:
    """
    Минимальный отчёт по смене.
    - Что делаем: считаем packed_count через events и суммируем времена из sessions.
    - Зачем: нужен быстрый источник метрик для API /report.
    - Как влияет на метрики: packed_count растёт по событиям PACKED_CONFIRMED.
    - Тестирование (curl):
      1) Запустить смену и упаковать комплект со status=done.
      2) GET /api/kiosk/report/shift?shift_id=...
    """
    if not shift_id:
        return {
            "shift_id": 0,
            "packed_count": 0,
            "worktime_sec": 0,
            "downtime_sec": 0,
            "per_worker": {},
        }

    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """SELECT COUNT(*) AS cnt
           FROM events
           WHERE shift_id=? AND type='PACKED_CONFIRMED'""",
        [shift_id],
    )
    row = cur.fetchone()
    packed_count = int(row["cnt"] if row else 0)

    cur.execute(
        """SELECT
               COALESCE(SUM(worktime_sec), 0) AS worktime_sec,
               COALESCE(SUM(downtime_sec), 0) AS downtime_sec
           FROM sessions
           WHERE shift_id=?""",
        [shift_id],
    )
    totals = cur.fetchone()
    worktime_sec = int((totals["worktime_sec"] if totals else 0) or 0)
    downtime_sec = int((totals["downtime_sec"] if totals else 0) or 0)

    cur.execute(
        """SELECT worker_id,
                  COALESCE(SUM(worktime_sec), 0) AS worktime_sec,
                  COALESCE(SUM(downtime_sec), 0) AS downtime_sec
           FROM sessions
           WHERE shift_id=?
           GROUP BY worker_id""",
        [shift_id],
    )
    per_worker_rows = cur.fetchall() or []

    cur.execute(
        """SELECT worker_id, COUNT(*) AS cnt
           FROM events
           WHERE shift_id=? AND type='PACKED_CONFIRMED'
           GROUP BY worker_id""",
        [shift_id],
    )
    packed_per_worker_rows = cur.fetchall() or []

    conn.close()

    per_worker = {}
    for row in per_worker_rows:
        wid = row["worker_id"] or ""
        per_worker[wid] = {
            "packed_count": 0,
            "worktime_sec": int(row["worktime_sec"] or 0),
            "downtime_sec": int(row["downtime_sec"] or 0),
        }

    for row in packed_per_worker_rows:
        wid = row["worker_id"] or ""
        per_worker.setdefault(wid, {"packed_count": 0, "worktime_sec": 0, "downtime_sec": 0})
        per_worker[wid]["packed_count"] = int(row["cnt"] or 0)

    return {
        "shift_id": int(shift_id),
        "packed_count": packed_count,
        "worktime_sec": worktime_sec,
        "downtime_sec": downtime_sec,
        "per_worker": per_worker,
    }
