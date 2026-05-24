import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from comms.models import Employee, Organization, Team
from comms.services.onboarding import ensure_employee_onboarding


class Command(BaseCommand):
    help = "Import employees from CSV into an organization."

    def add_arguments(self, parser):
        parser.add_argument("path", type=str, help="Path to employees.csv")
        parser.add_argument("--organization-id", type=int, required=True, help="Organization ID")

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["path"]).expanduser()
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        org = Organization.objects.get(pk=options["organization_id"])
        created = 0
        updated = 0
        skipped = 0
        imported_by_email = {}
        pending_managers = []

        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                name = (row.get("name") or "").strip()
                email = (row.get("email") or "").strip().lower()
                role = (row.get("role") or "").strip()
                team_name = (row.get("team") or "").strip()
                manager_email = (row.get("manager_email") or "").strip().lower()
                seniority_level = (row.get("seniority_level") or "").strip()

                if not name or not role:
                    skipped += 1
                    continue

                team = None
                if team_name:
                    team, _ = Team.objects.get_or_create(organization=org, name=team_name)

                employee = None
                was_created = False
                if email:
                    employee = Employee.objects.filter(organization=org, email__iexact=email).first()
                if employee is None:
                    employee = Employee.objects.filter(organization=org, name=name).first()

                if employee is None:
                    employee = Employee.objects.create(
                        organization=org,
                        name=name,
                        email=email,
                        role=role,
                        team=team,
                        seniority_level=seniority_level,
                    )
                    was_created = True
                    created += 1
                else:
                    employee.name = name
                    employee.email = email or employee.email
                    employee.role = role
                    employee.team = team
                    employee.seniority_level = seniority_level
                    employee.save(update_fields=["name", "email", "role", "team", "seniority_level", "updated_at"])
                    updated += 1

                ensure_employee_onboarding(employee)
                if employee.email:
                    imported_by_email[employee.email.lower()] = employee
                if manager_email:
                    pending_managers.append((employee, manager_email))

        for employee, manager_email in pending_managers:
            manager = imported_by_email.get(manager_email) or Employee.objects.filter(
                organization=org,
                email__iexact=manager_email,
            ).first()
            if manager and employee.manager_id != manager.id:
                employee.manager = manager
                employee.save(update_fields=["manager"])

        self.stdout.write(f"created: {created}")
        self.stdout.write(f"updated: {updated}")
        self.stdout.write(f"skipped: {skipped}")
