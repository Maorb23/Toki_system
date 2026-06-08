import importlib.util
import json
import os

from django.core.management.base import BaseCommand

from comms.services.weave_monitor import clear_weave_cache, _weave_module, trace_operation, weave_enabled, weave_project


class Command(BaseCommand):
    help = "Check optional W&B Weave monitoring configuration."

    def add_arguments(self, parser):
        parser.add_argument(
            "--send-test-trace",
            action="store_true",
            help="Initialize Weave and send a small sanitized test trace.",
        )

    def handle(self, *args, **options):
        weave_installed = importlib.util.find_spec("weave") is not None
        status = {
            "WEAVE_TRACING": os.getenv("WEAVE_TRACING", ""),
            "WEAVE_PROJECT": os.getenv("WEAVE_PROJECT", ""),
            "WANDB_PROJECT": os.getenv("WANDB_PROJECT", ""),
            "WANDB_API_KEY_present": bool(os.getenv("WANDB_API_KEY")),
            "enabled": weave_enabled(),
            "project": weave_project(),
            "weave_installed": weave_installed,
            "initialized": False,
            "test_trace_sent": False,
        }

        if options["send_test_trace"]:
            clear_weave_cache()

            def operation():
                return {"ok": True}

            result = trace_operation(
                "communication_agent.monitoring_status",
                {"source": "management_command"},
                operation,
                output=lambda value: value,
            )
            status["initialized"] = _weave_module() is not None
            status["test_trace_sent"] = result == {"ok": True} and status["initialized"]

        self.stdout.write(json.dumps(status, indent=2, sort_keys=True))
