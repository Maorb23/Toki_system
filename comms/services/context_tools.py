from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from comms.models import Employee, MeetingContext, Message, Organization, ProjectContext, SystemEvent
from comms.services.event_log import log_event


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

STOPWORDS = {
    "about",
    "also",
    "and",
    "can",
    "could",
    "for",
    "from",
    "known",
    "let",
    "me",
    "new",
    "our",
    "regarding",
    "the",
    "this",
    "to",
    "want",
    "we",
    "what",
    "when",
    "with",
    "you",
}


def _compact_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def _normalize_lookup(value: str | int | None) -> str:
    return str(value or "").strip()


def _normalize_search_text(value: str) -> str:
    normalized = (value or "").lower()
    for typo, replacement in TYPO_NORMALIZATIONS.items():
        normalized = re.sub(rf"\b{re.escape(typo)}\b", replacement, normalized)
    return normalized


def _search_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", _normalize_search_text(value))
        if token not in STOPWORDS
    }


def _token_overlap_score(tokens: set[str], haystack: str) -> int:
    haystack_text = _normalize_search_text(haystack)
    haystack_tokens = _search_tokens(haystack_text)
    score = 0
    for token in tokens:
        if token in haystack_text or token in haystack_tokens:
            score += 1
            continue
        if any(SequenceMatcher(None, token, candidate).ratio() >= 0.84 for candidate in haystack_tokens):
            score += 1
    return score


def _organization_context_payload(org: Organization) -> dict[str, Any]:
    try:
        context = org.context
    except ObjectDoesNotExist:
        return {}

    return _compact_dict({
        "operating_context": context.operating_context,
        "current_priorities": context.current_priorities,
        "communication_patterns": context.communication_patterns,
        "customer_segments": context.customer_segments,
        "known_constraints": context.known_constraints,
    })


def _project_payload(project: ProjectContext) -> dict[str, Any]:
    return _compact_dict({
        "type": "project",
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "priority": project.priority,
        "quarter": project.quarter,
        "team": project.team.name if project.team else None,
        "owner": project.owner.name if project.owner else None,
        "goals": project.goals,
        "risks": project.risks,
        "dependencies": project.dependencies,
        "stakeholders": project.stakeholders,
        "updated_at": project.updated_at.isoformat() if project.updated_at else "",
    })


def _meeting_payload(meeting: MeetingContext) -> dict[str, Any]:
    return _compact_dict({
        "type": "meeting",
        "title": meeting.title,
        "meeting_type": meeting.meeting_type,
        "cadence": meeting.cadence,
        "status": meeting.status,
        "team": meeting.team.name if meeting.team else None,
        "owner": meeting.owner.name if meeting.owner else None,
        "participants": meeting.participants,
        "related_projects": meeting.related_projects,
        "summary": meeting.summary,
        "decisions": meeting.decisions,
        "open_questions": meeting.open_questions,
        "action_items": meeting.action_items,
        "updated_at": meeting.updated_at.isoformat() if meeting.updated_at else "",
    })


def get_company_context(company_id: int | str | None) -> dict[str, Any]:
    """Return stable organization context without loading large histories."""
    if not company_id:
        return {}
    return _get_company_context_cached(str(company_id), _company_fingerprint(company_id))


@lru_cache(maxsize=128)
def _get_company_context_cached(company_id: str, fingerprint: tuple[Any, ...]) -> dict[str, Any]:
    org = Organization.objects.prefetch_related("values", "teams").filter(pk=company_id).first()
    if org is None:
        return {}

    return {
        "id": org.id,
        "name": org.name,
        "description": org.description,
        "context": _organization_context_payload(org),
        "values": [
            _compact_dict({"name": value.name, "description": value.description})
            for value in org.values.all()
        ],
        "teams": [
            _compact_dict({
                "name": team.name,
                "description": team.description,
                "norms": team.norms,
            })
            for team in org.teams.all()
        ],
    }


def get_receiver_profile(receiver: str | int | None, company_id: int | str | None) -> dict[str, Any]:
    """Return the receiver profile used by the rewrite prompt."""
    if not receiver or not company_id:
        return {}
    return _get_receiver_profile_cached(
        _normalize_lookup(receiver),
        str(company_id),
        _receiver_fingerprint(receiver, company_id),
    )


