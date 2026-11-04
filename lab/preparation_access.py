APPROVAL_PERMISSION = "lab.approve_preparation"
TRAINEE_GROUPS = {"Стажёры", "Стажеры"}
EMPLOYEE_GROUP = "Исполнители"
LEAD_GROUP = "Руководители"


def role_state(user):
    if not user or not user.is_active or not user.is_staff:
        return "denied"
    if user.is_superuser:
        return "administrator"
    groups = set(user.groups.values_list("name", flat=True))
    trainee = bool(groups & TRAINEE_GROUPS)
    worker = bool(groups & {EMPLOYEE_GROUP, LEAD_GROUP})
    if trainee and worker:
        return "conflict"
    if trainee:
        return "trainee"
    if LEAD_GROUP in groups:
        return "lead"
    if EMPLOYEE_GROUP in groups:
        return "employee"
    return "denied"


def can_approve_preparation(user):
    state = role_state(user)
    return state in {"administrator", "lead"} or (state == "employee" and user.has_perm(APPROVAL_PERMISSION))


def can_mark_autoclaving(user, preparation):
    state = role_state(user)
    if state in {"administrator", "lead"}:
        return True
    return state in {"employee", "trainee"} and preparation.actor_id == user.pk
