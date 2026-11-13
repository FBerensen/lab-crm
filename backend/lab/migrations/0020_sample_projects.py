from django.db import migrations,models

def copy_links(apps,schema_editor):
    Sample=apps.get_model("lab","Sample");Link=apps.get_model("lab","SampleProjectMembership")
    alias=schema_editor.connection.alias
    if Sample.objects.using(alias).filter(project_id__isnull=True).exists():raise RuntimeError("Sample without legacy project")
    batch=[]
    for pk,project_id in Sample.objects.using(alias).order_by("pk").values_list("pk","project_id").iterator(chunk_size=1000):
        batch.append(Link(sample_id=pk,project_id=project_id))
        if len(batch)==1000:Link.objects.using(alias).bulk_create(batch);batch=[]
    if batch:Link.objects.using(alias).bulk_create(batch)
    if Link.objects.using(alias).count()!=Sample.objects.using(alias).count():raise RuntimeError("Project link copy count mismatch")

def reject_reverse(apps,schema_editor):
    raise RuntimeError("8A cannot be reversed automatically: multiple links would be lost. Restore an approved backup in a controlled procedure.")

class Migration(migrations.Migration):
    dependencies=[("lab","0019_preparation_draft_selection")]
    operations=[
        migrations.CreateModel(name="SampleProjectMembership",fields=[
            ("id",models.BigAutoField(primary_key=True,serialize=False)),
            ("sample",models.ForeignKey("lab.sample",on_delete=models.CASCADE,related_name="project_memberships",verbose_name="Образец/Проба")),
            ("project",models.ForeignKey("lab.project",on_delete=models.PROTECT,related_name="sample_memberships",verbose_name="Проект")),
        ],options={"verbose_name":"Связь образца с проектом","verbose_name_plural":"Связи образцов с проектами","constraints":[models.UniqueConstraint(fields=["sample","project"],name="sample_project_unique_8a")]}),
        migrations.AddField(model_name="sample",name="projects",field=models.ManyToManyField("lab.project",through="lab.SampleProjectMembership",related_name="linked_samples",verbose_name="Проекты")),
        migrations.RunPython(copy_links,reject_reverse),
    ]
