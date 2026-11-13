import time
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from lab.reports import build_report, send_approved_reports


class Command(BaseCommand):
    help = "Квартальные черновики и отправка утверждённых отчётов."

    def handle(self, *args, **options):
        while True:
            try:
                close_old_connections()
                today = timezone.localdate()
                current_q = (today.month - 1) // 3 + 1
                quarter_start = date(
                    today.year, (current_q - 1) * 3 + 1, 1
                )

                # Начиная с пятого числа первого месяца квартала.
                # После простоя сформирует последний положенный черновик.
                if today >= quarter_start + timedelta(days=4):
                    year, q = (
                        (today.year - 1, 4)
                        if current_q == 1
                        else (today.year, current_q - 1)
                    )
                    build_report(year, q)

                send_approved_reports()
            except Exception as exc:
                self.stderr.write(f"Ошибка планировщика: {exc}")

            close_old_connections()
            time.sleep(3600)
