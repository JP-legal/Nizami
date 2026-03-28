import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chats', '0022_rename_chats_pendi_tenant__status_idx_chats_pendi_tenant__6e0d3e_idx'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ChatExport',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('chat_json', models.JSONField(help_text='[{role, content, timestamp}]')),
                ('summary_json', models.JSONField(help_text='{overview, problem, root_cause, solution, next_steps}')),
                ('pdf_s3_key', models.CharField(blank=True, max_length=512, null=True)),
                ('pdf_url', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                (
                    'chat',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='exports',
                        to='chats.chat',
                    ),
                ),
                (
                    'owner',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='chat_exports',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'db_table': 'chats_chat_export',
                'indexes': [
                    models.Index(fields=['owner', 'created_at'], name='chats_export_owner_created_idx'),
                ],
            },
        ),
    ]
