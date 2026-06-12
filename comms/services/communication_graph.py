from __future__ import annotations

import base64
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Literal, TypedDict

from django.conf import settings
from django.db import transaction

from comms.models import Employee, Message, MessageRevision
from comms.services.context_tools import CommunicationContextTools
from comms.services.event_log import log_event
from comms.services.score_engine import normalize_scores
from comms.services.weave_monitor import (
    execution_summary_trace_inputs,
    execution_summary_trace_output,
    graph_trace_inputs,
    graph_trace_output,
    node_trace_inputs,
    node_trace_output,
    tool_trace_inputs,
    tool_trace_output,
    trace_operation,
    weave_enabled,
    weave_project,
)

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - exercised only when LangGraph is unavailable locally.
    END = "__end__"
    START = "__start__"
    StateGraph = None


Route = Literal["bypass", "validate_only", "rewrite", "rewrite_with_context"]


class CommunicationState(TypedDict, total=False):
    original_message: str
    normalized_message: str
    receiver: str | None
    sender: str | None
    company_id: str | int | None

    route: Route | None
    typo_corrected_message: str | None
    needs_typo_cleanup: bool

    needs_context: bool
    selected_tools: list[str]
    tool_results: dict[str, Any] | None

    final_message: str
    metadata: dict[str, Any]

    message_id: int | None


LegacyAnalyzeCallable = Callable[[str, dict[str, Any] | None], Message]


BYPASS_MESSAGES = {
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank you",
    "ok",
    "okay",
    "got it",
    "best",
    "best regards",
    "regards",
    "sounds good",
    "looks good",
}

TONE_REWRITE_PATTERNS = [
    r"\basap\b",
    r"\bfix it\b",
    r"\bfix this\b",
    r"\byou did(?:n'?t| not)\b",
    r"\bwhat i asked\b",
    r"\bnot acceptable\b",
    r"\bwrong\b",
    r"\bfailed\b",
    r"\bnow\b",
]

WRITE_INTENT_PATTERN = re.compile(
    r"\b(remember|save|store|update|learn|note)\b.*\b(prefers?|preference|pattern|likes?|wants?)\b",
    re.IGNORECASE,
)

PROJECT_LOOKUP_PATTERN = re.compile(
    r"\b(project|prokject|projerc|initiative|workstream|roadmap|sprint|milestone|q[1-4])\b",
    re.IGNORECASE,
)
MEETING_LOOKUP_PATTERN = re.compile(
    r"\b(meeting|sync|schedule|calendar|talk|call|roadmap review|review)\b|\bq[1-4]\b",
    re.IGNORECASE,
)
COMPANY_LOOKUP_PATTERN = re.compile(
    r"\b(company|org|organization|team|values?|policy|priorit(?:y|ies)|standard|customer needs?)\b",
    re.IGNORECASE,
)
RECEIVER_LOOKUP_PATTERN = re.compile(
    r"\b(receiver|recipient|dana|dan|their style|prefers?|preference)\b",
    re.IGNORECASE,
)
ENTITY_NAME_PATTERN = re.compile(
    r"\b[A-Z][a-zA-Z]{3,}\s+[A-Z][a-zA-Z]{3,}(?:\s+[A-Z][a-zA-Z]{3,})?\b"
)
ENTITY_CONTEXT_ASK_PATTERN = re.compile(
    r"\b(what(?:'s| is)?|next|phase|status|update|timeline|when|complete|deadline|fix|improve|implement|meeting|review|about)\b|\?",
    re.IGNORECASE,
)

COMMON_TYPO_REPLACEMENTS = {
    "reciever": "receiver",
    "recievers": "receivers",
    "organiztation": "organization",
    "organiztations": "organizations",
    "wsnt": "want",
    "didnt": "didn't",
    "dont": "don't",
    "cant": "can't",
    "wont": "won't",
    "isnt": "isn't",
    "arent": "aren't",
    "lets": "let's",
    "projerc": "project",
    "prokject": "project",
    "projec": "project",
}

