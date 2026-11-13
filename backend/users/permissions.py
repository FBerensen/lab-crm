"""Единый модуль проверки ролей лаборатории."""
from lab.models import lead, warehouse
from solutions.modules.permissions import (
    role_state, can_approve_preparation, can_mark_autoclaving, can_manage_preparation_stock,
)
from worklog.modules.permissions import trainee, active, can_post, can_edit_pending