@lru_cache(maxsize=512)
def _get_receiver_profile_cached(lookup: str, company_id: str, fingerprint: tuple[Any, ...]) -> dict[str, Any]:
    qs = Employee.objects.select_related("team").filter(organization_id=company_id)
    employee = qs.filter(pk=lookup).first() if lookup.isdigit() else None
    if employee is None:
        employee = qs.filter(name__iexact=lookup).first()
    if employee is None:
        return {}

    return _compact_dict({
        "id": employee.id,
        "name": employee.name,
        "role": employee.role,
        "team": employee.team.name if employee.team else None,
        "communication_preferences": employee.communication_preferences,
        "pain_points": employee.pain_points,
        "receiver_prompt": employee.receiver_prompt,
    })


def retrieve_company_patterns(company_id: int | str | None) -> list[dict[str, Any]]:
    """Return saved communication patterns from explicit agent tool writes."""
    if not company_id:
        return []
    return _retrieve_company_patterns_cached(str(company_id), _patterns_fingerprint(company_id))


@lru_cache(maxsize=128)
def _retrieve_company_patterns_cached(company_id: str, fingerprint: tuple[Any, ...]) -> list[dict[str, Any]]:
    events = SystemEvent.objects.filter(
        organization_id=company_id,
        event_type="communication_pattern.saved",
        status="success",
    ).order_by("-created_at")[:10]

    return [
        _compact_dict({
            "pattern": event.payload.get("pattern", ""),
            "created_at": event.created_at.isoformat(),
        })
        for event in events
    ]


def suggest_related_projects(
    message: str,
    company_id: int | str | None,
    receiver: str | int | None = None,
) -> list[dict[str, Any]]:
    """
    Lightweight project-like hints from existing teams and recent messages.

    There is no Project model in the POC, so this keeps the tool boundary ready
    without inventing a new persistence model.
    """
    if not company_id:
        return []

    text = _normalize_search_text(message or "")
    tokens = _search_tokens(text)
    if not tokens:
        return []
    mentions_project = bool(re.search(r"\b(project|initiative|workstream)\b", text, re.I))
    receiver_profile = get_receiver_profile(receiver, company_id) if receiver else {}
    receiver_name = _normalize_search_text(receiver_profile.get("name") or "")
    receiver_team = _normalize_search_text(receiver_profile.get("team") or "")

    matches: list[dict[str, Any]] = []
    projects = ProjectContext.objects.select_related("team", "owner").filter(
        organization_id=company_id,
    ).exclude(status=ProjectContext.Status.DONE)

    for project in projects:
        haystack = " ".join([
            project.name,
            project.description,
            project.status,
            project.priority,
            project.quarter,
            project.team.name if project.team else "",
            project.owner.name if project.owner else "",
            " ".join(project.goals or []),
            " ".join(project.risks or []),
            " ".join(project.dependencies or []),
            " ".join(project.stakeholders or []),
        ])
        score = _token_overlap_score(tokens, haystack)
        normalized_haystack = _normalize_search_text(haystack)
        if mentions_project and receiver_name and receiver_name in normalized_haystack:
            score += 2
        if mentions_project and receiver_team and receiver_team in normalized_haystack:
            score += 1
        if score:
            payload = _project_payload(project)
            payload["score"] = score
            if mentions_project and receiver_name and receiver_name in normalized_haystack:
                payload["fallback_reason"] = "vague_project_reference_for_receiver"
            matches.append(payload)

    if matches:
        return sorted(matches, key=lambda item: item.get("score", 0), reverse=True)[:5]

    org = Organization.objects.prefetch_related("teams").filter(pk=company_id).first()
    if org:
        for team in org.teams.all():
            haystack = " ".join([team.name, team.description, " ".join(team.norms or [])])
            score = _token_overlap_score(tokens, haystack)
            if score:
                matches.append({
                    "type": "team_context",
                    "name": team.name,
                    "score": score,
                    "description": team.description,
                    "norms": team.norms,
                })

    recent_messages = Message.objects.filter(organization_id=company_id).order_by("-created_at")[:25]
    for prior in recent_messages:
        haystack = " ".join([
            prior.intent or "",
            prior.original_text or "",
            prior.summary_of_changes or "",
        ])
        score = _token_overlap_score(tokens, haystack)
        if score >= 2:
            matches.append({
                "type": "recent_message",
                "message_id": prior.id,
                "intent": prior.intent,
                "score": score,
                "created_at": prior.created_at.isoformat(),
            })

    return sorted(matches, key=lambda item: item.get("score", 0), reverse=True)[:5]


