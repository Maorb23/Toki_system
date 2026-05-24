from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.utils import timezone

from comms.models import FeedbackReminder, Message, Organization
from comms.services.event_log import log_event
from comms.services.webhooks import deliver_event_to_webhooks, delivery_summary


class Command(BaseCommand):
    help = "Create pending reminders for feedback-relevant messages without receiver feedback."

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, help="Limit reminders to one organization")
        parser.add_argument("--days", type=int, default=7, help="Only include messages older than this many days")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timezone.timedelta(days=options["days"])
        iso_year, iso_week, _ = timezone.localdate().isocalendar()
        orgs = Organization.objects.all().order_by("name")
        if options.get("organization_id"):
            orgs = orgs.filter(pk=options["organization_id"])

        created = 0
        duplicates = 0
        processed = 0
        webhook_deliveries = []
        for org in orgs:
            processed += 1
            messages = Message.objects.filter(
                organization=org,
                status__in=[Message.Status.ANALYZED, Message.Status.SENT],
                created_at__lte=cutoff,
                receiver_feedback__isnull=True,
            ).select_related("receiver")

            for message in messages:
                reminder_key = f"{org.id}:{message.id}:{iso_year}-W{iso_week:02d}"
                payload = {
                    "message_id": message.id,
                    "receiver_id": message.receiver_id,
                    "status": message.status,
                    "message_created_at": message.created_at.isoformat(),
                }
                try:
                    reminder, was_created = FeedbackReminder.objects.get_or_create(
                        reminder_key=reminder_key,
                        defaults={
                            "organization": org,
                            "message": message,
                            "receiver": message.receiver,
                            "status": FeedbackReminder.Status.PENDING,
                            "payload": payload,
                        },
                    )
                except IntegrityError:
                    duplicates += 1
                    continue

                if was_created:
                    created += 1
                    event = log_event(
                        "feedback.missing",
                        organization=org,
                        receiver=message.receiver,
                        message=message,
                        payload={"reminder_id": reminder.id, "reminder_key": reminder.reminder_key},
                    )
                    if event:
                        webhook_deliveries.extend(deliver_event_to_webhooks(event))
                else:
                    duplicates += 1

        self.stdout.write(f"reminders created: {created}")
        self.stdout.write(f"duplicates skipped: {duplicates}")
        self.stdout.write(f"organizations processed: {processed}")
        self.stdout.write(delivery_summary(webhook_deliveries))
