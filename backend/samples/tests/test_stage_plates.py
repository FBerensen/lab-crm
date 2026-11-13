from decimal import Decimal
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from lab.models import SourceBlock, SourceBlockWell, Sample, Project, SampleProjectMembership, Audit, Movement
from lab.source_blocks8v import save_block, set_well, clear_well

class SourceBlocks8VTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.user=U.objects.create_superuser(username="sb8v",email="",password="test-only-password")
        self.denied=U.objects.create_user(username="sb8v_denied",password="test-only-password")
        self.project=Project.objects.create(title="Тест 8В",kind="Проект",manager=self.user)
        self.sample=Sample(series="SV",number="1",project=self.project,kind="ДНК",location="Тест")
        # Seed an existing canonical sample without invoking unrelated collection/passport workflows.
        Sample.objects.bulk_create([self.sample])
        SampleProjectMembership.objects.create(sample=self.sample,project=self.project)
        self.block=SourceBlock(name="Проверка 8В",default_dna_ul=Decimal("1"),default_water_ul=Decimal("2"))
        save_block(self.block,self.user,0)
        self.client.force_login(self.user)
    def rev(self):
        self.block.refresh_from_db()
        return self.block.revision
    def fill(self,row="A",column=1,**kwargs):
        return set_well(self.block.pk,row,column,self.sample.pk,self.user,self.rev(),**kwargs)
    def test_defaults_copied(self):
        w=self.fill()
        self.assertEqual((w.dna_ul,w.water_ul),(Decimal("1"),Decimal("2")))
    def test_replicates_allowed(self):
        self.fill();self.fill(column=2)
        self.assertEqual(SourceBlockWell.objects.filter(sample=self.sample).count(),2)
        self.assertEqual(Sample.objects.count(),1)
    def test_overrides_independent(self):
        a=self.fill(dna_ul=Decimal("0.125"));b=self.fill(column=2,water_ul=Decimal("4"))
        self.assertEqual((a.dna_ul,a.water_ul),(Decimal("0.125"),Decimal("2")))
        self.assertEqual((b.dna_ul,b.water_ul),(Decimal("1"),Decimal("4")))
    def test_defaults_change_preserves_existing(self):
        a=self.fill();self.block.refresh_from_db()
        self.block.default_dna_ul=Decimal("5");save_block(self.block,self.user,self.block.revision)
        a.refresh_from_db();b=self.fill(column=2)
        self.assertEqual(a.dna_ul,Decimal("1"));self.assertEqual(b.dna_ul,Decimal("5"))
    def test_zero_is_explicit_valid_value(self):
        self.assertEqual(self.fill(dna_ul=Decimal("0")).dna_ul,Decimal("0"))
    def test_negative_rejected(self):
        with self.assertRaises(ValidationError):self.fill(dna_ul=Decimal("-1"))
        self.assertFalse(SourceBlockWell.objects.exists())
    def test_nonfinite_rejected(self):
        with self.assertRaises(ValidationError):self.fill(water_ul=Decimal("NaN"))
    def test_too_many_decimals_rejected(self):
        with self.assertRaises(ValidationError):self.fill(dna_ul=Decimal("0.00001"))
    def test_coordinates_rejected(self):
        for row,col in (("I",1),("A",0),("A",13),("a",1)):
            with self.assertRaises(ValidationError):self.fill(row=row,column=col)
    def test_stale_well_write_rejected(self):
        old=self.rev();self.fill()
        with self.assertRaises(ValidationError):
            set_well(self.block.pk,"A",2,self.sample.pk,self.user,old)
        self.assertEqual(SourceBlockWell.objects.count(),1)
    def test_stale_metadata_write_rejected(self):
        old=SourceBlock.objects.get(pk=self.block.pk);self.fill()
        old.name="Не должно сохраниться"
        with self.assertRaises(ValidationError):save_block(old,self.user,0)
        self.block.refresh_from_db();self.assertNotEqual(self.block.name,old.name)
    def test_clear_keeps_sample_and_other_positions(self):
        self.fill();self.fill(column=2)
        clear_well(self.block.pk,"A",1,self.user,self.rev())
        self.assertTrue(Sample.objects.filter(pk=self.sample.pk).exists())
        self.assertEqual(list(self.block.wells.values_list("column",flat=True)),[2])
    def test_no_stock_movements(self):
        before=Movement.objects.count();self.fill();clear_well(self.block.pk,"A",1,self.user,self.rev())
        self.assertEqual(Movement.objects.count(),before)
    def test_edit_permission_checked(self):
        with self.assertRaises(PermissionDenied):
            set_well(self.block.pk,"A",1,self.sample.pk,self.denied,self.rev())
    def test_audit_created(self):
        before=Audit.objects.count();self.fill()
        self.assertEqual(Audit.objects.count(),before+1)
    def test_unique_position_database_constraint(self):
        w=self.fill()
        with self.assertRaises(IntegrityError),transaction.atomic():
            SourceBlockWell.objects.create(block=self.block,row="A",column=1,sample=self.sample,
                dna_ul=1,water_ul=2,created_by=self.user,updated_by=self.user)
    def test_plate_has_96_edit_links(self):
        url=reverse("admin:lab_sourceblock_layout",args=[self.block.pk])
        r=self.client.get(url)
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.content.decode().count('/well/'),96)
        self.assertContains(r,"A1");self.assertContains(r,"H12")
    def test_well_get_is_read_only(self):
        before=Audit.objects.count()
        r=self.client.get(reverse("admin:lab_sourceblock_cell",args=[self.block.pk,"A",1]))
        self.assertEqual(r.status_code,200)
        self.assertFalse(SourceBlockWell.objects.exists());self.assertEqual(Audit.objects.count(),before)
    def test_cell_post_creates_mapping(self):
        r=self.client.post(reverse("admin:lab_sourceblock_cell",args=[self.block.pk,"A",1]),
            {"operation":"save","expected_revision":self.rev(),"sample":self.sample.pk,"dna_ul":"1","water_ul":"2","note":""})
        self.assertEqual(r.status_code,302);self.assertEqual(SourceBlockWell.objects.count(),1)
    def test_clear_needs_confirmation(self):
        self.fill()
        r=self.client.post(reverse("admin:lab_sourceblock_cell",args=[self.block.pk,"A",1]),
            {"operation":"clear","expected_revision":self.rev()})
        self.assertEqual(r.status_code,200);self.assertEqual(SourceBlockWell.objects.count(),1)
    def test_menu_in_samples_section(self):
        r=self.client.get(reverse("admin:lab_section8b",args=["lab_samples"]))
        self.assertEqual(r.status_code,200);self.assertContains(r,"Исходные блоки")
    def test_no_admin_deletion(self):
        from django.test import RequestFactory
        request=RequestFactory().get("/");request.user=self.user
        self.assertFalse(admin.site._registry[SourceBlock].has_delete_permission(request,self.block))
    def test_native_add_form(self):
        r=self.client.post(reverse("admin:lab_sourceblock_add"),{"name":"Новый", "default_dna_ul":"3",
            "default_water_ul":"4","note":"","expected_revision":"0","_save":"Сохранить"})
        self.assertEqual(r.status_code,302)
        self.assertTrue(SourceBlock.objects.filter(name="Новый").exists())
    def test_native_change_form(self):
        r=self.client.post(reverse("admin:lab_sourceblock_change",args=[self.block.pk]),{"name":"Переименован",
            "default_dna_ul":"3","default_water_ul":"4","note":"","expected_revision":self.rev(),"_save":"Сохранить"})
        self.assertEqual(r.status_code,302)
        self.block.refresh_from_db();self.assertEqual(self.block.name,"Переименован")
    def test_new_instance_cannot_overwrite_existing(self):
        obj=SourceBlock(pk=self.block.pk,name="Подмена",default_dna_ul=1,default_water_ul=2)
        with self.assertRaises(ValidationError):save_block(obj,self.user,0)