def suggest_meeting_context(
    message: str,
    receiver: str | int | None,
    company_id: int | str | None,
) -> dict[str, Any]:
    """Return a small summary of recent sender/receiver context."""
    if not company_id or not receiver:
        return {}

    profile = get_receiver_profile(receiver, company_id)
    if not profile:
        return {}

    meeting_matches = []
    meetings = MeetingContext.objects.select_related("team", "owner").filter(
        organization_id=company_id,
    ).exclude(status=MeetingContext.Status.PAUSED)

    text = _normalize_search_text(message or "")
    tokens = _search_tokens(text)
    receiver_name = (profile.get("name") or "").lower()
    mentions_meeting = bool(re.search(r"\b(meeting|sync|1:1|one-on-one|talk|call)\b", text, re.I))
    mentions_schedule = bool(re.search(r"\b(schedule|calendar|deadline|roadmap|q[1-4]|review|tuesday|monday|wednesday|thursday|friday|date|time)\b", text, re.I))
    for meeting in meetings:
        haystack = " ".join([
            meeting.title,
            meeting.meeting_type,
            meeting.cadence,
            meeting.summary,
            meeting.team.name if meeting.team else "",
            meeting.owner.name if meeting.owner else "",
            " ".join(meeting.participants or []),
            " ".join(meeting.related_projects or []),
            " ".join(meeting.decisions or []),
            " ".join(meeting.open_questions or []),
            " ".join(str(item) for item in meeting.action_items or []),
        ])
        score = _token_overlap_score(tokens, haystack)
        if receiver_name and receiver_name in _normalize_search_text(haystack):
            score += 2
        if score:
            payload = _meeting_payload(meeting)
            payload["score"] = score
            meeting_matches.append(payload)

    if mentions_meeting and not meeting_matches:
        for meeting in meetings[:5]:
            haystack = _normalize_search_text(" ".join([
                meeting.owner.name if meeting.owner else "",
                " ".join(meeting.participants or []),
                meeting.team.name if meeting.team else "",
            ]))
            if receiver_name and receiver_name in haystack:
                payload = _meeting_payload(meeting)
                payload["score"] = 1
                payload["fallback_reason"] = "vague_meeting_reference_for_receiver"
                meeting_matches.append(payload)

    if (mentions_meeting or mentions_schedule) and not meeting_matches:
        for meeting in meetings[:5]:
            payload = _meeting_payload(meeting)
            payload["score"] = 0
            payload["fallback_reason"] = "vague_meeting_reference"
            meeting_matches.append(payload)

    recent = Message.objects.filter(
        organization_id=company_id,
        receiver_id=profile["id"],
    ).order_by("-created_at")[:5]

    return {
        "receiver": profile.get("name"),
        "relevant_meetings": sorted(meeting_matches, key=lambda item: item.get("score", 0), reverse=True)[:5],
        "recent_messages": [
            _compact_dict({
                "message_id": item.id,
                "intent": item.intent,
                "channel": item.channel,
                "summary": item.summary_of_changes,
                "created_at": item.created_at.isoformat(),
            })
            for item in recent
        ],
        "message_signals": {
            "mentions_meeting": mentions_meeting,
            "mentions_schedule": mentions_schedule,
        },
    }


