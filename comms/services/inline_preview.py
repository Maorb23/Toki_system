import hashlib
import re
import time
from typing import Any

from comms.models import Employee
from comms.services.context_tools import CommunicationContextTools
from comms.services.llm_client import NebiusLLMClient
from comms.services.message_analyzer import LLMResponseValidationError
from comms.services.prompt_builder import INLINE_PREVIEW_SYSTEM_PROMPT, build_inline_preview_prompt
from comms.services.score_engine import SCORE_KEYS, clamp_score
from comms.services.weave_monitor import (
    content_trace_fields,
    tool_trace_inputs,
    tool_trace_output,
    trace_operation,
    weave_enabled,
    weave_project,
)


INLINE_TRACE_ROOT = "inline_preview"
INLINE_TRACE_SUGGESTION = "inline/suggestion"
INLINE_TRACE_SUMMARY = "inline/summary"

INLINE_NODE_TRACE_NAMES = {
    "input_normalizer": "inline/input",
    "router": "inline/router",
    "context_tools": "inline/tools",
    "prompt_builder": "inline/prompt",
    "llm_preview": "inline/llm",
    "final_validator": "inline/postprocess/validate",
    "deterministic_inline_coach": "inline/postprocess/local_rules",
    "context_inline_coach": "inline/postprocess/context_rules",
    "final_response": "inline/final",
}

INLINE_TOOL_TRACE_NAMES = {
    "suggest_related_projects": "inline/tools/projects",
    "suggest_meeting_context": "inline/tools/meeting",
    "get_receiver_profile": "inline/tools/receiver",
    "get_company_context": "inline/tools/company",
    "retrieve_company_patterns": "inline/tools/patterns",
    "update_receiver_preference": "inline/tools/preference",
    "save_company_pattern": "inline/tools/save_pattern",
}

INLINE_TOOL_JOBS = {
    "suggest_related_projects": ("project_context_lookup", "project_or_entity_context_signal"),
    "suggest_meeting_context": ("meeting_context_lookup", "meeting_or_schedule_signal"),
    "get_receiver_profile": ("receiver_profile_lookup", "receiver_or_recipient_signal"),
    "get_company_context": ("company_context_lookup", "company_or_values_signal"),
    "retrieve_company_patterns": ("company_patterns_lookup", "company_or_values_signal"),
    "update_receiver_preference": ("receiver_preference_update", "explicit_preference_write_intent"),
    "save_company_pattern": ("company_pattern_write", "explicit_company_pattern_write_intent"),
}


INLINE_PREVIEW_WRITE_INTENT_PATTERN = re.compile(
    r"\b(remember|save|store|update|learn|note)\b.*\b(prefers?|preference|pattern|likes?|wants?)\b",
    re.IGNORECASE,
)

INLINE_PREVIEW_PROJECT_LOOKUP_PATTERN = re.compile(
    r"\b(project|prokject|projerc|initiative|workstream|roadmap|sprint|milestone|q[1-4])\b",
    re.IGNORECASE,
)
INLINE_PREVIEW_MEETING_LOOKUP_PATTERN = re.compile(
    r"\b(meeting|sync|schedule|calendar|talk|call|roadmap review|review)\b|\bq[1-4]\b",
    re.IGNORECASE,
)
INLINE_PREVIEW_COMPANY_LOOKUP_PATTERN = re.compile(
    r"\b(company|org|organization|team|values?|policy|priorit(?:y|ies)|standard|customer needs?)\b",
    re.IGNORECASE,
)
INLINE_PREVIEW_RECEIVER_LOOKUP_PATTERN = re.compile(
    r"\b(receiver|recipient|dana|dan|their style|prefers?|preference)\b",
    re.IGNORECASE,
)
INLINE_PREVIEW_ENTITY_NAME_PATTERN = re.compile(
    r"\b[A-Z][a-zA-Z]{3,}\s+[A-Z][a-zA-Z]{3,}(?:\s+[A-Z][a-zA-Z]{3,})?\b"
)
INLINE_PREVIEW_ENTITY_CONTEXT_ASK_PATTERN = re.compile(
    r"\b(what(?:'s| is)?|next|phase|status|update|timeline|when|complete|deadline|fix|improve|implement|meeting|review|about)\b|\?",
    re.IGNORECASE,
)

TYPO_NORMALIZATIONS = {
    "abiut": "about",
    "abouut": "about",
    "projerc": "project",
    "prokject": "project",
    "peoject": "project",
    "projct": "project",
    "projec": "project",
    "projet": "project",
    "wetalked": "we talked",
    "road map": "roadmap",
}

INLINE_TYPO_REPLACEMENTS = {
    "abiut": "about",
    "abouut": "about",
    "beautoiful": "beautiful",
    "gorgoeus": "gorgeous",
    "goregoes": "gorgeous",
    "gorgues": "gorgeous",
    "peoject": "project",
    "projct": "project",
    "projec": "project",
    "projerc": "project",
    "projet": "project",
    "prokject": "project",
    "reciever": "receiver",
    "recievers": "receivers",
    "talkeda": "talked about",
    "woker": "worker",
    "wsnt": "want",
}

