from django.db import migrations,models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[("lab","0022_source_blocks_excel")]
    operations=[migrations.AddField(model_name="sourceblock",name="storage_place",
        field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,
            related_name="source_blocks",to="lab.storageplace",verbose_name="Место хранения"))]
