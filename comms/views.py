import json
from django.contrib import messages
from django.http import JsonResponse, HttpRequest, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from comms.models import Organization, Employee, Message, InlineSuggestion, ReceiverFeedback
from comms.services.feedback_processor import update_receiver_profile_from_feedback
from comms.services.llm_client import NebiusConfigurationError, NebiusRuntimeError
from comms.services.message_analyzer import MessageAnalyzer, LLMResponseValidationError
from comms.services.score_engine import set_suggestion_decision, recalculate_scores, apply_accepted_suggestions, sync_suggestion_decisions


def _get_default_org() -> Organization | None:
    return Organization.objects.prefetch_related("values", "teams", "employees").first()

def dashboard(request: HttpRequest):
    org = _get_default_org()
    if not org:
        return render(request, "comms/empty.html")

    messages_qs = Message.objects.filter(organization=org).order_by("-created_at")[:8]
    avg_scores = {
        "clarity": 0,
        "tone": 0,
        "receiver_fit": 0,
        "org_values_alignment": 0,
    }
    scored_messages = Message.objects.filter(organization=org).exclude(current_scores={})
    if scored_messages.exists():
        totals = {key: [] for key in avg_scores}
        for m in scored_messages:
            for key in totals:
                if key in (m.current_scores or {}):
                    totals[key].append(m.current_scores[key])
        avg_scores = {
            key: int(sum(values) / len(values)) if values else 0
            for key, values in totals.items()
        }

    return render(request, "comms/dashboard.html", {
        "org": org,
        "recent_messages": messages_qs,
        "avg_scores": avg_scores,
    })

def org_graph(request: HttpRequest):
    org = _get_default_org()
    if not org:
        return render(request, "comms/empty.html")

    teams = org.teams.prefetch_related("employees").all()
    employees = Employee.objects.filter(organization=org).select_related("team", "manager")
    return render(request, "comms/org_graph.html", {
        "org": org,
        "teams": teams,
        "employees_json": json.dumps([
            {
                "id": e.id,
                "name": e.name,
                "role": e.role,
                "team": e.team.name if e.team else "",
                "manager_id": e.manager_id,
                "url": reverse("comms:employee_detail", args=[e.id]),
            }
            for e in employees
        ]),
    })

def employee_detail(request: HttpRequest, employee_id: int):
    employee = get_object_or_404(Employee.objects.select_related("team", "manager", "organization"), pk=employee_id)

    if request.method == "POST":
        employee.receiver_prompt = request.POST.get("receiver_prompt", "").strip()
        style = request.POST.get("style", "").strip()
        detail = request.POST.get("detail", "").strip()
        structure = request.POST.get("structure", "").strip()

        prefs = dict(employee.communication_preferences or {})
        if style:
            prefs["style"] = style
        if detail:
            prefs["detail"] = detail
        if structure:
            prefs["structure"] = structure
        employee.communication_preferences = prefs
        employee.save(update_fields=["receiver_prompt", "communication_preferences", "updated_at"])
        messages.success(request, "Employee communication profile updated.")
        return redirect("comms:employee_detail", employee_id=employee.id)

    feedback = ReceiverFeedback.objects.filter(receiver=employee).order_by("-created_at")[:10]
    sent_messages = Message.objects.filter(sender=employee).order_by("-created_at")[:6]
    received_messages = Message.objects.filter(receiver=employee).order_by("-created_at")[:6]
    return render(request, "comms/employee_detail.html", {
        "employee": employee,
        "feedback": feedback,
        "sent_messages": sent_messages,
        "received_messages": received_messages,
    })

def workspace(request: HttpRequest):
    org = _get_default_org()
    if not org:
        return render(request, "comms/empty.html")

    employees = Employee.objects.filter(organization=org).select_related("team").order_by("team__name", "name")

    if request.method == "POST":
        sender = get_object_or_404(Employee, pk=request.POST.get("sender_id"), organization=org)
        receiver = get_object_or_404(Employee, pk=request.POST.get("receiver_id"), organization=org)
        channel = request.POST.get("channel")
        intent = request.POST.get("intent")
        original_message = request.POST.get("original_message", "").strip()

        if not original_message:
            messages.error(request, "Draft message is required.")
            return redirect("comms:workspace")

        try:
            message = MessageAnalyzer().analyze(
                sender=sender,
                receiver=receiver,
                channel=channel,
                intent=intent,
                original_message=original_message,
            )
        except (NebiusConfigurationError, NebiusRuntimeError, LLMResponseValidationError, ValueError) as exc:
            messages.error(request, f"Message analysis failed: {exc}")
            return render(request, "comms/workspace.html", {
                "org": org,
                "employees": employees,
                "channels": Message.Channel.choices,
                "intents": Message.Intent.choices,
                "analysis_error": str(exc),
                "form_data": request.POST,
            })

        return redirect("comms:message_detail", message_id=message.id)

    return render(request, "comms/workspace.html", {
        "org": org,
        "employees": employees,
        "channels": Message.Channel.choices,
        "intents": Message.Intent.choices,
    })