PROJECT_REFERENCE_PATTERN = re.compile(
    r"\b(?:(?:our|the|this)\s+)?(?:project|prokject|projerc|peoject|projct|projec|projet)\b",
    re.IGNORECASE,
)

APPEARANCE_WORD_PATTERN = r"(?:beautiful|beautoiful|gorgeous|gorgoeus|goregoes|gorgues|hot|sexy)"

DETERMINISTIC_INLINE_PATTERNS = [
    {
        "pattern": re.compile(
            rf"(?:^|(?<=[.!?\n]))\s*[^.!?\n]*(?:\b(?:your|ur)?\s*(?:wife|husband|partner|spouse)\b[^.!?\n]*(?:{APPEARANCE_WORD_PATTERN}|\bhook\s+up\b)|\bwhen\s+can\s+i\s+see\s+(?:your|ur)\s+{APPEARANCE_WORD_PATTERN}\s+(?:wife|husband|partner|spouse)\s+again\??|\b(?:your|ur)\s+{APPEARANCE_WORD_PATTERN}\s+(?:wife|husband|partner|spouse)\b)[^.!?\n]*[.!?]?",
            re.IGNORECASE,
        ),
        "replacement": "When are you available to discuss the next steps?",
        "issue": "Inappropriate personal comment",
        "reason": "Removes a personal sexualized comment and keeps the message focused on a professional next step.",
        "affected_scores": {"clarity": 6, "tone": 10, "receiver_fit": 9, "org_values_alignment": 8},
    },
    {
        "pattern": re.compile(
            rf"(?:^|(?<=[.!?\n]))\s*[^.!?\n]*(?:\b(?:you|u)\s+(?:are\s+)?{APPEARANCE_WORD_PATTERN}\b|\bmake\s+you\s+{APPEARANCE_WORD_PATTERN}\b|\btight\s+jeans\b|\bhook\s+up\b)[^.!?\n]*[.!?]?",
            re.IGNORECASE,
        ),
        "replacement": "Could you share your thoughts on the next step for this work?",
        "issue": "Inappropriate personal comment",
        "reason": "Replaces an objectifying personal comment with a professional project-focused ask.",
        "affected_scores": {"clarity": 6, "tone": 10, "receiver_fit": 9, "org_values_alignment": 8},
    },
    {
        "pattern": re.compile(
            r"(?:^|(?<=[.!?\n]))\s*[^.!?\n]*\b(?:you|u)\s+unctuous\b[^.!?\n]*[.!?]?",
            re.IGNORECASE,
        ),
        "replacement": "Could you share what you need to move this forward?",
        "issue": "Unprofessional wording",
        "reason": "Removes an insulting or confusing personal phrase and turns it into a clear work-focused ask.",
        "affected_scores": {"clarity": 7, "tone": 9, "receiver_fit": 8, "org_values_alignment": 7},
    },
    {
        "pattern": re.compile(
            r"(?:^|(?<=[.!?\n]))\s*[^.!?\n]*\b(?:send|show|share)\s+(?:you\s+)?(?:good\s+|nice\s+|hot\s+|sexy\s+)?(?:photos?|pictures?|pics?)\s+of\s+me\b[^.!?\n]*[.!?]?",
            re.IGNORECASE,
        ),
        "replacement": "Could you review the relevant materials for this task?",
        "issue": "Inappropriate personal content",
        "reason": "Replaces an unrelated personal photo reference with a professional work-focused request.",
        "affected_scores": {"clarity": 6, "tone": 10, "receiver_fit": 9, "org_values_alignment": 8},
    },
    {
        "pattern": re.compile(
            rf"(?:^|(?<=[.!?\n]))\s*[^.!?\n]*\b(?:find|meet|see|look\s+for)\s+(?:some\s+)?{APPEARANCE_WORD_PATTERN}\s+(?:women|woman|girls?|people)\b[^.!?\n]*[.!?]?",
            re.IGNORECASE,
        ),
        "replacement": "Could you help identify the right stakeholders for this work?",
        "issue": "Inappropriate workplace comment",
        "reason": "Replaces a non-workplace objectifying comment with a professional stakeholder-focused request.",
        "affected_scores": {"clarity": 6, "tone": 10, "receiver_fit": 9, "org_values_alignment": 8},
    },
    {
        "pattern": re.compile(r"\b(?:let'?s\s+do\s+it\s+)?good\s+and\s+great\s+and\s+shiny!?", re.IGNORECASE),
        "replacement": "Let's make it polished, user-facing, and fully functional.",
        "issue": "Vague polish language",
        "reason": "Replaces subjective wording with a clearer product-quality goal.",
        "affected_scores": {"clarity": 8, "tone": 4, "receiver_fit": 5, "org_values_alignment": 5},
    },
    {
        "pattern": re.compile(r"\bgo\s+fetch\s+the\s+data\s+right\s+now\?", re.IGNORECASE),
        "replacement": "Can you fetch the relevant data now?",
        "issue": "Too blunt",
        "reason": "Keeps the urgency while making the ask more collaborative.",
        "affected_scores": {"clarity": 5, "tone": 8, "receiver_fit": 6, "org_values_alignment": 4},
    },
]


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _require_dict(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise LLMResponseValidationError(f"{name} must be an object")
    return value


def _require_list(value: Any, name: str) -> list:
    if not isinstance(value, list):
        raise LLMResponseValidationError(f"{name} must be a list")
    return value


def _normalize_affected_scores(value: Any) -> dict:
    scores = value if isinstance(value, dict) else {}
    return {key: clamp_score(scores.get(key, 0)) for key in SCORE_KEYS}


def _new_preview_metadata() -> dict[str, Any]:
    return {
        "_preview_start_time": time.perf_counter(),
        "nodes_executed": [],
        "tools_called": [],
        "selected_tools": [],
        "selected_jobs": [],
        "tool_reasons": {},
        "route": None,
        "tool_results": {},
        "project_candidates": [],
        "steps": [],
        "used_llm": False,
        "used_tools": False,
        "latency_ms_by_node": {},
        "latency_ms_by_tool": {},
        "latency_ms_by_job": {},
        "total_latency_ms": 0,
        "errors": [],
        "weave_tracing": weave_enabled(),
        "weave_project": weave_project() if weave_enabled() else "",
    }


def _record_preview_step(
    metadata: dict[str, Any],
    node_name: str,
    start_time: float,
    error: str | None = None,
) -> None:
    metadata.setdefault("nodes_executed", []).append(node_name)
    metadata.setdefault("latency_ms_by_node", {})[node_name] = round(
        (time.perf_counter() - start_time) * 1000,
        2,
    )
    status = "error" if error else "ok"
    metadata.setdefault("steps", []).append({
        "name": node_name,
        "type": "node",
        "trace_name": INLINE_NODE_TRACE_NAMES.get(node_name, f"inline/{node_name}"),
        "status": status,
    })
    if error:
        metadata.setdefault("errors", []).append({"node": node_name, "error": error})


def _trace_preview_node(
    metadata: dict[str, Any],
    node_name: str,
    inputs: dict[str, Any],
    operation,
    *,
    output=None,
):
    start = time.perf_counter()
    try:
        result = trace_operation(
            INLINE_NODE_TRACE_NAMES.get(node_name, f"inline/{node_name}"),
            {
                "node": node_name,
                "trace_group": _trace_group_for_node(node_name),
                "selected_tools": metadata.get("selected_tools", []),
                "selected_jobs": metadata.get("selected_jobs", []),
                "tool_reasons": metadata.get("tool_reasons", {}),
                "used_tools": metadata.get("used_tools", False),
                **inputs,
            },
            operation,
            output=output,
        )
    except Exception as exc:
        _record_preview_step(metadata, node_name, start, str(exc))
        raise
    _record_preview_step(metadata, node_name, start)
    return result


def _preview_trace_output(result: dict, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "text_hash": result.get("text_hash"),
        "route": metadata.get("route"),
        "suggestion_count": len(result.get("suggestions") or []),
        "selected_jobs": metadata.get("selected_jobs", []),
        "selected_tools": metadata.get("selected_tools", []),
        "tool_reasons": metadata.get("tool_reasons", {}),
        "project_candidates": metadata.get("project_candidates", []),
        "nodes_executed": metadata.get("nodes_executed", []),
        "tools_called": metadata.get("tools_called", []),
        "steps": metadata.get("steps", []),
        "used_llm": metadata.get("used_llm", False),
        "used_tools": metadata.get("used_tools", False),
        "latency_ms_by_node": metadata.get("latency_ms_by_node", {}),
        "latency_ms_by_tool": metadata.get("latency_ms_by_tool", {}),
        "latency_ms_by_job": metadata.get("latency_ms_by_job", {}),
        "total_latency_ms": metadata.get("total_latency_ms"),
        "error_count": len(metadata.get("errors") or []),
    }


def _trace_group_for_node(node_name: str) -> str:
    if node_name in {"final_validator", "deterministic_inline_coach", "context_inline_coach"}:
        return "postprocess"
    if node_name == "context_tools":
        return "tools"
    if node_name == "llm_preview":
        return "llm"
    return node_name.replace("_", "-")


def _select_preview_tools(
    changed_text: str,
    surrounding_context: str,
    prior_review_context: list[dict] | None = None,
) -> tuple[str, list[str], list[str], dict[str, str]]:
    raw_text = " ".join([
        changed_text or "",
        surrounding_context or "",
        _prior_review_text(prior_review_context),
    ])
    text = _normalize_routing_text(raw_text)
    if not text.strip():
        return "bypass", [], [], {}

    selected_tools: list[str] = []
    selected_jobs: list[str] = []
    tool_reasons: dict[str, str] = {}
    if _has_local_edit_signal(raw_text, text):
        selected_jobs.append("local_edit")

    if INLINE_PREVIEW_PROJECT_LOOKUP_PATTERN.search(text):
        selected_tools.append("suggest_related_projects")
        selected_jobs.append("project_context_lookup")
        tool_reasons["suggest_related_projects"] = "project_keyword_or_context_typo"

    if _has_entity_context_ask(raw_text):
        selected_tools.append("suggest_related_projects")
        selected_jobs.append("project_context_lookup")
        tool_reasons["suggest_related_projects"] = "entity_name_context_ask"

    if INLINE_PREVIEW_MEETING_LOOKUP_PATTERN.search(text):
        selected_tools.append("suggest_meeting_context")
        selected_jobs.append("meeting_context_lookup")
        tool_reasons["suggest_meeting_context"] = "meeting_or_schedule_signal"

    if INLINE_PREVIEW_WRITE_INTENT_PATTERN.search(text):
        selected_tools.append("update_receiver_preference")
        selected_jobs.append("receiver_preference_update")
        tool_reasons["update_receiver_preference"] = "explicit_preference_write_intent"

    if INLINE_PREVIEW_COMPANY_LOOKUP_PATTERN.search(text):
        selected_tools.extend(["get_company_context", "retrieve_company_patterns"])
        selected_jobs.append("company_context_lookup")
        tool_reasons["get_company_context"] = "company_or_values_signal"
        tool_reasons["retrieve_company_patterns"] = "company_or_values_signal"

    if INLINE_PREVIEW_RECEIVER_LOOKUP_PATTERN.search(text) or re.search(r"\bwith\s+[a-z][a-z]+\b", text):
        selected_tools.append("get_receiver_profile")
        selected_jobs.append("receiver_profile_lookup")
        tool_reasons["get_receiver_profile"] = "receiver_or_recipient_signal"

    selected_tools = _dedupe(selected_tools)
    selected_jobs = _dedupe(selected_jobs)
    return ("preview_with_context" if selected_tools else "preview", selected_tools, selected_jobs, tool_reasons)


def _has_local_edit_signal(raw_text: str, normalized_text: str) -> bool:
    return (raw_text or "").lower() != (normalized_text or "").lower()


def _has_entity_context_ask(raw_text: str) -> bool:
    return bool(
        INLINE_PREVIEW_ENTITY_NAME_PATTERN.search(raw_text or "")
        and INLINE_PREVIEW_ENTITY_CONTEXT_ASK_PATTERN.search(raw_text or "")
    )


def _normalize_routing_text(value: str) -> str:
    normalized = (value or "").lower()
    for typo, replacement in TYPO_NORMALIZATIONS.items():
        normalized = re.sub(rf"\b{re.escape(typo)}\b", replacement, normalized)
    return normalized


def _prior_review_text(prior_review_context: list[dict] | None) -> str:
    parts: list[str] = []
    for item in prior_review_context or []:
        if not isinstance(item, dict):
            continue
        parts.append(str(item.get("text") or ""))
        for suggestion in item.get("suggestions") or []:
            if not isinstance(suggestion, dict):
                continue
            parts.append(str(suggestion.get("target_text") or ""))
            parts.append(str(suggestion.get("suggested_replacement") or ""))
            parts.append(str(suggestion.get("issue") or ""))
    return " ".join(parts)


def _sanitize_prior_review_context(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    clean: list[dict[str, Any]] = []
    for index, item in enumerate(value[-5:]):
        if not isinstance(item, dict):
            continue
        suggestions = []
        for suggestion in item.get("suggestions") or []:
            if not isinstance(suggestion, dict):
                continue
            suggestions.append({
                "target_text": str(suggestion.get("target_text") or "")[:280],
                "suggested_replacement": str(suggestion.get("suggested_replacement") or "")[:500],
                "issue": str(suggestion.get("issue") or "")[:180],
                "reason": str(suggestion.get("reason") or "")[:280],
            })
        clean.append({
            "id": item.get("id", index),
            "text": str(item.get("text") or "")[:1000],
            "text_hash": str(item.get("text_hash") or "")[:80],
            "status": str(item.get("status") or "")[:40],
            "suggestions": suggestions[:5],
        })
    return clean


def _extract_preview_preference(message: str) -> str:
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


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _is_correction_like(target_text: str, suggested_replacement: str) -> bool:
    target_words = target_text.split()
    replacement_words = suggested_replacement.split()
    if len(target_words) > 2 or len(replacement_words) > 2:
        return False
    return abs(len(target_text) - len(suggested_replacement)) <= 4


def _suggestion_sort_key(suggestion: dict) -> tuple[int, int, int]:
    correction_rank = 0 if suggestion.get("_correction_like") else 1
    return (correction_rank, suggestion["_end_index"] - suggestion["_start_index"], suggestion["_start_index"])


def _dedupe_inline_suggestions(suggestions: list[dict]) -> list[dict]:
    accepted: list[dict] = []
    seen: set[tuple[int, int, str, str]] = set()
    for suggestion in sorted(suggestions, key=_suggestion_sort_key):
        start = suggestion["_start_index"]
        end = suggestion["_end_index"]
        key = (
            start,
            end,
            str(suggestion.get("target_text") or ""),
            str(suggestion.get("suggested_replacement") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        accepted.append(suggestion)

    accepted.sort(key=lambda suggestion: (
        suggestion["_start_index"],
        0 if suggestion.get("_correction_like") else 1,
        suggestion["_end_index"] - suggestion["_start_index"],
    ))
    return [
        {key: value for key, value in suggestion.items() if not key.startswith("_")}
        for suggestion in accepted
    ]


def _clean_inline_suggestion(item: dict, changed_text: str) -> dict | None:
    target_text = (item.get("target_text") or "").strip()
    suggested_replacement = (item.get("suggested_replacement") or "").strip()
    if not target_text or not suggested_replacement:
        return None
    target_start = changed_text.find(target_text)
    if target_start < 0:
        return None

    return {
        "target_text": target_text,
        "suggested_replacement": suggested_replacement,
        "issue": str(item.get("issue") or ""),
        "reason": str(item.get("reason") or ""),
        "affected_scores": _normalize_affected_scores(item.get("affected_scores")),
        "source": str(item.get("source") or "llm"),
        "_start_index": target_start,
        "_end_index": target_start + len(target_text),
        "_correction_like": _is_correction_like(target_text, suggested_replacement),
    }


def deterministic_inline_suggestions(changed_text: str) -> list[dict]:
    suggestions: list[dict] = []
    for typo, replacement in INLINE_TYPO_REPLACEMENTS.items():
        for match in re.finditer(rf"\b{re.escape(typo)}\b", changed_text or "", flags=re.IGNORECASE):
            cleaned = _clean_inline_suggestion(
                {
                    "target_text": match.group(0),
                    "suggested_replacement": _preserve_case(match.group(0), replacement),
                    "issue": "Typo",
                    "reason": "Corrects a spelling issue without rewriting the surrounding sentence.",
                    "affected_scores": {"clarity": 4, "tone": 1, "receiver_fit": 2, "org_values_alignment": 1},
                    "source": "local_typo_rules",
                },
                changed_text,
            )
            if cleaned:
                suggestions.append(cleaned)

    for rule in DETERMINISTIC_INLINE_PATTERNS:
        for match in rule["pattern"].finditer(changed_text):
            target_text = match.group(0)
            cleaned = _clean_inline_suggestion(
                {
                    "target_text": target_text,
                    "suggested_replacement": rule["replacement"],
                    "issue": rule["issue"],
                    "reason": rule["reason"],
                    "affected_scores": rule["affected_scores"],
                    "source": "local_rules",
                },
                changed_text,
            )
            if cleaned:
                suggestions.append(cleaned)
    return suggestions


def _preserve_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def deterministic_context_suggestions(changed_text: str, tool_results: dict[str, Any] | None) -> list[dict]:
    project = _best_related_project(tool_results)
    if not project:
        return []

    project_name = str(project.get("name") or "").strip()
    if not project_name or project_name.lower() in (changed_text or "").lower():
        return []

    suggestions: list[dict] = []
    for match in PROJECT_REFERENCE_PATTERN.finditer(changed_text or ""):
        cleaned = _clean_inline_suggestion(
            {
                "target_text": match.group(0),
                "suggested_replacement": project_name,
                "issue": "Name the project",
                "reason": "Uses retrieved project context so the receiver knows which project you mean.",
                "affected_scores": {"clarity": 7, "receiver_fit": 5, "org_values_alignment": 3},
                "source": "context_rules",
            },
            changed_text,
        )
        if cleaned:
            suggestions.append(cleaned)
    return suggestions


def _best_related_project(tool_results: dict[str, Any] | None) -> dict[str, Any] | None:
    projects = (tool_results or {}).get("suggest_related_projects") or []
    if not isinstance(projects, list):
        return None
    for project in projects:
        if isinstance(project, dict) and project.get("type") == "project" and project.get("name"):
            return project
    return None


def _project_candidate_names(tool_results: dict[str, Any] | None) -> list[str]:
    projects = (tool_results or {}).get("suggest_related_projects") or []
    if not isinstance(projects, list):
        return []
    names = []
    for project in projects[:5]:
        if isinstance(project, dict) and project.get("name"):
            names.append(str(project["name"]))
    return names


def _trace_inline_suggestions(
    *,
    metadata: dict[str, Any],
    review_id: Any,
    review_text: str,
    review_text_hash: str,
    normalized_changed_text: str,
    suggestions: list[dict],
) -> None:
    for index, suggestion in enumerate(suggestions):
        target_text = str(suggestion.get("target_text") or "")
        suggested_replacement = str(suggestion.get("suggested_replacement") or "")
        source = str(suggestion.get("source") or "unknown")
        trace_operation(
            INLINE_TRACE_SUGGESTION,
            {
                "review_id": review_id,
                "review_text_hash": review_text_hash or text_hash(review_text or normalized_changed_text),
                "suggestion_index": index,
                "suggestion_source": source,
                "target_text_hash": text_hash(target_text),
                "replacement_hash": text_hash(suggested_replacement),
                "issue": suggestion.get("issue", ""),
                "route": metadata.get("route"),
                "selected_jobs": metadata.get("selected_jobs", []),
                "selected_tools": metadata.get("selected_tools", []),
                "tool_reasons": metadata.get("tool_reasons", {}),
                "project_candidates": metadata.get("project_candidates", []),
                **content_trace_fields(
                    review_text=review_text or normalized_changed_text,
                    target_text=target_text,
                    suggested_replacement=suggested_replacement,
                    reason=suggestion.get("reason", ""),
                ),
            },
            lambda suggestion=suggestion: suggestion,
            output=lambda value, index=index, source=source: {
                "review_id": review_id,
                "suggestion_index": index,
                "suggestion_source": source,
                "target_text_hash": text_hash(str(value.get("target_text") or "")),
                "replacement_hash": text_hash(str(value.get("suggested_replacement") or "")),
                "issue": value.get("issue", ""),
                "selected_jobs": metadata.get("selected_jobs", []),
                "selected_tools": metadata.get("selected_tools", []),
                "tool_reasons": metadata.get("tool_reasons", {}),
                "project_candidates": metadata.get("project_candidates", []),
                **content_trace_fields(
                    target_text=value.get("target_text", ""),
                    suggested_replacement=value.get("suggested_replacement", ""),
                    reason=value.get("reason", ""),
                ),
            },
        )


def validate_inline_preview_response(data: dict, changed_text: str) -> list[dict]:
    _require_dict(data, "root")
    raw_suggestions = data.get("inline_suggestions")
    if isinstance(raw_suggestions, dict):
        suggestions = [raw_suggestions]
    elif isinstance(raw_suggestions, list):
        suggestions = raw_suggestions
    else:
        suggestions = []

    clean = []
    for index, item in enumerate(suggestions):
        if not isinstance(item, dict):
            continue
        cleaned = _clean_inline_suggestion(item, changed_text)
        if cleaned:
            clean.append(cleaned)

    return _dedupe_inline_suggestions(clean)


class InlineSuggestionPreviewer:
    def __init__(self, llm_client: NebiusLLMClient | None = None) -> None:
        self.llm_client = llm_client or NebiusLLMClient()

    def preview(
        self,
        *,
        sender: Employee,
        receiver: Employee,
        channel: str,
        intent: str,
        full_draft: str,
        changed_text: str,
        surrounding_context: str,
        prior_review_context: list[dict] | None = None,
        review_id: Any = None,
        review_text: str = "",
        review_text_hash: str = "",
    ) -> dict:
        if sender.organization_id != receiver.organization_id:
            raise ValueError("Sender and receiver must belong to the same organization")

        full_draft = full_draft or ""
        raw_changed_text = changed_text or ""
        review_text = review_text or raw_changed_text
        review_text_hash = review_text_hash or text_hash(review_text.strip())
        surrounding_context = surrounding_context or ""
        prior_review_context = _sanitize_prior_review_context(prior_review_context)
        metadata = _new_preview_metadata()

        def run_preview() -> dict:
            normalized_changed_text = _trace_preview_node(
                metadata,
                "input_normalizer",
                {
                    "changed_text_length": len(raw_changed_text),
                    "changed_text_hash": text_hash(raw_changed_text.strip()),
                    "review_id": review_id,
                    "review_text_hash": review_text_hash,
                    **content_trace_fields(changed_text=raw_changed_text, review_text=review_text),
                },
                lambda: raw_changed_text.strip(),
                output=lambda value: {
                    "changed_text_length": len(value),
                    "changed_text_hash": text_hash(value),
                },
            )

            if not normalized_changed_text:
                metadata["route"] = "bypass"
                return _trace_preview_node(
                    metadata,
                    "final_response",
                    {"reason": "empty_changed_text"},
                    lambda: {"text_hash": text_hash(""), "suggestions": []},
                    output=lambda result: _preview_trace_output(result, metadata),
                )

            route, selected_tools, selected_jobs, tool_reasons = _trace_preview_node(
                metadata,
                "router",
                {
                    "changed_text_hash": text_hash(normalized_changed_text),
                    "changed_text_length": len(normalized_changed_text),
                    "review_id": review_id,
                    "review_text_hash": review_text_hash,
                    "surrounding_context_length": len(surrounding_context),
                    "prior_review_count": len(prior_review_context),
                    **content_trace_fields(
                        changed_text=normalized_changed_text,
                        review_text=review_text,
                        surrounding_context=surrounding_context,
                        prior_review_context=prior_review_context,
                    ),
                },
                lambda: _select_preview_tools(normalized_changed_text, surrounding_context, prior_review_context),
                output=lambda value: {
                    "route": value[0],
                    "selected_tools": value[1],
                    "selected_jobs": value[2],
                    "tool_reasons": value[3],
                    "job_count": len(value[2]),
                    "tool_count": len(value[1]),
                },
            )
            metadata["route"] = route
            metadata["selected_tools"] = selected_tools
            metadata["selected_jobs"] = selected_jobs
            metadata["tool_reasons"] = tool_reasons

            tool_results = _trace_preview_node(
                metadata,
                "context_tools",
                {
                    "route": route,
                    "selected_tools": selected_tools,
                    "selected_jobs": selected_jobs,
                    "tool_reasons": tool_reasons,
                    "company_id": sender.organization_id,
                    "receiver": receiver.id,
                    "changed_text_hash": text_hash(normalized_changed_text),
                    "changed_text_length": len(normalized_changed_text),
                    **content_trace_fields(changed_text=normalized_changed_text),
                },
                lambda: self._run_context_tools(
                    sender=sender,
                    receiver=receiver,
                    message=" ".join([
                        normalized_changed_text,
                        surrounding_context,
                        _prior_review_text(prior_review_context),
                    ]),
                    selected_tools=selected_tools,
                    metadata=metadata,
                ),
                output=lambda value: {
                    "tools_called": sorted(str(key) for key in value.keys()),
                    "selected_jobs": metadata.get("selected_jobs", []),
                    "tool_reasons": metadata.get("tool_reasons", {}),
                    "latency_ms_by_tool": metadata.get("latency_ms_by_tool", {}),
                    "latency_ms_by_job": metadata.get("latency_ms_by_job", {}),
                    "tool_count": len(value),
                },
            )
            metadata["tool_results"] = tool_results
            metadata["project_candidates"] = _project_candidate_names(tool_results)
            metadata["tools_called"] = selected_tools
            metadata["used_tools"] = bool(selected_tools)

            prompt = _trace_preview_node(
                metadata,
                "prompt_builder",
                {
                    "company_id": sender.organization_id,
                    "receiver": receiver.name,
                    "changed_text_hash": text_hash(normalized_changed_text),
                    "changed_text_length": len(normalized_changed_text),
                    "full_draft_length": len(full_draft),
                    "surrounding_context_length": len(surrounding_context),
                    "prior_review_count": len(prior_review_context),
                    **content_trace_fields(
                        full_draft=full_draft,
                        changed_text=normalized_changed_text,
                        surrounding_context=surrounding_context,
                    ),
                },
                lambda: build_inline_preview_prompt(
                    organization=sender.organization,
                    sender=sender,
                    receiver=receiver,
                    channel=channel,
                    intent=intent,
                    full_draft=full_draft,
                    changed_text=normalized_changed_text,
                    surrounding_context=surrounding_context,
                    prior_review_context=prior_review_context,
                    tool_results=tool_results,
                ),
                output=lambda value: {"prompt_length": len(value)},
            )

            metadata["used_llm"] = True
            raw = _trace_preview_node(
                metadata,
                "llm_preview",
                {
                    "provider": "nebius",
                    "prompt_length": len(prompt),
                    "changed_text_hash": text_hash(normalized_changed_text),
                    "changed_text_length": len(normalized_changed_text),
                    **content_trace_fields(changed_text=normalized_changed_text),
                },
                lambda: self.llm_client.chat_json(
                    system_prompt=INLINE_PREVIEW_SYSTEM_PROMPT,
                    user_prompt=prompt,
                    temperature=0.1,
                ),
                output=lambda value: {
                    "keys": sorted(str(key) for key in value.keys())[:25] if isinstance(value, dict) else [],
                    "inline_suggestion_count": len(value.get("inline_suggestions") or []) if isinstance(value, dict) else 0,
                },
            )
            suggestions = _trace_preview_node(
                metadata,
                "final_validator",
                {
                    "changed_text_hash": text_hash(normalized_changed_text),
                    "changed_text_length": len(normalized_changed_text),
                    "raw_suggestion_count": len(raw.get("inline_suggestions") or []) if isinstance(raw, dict) else 0,
                    **content_trace_fields(raw_inline_suggestions=raw.get("inline_suggestions") if isinstance(raw, dict) else None),
                },
                lambda: validate_inline_preview_response(raw, normalized_changed_text),
                output=lambda value: {
                    "suggestion_count": len(value),
                    **content_trace_fields(suggestions=value),
                },
            )
            deterministic_suggestions = _trace_preview_node(
                metadata,
                "deterministic_inline_coach",
                {
                    "changed_text_hash": text_hash(normalized_changed_text),
                    "changed_text_length": len(normalized_changed_text),
                },
                lambda: deterministic_inline_suggestions(normalized_changed_text),
                output=lambda value: {
                    "suggestion_count": len(value),
                    **content_trace_fields(suggestions=value),
                },
            )
            context_suggestions = _trace_preview_node(
                metadata,
                "context_inline_coach",
                {
                    "changed_text_hash": text_hash(normalized_changed_text),
                    "changed_text_length": len(normalized_changed_text),
                    "project_context_count": len(tool_results.get("suggest_related_projects") or []) if isinstance(tool_results, dict) else 0,
                },
                lambda: deterministic_context_suggestions(normalized_changed_text, tool_results),
                output=lambda value: {
                    "suggestion_count": len(value),
                    **content_trace_fields(suggestions=value),
                },
            )
            suggestions = _dedupe_inline_suggestions([
                *[
                    {
                        **suggestion,
                        "_start_index": normalized_changed_text.find(suggestion["target_text"]),
                        "_end_index": normalized_changed_text.find(suggestion["target_text"]) + len(suggestion["target_text"]),
                        "_correction_like": _is_correction_like(
                            suggestion["target_text"],
                            suggestion["suggested_replacement"],
                        ),
                    }
                    for suggestion in suggestions
                    if normalized_changed_text.find(suggestion["target_text"]) >= 0
                ],
                *deterministic_suggestions,
                *context_suggestions,
            ])
            result = _trace_preview_node(
                metadata,
                "final_response",
                {"suggestion_count": len(suggestions)},
                lambda: {
                    "text_hash": text_hash(normalized_changed_text),
                    "suggestions": suggestions,
                },
                output=lambda value: {
                    **_preview_trace_output(value, metadata),
                    **content_trace_fields(suggestions=value.get("suggestions", [])),
                },
            )
            _trace_inline_suggestions(
                metadata=metadata,
                review_id=review_id,
                review_text=review_text,
                review_text_hash=review_text_hash,
                normalized_changed_text=normalized_changed_text,
                suggestions=suggestions,
            )
            preview_start = metadata.pop("_preview_start_time", None)
            if preview_start is not None:
                metadata["total_latency_ms"] = round((time.perf_counter() - preview_start) * 1000, 2)
            trace_operation(
                INLINE_TRACE_SUMMARY,
                {
                    "text_hash": result.get("text_hash"),
                    "review_id": review_id,
                    "review_text_hash": review_text_hash,
                    "route": metadata.get("route"),
                    "selected_jobs": metadata.get("selected_jobs", []),
                    "node_count": len(metadata.get("nodes_executed") or []),
                    "tool_count": len(metadata.get("tools_called") or []),
                    "used_llm": metadata.get("used_llm", False),
                    "used_tools": metadata.get("used_tools", False),
                    **content_trace_fields(changed_text=normalized_changed_text, review_text=review_text),
                },
                lambda: metadata,
                output=lambda value: {
                    "nodes_executed": value.get("nodes_executed", []),
                    "route": value.get("route"),
                    "selected_jobs": value.get("selected_jobs", []),
                    "selected_tools": value.get("selected_tools", []),
                    "tool_reasons": value.get("tool_reasons", {}),
                    "project_candidates": value.get("project_candidates", []),
                    "tools_called": value.get("tools_called", []),
                    "steps": value.get("steps", []),
                    "latency_ms_by_node": value.get("latency_ms_by_node", {}),
                    "latency_ms_by_tool": value.get("latency_ms_by_tool", {}),
                    "latency_ms_by_job": value.get("latency_ms_by_job", {}),
                    "total_latency_ms": value.get("total_latency_ms"),
                    "used_llm": value.get("used_llm", False),
                    "used_tools": value.get("used_tools", False),
                    "error_count": len(value.get("errors") or []),
                    **content_trace_fields(suggestions=result.get("suggestions", [])),
                },
            )
            return result

        return trace_operation(
            INLINE_TRACE_ROOT,
            {
                "company_id": sender.organization_id,
                "sender": sender.name,
                "receiver": receiver.name,
                "channel": channel,
                "intent": intent,
                "full_draft_length": len(full_draft),
                "changed_text_hash": text_hash(raw_changed_text.strip()),
                "changed_text_length": len(raw_changed_text.strip()),
                "review_id": review_id,
                "review_text_hash": review_text_hash,
                "surrounding_context_length": len(surrounding_context),
                "prior_review_count": len(prior_review_context),
                "selected_tools": metadata.get("selected_tools", []),
                "selected_jobs": metadata.get("selected_jobs", []),
                "tool_reasons": metadata.get("tool_reasons", {}),
                "used_tools": metadata.get("used_tools", False),
                **content_trace_fields(
                    full_draft=full_draft,
                    changed_text=raw_changed_text.strip(),
                    review_text=review_text,
                    surrounding_context=surrounding_context,
                    prior_review_context=prior_review_context,
                ),
            },
            run_preview,
            output=lambda result: {
                **_preview_trace_output(result, metadata),
                **content_trace_fields(suggestions=result.get("suggestions", [])),
            },
        )

    def _run_context_tools(
        self,
        *,
        sender: Employee,
        receiver: Employee,
        message: str,
        selected_tools: list[str],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        if not selected_tools:
            return {}

        tools = CommunicationContextTools(
            company_id=sender.organization_id,
            receiver=receiver.id,
        )
        preference = _extract_preview_preference(message)
        has_explicit_preference = INLINE_PREVIEW_WRITE_INTENT_PATTERN.search(message) is not None
        results: dict[str, Any] = {}
        for tool_name in selected_tools:
            tool_job, default_reason = INLINE_TOOL_JOBS.get(tool_name, (tool_name, "selected_by_router"))
            tool_alias = INLINE_TOOL_TRACE_NAMES.get(tool_name, f"inline/tools/{tool_name}")
            tool_metrics: dict[str, Any] = {}

            def run_tool(tool_name=tool_name, tool_job=tool_job):
                started = time.perf_counter()
                try:
                    return tools.run(
                        tool_name,
                        message=message,
                        preference=preference,
                    )
                finally:
                    latency_ms = round((time.perf_counter() - started) * 1000, 2)
                    tool_metrics["latency_ms"] = latency_ms
                    metadata.setdefault("latency_ms_by_tool", {})[tool_name] = latency_ms
                    job_latencies = metadata.setdefault("latency_ms_by_job", {})
                    job_latencies[tool_job] = round(job_latencies.get(tool_job, 0) + latency_ms, 2)

            results[tool_name] = trace_operation(
                tool_alias,
                {
                    **tool_trace_inputs(
                        tool_name,
                        company_id=sender.organization_id,
                        receiver=receiver.id,
                        message=message,
                        has_preference=has_explicit_preference
                        and tool_name in {"save_company_pattern", "update_receiver_preference"},
                    ),
                    "trace_group": "tools",
                    "tool_alias": tool_alias,
                    "tool_job": tool_job,
                    "tool_reason": metadata.get("tool_reasons", {}).get(tool_name, default_reason),
                },
                run_tool,
                output=lambda result, tool_name=tool_name, tool_job=tool_job, default_reason=default_reason, tool_metrics=tool_metrics: {
                    **tool_trace_output(result),
                    "tool": tool_name,
                    "tool_job": tool_job,
                    "tool_reason": metadata.get("tool_reasons", {}).get(tool_name, default_reason),
                    "latency_ms": tool_metrics.get("latency_ms"),
                },
            )
        return results
