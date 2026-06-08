import json
import statistics
import time

from django.core.management.base import BaseCommand, CommandError

from comms.models import Employee, InlineSuggestion, Message, MessageRevision, Organization, Team
from comms.services.communication_graph import CommunicationGraphRunner
from comms.services.score_engine import normalize_scores


SAMPLE_MESSAGES = [
    "Hi Tal,",
    "Thanks",
    "Can you send me the file?",
    "you didnt do what i asked fix it asap",
    "lets talk about q3 roadmap with dan",
    "Please remember that Dan prefers concise bullet points",
]


class Command(BaseCommand):
    help = "Benchmark old direct-analysis flow against the LangGraph communication-agent flow."

    def add_arguments(self, parser):
        parser.add_argument("--iterations", type=int, default=5)
        parser.add_argument("--latency-threshold-percent", type=float, default=50.0)
        parser.add_argument("--fail-on-regression", action="store_true")

    def handle(self, *args, **options):
        iterations = max(1, options["iterations"])
        threshold = max(0.0, options["latency_threshold_percent"])
        sender, receiver = _benchmark_participants()
        samples = SAMPLE_MESSAGES * iterations

        old_counter = {"llm_calls": 0}
        old_latencies = []
        old_legacy = _stub_legacy_analyzer(sender, receiver, old_counter)
        for sample in samples:
            start = time.perf_counter()
            old_legacy(sample, None)
            old_latencies.append(_elapsed_ms(start))

        new_counter = {"llm_calls": 0}
        new_latencies = []
        route_counts = {}
        tool_call_count = 0
        new_legacy = _stub_legacy_analyzer(sender, receiver, new_counter)
        for sample in samples:
            runner = CommunicationGraphRunner(
                sender=sender,
                receiver=receiver,
                channel=Message.Channel.SLACK,
                intent=Message.Intent.REQUEST,
                legacy_analyze=new_legacy,
            )
            start = time.perf_counter()
            state = runner.invoke(sample)
            new_latencies.append(_elapsed_ms(start))
            metadata = state.get("metadata") or {}
            route = metadata.get("route") or "unknown"
            route_counts[route] = route_counts.get(route, 0) + 1
            tool_call_count += len(metadata.get("tools_called") or [])

        old_avg = _avg(old_latencies)
        new_avg = _avg(new_latencies)
        regression_limit = old_avg * (1 + threshold / 100)
        warning = new_avg > regression_limit if old_avg else False

        metrics = {
            "old_avg_latency_ms": round(old_avg, 2),
            "new_avg_latency_ms": round(new_avg, 2),
            "old_p95_latency_ms": round(_p95(old_latencies), 2),
            "new_p95_latency_ms": round(_p95(new_latencies), 2),
            "llm_calls_per_100_requests": round((new_counter["llm_calls"] / len(samples)) * 100, 2),
            "tool_calls_per_100_requests": round((tool_call_count / len(samples)) * 100, 2),
            "bypass_rate": round((route_counts.get("bypass", 0) / len(samples)) * 100, 2),
            "route_distribution": route_counts,
            "old_llm_calls_per_100_requests": round((old_counter["llm_calls"] / len(samples)) * 100, 2),
            "latency_regression_warning": warning,
            "latency_threshold_percent": threshold,
        }

        if warning and options["fail_on_regression"]:
            raise CommandError(json.dumps(metrics, indent=2, sort_keys=True))

        self.stdout.write(json.dumps(metrics, indent=2, sort_keys=True))


def _benchmark_participants() -> tuple[Employee, Employee]:
    org, _ = Organization.objects.get_or_create(
        name="Communication Agent Benchmark",
        defaults={"description": "Synthetic benchmark organization."},
    )
    team, _ = Team.objects.get_or_create(
        organization=org,
        name="Benchmark Team",
        defaults={"description": "Benchmark team", "norms": ["Prefer clear asks."]},
    )
    sender, _ = Employee.objects.get_or_create(
        organization=org,
        name="Benchmark Sender",
        defaults={"team": team, "role": "PM"},
    )
    receiver, _ = Employee.objects.get_or_create(
        organization=org,
        name="Dan",
        defaults={
            "team": team,
            "role": "Engineer",
            "communication_preferences": {"style": "concise"},
            "receiver_prompt": "Dan prefers concise context and clear next steps.",
        },
    )
    if sender.team_id != team.id:
        sender.team = team
        sender.save(update_fields=["team"])
    if receiver.team_id != team.id:
        receiver.team = team
        receiver.save(update_fields=["team"])
    return sender, receiver


def _stub_legacy_analyzer(sender: Employee, receiver: Employee, counter: dict):
    def analyze(message_text: str, tool_results: dict | None = None) -> Message:
        counter["llm_calls"] += 1
        overall = _stub_rewrite(message_text)
        scores_before = normalize_scores({
            "clarity": 55,
            "tone": 55,
            "receiver_fit": 55,
            "org_values_alignment": 55,
        })
        scores_after = normalize_scores({
            "clarity": 75,
            "tone": 75,
            "receiver_fit": 75,
            "org_values_alignment": 75,
        })
        raw = {
            "benchmark_stub": True,
            "tool_results_used": bool(tool_results),
        }
        message = Message.objects.create(
            organization=sender.organization,
            sender=sender,
            receiver=receiver,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            original_text=message_text,
            final_text=message_text,
            overall_suggested_message=overall,
            scores_before=scores_before,
            estimated_scores_after_all=scores_after,
            current_scores=scores_before,
            summary_of_changes="Benchmark rewrite.",
            explanation="Synthetic benchmark response.",
            raw_llm_response=raw,
            status=Message.Status.ANALYZED,
        )
        MessageRevision.objects.create(
            message=message,
            version_index=1,
            text=message_text,
            note="Original draft",
        )
        if message_text and overall != message_text:
            InlineSuggestion.objects.create(
                message=message,
                target_text=message_text,
                start_index=0,
                end_index=len(message_text),
                issue="Benchmark improvement",
                suggested_replacement=overall,
                reason="Synthetic rewrite for benchmark measurement.",
                affected_scores={"clarity": 10, "tone": 10, "receiver_fit": 10, "org_values_alignment": 10},
            )
        return message

    return analyze


def _stub_rewrite(message_text: str) -> str:
    lowered = message_text.lower()
    if "roadmap" in lowered:
        return "Dan, could we discuss the Q3 roadmap and align on the next steps?"
    if "remember" in lowered or "prefers" in lowered:
        return "Noted: Dan prefers concise bullet points."
    if "fix" in lowered:
        return "Could you revisit what I asked and fix it as soon as you can?"
    return message_text


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _avg(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95))))
    return ordered[index]
