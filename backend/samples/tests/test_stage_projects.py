# 8A_FIX1_TASK_CLEAN_AND_TEST_SAVEPOINTS
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.contrib import admin
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import connection,connections,IntegrityError,transaction
from django.test import TestCase,TransactionTestCase,RequestFactory
from django.urls import reverse
from lab.test_stage6a import Fixtures
from lab.models import Sample,Project,Task,StoragePlace,SampleProjectMembership,Lot,Movement,Audit
from lab.sample_projects8a import build_sample,set_projects,add_projects,current_ids

class Seed(Fixtures):
    def setUp(self):
        super().setUp();self.p1=self.sample.project
        self.p2=Project.objects.create(title="8A common two",kind=self.p1.kind,manager=self.leader)
        self.p3=Project.objects.create(title="8A common three",kind=self.p1.kind,manager=self.leader)
        self.place=StoragePlace.objects.create(name="8A test place")
        self.n=10;self.ma=admin.site._registry[Sample]
        self.request=RequestFactory().post("/");self.request.user=self.executor
    def card(self,projects=None,parent=None):
        self.n+=1
        s=build_sample(project=projects or [self.p1],series="Z8A",number=str(self.n),kind=self.sample.kind,storage_place=self.place,parent=parent,location="",note="8A")
        s.full_clean();s.save();return s
    def ids(self,s):return current_ids(s.pk)
    def set(self,s,ids):return set_projects(s.pk,ids,self.executor)
    def batch_data(self):return {"series":"Z8B","first":"20","last":"21","extra_numbers":"","project":[self.p1.pk,self.p2.pk],"kind":self.sample.kind,"storage_place":self.place.pk,"note":"8A batch"}

