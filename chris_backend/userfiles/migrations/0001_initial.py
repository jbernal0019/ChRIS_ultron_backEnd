# Generated by Django 4.2.5 on 2024-04-10 04:27

from django.db import migrations


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserFile',
            fields=[
            ],
            options={
                'ordering': ('-fname',),
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('core.chrisfile',),
        ),
    ]
