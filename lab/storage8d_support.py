import re

def block_code_id(query):
    match=re.fullmatch(r"(?:ИБ-|IB-)?([0-9]+)",query.strip(),re.IGNORECASE)
    if not match:return None
    digits=match.group(1).lstrip("0") or "0"
    if len(digits)>19:return None
    value=int(digits)
    return value if 0<value<=9223372036854775807 else None

def occupancy(wells):
    values=list(wells)
    return {"filled":len(values),"sample_wells":sum(w.kind=="sample" for w in values),
        "unique_samples":len({w.sample_id for w in values if w.kind=="sample" and w.sample_id is not None}),
        "controls":sum(w.kind=="water" for w in values),"raw":sum(w.kind=="unregistered" for w in values)}
