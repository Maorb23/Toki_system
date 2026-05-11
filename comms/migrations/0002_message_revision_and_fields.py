# Generated for receiver-aware communication POC.

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ("comms", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="slack_short_version",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="message",
            name="teams_short_version",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="message",
            name="accepted_suggestion_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="message",
            name="rejected_suggestion_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name="MessageRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version_index", models.IntegerField()),
                ("text", models.TextField()),
                ("note", models.CharField(blank=True, max_length=200)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("message", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="revisions", to="comms.message")),
            ],
            options={
                "ordering": ["version_index"],
                "unique_together": {("message", "version_index")},
            },
        ),
    ]
