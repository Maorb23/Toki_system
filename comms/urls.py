from django.urls import path
from . import views
from . import api

app_name = "comms"

urlpatterns = [
    path("", views.mode_select, name="mode_select"),
    path("orgs/<int:org_id>/select/", views.select_org, name="select_org"),
    path("org/", views.dashboard, name="dashboard"),
    path("org-graph/", views.org_graph, name="org_graph"),
    path("employees/<int:employee_id>/", views.employee_detail, name="employee_detail"),
    path("workspace/", views.workspace, name="workspace"),
    path("employee/", views.employee_home, name="employee_home"),
    path("employee/sign-in/<int:employee_id>/", views.employee_sign_in, name="employee_sign_in"),
    path("employee/sign-out/", views.employee_sign_out, name="employee_sign_out"),
    path("employee/workspace/", views.employee_workspace, name="employee_workspace"),
    path("integrations/gmail/demo/", views.gmail_demo, name="gmail_demo"),
    path("messages/<int:message_id>/", views.message_detail, name="message_detail"),
    path("messages/<int:message_id>/mark-sent/", views.mark_message_sent, name="mark_message_sent"),
    path("messages/<int:message_id>/receiver-feedback/", views.receiver_feedback, name="receiver_feedback"),
    path("api/messages/<int:message_id>/suggestions/<int:suggestion_id>/decision/", views.suggestion_decision, name="suggestion_decision"),
    path("api/messages/<int:message_id>/suggestions/bulk-decision/", views.bulk_suggestion_decision, name="bulk_suggestion_decision"),
    path("api/orgs/<int:org_id>/inline-suggestions/preview/", api.api_inline_suggestions_preview, name="api_inline_suggestions_preview"),
    path("api/integrations/gmail/inline-suggestions/preview/", api.api_gmail_inline_suggestions_preview, name="api_gmail_inline_suggestions_preview"),

    path("api/v1/orgs/", api.api_list_orgs, name="api_list_orgs"),
    path("api/v1/orgs/<int:org_id>/teams/", api.api_list_teams, name="api_list_teams"),
    path("api/v1/orgs/<int:org_id>/employees/", api.api_list_employees, name="api_list_employees"),
    path("api/v1/employees/<int:employee_id>/", api.api_employee_detail, name="api_employee_detail"),
    path("api/v1/messages/analyze/", api.api_analyze_message, name="api_analyze_message"),
    path("api/v1/messages/<int:message_id>/", api.api_message_detail, name="api_message_detail"),
    path("api/v1/messages/<int:message_id>/suggestions/<int:suggestion_id>/decision/", api.api_suggestion_decision, name="api_suggestion_decision"),
    path("api/v1/messages/<int:message_id>/suggestions/bulk-decision/", api.api_bulk_suggestion_decision, name="api_bulk_suggestion_decision"),
    path("api/v1/messages/<int:message_id>/receiver-feedback/", api.api_receiver_feedback, name="api_receiver_feedback"),
    path("api/v1/integrations/gmail/health/", api.api_gmail_health, name="api_gmail_health"),
    path("api/v1/integrations/gmail/analyze-draft/", api.api_gmail_analyze_draft, name="api_gmail_analyze_draft"),
    path("api/v1/integrations/gmail/inline-suggestions/preview/", api.api_gmail_inline_suggestions_preview_v1, name="api_gmail_inline_suggestions_preview_v1"),
    path("api/v1/integrations/outlook/health/", api.api_outlook_health, name="api_outlook_health"),
    path("api/v1/integrations/outlook/analyze-draft/", api.api_outlook_analyze_draft, name="api_outlook_analyze_draft"),
    path("api/v1/integrations/outlook/inline-suggestions/preview/", api.api_outlook_inline_suggestions_preview, name="api_outlook_inline_suggestions_preview"),
    path("api/v1/integrations/outlook/events/", api.api_outlook_event, name="api_outlook_event"),
]
