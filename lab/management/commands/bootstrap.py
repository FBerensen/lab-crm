from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from lab.models import BufferName, ProductCategory, Project, StoragePlace

ROLE_GROUPS = ("Руководители", "Склад", "Исполнители")

DEFAULT_CATEGORIES = (
    ("Пластик", 1),
    ("Реактив", 2),
    ("Буфер", 3),
    ("Нанофор 05", 4),
    ("реактив для ПЦР", 5),
    ("реактивы", 6),
)

DEFAULT_PLACES = (
    ("15к. холодильник серый +4", 1),
    ("15к. холодильник адмирал +4", 2),
    ("15к. морозильник ПЦР -20", 3),
    ("15к. морозилка адмирал -20", 4),
    ("15к. морозилка горизонтальная -20", 5),
    ("15к. морозилка Bosch -20", 6),
    ("15к. холодильник Bosch +4", 7),
    ("15к. холодильник POZIS +4", 8),
)

DEFAULT_BUFFERS = (
    ("PBS 1x", 10),
    ("TAE 1x", 20),
    ("TBE 1x", 30),
    ("TE буфер (pH 8.0)", 40),
    ("Лизирующий буфер CTAB", 50),
)


class Command(BaseCommand):
    help = (
        "Инициализировать группы ролей, базовые справочники склада "
        "и (опционально с флагом --demo) тестовых пользователей для локальной проверки."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--demo",
            action="store_true",
            help="Создать демонстрационных пользователей (admin, lead1, lead2, warehouse1, worker1) и тестовый проект.",
        )
        parser.add_argument(
            "--demo-password",
            default="LabCrmDemo2026!",
            help="Пароль для демонстрационных пользователей (по умолчанию: LabCrmDemo2026!).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        groups = {}
        for name in ROLE_GROUPS:
            group, _ = Group.objects.get_or_create(name=name)
            groups[name] = group

        for name, sort_order in DEFAULT_CATEGORIES:
            cat, created = ProductCategory.objects.get_or_create(
                name=name,
                defaults={"active": True, "sort_order": sort_order},
            )
            if not created and not cat.active and name in ("Пластик", "Реактив", "Буфер"):
                cat.active = True
                cat.save(update_fields=["active"])

        for name, sort_order in DEFAULT_PLACES:
            StoragePlace.objects.get_or_create(
                name=name,
                defaults={"active": True, "sort_order": sort_order},
            )

        for name, sort_order in DEFAULT_BUFFERS:
            BufferName.objects.get_or_create(
                name=name,
                defaults={"active": True, "sort_order": sort_order},
            )

        self.stdout.write(
            self.style.SUCCESS(
                "Группы ролей и справочники склада инициализированы."
            )
        )

        if options["demo"]:
            User = get_user_model()
            pwd = options["demo_password"]

            demo_accounts = [
                ("admin", True, ["Руководители"]),
                ("lead1", False, ["Руководители"]),
                ("lead2", False, ["Руководители"]),
                ("warehouse1", False, ["Склад"]),
                ("worker1", False, ["Исполнители"]),
            ]

            created_users = {}
            for username, is_super, group_names in demo_accounts:
                user, created = User.objects.get_or_create(
                    username=username,
                    defaults={
                        "is_staff": True,
                        "is_active": True,
                        "is_superuser": is_super,
                    },
                )
                if created:
                    user.set_password(pwd)
                    user.save()
                user.groups.set([groups[g] for g in group_names])
                created_users[username] = user

            Project.objects.get_or_create(
                title="Пилотный проект генотипирования",
                defaults={
                    "kind": "Госзадание",
                    "manager": created_users["lead1"],
                    "description": "Демонстрационный проект для локального тестирования.",
                },
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Демо-пользователи готовы (общий пароль для новых аккаунтов: {pwd}):\n"
                    "  - admin (Суперпользователь + Руководители)\n"
                    "  - lead1, lead2 (Руководители)\n"
                    "  - warehouse1 (Склад)\n"
                    "  - worker1 (Исполнители)"
                )
            )
