from lab.models import lead

def trainee(user):
    return bool(user and user.pk and user.groups.filter(name="Стажёры").exists())

def active(user):
    return bool(user and user.is_active and user.is_staff)

def can_post(user):
    if not active(user) or trainee(user):
        return False
    return bool(lead(user) or user.has_perm("lab.post_worklog_expense"))

def can_edit_pending(user, worklog):
    if not active(user):
        return False
    return worklog.actor_id == user.pk or (not trainee(user) and lead(user))
