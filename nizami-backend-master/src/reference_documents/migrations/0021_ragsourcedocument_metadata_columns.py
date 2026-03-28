from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('reference_documents', '0020_add_ragsourcedocumentchunk_and_embed_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='ragsourcedocument',
            name='doc_type',
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='ragsourcedocument',
            name='entity',
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='ragsourcedocument',
            name='date_gregorian',
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='ragsourcedocument',
            name='date_hijri',
            field=models.CharField(blank=True, max_length=30, null=True),
        ),
        migrations.AddField(
            model_name='ragsourcedocument',
            name='decision_number',
            field=models.CharField(blank=True, db_index=True, max_length=150, null=True),
        ),
        migrations.AddField(
            model_name='ragsourcedocument',
            name='decision_date_hijri',
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='ragsourcedocument',
            name='source',
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='ragsourcedocument',
            name='incomplete_flag',
            field=models.BooleanField(default=False, db_index=True),
        ),
        migrations.AddField(
            model_name='ragsourcedocument',
            name='is_duplicate',
            field=models.BooleanField(default=False, db_index=True),
        ),
        migrations.AddField(
            model_name='ragsourcedocument',
            name='format_confidence',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
