from decimal import Decimal


def compact_number(value):
    if value is None:
        return "—"
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite():
        return "—"
    if number == 0:
        return "0"
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def preparation_label(preparation):
    output = preparation.output if preparation.output_id else None
    recipe = preparation.recipe if preparation.recipe_id else None
    name = output.name if output else (str(recipe.product) if recipe else preparation.protocol or "Приготовление")
    day = preparation.actual_day.strftime("%d.%m.%Y") if preparation.actual_day else "дата не указана"
    if preparation.processed:
        volume = preparation.actual_volume_ml
        if volume is not None:
            amount = compact_number(volume) + " мл"
        else:
            amount = compact_number(preparation.qty) + " " + (output.unit if output else "(единица не указана)")
    else:
        volume = preparation.planned_volume_ml
        amount = (compact_number(volume) + " мл (план)") if volume is not None else "объём не указан"
    if preparation.actor_id:
        actor = preparation.actor
        person = actor.get_full_name().strip() or actor.get_username()
    else:
        person = "исполнитель не указан"
    return " · ".join((name, day, amount, person))


def lot_label(lot):
    if lot.pk:
        try:
            preparation = lot.production_record
        except AttributeError:
            preparation = None
        if preparation is not None and preparation.kind == "Приготовление реактива":
            return preparation_label(preparation)
    return f"{lot.name} · {lot.code} [{lot.unit}]"
