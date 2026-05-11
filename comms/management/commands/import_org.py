import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from comms.models import Organization, OrgValue, Team, Employee

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

        self.stdout.write(self.style.SUCCESS(f"Imported organization: {org.name}"))
