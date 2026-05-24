from django.core.management.base import BaseCommand
from django.utils import timezone

from comms.models import InlineSuggestion, Message, Organization, ReceiverFeedback, WeeklyCommunicationReport
from comms.services.automation_metrics import average_scores, low_score_areas
from comms.services.event_log import log_event
from comms.services.webhooks import deliver_event_to_webhooks, delivery_summary


class Command(BaseCommand):
    help = "Generate weekly communication reports from message metadata and scores."

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, help="Limit report generation to one organization")

    def handle(self, *args, **options):
        period_end = timezone.now()
        period_start = period_end - timezone.timedelta(days=7)
        orgs = Organization.objects.all().order_by("name")
        if options.get("organization_id"):
            orgs = orgs.filter(pk=options["organization_id"])

        created = 0
        for org in orgs:
            messages = Message.objects.filter(organization=org, created_at__gte=period_start, created_at__lt=period_end)
            message_ids = list(messages.values_list("id", flat=True))
            scores = average_scores(messages)
            metrics = {
                "message_count": messages.count(),
                "sent_message_count": messages.filter(status=Message.Status.SENT).count(),
                "feedback_count": ReceiverFeedback.objects.filter(
                    message__organization=org,
                    created_at__gte=period_start,
                    created_at__lt=period_end,
                ).count(),
                "missing_feedback_count": messages.filter(
                    status__in=[Message.Status.ANALYZED, Message.Status.SENT],
                    receiver_feedback__isnull=True,
                ).count(),
                "accepted_suggestion_count": InlineSuggestion.objects.filter(
                    message_id__in=message_ids,
                    decision=InlineSuggestion.Decision.ACCEPTED,
                ).count(),
                "rejected_suggestion_count": InlineSuggestion.objects.filter(
                    message_id__in=message_ids,
                    decision=InlineSuggestion.Decision.REJECTED,
                ).count(),
                "average_scores": scores,
                "low_score_areas": low_score_areas(scores),
            }
            summary = (
                f"{org.name}: {metrics['message_count']} messages, "
                f"{metrics['feedback_count']} feedback entries, "
                f"low score areas: {', '.join(metrics['low_score_areas']) or 'none'}."
            )
            report = WeeklyCommunicationReport.objects.create(
                organization=org,
                period_start=period_start,
                period_end=period_end,
                metrics=metrics,
                summary=summary,
            )
            event = log_event(
                "weekly_report.generated",
                organization=org,
                payload={"report_id": report.id, "period_start": period_start.isoformat(), "period_end": period_end.isoformat()},
            )
            if event:
                self.stdout.write(delivery_summary(deliver_event_to_webhooks(event)))
            created += 1
            self.stdout.write(summary)

        self.stdout.write(self.style.SUCCESS(f"Weekly reports created: {created}"))
