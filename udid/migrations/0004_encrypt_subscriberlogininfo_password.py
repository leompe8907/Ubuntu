# Generated manually (entorno local sin dj_database_url instalado para correr makemigrations)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('udid', '0003_apikey_plan_tenant_tenant_tenants_name_bc4541_idx_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='subscriberlogininfo',
            name='password',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
