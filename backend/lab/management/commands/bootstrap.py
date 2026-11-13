from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Создать группы ролей без создания пользователей."

    def handle(self, *args, **options):
        for name in ("Руководители", "Склад", "Исполнители", "Стажёры"):
            Group.objects.get_or_create(name=name)
        self.stdout.write("Группы ролей созданы.")