MERMAID_GRAPH = """graph TD
    START((START)) --> input_normalizer
    input_normalizer --> message_router
    message_router --> route_decision
    route_decision -- bypass --> final_response
    route_decision -- validate_only --> typo_cleanup
    route_decision -- "rewrite + typos" --> typo_cleanup
    route_decision -- "rewrite_with_context + typos" --> typo_cleanup
    typo_cleanup -- validate_only --> final_response
    typo_cleanup -- rewrite --> communication_agent
    typo_cleanup -- rewrite_with_context --> tool_selector
    route_decision -- rewrite --> communication_agent
    communication_agent --> final_validator
    final_validator --> final_response
    route_decision -- rewrite_with_context --> tool_selector
    tool_selector --> context_tools
    context_tools --> communication_agent
    final_response --> END((END))
"""

FALLBACK_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lZ3ArgAAAABJRU5ErkJggg=="
)

_VISUALIZATION_EXPORTED = False


def update_node_metadata(
    state: dict[str, Any],
    node_name: str,
    start_time: float,
    error: str | None = None,
) -> dict[str, Any]:
    metadata = dict(state.get("metadata") or {})
    metadata.setdefault("nodes_executed", [])
    metadata.setdefault("latency_ms_by_node", {})
    metadata.setdefault("errors", [])
    metadata.setdefault("tools_called", [])
    metadata.setdefault("used_llm", False)
    metadata.setdefault("used_tools", False)

    metadata["nodes_executed"].append(node_name)
    metadata["latency_ms_by_node"][node_name] = round(
        (time.perf_counter() - start_time) * 1000,
        2,
    )

    if error:
        metadata["errors"].append({"node": node_name, "error": error})

    return metadata


def cleanup_typos(text: str) -> str:
    cleaned = normalize_spacing(text)

    for typo, replacement in COMMON_TYPO_REPLACEMENTS.items():
        cleaned = re.sub(
            rf"\b{re.escape(typo)}\b",
            lambda match: _preserve_case(match.group(0), replacement),
            cleaned,
            flags=re.IGNORECASE,
        )

    return cleaned.strip()


def normalize_message(text: str) -> str:
    return normalize_spacing(text)


def normalize_spacing(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    normalized = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in normalized.split("\n"))
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = re.sub(r"\b([A-Za-z])\s+([A-Za-z])\b", _join_known_split_word, normalized)
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    normalized = re.sub(r"([,.;:!?])(?=[A-Za-z])", r"\1 ", normalized)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    return normalized


def needs_typo_cleanup(text: str) -> bool:
    normalized = normalize_spacing(text)
    return cleanup_typos(normalized) != normalized


def route_message(message: str) -> Route:
    text = (message or "").strip()
    lowered = text.lower().strip()
    lowered_no_trailing = lowered.rstrip(".,!?:;")
    word_count = len(re.findall(r"\b\w+\b", lowered))

    if not text:
        return "bypass"

    if lowered_no_trailing in BYPASS_MESSAGES:
        return "bypass"

    if word_count <= 4 and re.match(r"^(hi|hello|hey)\b", lowered):
        return "bypass"

    if WRITE_INTENT_PATTERN.search(text):
        return "rewrite_with_context"

    if _has_context_signal(text):
        return "rewrite_with_context"

    if any(re.search(pattern, lowered) for pattern in TONE_REWRITE_PATTERNS):
        return "rewrite"

    if word_count <= 12 and text.endswith("?"):
        return "validate_only"

    if word_count <= 8:
        return "validate_only"

    return "rewrite"


def validate_final_message(original_message: str, final_message: str, route: str | None) -> list[str]:
    issues: list[str] = []
    original = (original_message or "").strip()
    final = (final_message or "").strip()

    if not final:
        issues.append("Final message is empty.")

    if route in {"rewrite", "rewrite_with_context"} and original and final:
        original_tokens = _meaningful_tokens(original)
        final_tokens = _meaningful_tokens(final)
        if original_tokens and final_tokens:
            overlap = original_tokens.intersection(final_tokens)
            if len(overlap) / max(len(original_tokens), 1) < 0.2:
                issues.append("Final message may not preserve the original intent.")

    if route in {"rewrite", "rewrite_with_context"} and final:
        unsupported_markers = ["according to company policy", "per the qbr", "approved by leadership"]
        lowered = final.lower()
        for marker in unsupported_markers:
            if marker in lowered and marker not in original.lower():
                issues.append("Final message may add unsupported company facts.")
                break

    return issues


