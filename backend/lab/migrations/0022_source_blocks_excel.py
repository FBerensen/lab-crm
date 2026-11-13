from decimal import Decimal
import uuid
from django.conf import settings
from django.db import migrations,models
import django.db.models.deletion
import django.core.validators

class Migration(migrations.Migration):
    dependencies=[("lab","0021_source_blocks"),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.AddField(model_name="sourceblockwell",name="kind",field=models.CharField(choices=[("sample","Зарегистрированная проба"),("water","Водный контроль"),("unregistered","Не зарегистрировано")],default="sample",max_length=12,verbose_name="Тип позиции")),
        migrations.AddField(model_name="sourceblockwell",name="raw_label",field=models.CharField(blank=True,default="",max_length=500,verbose_name="Незарегистрированное обозначение")),
        migrations.AlterField(model_name="sourceblockwell",name="sample",field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name="source_block_wells",to="lab.sample",verbose_name="Образец / проба")),
        migrations.AlterField(model_name="sourceblockwell",name="water_ul",field=models.DecimalField(blank=True,null=True,decimal_places=4,max_digits=16,validators=[django.core.validators.MinValueValidator(Decimal("0"))],verbose_name="Вода, мкл")),
        migrations.AddConstraint(model_name="sourceblockwell",constraint=models.CheckConstraint(condition=(models.Q(kind="sample",sample__isnull=False,raw_label="",water_ul__isnull=False)|models.Q(kind="water",sample__isnull=True,raw_label="",dna_ul=0)|models.Q(kind="unregistered",sample__isnull=True,raw_label__regex=r"\S",water_ul__isnull=False)),name="sb8g_kind_consistent")),
        migrations.CreateModel(name="SourceBlockImport",fields=[
            ("id",models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),
            ("created_at",models.DateTimeField(auto_now_add=True)),
            ("expires_at",models.DateTimeField()),
            ("expected_revision",models.PositiveBigIntegerField()),
            ("file_digest",models.CharField(max_length=64)),
            ("payload",models.JSONField()),
            ("applied_at",models.DateTimeField(blank=True,null=True)),
            ("result_revision",models.PositiveBigIntegerField(blank=True,null=True)),
            ("block",models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name="excel_imports",to="lab.sourceblock",verbose_name="Исходный блок")),
            ("created_by",models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name="source_imports_created_8g",to=settings.AUTH_USER_MODEL,verbose_name="Подготовил импорт")),
        ],options={"verbose_name":"Предпросмотр импорта блока","verbose_name_plural":"Предпросмотры импорта блоков","default_permissions":()}),
    ]
