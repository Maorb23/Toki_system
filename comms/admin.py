from django.contrib import admin
from .models import Organization, OrgValue, Team, Employee, Message, InlineSuggestion, ReceiverFeedback, SystemEvent

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("id", "name")

@admin.register(OrgValue)
class OrgValueAdmin(admin.ModelAdmin):
    list_display = ("name", "organization")

@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "organization")

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "role", "team", "manager")
    search_fields = ("name", "email", "role")

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
