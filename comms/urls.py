from django.urls import path
from . import views
from . import api

app_name = "comms"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("org-graph/", views.org_graph, name="org_graph"),
    path("employees/<int:employee_id>/", views.employee_detail, name="employee_detail"),
    path("workspace/", views.workspace, name="workspace"),
    path("messages/<int:message_id>/", views.message_detail, name="message_detail"),
    path("messages/<int:message_id>/mark-sent/", views.mark_message_sent, name="mark_message_sent"),
    path("messages/<int:message_id>/receiver-feedback/", views.receiver_feedback, name="receiver_feedback"),
    path("api/messages/<int:message_id>/suggestions/<int:suggestion_id>/decision/", views.suggestion_decision, name="suggestion_decision"),
    path("api/messages/<int:message_id>/suggestions/bulk-decision/", views.bulk_suggestion_decision, name="bulk_suggestion_decision"),

    path("api/v1/orgs/", api.api_list_orgs, name="api_list_orgs"),
    path("api/v1/orgs/<int:org_id>/teams/", api.api_list_teams, name="api_list_teams"),
    path("api/v1/orgs/<int:org_id>/employees/", api.api_list_employees, name="api_list_employees"),
    path("api/v1/employees/<int:employee_id>/", api.api_employee_detail, name="api_employee_detail"),
    path("api/v1/messages/analyze/", api.api_analyze_message, name="api_analyze_message"),
    path("api/v1/messages/<int:message_id>/", api.api_message_detail, name="api_message_detail"),
    path("api/v1/messages/<int:message_id>/suggestions/<int:suggestion_id>/decision/", api.api_suggestion_decision, name="api_suggestion_decision"),
    path("api/v1/messages/<int:message_id>/suggestions/bulk-decision/", api.api_bulk_suggestion_decision, name="api_bulk_suggestion_decision"),
    path("api/v1/messages/<int:message_id>/receiver-feedback/", api.api_receiver_feedback, name="api_receiver_feedback"),
]