def message_detail(request: HttpRequest, message_id: int):
    message = get_object_or_404(
        Message.objects.select_related("sender", "receiver", "organization").prefetch_related("suggestions", "revisions"),
        pk=message_id,
    )
    recalculate_scores(message)
    apply_accepted_suggestions(message)
    return render(request, "comms/message_detail.html", {
        "message": message,
        "revisions": message.revisions.all(),
        "suggestions_json": json.dumps([
            {
                "id": s.id,
                "target_text": s.target_text,
                "start_index": s.start_index,
                "end_index": s.end_index,
                "issue": s.issue,
                "suggested_replacement": s.suggested_replacement,
                "reason": s.reason,
                "decision": s.decision,
                "affected_scores": s.affected_scores,
                "org_values_used": s.org_values_used,
            }
            for s in message.suggestions.all()
        ]),
    })

@require_POST
def suggestion_decision(request: HttpRequest, message_id: int, suggestion_id: int):
    message = get_object_or_404(Message, pk=message_id)
    suggestion = get_object_or_404(InlineSuggestion, pk=suggestion_id, message=message)

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    decision = payload.get("decision")
    try:
        set_suggestion_decision(suggestion, decision)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))

    message.refresh_from_db()
    return JsonResponse({
        "ok": True,
        "decision": suggestion.decision,
        "current_scores": message.current_scores,
        "final_text": message.final_text,
    })

@require_POST
def bulk_suggestion_decision(request: HttpRequest, message_id: int):
    message = get_object_or_404(Message, pk=message_id)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")

    decision = payload.get("decision")
    if decision not in {InlineSuggestion.Decision.ACCEPTED, InlineSuggestion.Decision.REJECTED}:
        return HttpResponseBadRequest("Invalid bulk decision")

    message.suggestions.update(decision=decision, decided_at=timezone.now())
    recalculate_scores(message)
    apply_accepted_suggestions(message)
    sync_suggestion_decisions(message)

    message.refresh_from_db()
    return JsonResponse({
        "ok": True,
        "current_scores": message.current_scores,
        "final_text": message.final_text,
    })

@require_POST
def mark_message_sent(request: HttpRequest, message_id: int):
    message = get_object_or_404(Message, pk=message_id)
    apply_accepted_suggestions(message)
    recalculate_scores(message)
    message.status = Message.Status.SENT
    message.sent_at = timezone.now()
    message.save(update_fields=["status", "sent_at", "final_text", "current_scores"])
    messages.success(request, "Message marked as sent/received in the POC. Receiver can now provide feedback.")
    return redirect("comms:receiver_feedback", message_id=message.id)

def receiver_feedback(request: HttpRequest, message_id: int):
    message = get_object_or_404(Message.objects.select_related("sender", "receiver"), pk=message_id)

    if request.method == "POST":
        feedback = ReceiverFeedback.objects.create(
            message=message,
            receiver=message.receiver,
            sender=message.sender,
            clear=bool(request.POST.get("clear")),
            too_direct=bool(request.POST.get("too_direct")),
            too_soft=bool(request.POST.get("too_soft")),
            too_long=bool(request.POST.get("too_long")),
            too_short=bool(request.POST.get("too_short")),
            missed_context=bool(request.POST.get("missed_context")),
            too_much_context=bool(request.POST.get("too_much_context")),
            unclear_ask=bool(request.POST.get("unclear_ask")),
            unclear_ownership=bool(request.POST.get("unclear_ownership")),
            not_aligned_with_preferences=bool(request.POST.get("not_aligned_with_preferences")),
            good_message=bool(request.POST.get("good_message")),
            free_text=request.POST.get("free_text", "").strip(),
        )
        update_receiver_profile_from_feedback(feedback)
        messages.success(request, "Receiver feedback saved and receiver profile updated.")
        return redirect("comms:employee_detail", employee_id=message.receiver_id)

    return render(request, "comms/receiver_feedback.html", {
        "message": message,
    })