def save_company_pattern(company_id: int | str | None, pattern: str) -> dict[str, Any]:
    """Persist an explicit organization-level pattern through the event log."""
    pattern = (pattern or "").strip()
    if not company_id or not pattern:
        return {"status": "skipped", "reason": "missing_company_or_pattern"}

    org = Organization.objects.filter(pk=company_id).first()
    if org is None:
        return {"status": "skipped", "reason": "company_not_found"}

    log_event(
        "communication_pattern.saved",
        organization=org,
        source="agent_tool",
        payload={"pattern": pattern, "saved_at": timezone.now().isoformat()},
    )
    _retrieve_company_patterns_cached.cache_clear()
    return {"status": "saved", "pattern": pattern}


def update_receiver_preference(
    receiver: str | int | None,
    company_id: int | str | None,
    preference: str,
) -> dict[str, Any]:
    """Append an explicit receiver preference while avoiding duplicates."""
    preference = (preference or "").strip()
    if not receiver or not company_id or not preference:
        return {"status": "skipped", "reason": "missing_receiver_company_or_preference"}

    profile = get_receiver_profile(receiver, company_id)
    if not profile:
        return {"status": "skipped", "reason": "receiver_not_found"}

    employee = Employee.objects.get(pk=profile["id"], organization_id=company_id)
    prefs = dict(employee.communication_preferences or {})
    saved = list(prefs.get("agent_saved_preferences") or [])
    if preference not in saved:
        saved.append(preference)
    prefs["agent_saved_preferences"] = saved
    employee.communication_preferences = prefs
    employee.save(update_fields=["communication_preferences", "updated_at"])

    log_event(
        "receiver_preference.updated",
        organization=employee.organization,
        receiver=employee,
        source="agent_tool",
        payload={"preference": preference},
    )
    _get_receiver_profile_cached.cache_clear()
    return {"status": "saved", "receiver": employee.name, "preference": preference}


class CommunicationContextTools:
    """Small tool facade used by the LangGraph context node."""

    def __init__(self, *, company_id: int | str | None, receiver: str | int | None) -> None:
        self.company_id = company_id
        self.receiver = receiver

    def run(self, tool_name: str, *, message: str, preference: str = "") -> Any:
        if tool_name == "get_company_context":
            return get_company_context(self.company_id)
        if tool_name == "get_receiver_profile":
            return get_receiver_profile(self.receiver, self.company_id)
        if tool_name == "retrieve_company_patterns":
            return retrieve_company_patterns(self.company_id)
        if tool_name == "suggest_related_projects":
            return suggest_related_projects(message, self.company_id, self.receiver)
        if tool_name == "suggest_meeting_context":
            return suggest_meeting_context(message, self.receiver, self.company_id)
        if tool_name == "save_company_pattern":
            return save_company_pattern(self.company_id, preference or message)
        if tool_name == "update_receiver_preference":
            return update_receiver_preference(self.receiver, self.company_id, preference)
        return {"status": "skipped", "reason": "unknown_tool"}


def _company_fingerprint(company_id: int | str | None) -> tuple[Any, ...]:
    org = Organization.objects.filter(pk=company_id).first()
    if org is None:
        return ()
    try:
        org_context = org.context
    except ObjectDoesNotExist:
        org_context = None
    return (
        org.name,
        org.description,
        org_context.updated_at.isoformat() if org_context else "",
        org.values.count(),
        org.teams.count(),
        org.projects.count(),
        org.meeting_contexts.count(),
    )


def _receiver_fingerprint(receiver: str | int | None, company_id: int | str | None) -> tuple[Any, ...]:
    lookup = _normalize_lookup(receiver)
    qs = Employee.objects.filter(organization_id=company_id)
    employee = qs.filter(pk=lookup).first() if lookup.isdigit() else None
    if employee is None:
        employee = qs.filter(name__iexact=lookup).first()
    if employee is None:
        return ()
    return (
        employee.id,
        employee.name,
        employee.updated_at.isoformat() if employee.updated_at else "",
    )


def _patterns_fingerprint(company_id: int | str | None) -> tuple[Any, ...]:
    events = SystemEvent.objects.filter(
        organization_id=company_id,
        event_type="communication_pattern.saved",
        status="success",
    )
    latest = events.order_by("-created_at").values_list("created_at", flat=True).first()
    return (events.count(), latest.isoformat() if latest else "")
