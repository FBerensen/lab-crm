def descendant_ids(root_id, places):
    children = {}
    for place in places:
        children.setdefault(place.parent_id, []).append(place.pk)
    result = set()
    pending = [root_id]
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.add(current)
        pending.extend(children.get(current, ()))
    return result


def place_path(place_id, places):
    mapping = {p.pk: p for p in places}
    parts = []
    seen = set()
    while place_id in mapping:
        if place_id in seen:
            parts.append("[цикл]")
            break
        seen.add(place_id)
        place = mapping[place_id]
        parts.append(place.name)
        place_id = place.parent_id
    return " / ".join(reversed(parts))
