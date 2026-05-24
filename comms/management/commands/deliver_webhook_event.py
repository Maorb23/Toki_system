from django.core.management.base import BaseCommand

from comms.models import SystemEvent
from comms.services.webhooks import deliver_event_to_webhooks


class Command(BaseCommand):
    help = "Manually deliver one SystemEvent to matching webhook subscriptions."

    def add_arguments(self, parser):
        parser.add_argument("--event-id", type=int, required=True, help="SystemEvent ID to deliver")

    def handle(self, *args, **options):
        event = SystemEvent.objects.get(pk=options["event_id"])
        deliveries = deliver_event_to_webhooks(event)
        if not deliveries:
            self.stdout.write("No matching webhook subscriptions.")
            return

        for delivery in deliveries:
            self.stdout.write(
                f"subscription={delivery.subscription_id} status={delivery.status} "
                f"response_status_code={delivery.response_status_code or ''} "
                f"error={delivery.error_message}"
            )
