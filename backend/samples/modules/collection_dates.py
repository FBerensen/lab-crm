from datetime import date
from django.core.exceptions import ValidationError
from django.utils import timezone

SAMPLE_FIELDS = (
    "sample_type", "plant_part", "sampling_location",
    "sampling_date", "received_in_lab_date",
)

def validate_sample_dates(sampling_date, received_in_lab_date):
    today = timezone.localdate()
    errors = {}
    for name, value in (("sampling_date", sampling_date),
                        ("received_in_lab_date", received_in_lab_date)):
        if isinstance(value, date) and value > today:
            errors[name] = "Дата не может быть позже сегодняшней."
    if (isinstance(sampling_date, date)
            and isinstance(received_in_lab_date, date)
            and received_in_lab_date < sampling_date):
        errors["received_in_lab_date"] = (
            "Дата получения не может быть раньше даты отбора."
        )
    if errors:
        raise ValidationError(errors)
