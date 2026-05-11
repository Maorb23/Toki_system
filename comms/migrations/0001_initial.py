# Generated for receiver-aware communication POC.

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Organization',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True)),
            ],
        ),
        migrations.CreateModel(
            name='Team',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('description', models.TextField(blank=True)),
                ('norms', models.JSONField(blank=True, default=list)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='teams', to='comms.organization')),
            ],
            options={'unique_together': {('organization', 'name')}},
        ),
        migrations.CreateModel(
            name='OrgValue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('description', models.TextField(blank=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='values', to='comms.organization')),
            ],
            options={'unique_together': {('organization', 'name')}},
        ),
        migrations.CreateModel(
            name='Employee',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120)),
                ('role', models.CharField(max_length=160)),
                ('seniority_level', models.CharField(blank=True, max_length=80)),
                ('communication_preferences', models.JSONField(blank=True, default=dict)),
                ('pain_points', models.JSONField(blank=True, default=list)),
                ('receiver_prompt', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('manager', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='direct_reports', to='comms.employee')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='employees', to='comms.organization')),
                ('team', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='employees', to='comms.team')),
            ],
            options={'unique_together': {('organization', 'name')}},
        ),
        migrations.CreateModel(
            name='Message',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel', models.CharField(choices=[('slack', 'Slack'), ('teams', 'Teams'), ('gmail', 'Gmail'), ('email', 'Email')], max_length=30)),
                ('intent', models.CharField(choices=[('update', 'Update'), ('request', 'Request'), ('disagreement', 'Disagreement'), ('escalation', 'Escalation'), ('feedback', 'Feedback'), ('decision', 'Decision'), ('apology', 'Apology'), ('alignment', 'Alignment')], max_length=40)),
                ('original_text', models.TextField()),
                ('final_text', models.TextField(blank=True)),
                ('overall_suggested_message', models.TextField(blank=True)),
                ('subject_line', models.CharField(blank=True, max_length=240)),
                ('scores_before', models.JSONField(blank=True, default=dict)),
                ('estimated_scores_after_all', models.JSONField(blank=True, default=dict)),
                ('current_scores', models.JSONField(blank=True, default=dict)),
                ('risks', models.JSONField(blank=True, default=list)),
                ('summary_of_changes', models.TextField(blank=True)),
                ('explanation', models.TextField(blank=True)),
                ('raw_llm_response', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('analyzed', 'Analyzed'), ('sent', 'Sent / Received'), ('feedback_received', 'Feedback received')], default='draft', max_length=40)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='comms.organization')),
                ('receiver', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='received_messages', to='comms.employee')),
                ('sender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sent_messages', to='comms.employee')),
            ],
        ),
        migrations.CreateModel(
            name='InlineSuggestion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('external_id', models.CharField(blank=True, max_length=80)),
                ('target_text', models.TextField()),
                ('start_index', models.IntegerField(blank=True, null=True)),
                ('end_index', models.IntegerField(blank=True, null=True)),
                ('issue', models.TextField(blank=True)),
                ('suggested_replacement', models.TextField()),
                ('reason', models.TextField(blank=True)),
                ('affected_scores', models.JSONField(blank=True, default=dict)),
                ('org_values_used', models.JSONField(blank=True, default=list)),
                ('decision', models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('rejected', 'Rejected')], default='pending', max_length=20)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('message', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='suggestions', to='comms.message')),
            ],
        ),
        migrations.CreateModel(
            name='ReceiverFeedback',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('clear', models.BooleanField(default=False)),
                ('too_direct', models.BooleanField(default=False)),
                ('too_soft', models.BooleanField(default=False)),
                ('too_long', models.BooleanField(default=False)),
                ('too_short', models.BooleanField(default=False)),
                ('missed_context', models.BooleanField(default=False)),
                ('too_much_context', models.BooleanField(default=False)),
                ('unclear_ask', models.BooleanField(default=False)),
                ('unclear_ownership', models.BooleanField(default=False)),
                ('not_aligned_with_preferences', models.BooleanField(default=False)),
                ('good_message', models.BooleanField(default=False)),
                ('free_text', models.TextField(blank=True)),
                ('prompt_update_summary', models.TextField(blank=True)),
                ('receiver_prompt_before', models.TextField(blank=True)),
                ('receiver_prompt_after', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('message', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='receiver_feedback', to='comms.message')),
                ('receiver', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='feedback_given', to='comms.employee')),
                ('sender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='feedback_received_from_receivers', to='comms.employee')),
            ],
        ),
    ]
