from django.core.management.base import BaseCommand

from comms.models import Employee, OrgValue, Organization, Team


def get_or_create_demo_employee(org, *, email, name, defaults):
    employee = Employee.objects.filter(organization=org, email__iexact=email).first()
    if not employee:
        employee = Employee.objects.filter(organization=org, name=name).first()
    if employee:
        return employee, False
    return Employee.objects.create(organization=org, email=email, name=name, **defaults), True


class Command(BaseCommand):
    help = "Seed an idempotent organization and users for the Gmail Apps Script demo."

    def handle(self, *args, **options):
        org, _ = Organization.objects.get_or_create(
            name="Acme Demo Org",
            defaults={"description": "A small demo organization for Gmail add-on testing."},
        )
        if not org.description:
            org.description = "A small demo organization for Gmail add-on testing."
            org.save(update_fields=["description"])

        for name, description in [
            ("Clarity", "Write messages that make the ask and context easy to understand."),
            ("Ownership", "Make owners, next steps, and expectations explicit."),
            ("Respectful urgency", "Communicate urgency without blame or unnecessary pressure."),
        ]:
            value, _ = OrgValue.objects.get_or_create(organization=org, name=name)
            if value.description != description:
                value.description = description
                value.save(update_fields=["description"])

        team, _ = Team.objects.get_or_create(
            organization=org,
            name="Demo Team",
            defaults={"description": "Team used for Gmail integration demos."},
        )

        sender, _ = get_or_create_demo_employee(
            org,
            email="sender@acme.test",
            name="Gmail Demo Sender",
            defaults={
                "role": "Product Manager",
                "team": team,
                "seniority_level": "IC",
            },
        )
        sender.name = "Gmail Demo Sender"
        sender.email = "sender@acme.test"
        sender.role = "Product Manager"
        sender.team = team
        sender.seniority_level = "IC"
        sender.save(update_fields=["name", "email", "role", "team", "seniority_level", "updated_at"])

        receiver, _ = get_or_create_demo_employee(
            org,
            email="receiver@acme.test",
            name="Dana Receiver",
            defaults={
                "role": "Engineering Lead",
                "team": team,
                "seniority_level": "Senior IC",
                "receiver_prompt": (
                    "Dana prefers clear, respectful messages with enough context to act. "
                    "Make the ask explicit, name the desired next step, and explain urgency without pressure."
                ),
                "communication_preferences": {
                    "style": "direct but respectful",
                    "structure": "context, ask, next step",
                },
                "pain_points": ["vague urgency", "unclear ownership"],
            },
        )
        receiver.name = "Dana Receiver"
        receiver.email = "receiver@acme.test"
        receiver.role = "Engineering Lead"
        receiver.team = team
        receiver.seniority_level = "Senior IC"
        if not receiver.receiver_prompt:
            receiver.receiver_prompt = (
                "Dana prefers clear, respectful messages with enough context to act. "
                "Make the ask explicit, name the desired next step, and explain urgency without pressure."
            )
        receiver.communication_preferences = receiver.communication_preferences or {
            "style": "direct but respectful",
            "structure": "context, ask, next step",
        }
        receiver.pain_points = receiver.pain_points or ["vague urgency", "unclear ownership"]
        receiver.save(update_fields=[
            "name",
            "email",
            "role",
            "team",
            "seniority_level",
            "receiver_prompt",
            "communication_preferences",
            "pain_points",
            "updated_at",
        ])

        self.stdout.write(self.style.SUCCESS(
            f"Seeded Gmail demo org id={org.id}; sender=sender@acme.test receiver=receiver@acme.test"
        ))