class ProjectsTests(Seed,TestCase):
    def test_legacy_creation_seeds_membership(self):self.assertEqual(self.ids(self.sample),{self.p1.pk})
    def test_build_multiple_projects_one_sample(self):
        before=Sample.objects.count();s=self.card([self.p1,self.p2])
        self.assertEqual(Sample.objects.count(),before+1);self.assertEqual(self.ids(s),{self.p1.pk,self.p2.pk})
    def test_names_show_all_projects(self):
        s=self.card([self.p1,self.p2]);self.assertIn(self.p2.title,s.project_names);self.assertIn(self.p1.title,s.project_names)
    def test_parent_common_project_not_same_legacy_fk(self):
        parent=self.card([self.p1,self.p2]);child=self.card([self.p2],parent)
        self.assertNotEqual(parent.project_id,child.project_id);child.full_clean()
    def test_disjoint_parent_rejected(self):
        parent=self.card([self.p1])
        with self.assertRaises(ValidationError):self.card([self.p2],parent)
    def test_extra_project_not_propagated_to_parent(self):
        parent=self.card();child=self.card(parent=parent);self.set(child,[self.p1.pk,self.p2.pk])
        self.assertEqual(self.ids(parent),{self.p1.pk})
    def test_last_common_project_child_removal_rejected(self):
        parent=self.card();child=self.card([self.p1,self.p2],parent)
        with self.assertRaises(ValidationError):self.set(child,[self.p2.pk])
        self.assertIn(self.p1.pk,self.ids(child))
    def test_parent_change_checks_children(self):
        parent=self.card([self.p1,self.p2]);self.card([self.p1],parent)
        with self.assertRaises(ValidationError):self.set(parent,[self.p2.pk])
    def test_only_direct_parent_overlap_required(self):
        ancestor=self.card([self.p1]);parent=self.card([self.p1,self.p2],ancestor);child=self.card([self.p2],parent)
        self.assertEqual(self.ids(child),{self.p2.pk})
    def test_self_parent_rejected(self):
        s=self.card();s.parent=s
        with self.assertRaises(ValidationError):s.full_clean()
    def test_empty_projects_rejected(self):
        s=self.card()
        with self.assertRaises(ValidationError):self.set(s,[])
    def test_task_accepts_additional_project(self):
        self.task.sample=None
        self.task.save(update_fields=["sample"])
        self.task.samples.add(self.sample)
        self.set(self.sample,[self.p1.pk,self.p2.pk])
        self.task.project=self.p2
        self.task.full_clean()
        self.task.save()
    def test_task_rejects_unrelated_project(self):
        self.task.sample=None
        self.task.save(update_fields=["sample"])
        self.task.samples.add(self.sample)
        self.task.project=self.p2
        with self.assertRaises(ValidationError):
            self.task.full_clean()
    def test_project_used_by_task_cannot_be_removed(self):
        self.task.samples.add(self.sample);self.set(self.sample,[self.p1.pk,self.p2.pk])
        with self.assertRaises(ValidationError):self.set(self.sample,[self.p2.pk])
    def test_trainee_service_denied(self):
        with self.assertRaises(PermissionDenied):set_projects(self.sample.pk,[self.p1.pk,self.p2.pk],self.student)
    def test_repeat_set_is_noop(self):
        s=self.card();count=Audit.objects.count()
        self.assertFalse(self.set(s,[self.p1.pk]));self.assertEqual(Audit.objects.count(),count)
    def test_mass_add_preserves_previous_links_no_stock(self):
        s=self.card();before=(Lot.objects.count(),Movement.objects.count(),self.balance())
        add_projects([s.pk,self.sample.pk],[self.p2.pk],self.executor)
        self.assertEqual(self.ids(s),{self.p1.pk,self.p2.pk});self.assertEqual(self.ids(self.sample),{self.p1.pk,self.p2.pk})
        self.assertEqual(before,(Lot.objects.count(),Movement.objects.count(),self.balance()))
    def test_mass_add_missing_target_rolls_back(self):
        before=self.ids(self.sample)
        with self.assertRaises(ValidationError):add_projects([self.sample.pk,99999999],[self.p2.pk],self.executor)
        self.assertEqual(self.ids(self.sample),before)
    def test_direct_remove_last_link_rejected(self):
        s=self.card()
        with self.assertRaises(ValidationError):
            with transaction.atomic():
                s.projects.remove(self.p1)
        self.assertEqual(self.ids(s),{self.p1.pk})
    def test_reverse_clear_rejected(self):
        self.card()
        with self.assertRaises(ValidationError):
            with transaction.atomic():
                self.p1.linked_samples.clear()
        self.assertIn(self.p1.pk,self.ids(self.sample))
    def test_unique_link_constraint(self):
        with self.assertRaises(IntegrityError),transaction.atomic():SampleProjectMembership.objects.create(sample=self.sample,project=self.p1)
    def test_form_has_multiple_project_and_passport_fields(self):
        form=self.ma.get_form(self.request,obj=self.sample)()
        from django.forms import ModelMultipleChoiceField
        self.assertIsInstance(form.fields["project"],ModelMultipleChoiceField)
        for name in ("passport_vir","passport_species","passport_origin"):self.assertIn(name,form.fields)
    def test_card_post_preserves_multiple_projects(self):
        self.client.force_login(self.executor)
        data={"series":self.sample.series,"number":self.sample.number,"kind":self.sample.kind,"parent":"","project":[self.p1.pk,self.p2.pk],
            "storage_place":self.place.pk,"note":"8A update","sample_type":"","plant_part":"","sampling_location":"","sampling_date":"","received_in_lab_date":"",
            "passport_vir":"","passport_species":"","passport_origin":"","_save":"Save"}
        response=self.client.post(reverse("admin:lab_sample_change",args=[self.sample.pk]),data)
        self.assertEqual(response.status_code,302);self.assertEqual(self.ids(self.sample),{self.p1.pk,self.p2.pk})
    def test_batch_form_and_create_multiple_projects(self):
        from lab.passport_excel import BatchForm,create_batch
        form=BatchForm(self.batch_data());self.assertTrue(form.is_valid(),form.errors)
        create_batch(form.cleaned_data,None,self.executor,self.ma,self.request)
        for s in Sample.objects.filter(series="Z8B"):self.assertEqual(self.ids(s),{self.p1.pk,self.p2.pk})
        self.assertEqual(Sample.objects.filter(series="Z8B").count(),2)
    def test_duplicate_batch_does_not_create_partial_rows(self):
        from lab.passport_excel import BatchForm,create_batch
        form=BatchForm(self.batch_data());self.assertTrue(form.is_valid(),form.errors)
        create_batch(form.cleaned_data,None,self.executor,self.ma,self.request);before=Sample.objects.count()
        with self.assertRaises(ValidationError):create_batch(form.cleaned_data,None,self.executor,self.ma,self.request)
        self.assertEqual(Sample.objects.count(),before)
    def test_series_counts_distinct_samples(self):
        from lab.sample_series_stage5 import series_summary
        s=self.card([self.p1,self.p2,self.p3]);row=series_summary(Sample.objects.filter(pk=s.pk)).get()
        self.assertEqual(row["samples"],1);self.assertEqual(row["projects"],3)
    def test_storage_search_does_not_duplicate_sample(self):
        s=self.card([self.p1,self.p2,self.p3]);self.client.force_login(self.executor)
        response=self.client.get(reverse("admin:lab_storageplace_contents",args=[self.place.pk]),{"q":"8A common"})
        self.assertEqual(response.status_code,200)
        self.assertEqual([r["obj"].pk for r in response.context_data["sample_rows"]],[s.pk])
    def test_admin_search_does_not_duplicate_sample(self):
        s=self.card([self.p1,self.p2,self.p3]);self.client.force_login(self.executor)
        response=self.client.get(reverse("admin:lab_sample_changelist"),{"all_samples":"1","q":"8A common"})
        self.assertEqual(response.status_code,200)
        self.assertEqual([obj.pk for obj in response.context_data["cl"].result_list],[s.pk])
    def test_csv_exports_all_projects(self):
        from lab.sample_projects8a_admin import export_samples_csv
        s=self.card([self.p1,self.p2]);response=export_samples_csv(self.ma,self.request,Sample.objects.filter(pk=s.pk))
        self.assertIn(self.p1.title,response.content.decode());self.assertIn(self.p2.title,response.content.decode())
    def test_adding_projects_does_not_change_legacy_fields(self):
        before=Sample.objects.filter(pk=self.sample.pk).values().get()
        self.set(self.sample,[self.p1.pk,self.p2.pk])
        self.assertEqual(before,Sample.objects.filter(pk=self.sample.pk).values().get())
    def test_migration_copies_legacy_links(self):
        SampleProjectMembership.objects.all().delete()
        migration=import_module("lab.migrations.0020_sample_projects")
        migration.copy_links(apps,SimpleNamespace(connection=connection))
        self.assertEqual(SampleProjectMembership.objects.count(),Sample.objects.count())
        self.assertEqual(self.ids(self.sample),{self.p1.pk})
    def test_technical_legacy_fk_not_editable(self):
        s=self.card([self.p1,self.p2]);s.project=self.p2
        with self.assertRaises(ValidationError):s.save()

class ProjectConcurrencyTests(Seed,TransactionTestCase):
    def test_concurrent_mass_add_keeps_both_additions(self):
        from django.contrib.auth import get_user_model
        pk=self.sample.pk;uid=self.executor.pk
        def run(project):
            connections.close_all()
            try:return add_projects([pk],[project],get_user_model().objects.get(pk=uid))
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,[self.p2.pk,self.p3.pk]))
        self.assertEqual(current_ids(pk),{self.p1.pk,self.p2.pk,self.p3.pk})