def export_graph_visualization(compiled_graph: Any | None = None, *, output_dir: str | Path | None = None) -> dict[str, str]:
    base_dir = Path(output_dir or getattr(settings, "BASE_DIR", Path.cwd()) / "docs")
    base_dir.mkdir(parents=True, exist_ok=True)
    png_path = base_dir / "communication_agent_graph.png"
    mermaid_path = base_dir / "communication_agent_graph.mmd"
    mermaid_path.write_text(MERMAID_GRAPH, encoding="utf-8")

    png_bytes: bytes | None = None
    if compiled_graph is not None:
        try:
            png_bytes = compiled_graph.get_graph(xray=True).draw_mermaid_png()
        except Exception as exc:  # pragma: no cover - depends on optional rendering backends.
            logger.warning("Could not render LangGraph Mermaid PNG, using fallback: %s", exc)

    png_path.write_bytes(png_bytes or FALLBACK_PNG_BYTES)
    return {"png": str(png_path), "mermaid": str(mermaid_path)}


def _join_known_split_word(match: re.Match[str]) -> str:
    candidate = f"{match.group(1)}{match.group(2)}".lower()
    return candidate if candidate in {"to"} else match.group(0)


def _preserve_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _has_context_signal(message: str) -> bool:
    return bool(_select_context_tools(message))


def _select_context_tools(message: str) -> list[str]:
    lowered = (message or "").lower()
    selected_tools: list[str] = []

    if PROJECT_LOOKUP_PATTERN.search(lowered):
        selected_tools.append("suggest_related_projects")

    if _has_entity_context_ask(message):
        selected_tools.append("suggest_related_projects")

    if MEETING_LOOKUP_PATTERN.search(lowered):
        selected_tools.append("suggest_meeting_context")

    if WRITE_INTENT_PATTERN.search(message or ""):
        selected_tools.append("update_receiver_preference")
        if COMPANY_LOOKUP_PATTERN.search(lowered):
            selected_tools.append("save_company_pattern")

    if COMPANY_LOOKUP_PATTERN.search(lowered):
        selected_tools.extend(["get_company_context", "retrieve_company_patterns"])

    if RECEIVER_LOOKUP_PATTERN.search(lowered) or re.search(r"\bwith\s+[a-z][a-z]+\b", lowered):
        selected_tools.append("get_receiver_profile")

    return _dedupe(selected_tools)


def _has_entity_context_ask(message: str) -> bool:
    return bool(
        ENTITY_NAME_PATTERN.search(message or "")
        and ENTITY_CONTEXT_ASK_PATTERN.search(message or "")
    )


def _meaningful_tokens(text: str) -> set[str]:
    stopwords = {
        "a",
        "about",
        "and",
        "can",
        "could",
        "for",
        "i",
        "it",
        "me",
        "please",
        "the",
        "this",
        "to",
        "we",
        "you",
    }
    return {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
        if token not in stopwords
    }


