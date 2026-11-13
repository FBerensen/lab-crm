from django.core.management.base import BaseCommand, CommandError
from lab.reports import build_report


class Command(BaseCommand):
    help = "Сформировать черновик за завершённый квартал."

    def add_arguments(self, parser):
        parser.add_argument("year", type=int)
        parser.add_argument("quarter", type=int)

    def handle(self, *args, **options):
        try:
            report = build_report(options["year"], options["quarter"])
        except Exception as exc:
            raise CommandError(str(exc))
        self.stdout.write(f"Черновик: {report}")
