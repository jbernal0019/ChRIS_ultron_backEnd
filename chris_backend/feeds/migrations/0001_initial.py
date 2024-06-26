# Generated by Django 4.2.5 on 2024-04-10 04:25

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Feed',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('creation_date', models.DateTimeField(auto_now_add=True)),
                ('modification_date', models.DateTimeField(auto_now_add=True)),
                ('name', models.CharField(blank=True, db_index=True, max_length=200)),
                ('public', models.BooleanField(blank=True, db_index=True, default=False)),
                ('folder', models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='feed', to='core.chrisfolder')),
                ('owner', models.ManyToManyField(related_name='feed', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ('-creation_date',),
            },
        ),
        migrations.CreateModel(
            name='Tag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(blank=True, max_length=100)),
                ('color', models.CharField(max_length=20)),
            ],
        ),
        migrations.CreateModel(
            name='Tagging',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('feed', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='feeds.feed')),
                ('tag', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='feeds.tag')),
            ],
            options={
                'unique_together': {('feed', 'tag')},
            },
        ),
        migrations.AddField(
            model_name='tag',
            name='feeds',
            field=models.ManyToManyField(related_name='tags', through='feeds.Tagging', to='feeds.feed'),
        ),
        migrations.AddField(
            model_name='tag',
            name='owner',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
        ),
        migrations.CreateModel(
            name='Note',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('creation_date', models.DateTimeField(auto_now_add=True)),
                ('modification_date', models.DateTimeField(auto_now_add=True)),
                ('title', models.CharField(blank=True, max_length=100)),
                ('content', models.TextField(blank=True)),
                ('feed', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='note', to='feeds.feed')),
            ],
        ),
        migrations.CreateModel(
            name='Comment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('creation_date', models.DateTimeField(auto_now_add=True)),
                ('title', models.CharField(blank=True, max_length=100)),
                ('content', models.TextField(blank=True)),
                ('feed', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='comments', to='feeds.feed')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ('-creation_date',),
            },
        ),
    ]
