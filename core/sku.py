import re


def build_canonical_sku(
    model_code: str,
    width_cm: int | str,
    fabric_code: str,
    color_code: int | str,
) -> str:
    """
    Собираем SKU в строгом каноническом формате.

    Канон: MM.Кровать.NNN-NN.Ткань.XX

    Учительская подсказка:
    - модель дополняем до 3 цифр;
    - ширину приводим к "кодовой" форме (160 -> 16);
    - цвет приводим к 2 цифрам.
    """
    model_raw = str(model_code or "").strip()
    if model_raw.isdigit() and len(model_raw) <= 3:
        model = model_raw.zfill(3)
    else:
        model = model_raw

    width_raw = str(width_cm or "").strip()
    if not width_raw:
        width = ""
    else:
        width_value = int(width_raw)
        if width_value >= 100 and width_value % 10 == 0:
            width_value = width_value // 10
        width = str(width_value)

    fabric = str(fabric_code or "").strip()
    color_raw = str(color_code or "").strip()
    color = color_raw.zfill(2)[-2:]

    return f"MM.Кровать.{model}-{width}.{fabric}.{color}"


def normalize_canonical_sku(raw: str) -> str:
    """
    Приводим входной SKU к каноническому виду.

    Учительская подсказка:
    - если формат не распознаётся, выбрасываем ValueError;
    - канон: MM.Кровать.NNN-NN.Ткань.XX
    """
    if raw is None:
        raise ValueError("SKU пустой.")
    text = str(raw).strip()
    if not text:
        raise ValueError("SKU пустой после очистки.")

    canonical = re.compile(r"^MM\.Кровать\.\d{3}-\d{1,3}\.[A-Za-z0-9]+\.\d{2}$")
    if canonical.match(text):
        return text

    legacy = re.compile(r"^(MM\.Кровать\.\d{3}-\d{1,3})-([A-Za-z0-9]+)\.(\d{2})$")
    match = legacy.match(text)
    if match:
        prefix, fabric, color = match.groups()
        return f"{prefix}.{fabric}.{color}"

    raise ValueError(
        "Неверный формат SKU. Ожидается MM.Кровать.001-16.VelutaLux.07"
    )


def parse_sku(value: str) -> dict | None:
    """
    Разбираем SKU в словарь с параметрами.

    Возвращаем:
    {model_num:int, size:int, fabric:str, color:int}
    """
    try:
        text = normalize_canonical_sku(value)
    except ValueError:
        return None
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


def safe_normalize_sku(raw: str) -> str:
    """
    Безопасная нормализация SKU для read-only сценариев.

    Учительская подсказка:
    - в UI/поиске можно оставлять исходное значение,
      если формат не распознан, чтобы не терять данные.
    """
    try:
        return normalize_canonical_sku(raw)
    except ValueError:
        return str(raw or "").strip()