def _extract_explicit_preference(message: str) -> str:
    text = (message or "").strip()
    patterns = [
        r"\bprefers?\s+(?P<preference>.+)$",
        r"\bpreference\s+(?:is|=)\s+(?P<preference>.+)$",
        r"\bpattern\s+(?:is|=)\s+(?P<preference>.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group("preference").strip(" .")
    return text


class _FallbackGraphView:
    def draw_mermaid_png(self) -> bytes:
        return FALLBACK_PNG_BYTES

    def draw_mermaid(self) -> str:
        return MERMAID_GRAPH


class _FallbackCompiledGraph:
    def __init__(self, runner: "CommunicationGraphRunner") -> None:
        self.runner = runner

    def invoke(self, input_state: CommunicationState, config: dict[str, Any] | None = None) -> CommunicationState:
        state = self.runner._invoke_node("input_normalizer", self.runner.input_normalizer, input_state)
        state = self.runner._invoke_node("message_router", self.runner.message_router, state)
        state = self.runner._invoke_node("route_decision", self.runner.route_decision, state)
        route = state.get("route")

        if route == "validate_only":
            state = self.runner._invoke_node("typo_cleanup", self.runner.typo_cleanup, state)
            state = self.runner._invoke_node("final_response", self.runner.final_response, state)
        elif route == "rewrite":
            if state.get("needs_typo_cleanup"):
                state = self.runner._invoke_node("typo_cleanup", self.runner.typo_cleanup, state)
            state = self.runner._invoke_node("communication_agent", self.runner.communication_agent, state)
            state = self.runner._invoke_node("final_validator", self.runner.final_validator, state)
            state = self.runner._invoke_node("final_response", self.runner.final_response, state)
        elif route == "rewrite_with_context":
            if state.get("needs_typo_cleanup"):
                state = self.runner._invoke_node("typo_cleanup", self.runner.typo_cleanup, state)
            state = self.runner._invoke_node("tool_selector", self.runner.tool_selector, state)
            state = self.runner._invoke_node("context_tools", self.runner.context_tools, state)
            state = self.runner._invoke_node("communication_agent", self.runner.communication_agent, state)
            state = self.runner._invoke_node("final_validator", self.runner.final_validator, state)
            state = self.runner._invoke_node("final_response", self.runner.final_response, state)
        else:
            state = self.runner._invoke_node("final_response", self.runner.final_response, state)

        return state

    def get_graph(self, xray: bool = False) -> _FallbackGraphView:
        return _FallbackGraphView()


class CommunicationGraphRunner:
    def __init__(
        self,
        *,
        sender: Employee,
        receiver: Employee,
        channel: str,
        intent: str,
        legacy_analyze: LegacyAnalyzeCallable,
    ) -> None:
        if sender.organization_id != receiver.organization_id:
            raise ValueError("Sender and receiver must belong to the same organization")

        self.sender = sender
        self.receiver = receiver
        self.channel = channel
        self.intent = intent
        self.legacy_analyze = legacy_analyze
        self.compiled_graph = self._compile_graph()
        self._export_visualization_once()

    def invoke(self, original_message: str) -> CommunicationState:
        graph_start = time.perf_counter()
        input_state: CommunicationState = {
            "original_message": original_message,
            "normalized_message": "",
            "receiver": self.receiver.name,
            "sender": self.sender.name,
            "company_id": self.sender.organization_id,
            "route": None,
            "typo_corrected_message": None,
            "needs_typo_cleanup": False,
            "needs_context": False,
            "selected_tools": [],
            "tool_results": None,
            "final_message": "",
            "message_id": None,
            "metadata": {
                "_graph_start_time": graph_start,
                "route": None,
                "nodes_executed": [],
                "tools_called": [],
                "used_llm": False,
                "used_tools": False,
                "latency_ms_by_node": {},
                "total_latency_ms": 0,
                "errors": [],
                "graph_engine": "langgraph" if StateGraph is not None else "fallback",
                "weave_tracing": weave_enabled(),
                "weave_project": weave_project() if weave_enabled() else "",
            },
        }

        config = {
            "tags": ["communication-agent", "langgraph"],
            "metadata": {
                "company_id": self.sender.organization_id,
                "receiver": self.receiver.name,
                "environment": os.getenv("DJANGO_ENV", "dev"),
                "weave_tracing": weave_enabled(),
                "weave_project": weave_project() if weave_enabled() else "",
            },
        }
        return trace_operation(
            "communication_agent.graph_run",
            graph_trace_inputs(
                state=input_state,
                sender=self.sender.name,
                receiver=self.receiver.name,
                company_id=self.sender.organization_id,
            ),
            lambda: self.compiled_graph.invoke(input_state, config=config),
            output=graph_trace_output,
        )

    def input_normalizer(self, state: CommunicationState) -> CommunicationState:
        start = time.perf_counter()
        next_state = dict(state)
        try:
            normalized = normalize_message(state.get("original_message", ""))
            next_state["normalized_message"] = normalized
            next_state["needs_typo_cleanup"] = needs_typo_cleanup(normalized)
        except Exception as exc:
            next_state["metadata"] = update_node_metadata(next_state, "input_normalizer", start, str(exc))
            raise
        next_state["metadata"] = update_node_metadata(next_state, "input_normalizer", start)
        return next_state

    def message_router(self, state: CommunicationState) -> CommunicationState:
        start = time.perf_counter()
        next_state = dict(state)
        try:
            route = route_message(state.get("normalized_message", ""))
            next_state["route"] = route
            metadata = dict(next_state.get("metadata") or {})
            metadata["route"] = route
            next_state["metadata"] = metadata
        except Exception as exc:
            next_state["metadata"] = update_node_metadata(next_state, "message_router", start, str(exc))
            raise
        next_state["metadata"] = update_node_metadata(next_state, "message_router", start)
        return next_state

    def route_decision(self, state: CommunicationState) -> CommunicationState:
        start = time.perf_counter()
        next_state = dict(state)
        next_state["metadata"] = update_node_metadata(next_state, "route_decision", start)
        return next_state

    def typo_cleanup(self, state: CommunicationState) -> CommunicationState:
        start = time.perf_counter()
        next_state = dict(state)
        try:
            corrected = cleanup_typos(state.get("normalized_message", ""))
            next_state["typo_corrected_message"] = corrected
            next_state["normalized_message"] = corrected
            next_state["final_message"] = corrected
            next_state["needs_typo_cleanup"] = False
        except Exception as exc:
            next_state["metadata"] = update_node_metadata(next_state, "typo_cleanup", start, str(exc))
            raise
        next_state["metadata"] = update_node_metadata(next_state, "typo_cleanup", start)
        return next_state

    def tool_selector(self, state: CommunicationState) -> CommunicationState:
        start = time.perf_counter()
        next_state = dict(state)
        try:
            message = state.get("normalized_message", "")
            next_state["selected_tools"] = _select_context_tools(message)
            next_state["needs_context"] = bool(next_state["selected_tools"])
        except Exception as exc:
            next_state["metadata"] = update_node_metadata(next_state, "tool_selector", start, str(exc))
            raise
        next_state["metadata"] = update_node_metadata(next_state, "tool_selector", start)
        return next_state

    def context_tools(self, state: CommunicationState) -> CommunicationState:
        start = time.perf_counter()
        next_state = dict(state)
        try:
            selected_tools = state.get("selected_tools") or []
            tool_results: dict[str, Any] = {}
            message = state.get("normalized_message", "")
            preference = _extract_explicit_preference(message)
            has_explicit_preference = WRITE_INTENT_PATTERN.search(message) is not None
            tools = CommunicationContextTools(
                company_id=state.get("company_id"),
                receiver=self.receiver.id,
            )

            for tool_name in selected_tools:
                tool_results[tool_name] = trace_operation(
                    f"communication_agent.tool.{tool_name}",
                    tool_trace_inputs(
                        tool_name,
                        company_id=state.get("company_id"),
                        receiver=self.receiver.id,
                        message=message,
                        has_preference=has_explicit_preference
                        and tool_name in {"save_company_pattern", "update_receiver_preference"},
                    ),
                    lambda tool_name=tool_name, message=message: tools.run(
                        tool_name,
                        message=message,
                        preference=preference,
                    ),
                    output=tool_trace_output,
                )

            metadata = dict(next_state.get("metadata") or {})
            metadata["tools_called"] = selected_tools
            metadata["used_tools"] = bool(selected_tools)
            next_state["metadata"] = metadata
            next_state["tool_results"] = tool_results
        except Exception as exc:
            next_state["metadata"] = update_node_metadata(next_state, "context_tools", start, str(exc))
            raise
        next_state["metadata"] = update_node_metadata(next_state, "context_tools", start)
        return next_state

    def communication_agent(self, state: CommunicationState) -> CommunicationState:
        start = time.perf_counter()
        next_state = dict(state)
        try:
            metadata = dict(next_state.get("metadata") or {})
            metadata["used_llm"] = True
            next_state["metadata"] = metadata

            message = self.legacy_analyze(
                state.get("normalized_message", ""),
                state.get("tool_results") or None,
            )
            next_state["message_id"] = message.id
            next_state["final_message"] = message.overall_suggested_message or message.final_text or message.original_text
        except Exception as exc:
            next_state["metadata"] = update_node_metadata(next_state, "communication_agent", start, str(exc))
            raise
        next_state["metadata"] = update_node_metadata(next_state, "communication_agent", start)
        return next_state

    def final_validator(self, state: CommunicationState) -> CommunicationState:
        start = time.perf_counter()
        next_state = dict(state)
        try:
            issues = validate_final_message(
                state.get("normalized_message", ""),
                state.get("final_message", ""),
                state.get("route"),
            )
            metadata = dict(next_state.get("metadata") or {})
            metadata["validator_passed"] = not issues
            metadata["validator_issues"] = issues
            if issues:
                metadata.setdefault("errors", [])
                metadata["errors"].append({"node": "final_validator", "error": "; ".join(issues)})
                self._append_validator_risks(state.get("message_id"), issues)
            next_state["metadata"] = metadata
        except Exception as exc:
            next_state["metadata"] = update_node_metadata(next_state, "final_validator", start, str(exc))
            raise
        next_state["metadata"] = update_node_metadata(next_state, "final_validator", start)
        return next_state

    def final_response(self, state: CommunicationState) -> CommunicationState:
        start = time.perf_counter()
        next_state = dict(state)
        try:
            route = state.get("route") or "bypass"
            if not state.get("message_id"):
                final_message = (
                    state.get("final_message")
                    or state.get("typo_corrected_message")
                    or state.get("normalized_message")
                    or state.get("original_message")
                    or ""
                )
                message = self._create_deterministic_message(state, final_message, route)
                next_state["message_id"] = message.id
                next_state["final_message"] = final_message

            metadata = update_node_metadata(next_state, "final_response", start)
            graph_start = metadata.pop("_graph_start_time", None)
            if graph_start is not None:
                metadata["total_latency_ms"] = round((time.perf_counter() - graph_start) * 1000, 2)
            metadata["route"] = route
            next_state["metadata"] = metadata
            self._attach_metadata_to_message(next_state.get("message_id"), metadata)
            self._trace_execution_summary(next_state)
        except Exception as exc:
            next_state["metadata"] = update_node_metadata(next_state, "final_response", start, str(exc))
            raise
        return next_state

    def _route_from_state(self, state: CommunicationState) -> str:
        if state.get("route") in {"rewrite", "rewrite_with_context"} and state.get("needs_typo_cleanup"):
            return "typo_cleanup"
        return state.get("route") or "bypass"

    def _route_after_typo_cleanup(self, state: CommunicationState) -> str:
        return state.get("route") or "validate_only"

    def _compile_graph(self) -> Any:
        if StateGraph is None:
            return _FallbackCompiledGraph(self)

        graph_builder = StateGraph(CommunicationState)
        graph_builder.add_node("input_normalizer", self._weave_node("input_normalizer", self.input_normalizer))
        graph_builder.add_node("message_router", self._weave_node("message_router", self.message_router))
        graph_builder.add_node("route_decision", self._weave_node("route_decision", self.route_decision))
        graph_builder.add_node("typo_cleanup", self._weave_node("typo_cleanup", self.typo_cleanup))
        graph_builder.add_node("tool_selector", self._weave_node("tool_selector", self.tool_selector))
        graph_builder.add_node("context_tools", self._weave_node("context_tools", self.context_tools))
        graph_builder.add_node("communication_agent", self._weave_node("communication_agent", self.communication_agent))
        graph_builder.add_node("final_validator", self._weave_node("final_validator", self.final_validator))
        graph_builder.add_node("final_response", self._weave_node("final_response", self.final_response))

        graph_builder.add_edge(START, "input_normalizer")
        graph_builder.add_edge("input_normalizer", "message_router")
        graph_builder.add_edge("message_router", "route_decision")
        graph_builder.add_conditional_edges(
            "route_decision",
            self._route_from_state,
            {
                "bypass": "final_response",
                "validate_only": "typo_cleanup",
                "rewrite": "communication_agent",
                "rewrite_with_context": "tool_selector",
                "typo_cleanup": "typo_cleanup",
            },
        )
        graph_builder.add_conditional_edges(
            "typo_cleanup",
            self._route_after_typo_cleanup,
            {
                "validate_only": "final_response",
                "bypass": "final_response",
                "rewrite": "communication_agent",
                "rewrite_with_context": "tool_selector",
            },
        )
        graph_builder.add_edge("tool_selector", "context_tools")
        graph_builder.add_edge("context_tools", "communication_agent")
        graph_builder.add_edge("communication_agent", "final_validator")
        graph_builder.add_edge("final_validator", "final_response")
        graph_builder.add_edge("final_response", END)
        return graph_builder.compile()

    def _weave_node(
        self,
        node_name: str,
        node_func: Callable[[CommunicationState], CommunicationState],
    ) -> Callable[[CommunicationState], CommunicationState]:
        def wrapped(state: CommunicationState) -> CommunicationState:
            return self._invoke_node(node_name, node_func, state)

        wrapped.__name__ = node_name
        return wrapped

    def _invoke_node(
        self,
        node_name: str,
        node_func: Callable[[CommunicationState], CommunicationState],
        state: CommunicationState,
    ) -> CommunicationState:
        return trace_operation(
            f"communication_agent.node.{node_name}",
            node_trace_inputs(node_name, state),
            lambda: node_func(state),
            output=node_trace_output,
        )

    def _trace_execution_summary(self, state: CommunicationState) -> None:
        trace_operation(
            "communication_agent.execution_summary",
            execution_summary_trace_inputs(state),
            lambda: state,
            output=execution_summary_trace_output,
        )

    def _export_visualization_once(self) -> None:
        global _VISUALIZATION_EXPORTED
        if _VISUALIZATION_EXPORTED:
            return
        export_graph_visualization(self.compiled_graph)
        _VISUALIZATION_EXPORTED = True

    @transaction.atomic
    def _create_deterministic_message(
        self,
        state: CommunicationState,
        final_message: str,
        route: str,
    ) -> Message:
        original = state.get("original_message") or ""
        final_text = final_message or original
        changed = final_text != original
        scores = _deterministic_scores(route, changed)
        summary = "No changes needed." if not changed else "Minor typo and formatting cleanup."
        explanation = "Handled by the LangGraph deterministic fast path without an LLM call."

        message = Message.objects.create(
            organization=self.sender.organization,
            sender=self.sender,
            receiver=self.receiver,
            channel=self.channel,
            intent=self.intent,
            original_text=original,
            final_text=final_text,
            overall_suggested_message=final_text,
            scores_before=normalize_scores(scores),
            estimated_scores_after_all=normalize_scores(scores),
            current_scores=normalize_scores(scores),
            risks=[],
            summary_of_changes=summary,
            explanation=explanation,
            raw_llm_response={"agent_metadata": state.get("metadata") or {}},
            status=Message.Status.ANALYZED,
        )
        MessageRevision.objects.create(
            message=message,
            version_index=1,
            text=original,
            note="Original draft",
        )
        if changed:
            MessageRevision.objects.create(
                message=message,
                version_index=2,
                text=final_text,
                note="Deterministic cleanup",
            )

        log_event(
            "message.analyzed",
            message=message,
            source="agent_graph",
            payload={"channel": self.channel, "intent": self.intent, "route": route},
        )
        return message

    def _append_validator_risks(self, message_id: int | None, issues: list[str]) -> None:
        if not message_id or not issues:
            return

        message = Message.objects.filter(pk=message_id).first()
        if message is None:
            return

        existing = list(message.risks or [])
        for issue in issues:
            if issue not in existing:
                existing.append(issue)
        message.risks = existing
        message.save(update_fields=["risks"])

    def _attach_metadata_to_message(self, message_id: int | None, metadata: dict[str, Any]) -> None:
        if not message_id:
            return

        message = Message.objects.filter(pk=message_id).first()
        if message is None:
            return

        raw = dict(message.raw_llm_response or {})
        raw["agent_metadata"] = metadata
        message.raw_llm_response = raw
        message.save(update_fields=["raw_llm_response"])


def _deterministic_scores(route: str, changed: bool) -> dict[str, int]:
    if route == "bypass":
        score = 92
    elif changed:
        score = 88
    else:
        score = 86
    return {
        "clarity": score,
        "tone": score,
        "receiver_fit": score,
        "org_values_alignment": score,
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
