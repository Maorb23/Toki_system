from django.contrib import admin
from .models import (
    Employee,
    FeedbackReminder,
    InlineSuggestion,
    MeetingContext,
    Message,
    OrgValue,
    OrgValuesDriftCheck,
    OrganizationContext,
    Organization,
    ProjectContext,
    ReceiverFeedback,
    ReceiverProfileRefreshProposal,
    SystemEvent,
    Team,
    WebhookDelivery,
    WebhookSubscription,
    WeeklyCommunicationReport,
)
from comms.services.profile_refresh import approve_profile_refresh_proposal, reject_profile_refresh_proposal

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("id", "name")

@admin.register(OrgValue)
class OrgValueAdmin(admin.ModelAdmin):
    list_display = ("name", "organization")

@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "organization")

@admin.register(OrganizationContext)
class OrganizationContextAdmin(admin.ModelAdmin):
    list_display = ("organization", "updated_at")
    search_fields = ("organization__name",)

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "role", "team", "manager")
    search_fields = ("name", "email", "role")

@admin.register(ProjectContext)
class ProjectContextAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "status", "priority", "quarter", "team", "owner")
    list_filter = ("organization", "status", "priority", "team")
    search_fields = ("name", "description", "owner__name")

@admin.register(MeetingContext)
class MeetingContextAdmin(admin.ModelAdmin):
    list_display = ("title", "organization", "meeting_type", "cadence", "status", "team", "owner")
    list_filter = ("organization", "status", "meeting_type", "team")
    search_fields = ("title", "summary", "owner__name")

class InlineSuggestionInline(admin.TabularInline):
    model = InlineSuggestion
    extra = 0

@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "sender", "receiver", "channel", "intent", "status", "created_at")
    list_filter = ("channel", "intent", "status")
    inlines = [InlineSuggestionInline]

@admin.register(ReceiverFeedback)
class ReceiverFeedbackAdmin(admin.ModelAdmin):
    list_display = ("id", "message", "receiver", "sender", "created_at")

@admin.register(SystemEvent)
class SystemEventAdmin(admin.ModelAdmin):
    list_display = ("id", "event_type", "organization", "actor", "receiver", "message", "source", "status", "created_at")
    list_filter = ("event_type", "source", "status", "created_at")
    search_fields = ("event_type", "error_message")

@admin.register(WeeklyCommunicationReport)
class WeeklyCommunicationReportAdmin(admin.ModelAdmin):
    list_display = ("id", "organization", "period_start", "period_end", "created_at")
    list_filter = ("organization", "period_start", "period_end", "created_at")

@admin.register(OrgValuesDriftCheck)
class OrgValuesDriftCheckAdmin(admin.ModelAdmin):
    list_display = ("id", "organization", "period_start", "period_end", "created_at")
    list_filter = ("organization", "period_start", "period_end", "created_at")

@admin.register(FeedbackReminder)
class FeedbackReminderAdmin(admin.ModelAdmin):
    list_display = ("id", "organization", "message", "receiver", "status", "reminder_key", "created_at", "sent_at")
    list_filter = ("organization", "status", "created_at", "sent_at")
    search_fields = ("reminder_key", "receiver__name", "receiver__email")

@admin.register(WebhookSubscription)
class WebhookSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "organization", "name", "target_url", "event_types", "is_active", "created_at", "updated_at")
    list_filter = ("organization", "is_active", "created_at", "updated_at")
    search_fields = ("name", "target_url")

@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ("id", "subscription", "event", "status", "response_status_code", "error_message", "created_at")
    list_filter = ("status", "response_status_code", "created_at", "subscription__organization")
    search_fields = ("subscription__name", "error_message", "response_body")

@admin.action(description="Approve selected pending profile refresh proposals")
def approve_profile_refresh_proposals(modeladmin, request, queryset):
    approved = 0
    skipped = 0
    for proposal in queryset:
        if proposal.status != ReceiverProfileRefreshProposal.Status.PENDING:
            skipped += 1
            continue
        approve_profile_refresh_proposal(proposal)
        approved += 1
    modeladmin.message_user(request, f"Approved {approved} proposal(s); skipped {skipped}.")

@admin.action(description="Reject selected pending profile refresh proposals")
def reject_profile_refresh_proposals(modeladmin, request, queryset):
    rejected = 0
    skipped = 0
    for proposal in queryset:
        if proposal.status != ReceiverProfileRefreshProposal.Status.PENDING:
            skipped += 1
            continue
        reject_profile_refresh_proposal(proposal)
        rejected += 1
    modeladmin.message_user(request, f"Rejected {rejected} proposal(s); skipped {skipped}.")

@admin.register(ReceiverProfileRefreshProposal)
class ReceiverProfileRefreshProposalAdmin(admin.ModelAdmin):
    list_display = ("id", "organization", "receiver", "status", "created_at", "reviewed_at")
    list_filter = ("organization", "status", "created_at")
    search_fields = ("receiver__name", "receiver__email", "explanation")
    actions = [approve_profile_refresh_proposals, reject_profile_refresh_proposals]
