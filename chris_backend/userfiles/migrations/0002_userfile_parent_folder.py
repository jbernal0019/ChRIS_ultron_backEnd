# Generated by Django 4.2.5 on 2023-12-19 04:41

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_chrisfolder_chrislinkfile'),
        ('userfiles', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='userfile',
            name='parent_folder',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='user_files', to='core.chrisfolder'),
            preserve_default=False,
        ),
    ]
