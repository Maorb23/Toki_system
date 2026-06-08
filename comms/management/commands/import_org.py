import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from comms.models import (
    Employee,
    MeetingContext,
    OrgValue,
    Organization,
    OrganizationContext,
    ProjectContext,
    Team,
)

class Command(BaseCommand):
    help = "Import an organization from a JSON config file."

    def add_arguments(self, parser):
        parser.add_argument("path", type=str, help="Path to org_config.json")

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["path"]).expanduser()
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON: {exc}") from exc

        org_data = payload.get("organization") or {}
        org_name = org_data.get("name")
        if not org_name:
            raise CommandError("organization.name is required")

        org, _ = Organization.objects.get_or_create(name=org_name)
        org.description = org_data.get("description", "")
        org.save(update_fields=["description"])

        values = payload.get("values") or []
        for value in values:
            name = value.get("name")
            if not name:
                continue
            org_value, _ = OrgValue.objects.get_or_create(organization=org, name=name)
            org_value.description = value.get("description", "")
            org_value.save(update_fields=["description"])

        teams = {}
        for team in payload.get("teams") or []:
            name = team.get("name")
            if not name:
                continue
            obj, _ = Team.objects.get_or_create(organization=org, name=name)
            obj.description = team.get("description", "")
            obj.norms = team.get("norms", [])
            obj.save(update_fields=["description", "norms"])
            teams[name] = obj

        employees_by_name = {}
        employees_by_id = {}
        for item in payload.get("employees") or []:
            name = item.get("name")
            role = item.get("role")
            if not name or not role:
                continue

            team_name = item.get("team") or ""
            team = teams.get(team_name)
            if not team and team_name:
                team, _ = Team.objects.get_or_create(organization=org, name=team_name)
                teams[team_name] = team

            employee, _ = Employee.objects.get_or_create(organization=org, name=name)
            employee.team = team
            employee.role = role
            employee.seniority_level = item.get("seniority_level", "")
            employee.communication_preferences = item.get("communication_preferences", {})
            employee.pain_points = item.get("pain_points", [])
            employee.receiver_prompt = item.get("receiver_prompt", "")
            employee.save()
            employees_by_name[name] = employee
            employees_by_id[employee.id] = employee

        for item in payload.get("employees") or []:
            name = item.get("name")
            if not name or name not in employees_by_name:
                continue
            manager_name = item.get("manager") or item.get("manager_name")
            manager_id = item.get("manager_id")
            manager = None

            if manager_name:
                manager = employees_by_name.get(manager_name)
            elif isinstance(manager_id, int):
                manager = employees_by_id.get(manager_id)

            employee = employees_by_name[name]
            employee.manager = manager
            employee.save(update_fields=["manager"])

        context = payload.get("context") or payload.get("organization_context") or {}
        if context:
            org_context, _ = OrganizationContext.objects.get_or_create(organization=org)
            org_context.operating_context = context.get("operating_context", {})
            org_context.current_priorities = context.get("current_priorities", [])
            org_context.communication_patterns = context.get("communication_patterns", [])
            org_context.customer_segments = context.get("customer_segments", [])
            org_context.known_constraints = context.get("known_constraints", [])
            org_context.save()

        for item in payload.get("projects") or []:
            name = item.get("name")
            if not name:
                continue
            project, _ = ProjectContext.objects.get_or_create(organization=org, name=name)
            project.description = item.get("description", "")
            project.status = item.get("status", ProjectContext.Status.ACTIVE)
            project.priority = item.get("priority", "")
            project.quarter = item.get("quarter", "")
            project.team = teams.get(item.get("team") or "")
            project.owner = employees_by_name.get(item.get("owner") or item.get("owner_name") or "")
            project.goals = item.get("goals", [])
            project.risks = item.get("risks", [])
            project.dependencies = item.get("dependencies", [])
            project.stakeholders = item.get("stakeholders", [])
            project.save()

        for item in payload.get("meetings") or payload.get("meeting_contexts") or []:
            title = item.get("title")
            if not title:
                continue
            meeting, _ = MeetingContext.objects.get_or_create(organization=org, title=title)
            meeting.meeting_type = item.get("meeting_type", "")
            meeting.cadence = item.get("cadence", "")
            meeting.status = item.get("status", MeetingContext.Status.RECURRING)
            meeting.team = teams.get(item.get("team") or "")
            meeting.owner = employees_by_name.get(item.get("owner") or item.get("owner_name") or "")
            meeting.participants = item.get("participants", [])
            meeting.related_projects = item.get("related_projects", [])
            meeting.summary = item.get("summary", "")
            meeting.decisions = item.get("decisions", [])
            meeting.open_questions = item.get("open_questions", [])
            meeting.action_items = item.get("action_items", [])
            meeting.save()

        self.stdout.write(self.style.SUCCESS(f"Imported organization: {org.name}"))
