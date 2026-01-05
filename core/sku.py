import re


def normalize_sku(raw: str) -> str:
    """
    Приводим SKU к каноническому виду:
    MM.Кровать.NNN-NN.Ткань.XX

    Учительская подсказка:
    - если строка уже каноническая — возвращаем как есть;
    - если вместо точки перед тканью стоит дефис, заменяем его;
    - если структура неизвестна — возвращаем исходную строку без падений.
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return text

    canonical = re.compile(r"^MM\.Кровать\.\d{3}-\d{1,3}\.[A-Za-z0-9]+\.\d{2}$")
    if canonical.match(text):
        return text

    legacy = re.compile(r"^(MM\.Кровать\.\d{3}-\d{1,3})-([A-Za-z0-9]+)\.(\d{2})$")
    match = legacy.match(text)
    if match:
        prefix, fabric, color = match.groups()
        return f"{prefix}.{fabric}.{color}"

    # Учительская подсказка: если формат не распознан, не ломаем данные.
    return text


def parse_sku(value: str) -> dict | None:
    """
    Разбираем SKU в словарь с параметрами.

    Возвращаем:
    {model_num:int, size:int, fabric:str, color:int}
    """
    text = normalize_sku(value)
    match = re.match(
        r"^MM\.Кровать\.(\d{3})-(\d{1,3})\.([A-Za-z0-9]+)\.(\d{2})$",
        text,
    )
    if not match:
        return None
    model, size, fabric, color = match.groups()
    return {
        "model_num": int(model),
        "size": int(size),
        "fabric": fabric,
        "color": int(color),
    }


def sku_sort_key(value: str) -> tuple:
    """
    Формируем ключ сортировки SKU.

    Учительская подсказка:
    - если SKU распознан, сортируем по модели/размеру/ткани/цвету;
    - если не распознан, отправляем в хвост списка.
    """
    parsed = parse_sku(value)
    if not parsed:
        safe = (value or "").strip().lower()
        return (9999, 9999, safe, 9999)
    return (
        parsed["model_num"],
        parsed["size"],
        parsed["fabric"].lower(),
        parsed["color"],
    )
