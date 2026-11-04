import signal
import time
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from lab.reports import build_report, send_approved_reports


class Command(BaseCommand):
    help = "Квартальные черновики и отправка утверждённых отчётов."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stop_requested = False

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Выполнить одну проверку и завершиться (для запуска через cron/systemd или в тестах).",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=3600,
            help="Интервал проверки в секундах при работе в режиме демона (по умолчанию 3600).",
        )

    def _handle_signal(self, signum, frame):
        self._stop_requested = True

    def run_cycle(self):
        close_old_connections()
        today = timezone.localdate()
        current_q = (today.month - 1) // 3 + 1
        quarter_start = date(today.year, (current_q - 1) * 3 + 1, 1)

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
        close_old_connections()

    def handle(self, *args, **options):
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        if options["once"]:
            self.run_cycle()
            self.stdout.write("Цикл планировщика выполнен.")
            return

        interval = max(10, options["interval"])
        while not self._stop_requested:
            try:
                self.run_cycle()
            except Exception as exc:
                self.stderr.write(f"Ошибка планировщика: {exc}")

            slept = 0
            while slept < interval and not self._stop_requested:
                time.sleep(min(5, interval - slept))
                slept += 5
