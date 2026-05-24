from django.db import models
from django.utils import timezone

class Organization(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    def __str__(self) -> str:
        return self.name

class OrgValue(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="values")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)

    class Meta:
        unique_together = ("organization", "name")

    def __str__(self) -> str:
        return self.name

class Team(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    norms = models.JSONField(default=list, blank=True)

    class Meta:
        unique_together = ("organization", "name")

    def __str__(self) -> str:
        return self.name

class Employee(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="employees")
    team = models.ForeignKey(Team, on_delete=models.SET_NULL, null=True, blank=True, related_name="employees")
    manager = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="direct_reports")

    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    role = models.CharField(max_length=160)
    seniority_level = models.CharField(max_length=80, blank=True)

    communication_preferences = models.JSONField(default=dict, blank=True)
    pain_points = models.JSONField(default=list, blank=True)
    receiver_prompt = models.TextField(blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("organization", "name")

    def __str__(self) -> str:
        return f"{self.name} — {self.role}"

class Message(models.Model):
    class Channel(models.TextChoices):
        SLACK = "slack", "Slack"
        TEAMS = "teams", "Teams"
        GMAIL = "gmail", "Gmail"
        EMAIL = "email", "Email"

    class Intent(models.TextChoices):
        UPDATE = "update", "Update"
        REQUEST = "request", "Request"
        DISAGREEMENT = "disagreement", "Disagreement"
        ESCALATION = "escalation", "Escalation"
        FEEDBACK = "feedback", "Feedback"
        DECISION = "decision", "Decision"
        APOLOGY = "apology", "Apology"
        ALIGNMENT = "alignment", "Alignment"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ANALYZED = "analyzed", "Analyzed"
        SENT = "sent", "Sent / Received"
        FEEDBACK_RECEIVED = "feedback_received", "Feedback received"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="sent_messages")
    receiver = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="received_messages")

    channel = models.CharField(max_length=30, choices=Channel.choices)
    intent = models.CharField(max_length=40, choices=Intent.choices)

    original_text = models.TextField()
    final_text = models.TextField(blank=True)
    overall_suggested_message = models.TextField(blank=True)
    subject_line = models.CharField(max_length=240, blank=True)
    slack_short_version = models.TextField(blank=True)
    teams_short_version = models.TextField(blank=True)

    scores_before = models.JSONField(default=dict, blank=True)
    estimated_scores_after_all = models.JSONField(default=dict, blank=True)
    current_scores = models.JSONField(default=dict, blank=True)

    accepted_suggestion_ids = models.JSONField(default=list, blank=True)
    rejected_suggestion_ids = models.JSONField(default=list, blank=True)

    risks = models.JSONField(default=list, blank=True)
    summary_of_changes = models.TextField(blank=True)
    explanation = models.TextField(blank=True)
    raw_llm_response = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=40, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.sender.name} → {self.receiver.name} ({self.intent})"

class MessageRevision(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="revisions")
    version_index = models.IntegerField()
    text = models.TextField()
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ("message", "version_index")
        ordering = ["version_index"]

    def __str__(self) -> str:
        return f"{self.message_id}:{self.version_index}"

class InlineSuggestion(models.Model):
    class Decision(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="suggestions")
    external_id = models.CharField(max_length=80, blank=True)

    target_text = models.TextField()
    start_index = models.IntegerField(null=True, blank=True)
    end_index = models.IntegerField(null=True, blank=True)

    issue = models.TextField(blank=True)
    suggested_replacement = models.TextField()
    reason = models.TextField(blank=True)
    affected_scores = models.JSONField(default=dict, blank=True)
    org_values_used = models.JSONField(default=list, blank=True)

    decision = models.CharField(max_length=20, choices=Decision.choices, default=Decision.PENDING)
    decided_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"{self.message_id}:{self.external_id or self.pk} {self.decision}"

class ReceiverFeedback(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="receiver_feedback")
    receiver = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="feedback_given")
    sender = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="feedback_received_from_receivers")

    clear = models.BooleanField(default=False)
    too_direct = models.BooleanField(default=False)
    too_soft = models.BooleanField(default=False)
    too_long = models.BooleanField(default=False)
    too_short = models.BooleanField(default=False)
    missed_context = models.BooleanField(default=False)
    too_much_context = models.BooleanField(default=False)
    unclear_ask = models.BooleanField(default=False)
    unclear_ownership = models.BooleanField(default=False)
    not_aligned_with_preferences = models.BooleanField(default=False)
    good_message = models.BooleanField(default=False)

    free_text = models.TextField(blank=True)
    prompt_update_summary = models.TextField(blank=True)
    receiver_prompt_before = models.TextField(blank=True)
    receiver_prompt_after = models.TextField(blank=True)

    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"Feedback from {self.receiver.name} on message {self.message_id}"


class SystemEvent(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.SET_NULL, null=True, blank=True, related_name="system_events")
    actor = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="actor_events")
    receiver = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="receiver_events")
    message = models.ForeignKey(Message, on_delete=models.SET_NULL, null=True, blank=True, related_name="system_events")

    event_type = models.CharField(max_length=120)
    source = models.CharField(max_length=80, default="app")
    status = models.CharField(max_length=40, default="success")
    payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.event_type} ({self.status})"
