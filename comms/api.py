import json
from functools import wraps
from django.conf import settings
from django.http import JsonResponse, HttpRequest
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone
from comms.models import Organization, Team, Employee, Message, InlineSuggestion, ReceiverFeedback
from comms.services.inline_preview import InlineSuggestionPreviewer
from comms.services.message_analyzer import MessageAnalyzer, LLMResponseValidationError
from comms.services.llm_client import NebiusConfigurationError, NebiusRuntimeError
from comms.services.score_engine import set_suggestion_decision, recalculate_scores, apply_accepted_suggestions, sync_suggestion_decisions
from comms.services.feedback_processor import update_receiver_profile_from_feedback


def require_api_key(view_func):
    @wraps(view_func)
    def wrapper(request: HttpRequest, *args, **kwargs):
        api_key = settings.COMMS_API_KEY
        if not api_key:
            return JsonResponse({"error": "COMMS_API_KEY is not configured"}, status=500)

        provided = request.headers.get("X-API-Key", "")
        if provided != api_key:
            return JsonResponse({"error": "Invalid API key"}, status=401)

        return view_func(request, *args, **kwargs)

    return wrapper


def parse_json(request: HttpRequest) -> dict:
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON") from exc


def get_org_id(request: HttpRequest, payload: dict | None = None) -> int | None:
    payload = payload or {}
    header_value = request.headers.get("X-Org-Id")
    query_value = request.GET.get("org_id")
    body_value = payload.get("org_id")

    for value in [header_value, query_value, body_value]:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def serialize_org(org: Organization) -> dict:
    return {
        "id": org.id,
        "name": org.name,
        "description": org.description,
    }


def serialize_team(team: Team) -> dict:
    return {
        "id": team.id,
        "name": team.name,
        "description": team.description,
        "norms": team.norms,
    }


def serialize_employee(employee: Employee) -> dict:
    return {
        "id": employee.id,
        "name": employee.name,
        "role": employee.role,
        "team_id": employee.team_id,
        "team_name": employee.team.name if employee.team else None,
        "manager_id": employee.manager_id,
        "seniority_level": employee.seniority_level,
        "communication_preferences": employee.communication_preferences,
        "pain_points": employee.pain_points,
        "receiver_prompt": employee.receiver_prompt,
    }


def serialize_suggestion(suggestion: InlineSuggestion) -> dict:
    return {
        "id": suggestion.id,
        "external_id": suggestion.external_id,
        "target_text": suggestion.target_text,
        "start_index": suggestion.start_index,
        "end_index": suggestion.end_index,
        "issue": suggestion.issue,
        "suggested_replacement": suggestion.suggested_replacement,
        "reason": suggestion.reason,
        "affected_scores": suggestion.affected_scores,
        "org_values_used": suggestion.org_values_used,
        "decision": suggestion.decision,
    }


def serialize_message(message: Message) -> dict:
    return {
        "id": message.id,
        "organization_id": message.organization_id,
        "sender_id": message.sender_id,
        "receiver_id": message.receiver_id,
        "channel": message.channel,
        "intent": message.intent,
        "original_text": message.original_text,
        "final_text": message.final_text,
        "overall_suggested_message": message.overall_suggested_message,
        "subject_line": message.subject_line,
        "slack_short_version": message.slack_short_version,
        "teams_short_version": message.teams_short_version,
        "scores_before": message.scores_before,
        "estimated_scores_after_all": message.estimated_scores_after_all,
        "current_scores": message.current_scores,
        "risks": message.risks,
        "summary_of_changes": message.summary_of_changes,
        "explanation": message.explanation,
        "status": message.status,
        "accepted_suggestion_ids": message.accepted_suggestion_ids,
        "rejected_suggestion_ids": message.rejected_suggestion_ids,
        "created_at": message.created_at.isoformat(),
        "sent_at": message.sent_at.isoformat() if message.sent_at else None,
    }


