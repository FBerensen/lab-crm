from decimal import Decimal, InvalidOperation


def display_quantity(value, unit):
    if value is None:
        return "—"
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return "—"
    if not number.is_finite():
        return "—"
    target = unit
    shift = 0
    magnitude = number.copy_abs()
    if unit in {"мл", "г"} and magnitude >= Decimal("1000"):
        target = "л" if unit == "мл" else "кг"
        shift = -3
    elif unit in {"л", "кг"} and magnitude < Decimal("1"):
        target = "мл" if unit == "л" else "г"
        shift = 3
    if shift:
        sign, digits, exponent = number.as_tuple()
        number = Decimal((sign, digits, exponent + shift))
    if number == 0:
        text = "0"
    else:
        text = format(number, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        text = text.replace(".", ",")
    return text + (" " + str(target) if target else "")
