import json
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError,transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from .models import SourceBlock,SourceBlockWell,Sample,Project,SampleProjectMembership,Audit,Movement
from .source_blocks8v_models import SourceBlockImport
from .source_blocks8v import save_block,set_well,set_position,entry,clear_well
from .source_blocks8g import create_preview,apply_preview,receipt_token,token_receipt
from .source_blocks8g_excel import export

class Import8GTests(TestCase):
    def setUp(self):
        U=get_user_model()
        self.user=U.objects.create_superuser(username="sb8g",email="",password="test-only-password")
        self.other=U.objects.create_superuser(username="sb8g_other",email="",password="test-only-password")
        self.denied=U.objects.create_user(username="sb8g_denied",password="test-only-password")
        p=Project.objects.create(title="Тест 8Г",kind="Проект",manager=self.user)
        self.sample=Sample(series="QQ",number="1",project=p,kind="ДНК",location="Тест")
        Sample.objects.bulk_create([self.sample]);SampleProjectMembership.objects.create(sample=self.sample,project=p)
        self.block=SourceBlock(name="8Г",default_dna_ul=Decimal("10"),default_water_ul=Decimal("160"))
        save_block(self.block,self.user,0);self.client.force_login(self.user)
    def rev(self):self.block.refresh_from_db();return self.block.revision
    def set(self,kind="sample",**kwargs):
        values={"sample_id":self.sample.pk} if kind=="sample" else {"dna_ul":0} if kind=="water" else {"label":"к-821"}
        values.update(kwargs)
        return set_position(self.block.pk,"A",1,self.user,self.rev(),kind=kind,**values)
    def book(self,labels=None):
        metadata={"id":self.block.pk,"revision":self.rev(),"name":self.block.name,"date":"2012-12-12","actor":"historical","dna":"10","water":"160"}
        data=export(metadata,[],True)
        if not labels:return data
        b=load_workbook(BytesIO(data))
        for coordinate,value in labels.items():b.worksheets[0][coordinate]=value
        out=BytesIO();b.save(out);b.close();return out.getvalue()
    def preview(self,data,**kwargs):return create_preview(data,self.block.pk,self.user,sample_scope=Sample.objects.all(),**kwargs)
    def apply(self,receipt,**kwargs):return apply_preview(receipt.pk,self.block.pk,self.user,sample_scope=Sample.objects.all(),**kwargs)
    def test_raw_position(self):
        w=self.set("unregistered");self.assertIsNone(w.sample_id);self.assertEqual(w.display_label,"к-821")
        self.assertEqual(Sample.objects.count(),1)
    def test_water_unknown_is_not_zero(self):
        w=self.set("water");self.assertIsNone(w.water_ul);self.assertEqual(w.dna_ul,0);self.assertIsNone(w.sample_id)
    def test_water_known_zero(self):self.assertEqual(self.set("water",water_ul=0).water_ul,0)
    def test_control_nonzero_dna_rejected(self):
        with self.assertRaises(ValidationError):self.set("water",dna_ul=2)
    def test_raw_cannot_have_sample(self):
        with self.assertRaises(ValidationError):self.set("unregistered",sample_id=self.sample.pk)
    def test_raw_blank_rejected(self):
        with self.assertRaises(ValidationError):self.set("unregistered",label="   ")
    def test_registered_has_no_raw_label(self):
        with self.assertRaises(ValidationError):self.set("sample",label="other")
    def test_legacy_set_well_still_supported(self):
        w=set_well(self.block.pk,"A",1,self.sample.pk,self.user,self.rev())
        self.assertEqual(w.kind,"sample");self.assertEqual(w.water_ul,160)
    def test_link_raw_to_registered_preserves_volumes(self):
        self.set("unregistered",dna_ul=3,water_ul=4,note="original")
        w=set_well(self.block.pk,"A",1,self.sample.pk,self.user,self.rev(),note="original")
        self.assertEqual((w.kind,w.dna_ul,w.water_ul),("sample",3,4));self.assertEqual(w.raw_label,"")
        detail=Audit.objects.order_by("-pk").first().detail
        try:detail=json.loads(detail)
        except (json.JSONDecodeError,TypeError):pass
        self.assertIn("к-821",str(detail))
    def test_preview_does_not_change_block_or_wells(self):
        rev=self.rev();audit=Audit.objects.count();r=self.preview(self.book({"B8":"QQ 1"}))
        self.assertEqual(self.rev(),rev);self.assertFalse(SourceBlockWell.objects.exists());self.assertEqual(Audit.objects.count(),audit)
        self.assertEqual(len(r.payload["changes"]),1)
    def test_import_canonical_sample(self):
        r=self.preview(self.book({"B8":"QQ 1"}));self.apply(r)
        self.assertEqual(self.block.wells.get().sample_id,self.sample.pk)
    def test_import_raw_requires_confirmation(self):
        r=self.preview(self.book({"B8":"к-821"}))
        with self.assertRaises(ValidationError):self.apply(r)
        self.assertFalse(SourceBlockWell.objects.exists())
        self.apply(r,acknowledge_raw=True);self.assertEqual(self.block.wells.get().kind,"unregistered")
    def test_import_control(self):
        self.apply(self.preview(self.book({"B8":"вода"})))
        w=self.block.wells.get();self.assertEqual(w.kind,"water");self.assertIsNone(w.water_ul)
    def test_repeated_samples_imported(self):
        self.apply(self.preview(self.book({"B8":"QQ 1","C8":"QQ 1"})))
        self.assertEqual(self.block.wells.count(),2);self.assertEqual(Sample.objects.count(),1)
    def test_batch_revision_and_audit_once(self):
        before=Audit.objects.count();rev=self.rev()
        self.apply(self.preview(self.book({"B8":"QQ 1","C8":"QQ 1"})))
        self.assertEqual(self.rev(),rev+1);self.assertEqual(Audit.objects.count(),before+1)
    def test_fill_keeps_existing_position(self):
        self.set("sample",dna_ul=3);r=self.preview(self.book({"B8":"unknown"}))
        self.assertEqual(len(r.payload["changes"]),0)
        self.apply(r,acknowledge_raw=True);self.assertEqual(self.block.wells.get().dna_ul,3)
    def test_empty_without_clear_preserves(self):
        self.set();r=self.preview(self.book(),mode="update");self.apply(r)
        self.assertEqual(self.block.wells.count(),1)
    def test_clear_requires_confirmation(self):
        self.set();r=self.preview(self.book(),mode="update",clear=True)
        with self.assertRaises(ValidationError):self.apply(r)
        self.apply(r,acknowledge_clear=True);self.assertFalse(self.block.wells.exists());self.assertEqual(Sample.objects.count(),1)
    def test_fill_with_clear_rejected(self):
        with self.assertRaises(ValidationError):self.preview(self.book(),mode="fill",clear=True)
    def test_stale_preview_rejected(self):
        r=self.preview(self.book({"C8":"QQ 1"}));self.set()
        with self.assertRaises(ValidationError):self.apply(r)
        self.assertEqual(self.block.wells.count(),1)
    def test_expired_preview_rejected(self):
        r=self.preview(self.book({"B8":"QQ 1"}));r.expires_at=timezone.now()-timedelta(seconds=1);r.save(update_fields=["expires_at"])
        with self.assertRaises(ValidationError):self.apply(r)
    def test_other_user_cannot_apply(self):
        r=self.preview(self.book({"B8":"QQ 1"}))
        with self.assertRaises(PermissionDenied):apply_preview(r.pk,self.block.pk,self.other,sample_scope=Sample.objects.all())
    def test_repeat_apply_is_noop(self):
        r=self.preview(self.book({"B8":"QQ 1"}));self.apply(r);rev=self.rev();before=Audit.objects.count()
        _,first=self.apply(r);self.assertFalse(first);self.assertEqual(self.rev(),rev);self.assertEqual(Audit.objects.count(),before)
    def test_sample_scope_rechecked(self):
        r=self.preview(self.book({"B8":"QQ 1"}))
        with self.assertRaises(ValidationError):apply_preview(r.pk,self.block.pk,self.user,sample_scope=Sample.objects.none())
    def test_sample_rename_rechecked(self):
        r=self.preview(self.book({"B8":"QQ 1"}));Sample.objects.filter(pk=self.sample.pk).update(series="QT")
        with self.assertRaises(ValidationError):self.apply(r)
    def test_import_rollback_on_late_invalid_entry(self):
        r=self.preview(self.book({"B8":"QQ 1","C8":"QQ 1"}));r.payload["changes"][1]["after"]["dna"]="-1";r.save(update_fields=["payload"])
        before=Audit.objects.count();rev=self.rev()
        with self.assertRaises(ValidationError):self.apply(r)
        self.assertFalse(self.block.wells.exists());self.assertEqual(self.rev(),rev);self.assertEqual(Audit.objects.count(),before)
    def test_roundtrip_export_is_noop(self):
        w=self.set("unregistered",note="=text")
        data=export({"name":"8Г","dna":"10","water":"160"},[entry(w)])
        r=self.preview(data,mode="update");self.assertEqual(len(r.payload["changes"]),0)
    def test_simple_file_keeps_existing_note(self):
        self.set(note="keep")
        self.apply(self.preview(self.book({"B8":"QQ 1"}),mode="update"))
        self.assertEqual(self.block.wells.get().note,"keep")
    def test_simple_file_keeps_known_control_water(self):
        self.set("water",water_ul=9)
        self.apply(self.preview(self.book({"B8":"вода"}),mode="update"))
        self.assertEqual(self.block.wells.get().water_ul,9)
    def test_no_stock_changes(self):
        count=Movement.objects.count();self.apply(self.preview(self.book({"B8":"QQ 1"})))
        self.assertEqual(Movement.objects.count(),count)
    def test_token_bound_to_user_and_block(self):
        r=self.preview(self.book({"B8":"QQ 1"}));token=receipt_token(r)
        self.assertEqual(token_receipt(token,self.user,self.block.pk).pk,r.pk)
        with self.assertRaises(PermissionDenied):token_receipt(token,self.other,self.block.pk)
    def test_invalid_token_rejected(self):
        with self.assertRaises(ValidationError):token_receipt("bad",self.user,self.block.pk)
    def test_layout_supports_raw_and_control(self):
        self.set("unregistered");r=self.client.get(reverse("admin:lab_sourceblock_layout",args=[self.block.pk]))
        self.assertContains(r,"Не зарегистрировано");self.assertContains(r,"к-821")
        self.set("water");r=self.client.get(reverse("admin:lab_sourceblock_layout",args=[self.block.pk]));self.assertContains(r,"не указано")
    def test_raw_editor_post(self):
        r=self.client.post(reverse("admin:lab_sourceblock_cell",args=[self.block.pk,"A",1]),{"operation":"save","kind":"unregistered",
            "raw_label":"X-777","dna_ul":"1","water_ul":"2","expected_revision":self.rev()})
        self.assertEqual(r.status_code,302);self.assertEqual(self.block.wells.get().raw_label,"X-777")
    def test_water_editor_post(self):
        r=self.client.post(reverse("admin:lab_sourceblock_cell",args=[self.block.pk,"A",1]),{"operation":"save","kind":"water",
            "dna_ul":"0","water_ul":"","expected_revision":self.rev()})
        self.assertEqual(r.status_code,302);self.assertIsNone(self.block.wells.get().water_ul)
    def test_export_endpoint(self):
        self.set();r=self.client.get(reverse("admin:lab_sourceblock_excel_export",args=[self.block.pk]))
        self.assertEqual(r.status_code,200);self.assertTrue(r.content.startswith(b"PK"))
    def test_upload_and_confirm(self):
        f=SimpleUploadedFile("block.xlsx",self.book({"B8":"unknown"}),content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        r=self.client.post(reverse("admin:lab_sourceblock_excel_import",args=[self.block.pk]),{"file":f,"mode":"fill"})
        self.assertEqual(r.status_code,200);self.assertContains(r,"Предпросмотр")
        receipt=SourceBlockImport.objects.get();self.assertFalse(self.block.wells.exists())
        r=self.client.post(reverse("admin:lab_sourceblock_excel_apply",args=[self.block.pk]),{"token":receipt_token(receipt),"ack_raw":"1"})
        self.assertEqual(r.status_code,302);self.assertEqual(self.block.wells.get().kind,"unregistered")
    def test_permission_rechecked(self):
        r=self.preview(self.book({"B8":"QQ 1"}));self.user.is_active=False;self.user.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):self.apply(r)
    def test_database_type_constraint(self):
        with self.assertRaises(IntegrityError),transaction.atomic():
            SourceBlockWell.objects.create(block=self.block,row="A",column=1,kind="unregistered",raw_label="bad",sample=self.sample,dna_ul=1,water_ul=2,created_by=self.user,updated_by=self.user)