@require_POST
def api_inline_suggestions_preview(request: HttpRequest, org_id: int):
    try:
        payload = parse_json(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    sender_id = payload.get("sender_id")
    receiver_id = payload.get("receiver_id")
    channel = payload.get("channel")
    intent = payload.get("intent")
    full_draft = payload.get("full_draft") or ""
    changed_text = (payload.get("changed_text") or "").strip()
    surrounding_context = payload.get("surrounding_context") or ""

    if not all([sender_id, receiver_id, channel, intent, full_draft, changed_text]):
        return JsonResponse({
            "error": "sender_id, receiver_id, channel, intent, full_draft, and changed_text are required"
        }, status=400)

    sender = get_object_or_404(Employee.objects.select_related("organization", "team"), pk=sender_id, organization_id=org_id)
    receiver = get_object_or_404(Employee.objects.select_related("organization", "team"), pk=receiver_id, organization_id=org_id)

    try:
        preview = InlineSuggestionPreviewer().preview(
            sender=sender,
            receiver=receiver,
            channel=channel,
            intent=intent,
            full_draft=full_draft,
            changed_text=changed_text,
            surrounding_context=surrounding_context,
        )
    except (NebiusConfigurationError, NebiusRuntimeError, LLMResponseValidationError, ValueError) as exc:
        return JsonResponse({"error": f"Inline preview failed: {exc}"}, status=400)

    return JsonResponse(preview)


@require_GET
@require_api_key
def api_list_orgs(request: HttpRequest):
    orgs = Organization.objects.all().order_by("name")
    return JsonResponse({"orgs": [serialize_org(org) for org in orgs]})


@require_GET
@require_api_key
def api_list_teams(request: HttpRequest, org_id: int):
    org = get_object_or_404(Organization, pk=org_id)
    teams = Team.objects.filter(organization=org).order_by("name")
    return JsonResponse({"organization": serialize_org(org), "teams": [serialize_team(t) for t in teams]})


@require_GET
@require_api_key
def api_list_employees(request: HttpRequest, org_id: int):
    org = get_object_or_404(Organization, pk=org_id)
    employees = Employee.objects.filter(organization=org).select_related("team", "manager").order_by("name")
    return JsonResponse({"organization": serialize_org(org), "employees": [serialize_employee(e) for e in employees]})


@require_GET
@require_api_key
def api_employee_detail(request: HttpRequest, employee_id: int):
    try:
        payload = parse_json(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    org_id = get_org_id(request, payload)
    if org_id is None:
        return JsonResponse({"error": "org_id is required"}, status=400)

    employee = get_object_or_404(Employee.objects.select_related("team"), pk=employee_id, organization_id=org_id)
    return JsonResponse({"employee": serialize_employee(employee)})


@require_POST
@require_api_key
def api_analyze_message(request: HttpRequest):
    try:
        payload = parse_json(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    org_id = get_org_id(request, payload)
    if org_id is None:
        return JsonResponse({"error": "org_id is required"}, status=400)

    sender_id = payload.get("sender_id")
    receiver_id = payload.get("receiver_id")
    channel = payload.get("channel")
    intent = payload.get("intent")
    original_message = (payload.get("original_message") or "").strip()

    if not all([sender_id, receiver_id, channel, intent, original_message]):
        return JsonResponse({"error": "sender_id, receiver_id, channel, intent, and original_message are required"}, status=400)

    sender = get_object_or_404(Employee, pk=sender_id, organization_id=org_id)
    receiver = get_object_or_404(Employee, pk=receiver_id, organization_id=org_id)

    try:
        message = MessageAnalyzer().analyze(
            sender=sender,
            receiver=receiver,
            channel=channel,
            intent=intent,
            original_message=original_message,
        )
    except (NebiusConfigurationError, NebiusRuntimeError, LLMResponseValidationError, ValueError) as exc:
        return JsonResponse({"error": f"Message analysis failed: {exc}"}, status=400)

    message.refresh_from_db()
    return JsonResponse({
        "message": serialize_message(message),
        "suggestions": [serialize_suggestion(s) for s in message.suggestions.all()],
    })


@require_GET
@require_api_key
def api_message_detail(request: HttpRequest, message_id: int):
    try:
        payload = parse_json(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    org_id = get_org_id(request, payload)
    if org_id is None:
        return JsonResponse({"error": "org_id is required"}, status=400)

    message = get_object_or_404(
        Message.objects.select_related("sender", "receiver"),
        pk=message_id,
        organization_id=org_id,
    )
    recalculate_scores(message)
    apply_accepted_suggestions(message)
    return JsonResponse({
        "message": serialize_message(message),
        "suggestions": [serialize_suggestion(s) for s in message.suggestions.all()],
        "revisions": [
            {
                "version_index": r.version_index,
                "text": r.text,
                "note": r.note,
                "created_at": r.created_at.isoformat(),
            }
            for r in message.revisions.all()
        ],
    })


@require_POST
@require_api_key
def api_suggestion_decision(request: HttpRequest, message_id: int, suggestion_id: int):
    try:
        payload = parse_json(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    org_id = get_org_id(request, payload)
    if org_id is None:
        return JsonResponse({"error": "org_id is required"}, status=400)

    decision = payload.get("decision")
    message = get_object_or_404(Message, pk=message_id, organization_id=org_id)
    suggestion = get_object_or_404(InlineSuggestion, pk=suggestion_id, message=message)

    try:
        set_suggestion_decision(suggestion, decision)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    message.refresh_from_db()
    return JsonResponse({
        "ok": True,
        "decision": suggestion.decision,
        "current_scores": message.current_scores,
        "final_text": message.final_text,
        "accepted_suggestion_ids": message.accepted_suggestion_ids,
        "rejected_suggestion_ids": message.rejected_suggestion_ids,
    })


@require_POST
@require_api_key
def api_bulk_suggestion_decision(request: HttpRequest, message_id: int):
    try:
        payload = parse_json(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    org_id = get_org_id(request, payload)
    if org_id is None:
        return JsonResponse({"error": "org_id is required"}, status=400)

    decision = payload.get("decision")
    if decision not in {InlineSuggestion.Decision.ACCEPTED, InlineSuggestion.Decision.REJECTED}:
        return JsonResponse({"error": "Invalid bulk decision"}, status=400)

    message = get_object_or_404(Message, pk=message_id, organization_id=org_id)
    message.suggestions.update(decision=decision, decided_at=timezone.now())
    recalculate_scores(message)
    apply_accepted_suggestions(message)
    sync_suggestion_decisions(message)

    message.refresh_from_db()
    return JsonResponse({
        "ok": True,
        "current_scores": message.current_scores,
        "final_text": message.final_text,
        "accepted_suggestion_ids": message.accepted_suggestion_ids,
        "rejected_suggestion_ids": message.rejected_suggestion_ids,
    })


@require_POST
@require_api_key
def api_receiver_feedback(request: HttpRequest, message_id: int):
    try:
        payload = parse_json(request)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    org_id = get_org_id(request, payload)
    if org_id is None:
        return JsonResponse({"error": "org_id is required"}, status=400)

    message = get_object_or_404(Message, pk=message_id, organization_id=org_id)
    receiver = message.receiver
    sender = message.sender

    feedback = ReceiverFeedback.objects.create(
        message=message,
        receiver=receiver,
        sender=sender,
        clear=bool(payload.get("clear")),
        too_direct=bool(payload.get("too_direct")),
        too_soft=bool(payload.get("too_soft")),
        too_long=bool(payload.get("too_long")),
        too_short=bool(payload.get("too_short")),
        missed_context=bool(payload.get("missed_context")),
        too_much_context=bool(payload.get("too_much_context")),
        unclear_ask=bool(payload.get("unclear_ask")),
        unclear_ownership=bool(payload.get("unclear_ownership")),
        not_aligned_with_preferences=bool(payload.get("not_aligned_with_preferences")),
        good_message=bool(payload.get("good_message")),
        free_text=(payload.get("free_text") or "").strip(),
    )

    update_receiver_profile_from_feedback(feedback)
    receiver.refresh_from_db()

    return JsonResponse({
        "ok": True,
        "feedback_id": feedback.id,
        "prompt_update_summary": feedback.prompt_update_summary,
        "receiver_prompt_before": feedback.receiver_prompt_before,
        "receiver_prompt_after": feedback.receiver_prompt_after,
        "receiver_profile": serialize_employee(receiver),
    })
