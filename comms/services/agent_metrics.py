from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean
from typing import Iterable

from django.db.models import QuerySet

from comms.models import Message


def collect_agent_metrics(messages: QuerySet[Message] | Iterable[Message] | None = None) -> dict:
    messages = messages if messages is not None else Message.objects.all()
    metadata_rows = [
        metadata
        for message in messages
        if (metadata := _agent_metadata(message))
    ]

    total = len(metadata_rows)
    route_counts = Counter(metadata.get("route") or "unknown" for metadata in metadata_rows)
    route_latencies: dict[str, list[float]] = defaultdict(list)
    node_latencies: dict[str, list[float]] = defaultdict(list)
    error_counts = Counter()
    tool_calls = 0
    llm_calls = 0
    validator_failures = 0

    for metadata in metadata_rows:
        route = metadata.get("route") or "unknown"
        total_latency = metadata.get("total_latency_ms")
        if isinstance(total_latency, (int, float)):
            route_latencies[route].append(float(total_latency))

        for node, latency in (metadata.get("latency_ms_by_node") or {}).items():
            if isinstance(latency, (int, float)):
                node_latencies[node].append(float(latency))

        if metadata.get("used_tools"):
            tool_calls += 1
        if metadata.get("used_llm"):
            llm_calls += 1
        if metadata.get("validator_passed") is False:
            validator_failures += 1

        for error in metadata.get("errors") or []:
            node = error.get("node") if isinstance(error, dict) else "unknown"
            error_counts[node or "unknown"] += 1

    return {
        "message_count": total,
        "route_distribution": dict(route_counts),
        "average_latency_by_route": {
            route: round(mean(values), 2)
            for route, values in route_latencies.items()
            if values
        },
        "p95_latency_by_route": {
            route: round(_p95(values), 2)
            for route, values in route_latencies.items()
            if values
        },
        "average_latency_by_node": {
            node: round(mean(values), 2)
            for node, values in node_latencies.items()
            if values
        },
        "tool_call_rate": _rate(tool_calls, total),
        "llm_call_rate": _rate(llm_calls, total),
        "bypass_rate": _route_rate(route_counts, "bypass", total),
        "rewrite_rate": _route_rate(route_counts, "rewrite", total),
        "rewrite_with_context_rate": _route_rate(route_counts, "rewrite_with_context", total),
        "validator_failure_rate": _rate(validator_failures, total),
        "error_rate_by_node": {
            node: _rate(count, total)
            for node, count in error_counts.items()
        },
    }


def _agent_metadata(message: Message) -> dict:
    raw = message.raw_llm_response or {}
    metadata = raw.get("agent_metadata") if isinstance(raw, dict) else None
    return metadata if isinstance(metadata, dict) else {}


def _rate(count: int, total: int) -> float:
    return round((count / total) * 100, 2) if total else 0.0


def _route_rate(route_counts: Counter, route: str, total: int) -> float:
    return _rate(route_counts.get(route, 0), total)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95))))
    return ordered[index]
