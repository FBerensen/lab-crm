import json
from unittest.mock import patch
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.db.models.deletion import ProtectedError
from django.test import TestCase,RequestFactory
from django.urls import reverse
from lab.models import SourceBlock,SourceBlockWell,StoragePlace,Sample,Project,SampleProjectMembership,Lot,Movement,Audit
from lab.source_blocks8v import save_block,set_well,set_position
from lab.storage8d import move_block,location_path
from lab.source_blocks8g import create_preview,apply_preview
from lab.source_blocks8g_excel import export

class Storage8DTests(TestCase):
    def setUp(self):
        U=get_user_model();self.user=U.objects.create_superuser(username="store8d",email="",password="test-only")
        self.denied=U.objects.create_user(username="store8d_denied",password="test-only")
        self.room=StoragePlace.objects.create(name="8D room A")
        self.fridge=StoragePlace.objects.create(name="8D freezer",parent=self.room)
        self.shelf=StoragePlace.objects.create(name="8D shelf",parent=self.fridge)
        self.other=StoragePlace.objects.create(name="8D room B")
        self.inactive=StoragePlace.objects.create(name="8D inactive",active=False)
        p=Project.objects.create(title="8D project",kind="Проект",manager=self.user)
        self.sample=Sample(series="SD",number="1",project=p,kind="ДНК",location=self.fridge.name,storage_place=self.fridge)
        Sample.objects.bulk_create([self.sample]);SampleProjectMembership.objects.create(sample=self.sample,project=p)
        self.lot=Lot.objects.create(name="8D material",unit="мкл",storage_place=self.fridge,quantity=10,reserved=2)
        self.block=SourceBlock(name="8D block",default_dna_ul=1,default_water_ul=2)
        save_block(self.block,self.user,0);self.client.force_login(self.user)
    def rev(self):self.block.refresh_from_db();return self.block.revision
    def move(self,target):return move_block(self.block.pk,target,self.user,self.rev())
    def req(self):
        r=RequestFactory().get("/");r.user=self.user;return r
    def contents(self,place=None,**params):
        return self.client.get(reverse("admin:lab_storageplace_contents",args=[(place or self.room).pk]),params)
    def test_default_location_unset(self):self.assertIsNone(self.block.storage_place_id)
    def test_create_with_location(self):
        b=SourceBlock(name="with place",default_dna_ul=1,default_water_ul=2,storage_place=self.shelf)
        save_block(b,self.user,0);self.assertEqual(b.storage_place_id,self.shelf.pk)
    def test_move_changes_place_and_revision(self):
        old=self.rev();self.move(self.shelf.pk);self.assertEqual(self.rev(),old+1);self.assertEqual(self.block.storage_place_id,self.shelf.pk)
    def test_clear_location(self):
        self.move(self.shelf.pk);self.move(None);self.block.refresh_from_db();self.assertIsNone(self.block.storage_place_id)
    def test_inactive_destination_rejected(self):
        with self.assertRaises(ValidationError):self.move(self.inactive.pk)
        self.block.refresh_from_db();self.assertIsNone(self.block.storage_place_id)
    def test_existing_inactive_location_retained(self):
        self.move(self.shelf.pk);self.shelf.active=False;self.shelf.save()
        self.block.refresh_from_db();self.block.note="metadata edit";save_block(self.block,self.user,self.block.revision)
        self.assertEqual(self.block.storage_place_id,self.shelf.pk)
    def test_nonexistent_place_rejected(self):
        with self.assertRaises(ValidationError):self.move(99999999)
    def test_bad_place_id_rejected(self):
        for target in (True,-1,"garbage"):
            with self.assertRaises(ValidationError):self.move(target)
    def test_permission_preserved(self):
        with self.assertRaises(PermissionDenied):move_block(self.block.pk,self.shelf.pk,self.denied,self.rev())
    def test_stale_move_rejected(self):
        old=self.rev();self.move(self.shelf.pk)
        with self.assertRaises(ValidationError):move_block(self.block.pk,self.other.pk,self.user,old)
    def test_occupied_wells_unchanged(self):
        set_well(self.block.pk,"A",1,self.sample.pk,self.user,self.rev())
        set_position(self.block.pk,"A",2,self.user,self.rev(),kind="water",dna_ul=0)
        set_position(self.block.pk,"A",3,self.user,self.rev(),kind="unregistered",label="unknown")
        before=list(self.block.wells.order_by("pk").values());self.move(self.shelf.pk)
        self.assertEqual(list(self.block.wells.order_by("pk").values()),before)
    def test_sample_and_stock_unchanged(self):
        sample=list(Sample.objects.filter(pk=self.sample.pk).values());lot=list(Lot.objects.filter(pk=self.lot.pk).values());movements=Movement.objects.count()
        self.move(self.other.pk)
        self.assertEqual(list(Sample.objects.filter(pk=self.sample.pk).values()),sample)
        self.assertEqual(list(Lot.objects.filter(pk=self.lot.pk).values()),lot);self.assertEqual(Movement.objects.count(),movements)
    def test_move_audit_has_old_and_new_path(self):
        self.move(self.shelf.pk);self.move(self.other.pk)
        detail=Audit.objects.order_by("-pk").first().detail
        try:detail=json.loads(detail)
        except (TypeError,json.JSONDecodeError):pass
        self.assertIn(self.room.name,str(detail));self.assertIn(self.shelf.name,str(detail));self.assertIn(self.other.name,str(detail))
    def test_place_deletion_protected(self):
        self.move(self.shelf.pk)
        with self.assertRaises(ProtectedError):self.shelf.delete()
    def test_full_path(self):
        self.move(self.shelf.pk);self.block.refresh_from_db()
        self.assertEqual(location_path(self.block),"8D room A / 8D freezer / 8D shelf")
    def test_parent_rename_updates_display_without_moving_block(self):
        self.move(self.shelf.pk);self.room.name="8D renamed room";self.room.save();self.block.refresh_from_db()
        self.assertTrue(location_path(self.block).startswith("8D renamed room /"));self.assertEqual(self.block.storage_place_id,self.shelf.pk)
    def test_existing_cycle_guard_preserved(self):
        self.room.parent=self.shelf
        with self.assertRaises(ValidationError):self.room.save()
    def test_direct_only_excludes_child_blocks(self):
        self.move(self.shelf.pk);r=self.contents(include_children="0")
        self.assertEqual(r.status_code,200);self.assertEqual(r.context_data["block_page"].paginator.count,0)
    def test_include_children_includes_block(self):
        self.move(self.shelf.pk);r=self.contents()
        self.assertEqual(r.status_code,200);self.assertEqual(r.context_data["block_page"].paginator.count,1)
        self.assertContains(r,"8D block");self.assertContains(r,"8D room A / 8D freezer / 8D shelf")
    def test_other_branch_excluded(self):
        self.move(self.other.pk);self.assertEqual(self.contents().context_data["block_page"].paginator.count,0)
    def test_unset_not_assigned_to_place(self):
        r=self.contents();self.assertEqual(r.context_data["block_page"].paginator.count,0);self.assertGreaterEqual(r.context_data["unplaced_blocks"],1)
    def test_search_by_name(self):
        self.move(self.shelf.pk);self.assertEqual(self.contents(q="8D block").context_data["block_page"].paginator.count,1)
    def test_search_by_code(self):
        self.move(self.shelf.pk);self.assertEqual(self.contents(q=self.block.code).context_data["block_page"].paginator.count,1)
    def test_repeated_sample_search_does_not_duplicate_block(self):
        self.move(self.shelf.pk)
        set_well(self.block.pk,"A",1,self.sample.pk,self.user,self.rev());set_well(self.block.pk,"A",2,self.sample.pk,self.user,self.rev())
        r=self.contents(q="SD №1");self.assertEqual(r.context_data["block_page"].paginator.count,1)
        stats=r.context_data["block_rows"][0]["stats"];self.assertEqual(stats["sample_wells"],2);self.assertEqual(stats["unique_samples"],1)
    def test_control_and_raw_counts(self):
        self.move(self.shelf.pk);set_position(self.block.pk,"A",1,self.user,self.rev(),kind="water",dna_ul=0)
        set_position(self.block.pk,"A",2,self.user,self.rev(),kind="unregistered",label="X-9")
        stats=self.contents().context_data["block_rows"][0]["stats"];self.assertEqual((stats["filled"],stats["controls"],stats["raw"]),(2,1,1))
    def test_show_empty_affects_lots_not_empty_blocks(self):
        self.move(self.shelf.pk);self.assertEqual(self.contents(show_empty="0").context_data["block_page"].paginator.count,1)
    def test_readonly_contents_no_audit_or_revision(self):
        self.move(self.shelf.pk);rev=self.rev();audit=Audit.objects.count();self.contents()
        self.assertEqual(self.rev(),rev);self.assertEqual(Audit.objects.count(),audit)
    def test_block_permission_hides_section_and_count(self):
        self.move(self.shelf.pk);ma=admin.site._registry[SourceBlock]
        with patch.object(ma,"has_view_permission",return_value=False):
            r=self.contents();self.assertFalse(r.context_data["can_blocks"]);self.assertNotContains(r,"8D block")
            p=admin.site._registry[StoragePlace].get_queryset(self.req()).get(pk=self.shelf.pk);self.assertIsNone(p.ui_block_count8d)
    def test_counter_counts_direct_blocks_only(self):
        self.move(self.shelf.pk);ma=admin.site._registry[StoragePlace];qs=ma.get_queryset(self.req())
        self.assertEqual(qs.get(pk=self.room.pk).ui_block_count8d,0);self.assertEqual(qs.get(pk=self.shelf.pk).ui_block_count8d,1)
    def test_card_has_location_field(self):
        ma=admin.site._registry[SourceBlock];f=ma.get_form(self.req(),self.block)(instance=self.block)
        self.assertIn("storage_place",f.fields);self.assertFalse(f.fields["storage_place"].required)
        self.assertNotIn(self.inactive.pk,list(f.fields["storage_place"].queryset.values_list("pk",flat=True)))
    def test_card_keeps_current_inactive_choice(self):
        self.move(self.shelf.pk);self.shelf.active=False;self.shelf.save();self.block.refresh_from_db()
        ma=admin.site._registry[SourceBlock];f=ma.get_form(self.req(),self.block)(instance=self.block)
        self.assertIn(self.shelf.pk,list(f.fields["storage_place"].queryset.values_list("pk",flat=True)))
    def test_native_card_post_saves_location(self):
        r=self.client.post(reverse("admin:lab_sourceblock_change",args=[self.block.pk]),{"name":self.block.name,"default_dna_ul":"1","default_water_ul":"2",
            "note":"","storage_place":self.shelf.pk,"expected_revision":self.rev(),"_save":"Save"})
        self.assertEqual(r.status_code,302);self.block.refresh_from_db();self.assertEqual(self.block.storage_place_id,self.shelf.pk)
    def test_layout_shows_path_and_contents_link(self):
        self.move(self.shelf.pk);r=self.client.get(reverse("admin:lab_sourceblock_layout",args=[self.block.pk]))
        self.assertContains(r,"8D room A / 8D freezer / 8D shelf");self.assertContains(r,reverse("admin:lab_storageplace_contents",args=[self.shelf.pk]))
    def test_place_management_permissions_unchanged(self):
        ma=admin.site._registry[StoragePlace];request=self.req();self.assertTrue(ma.has_add_permission(request))
        request.user=self.denied;self.assertFalse(ma.has_add_permission(request))
    def test_pending_excel_preview_invalidated_by_move(self):
        book=export({"name":"8D","dna":"1","water":"2"},[],True)
        r=create_preview(book,self.block.pk,self.user,sample_scope=Sample.objects.all())
        self.move(self.shelf.pk)
        with self.assertRaises(ValidationError):apply_preview(r.pk,self.block.pk,self.user,sample_scope=Sample.objects.all())
    def test_pagination_has_50_blocks_and_keeps_filters(self):
        for n in range(51):
            b=SourceBlock(name="paged %02d"%n,default_dna_ul=1,default_water_ul=2,storage_place=self.shelf);save_block(b,self.user,0)
        r=self.contents(q="paged",lot_page="2",sample_page="3")
        self.assertEqual(len(r.context_data["block_rows"]),50);self.assertIn("lot_page=2",r.context_data["block_links"]["next"])
        self.assertIn("sample_page=3",r.context_data["block_links"]["next"]);self.assertIn("q=paged",r.context_data["block_links"]["next"])
        r=self.contents(q="paged",block_page="2");self.assertEqual(len(r.context_data["block_rows"]),1)
