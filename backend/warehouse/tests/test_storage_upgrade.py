from copy import deepcopy
from uuid import uuid4
import unittest
from django.db import connection, connections
from django.db.migrations.executor import MigrationExecutor

TARGET = "61 комната, горизонтальный холодильник (-40С)"


class StoragePopulatedMigrationTests(unittest.TestCase):
    def test_upgrade_from_0009_with_52_existing_samples(self):
        if connection.vendor != "postgresql": self.skipTest("PostgreSQL schema migration test")
        self.assertTrue(str(connection.settings_dict["NAME"]).startswith("test_"))
        nonce = uuid4().hex
        schema = "labcrm_storage_regression_" + nonce
        alias = "storage_regression_" + nonce
        self.assertNotIn(alias, connections.databases)
        created = False
        try:
            with connection.cursor() as cursor:
                cursor.execute("CREATE SCHEMA " + connection.ops.quote_name(schema))
            created = True
            config = deepcopy(connection.settings_dict)
            config["OPTIONS"] = dict(config.get("OPTIONS", {}))
            config["OPTIONS"]["options"] = "-c search_path=" + schema
            connections.databases[alias] = config
            isolated = connections[alias]
            executor = MigrationExecutor(isolated)
            executor.migrate([("lab", "0009_preparation_self_approval_autoclaving_mark")])
            old_apps = executor.loader.project_state([("lab", "0009_preparation_self_approval_autoclaving_mark")]).apps
            User = old_apps.get_model("auth", "User")
            Project = old_apps.get_model("lab", "Project")
            Sample = old_apps.get_model("lab", "Sample")
            Place = old_apps.get_model("lab", "StoragePlace")
            Lot = old_apps.get_model("lab", "Lot")
            user = User.objects.using(alias).create(username="storage_migration_regression", password="!")
            project = Project.objects.using(alias).create(title="migration regression", kind="Проект", manager_id=user.pk)
            old_place_rows = list(Place.objects.using(alias).order_by("pk").values_list("pk", "name", "active", "sort_order"))
            self.assertEqual(len(old_place_rows), 8)
            old_place_ids = [row[0] for row in old_place_rows]
            parent_id = None
            sample_ids = []
            for number in range(1, 53):
                sample = Sample.objects.using(alias).create(series="RG", number=str(number), project_id=project.pk,
                    parent_id=parent_id, kind="ДНК", location=TARGET, note="original note")
                sample_ids.append(sample.pk)
                if number == 1:
                    parent_id = sample.pk
            unknown = Sample.objects.using(alias).create(series="RG", number="53", project_id=project.pk,
                kind="ДНК", location=TARGET + " ")
            lot = Lot.objects.using(alias).create(code="REGRESSION-STOCK", name="unchanged stock", unit="мл",
                storage_place_id=old_place_ids[0], location=old_place_rows[0][1], quantity=10, reserved=2)
            executor = MigrationExecutor(isolated)
            executor.migrate([("lab", "0011_link_sample_storage_locations")])
            latest = executor.loader.project_state([("lab", "0011_link_sample_storage_locations")]).apps
            NewSample = latest.get_model("lab", "Sample")
            NewPlace = latest.get_model("lab", "StoragePlace")
            NewLot = latest.get_model("lab", "Lot")
            Movement = latest.get_model("lab", "Movement")
            target = NewPlace.objects.using(alias).get(name=TARGET)
            self.assertEqual(NewPlace.objects.using(alias).count(), 9)
            self.assertEqual(list(NewPlace.objects.using(alias).filter(pk__in=old_place_ids).order_by("pk").values_list("pk", "name", "active", "sort_order")), old_place_rows)
            linked = NewSample.objects.using(alias).filter(pk__in=sample_ids)
            self.assertEqual(linked.count(), 52)
            self.assertEqual(linked.filter(storage_place_id=target.pk, original_location=TARGET, location=TARGET).count(), 52)
            self.assertEqual(linked.filter(project_id=project.pk, note="original note").count(), 52)
            self.assertEqual(NewSample.objects.using(alias).get(pk=sample_ids[1]).parent_id, sample_ids[0])
            other = NewSample.objects.using(alias).get(pk=unknown.pk)
            self.assertIsNone(other.storage_place_id)
            self.assertEqual(other.original_location, TARGET + " ")
            preserved = NewLot.objects.using(alias).get(pk=lot.pk)
            self.assertEqual(preserved.quantity, 10)
            self.assertEqual(preserved.reserved, 2)
            self.assertEqual(preserved.storage_place_id, old_place_ids[0])
            self.assertEqual(Movement.objects.using(alias).count(), 0)
        finally:
            if alias in connections.databases:
                connections[alias].close()
                del connections[alias]
                del connections.databases[alias]
            if created:
                with connection.cursor() as cursor:
                    cursor.execute("DROP SCHEMA " + connection.ops.quote_name(schema) + " CASCADE")
