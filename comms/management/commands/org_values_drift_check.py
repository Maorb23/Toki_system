from django.core.management.base import BaseCommand
from django.utils import timezone

from comms.models import Message, OrgValuesDriftCheck, Organization
from comms.services.automation_metrics import average_scores
from comms.services.event_log import log_event
from comms.services.webhooks import deliver_event_to_webhooks, delivery_summary


class Command(BaseCommand):
    help = "Check recent communication scores for org values drift signals."

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, help="Limit drift checks to one organization")

    def handle(self, *args, **options):
        period_end = timezone.now()
        period_start = period_end - timezone.timedelta(days=30)
        orgs = Organization.objects.all().order_by("name")
        if options.get("organization_id"):
            orgs = orgs.filter(pk=options["organization_id"])

        created = 0
        for org in orgs:
            messages = Message.objects.filter(organization=org, created_at__gte=period_start, created_at__lt=period_end)
            scores = average_scores(messages)
            warnings = []
            org_values = list(org.values.values_list("name", flat=True).order_by("name"))
            if scores.get("org_values_alignment", 0) and scores["org_values_alignment"] < 70:
                warnings.append({
                    "type": "low_org_values_alignment",
                    "score": scores["org_values_alignment"],
                    "threshold": 70,
                    "org_values": org_values,
                })

            metrics = {
                "message_count": messages.count(),
                "average_scores": scores,
                "org_values": org_values,
            }
            summary = (
                f"{org.name}: org values alignment average {scores.get('org_values_alignment', 0)}; "
                f"warnings: {len(warnings)}."
            )
            check = OrgValuesDriftCheck.objects.create(
                organization=org,
                period_start=period_start,
                period_end=period_end,
                metrics=metrics,
                warnings=warnings,
                summary=summary,
            )
            event = log_event(
                "org_values_drift.checked",
                organization=org,
                payload={"check_id": check.id, "warning_count": len(warnings)},
            )
            if event:
                self.stdout.write(delivery_summary(deliver_event_to_webhooks(event)))
            created += 1
            self.stdout.write(summary)

        self.stdout.write(self.style.SUCCESS(f"Org values drift checks created: {created}"))
