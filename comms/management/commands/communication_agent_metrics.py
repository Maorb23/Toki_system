import json

from django.core.management.base import BaseCommand

from comms.models import Message
from comms.services.agent_metrics import collect_agent_metrics


class Command(BaseCommand):
    help = "Print aggregate LangGraph communication-agent metrics."

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int)
        parser.add_argument("--limit", type=int, default=500)

    def handle(self, *args, **options):
        messages = Message.objects.order_by("-created_at")
        if options.get("organization_id"):
            messages = messages.filter(organization_id=options["organization_id"])
        if options.get("limit"):
            messages = messages[: options["limit"]]

        self.stdout.write(json.dumps(collect_agent_metrics(messages), indent=2, sort_keys=True))
