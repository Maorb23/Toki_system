import json
from django.contrib import messages
from django.http import JsonResponse, HttpRequest, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_POST

from comms.models import Organization, Employee, Message, InlineSuggestion, ReceiverFeedback
from comms.services.event_log import log_event
from comms.services.gmail_demo import analyze_gmail_draft
from comms.services.llm_client import NebiusConfigurationError, NebiusRuntimeError
from comms.services.message_analyzer import MessageAnalyzer, LLMResponseValidationError
from comms.services.score_engine import SCORE_KEYS, clamp_score, set_suggestion_decision, recalculate_scores, apply_accepted_suggestions, sync_suggestion_decisions

EMPLOYEE_MODE_PERSONAS = [
    {"name": "Rina Tal", "role": "Customer Success Manager"},
    {"name": "Dana Weiss", "role": "Backend Engineer"},
    {"name": "Noam Bar", "role": "VP Engineering"},
    {"name": "Ari Cohen", "role": "CEO"},
]

EMPLOYEE_MODE_PERSONAS_BY_ORG = {
    "Northstar Labs": EMPLOYEE_MODE_PERSONAS,
    "The Office": [
        {"name": "Michael Scott", "role": "Regional Manager"},
        {"name": "Pam Beesly", "role": "Office Administrator"},
        {"name": "Jim Halpert", "role": "Sales Lead"},
        {"name": "Oscar Martinez", "role": "Senior Data Scientist"},
    ],
}


def _get_current_org(request: HttpRequest) -> Organization | None:
    orgs = Organization.objects.prefetch_related("values", "teams", "employees")
    org_id = request.session.get("selected_org_id")
    org = orgs.filter(pk=org_id).first() if org_id else None
    if org:
        return org

    org = orgs.filter(name="Northstar Labs").first() or orgs.order_by("id").first()
    if org:
        request.session["selected_org_id"] = org.id
    return org


def _org_options() -> list[Organization]:
    preferred = {"Northstar Labs": 0, "The Office": 1}
    return sorted(
        Organization.objects.all(),
        key=lambda org: (preferred.get(org.name, 2), org.name),
    )

def _get_current_employee(request: HttpRequest) -> Employee | None:
    org = _get_current_org(request)
    if not org:
        return None
    employee_id = request.session.get("employee_mode_employee_id")
    if not employee_id:
        return None
    return Employee.objects.select_related("team", "manager", "organization").filter(
        pk=employee_id,
        organization=org,
    ).first()

def _persona_options(org: Organization | None) -> list[dict]:
    if not org:
        return []

    personas = EMPLOYEE_MODE_PERSONAS_BY_ORG.get(org.name) or [
        {"name": employee.name, "role": employee.role}
        for employee in Employee.objects.filter(organization=org).order_by("team__name", "name")[:4]
    ]
    employees = {
        employee.name: employee
        for employee in Employee.objects.filter(
            organization=org,
            name__in=[persona["name"] for persona in personas],
        ).select_related("team")
    }
    return [
        {
            "employee": employees.get(persona["name"]),
            "name": persona["name"],
            "role": persona["role"],
        }
        for persona in personas
    ]

def select_org(request: HttpRequest, org_id: int):
    org = get_object_or_404(Organization, pk=org_id)
    previous_org_id = request.session.get("selected_org_id")
    request.session["selected_org_id"] = org.id
    if previous_org_id != org.id:
        request.session.pop("employee_mode_employee_id", None)
    messages.success(request, f"Switched to {org.name}.")

    next_url = request.GET.get("next") or reverse("comms:mode_select")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = reverse("comms:mode_select")
    return redirect(next_url)

def mode_select(request: HttpRequest):
    org = _get_current_org(request)
    if not org:
        return render(request, "comms/empty.html")

    return render(request, "comms/mode_select.html", {
        "org": org,
        "orgs": _org_options(),
        "personas": _persona_options(org),
        "employees": Employee.objects.filter(organization=org).select_related("team").order_by("team__name", "name"),
        "current_employee": _get_current_employee(request),
    })

def dashboard(request: HttpRequest):
    org = _get_current_org(request)
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
        "current_employee": _get_current_employee(request),
    })

def employee_sign_in(request: HttpRequest, employee_id: int):
    org = _get_current_org(request)
    employee = get_object_or_404(Employee, pk=employee_id, organization=org)
    request.session["employee_mode_employee_id"] = employee.id
    messages.success(request, f"Continuing as {employee.name}.")
    return redirect("comms:employee_home")

def employee_sign_out(request: HttpRequest):
    request.session.pop("employee_mode_employee_id", None)
    messages.success(request, "Employee mode ended.")
    return redirect("comms:mode_select")

def employee_home(request: HttpRequest):
    employee = _get_current_employee(request)
    if not employee:
        messages.error(request, "Choose an employee to continue.")
        return redirect("comms:mode_select")

    sent_messages = Message.objects.filter(sender=employee).select_related("receiver").order_by("-created_at")[:8]
    received_messages = Message.objects.filter(receiver=employee).select_related("sender").order_by("-created_at")[:8]
    feedback = ReceiverFeedback.objects.filter(receiver=employee).order_by("-created_at")[:5]
    return render(request, "comms/employee_home.html", {
        "org": employee.organization,
        "employee": employee,
        "sent_messages": sent_messages,
        "received_messages": received_messages,
        "feedback": feedback,
        "current_employee": employee,
    })

def org_graph(request: HttpRequest):
    org = _get_current_org(request)
    if not org:
        return render(request, "comms/empty.html")

    teams = org.teams.prefetch_related("employees").all()
    employees = Employee.objects.filter(organization=org).select_related("team", "manager")
    return render(request, "comms/org_graph.html", {
        "org": org,
        "teams": teams,
        "current_employee": _get_current_employee(request),
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
    org = _get_current_org(request)
    employee = get_object_or_404(
        Employee.objects.select_related("team", "manager", "organization"),
        pk=employee_id,
        organization=org,
    )

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
        "org": org,
        "employee": employee,
        "feedback": feedback,
        "sent_messages": sent_messages,
        "received_messages": received_messages,
        "current_employee": _get_current_employee(request),
    })

def workspace(request: HttpRequest):
    org = _get_current_org(request)
    if not org:
        return render(request, "comms/empty.html")

    employees = Employee.objects.filter(organization=org).select_related("team").order_by("team__name", "name")
    return _workspace(request, org=org, employees=employees, sender=None, template="comms/workspace.html")

def employee_workspace(request: HttpRequest):
    org = _get_current_org(request)
    employee = _get_current_employee(request)
    if not org:
        return render(request, "comms/empty.html")
    if not employee:
        messages.error(request, "Choose an employee to continue.")
        return redirect("comms:mode_select")

    employees = Employee.objects.filter(organization=org).exclude(pk=employee.pk).select_related("team").order_by("team__name", "name")
    return _workspace(
        request,
        org=org,
        employees=employees,
        sender=employee,
        template="comms/employee_workspace.html",
    )

def _parse_lightweight_scores(raw_scores: str) -> dict:
    fallback = {
        "clarity": 80,
        "tone": 90,
        "receiver_fit": 70,
        "org_values_alignment": 80,
    }
    try:
        scores = json.loads(raw_scores or "{}")
    except json.JSONDecodeError:
        return fallback

    if not isinstance(scores, dict):
        return fallback

    return {
        key: clamp_score(scores.get(key, fallback[key]))
        for key in SCORE_KEYS
    }

def _workspace(request: HttpRequest, *, org: Organization, employees, sender: Employee | None, template: str):
    if request.method == "POST":
        selected_sender = sender or get_object_or_404(Employee, pk=request.POST.get("sender_id"), organization=org)
        receiver = get_object_or_404(Employee, pk=request.POST.get("receiver_id"), organization=org)
        channel = request.POST.get("channel")
        intent = request.POST.get("intent")
        original_message = request.POST.get("original_message", "").strip()

        if not original_message:
            messages.error(request, "Draft message is required.")
            return redirect("comms:employee_workspace" if sender else "comms:workspace")

        if request.POST.get("suggestion_mode") == "lightweight" and request.POST.get("compose_action") == "send":
            scores = _parse_lightweight_scores(request.POST.get("lightweight_scores", ""))
            message = Message.objects.create(
                organization=org,
                sender=selected_sender,
                receiver=receiver,
                channel=channel,
                intent=intent,
                original_text=original_message,
                final_text=original_message,
                scores_before=scores,
                current_scores=scores,
                status=Message.Status.SENT,
                sent_at=timezone.now(),
            )
            log_event(
                "message.sent",
                message=message,
                payload={"mode": "lightweight", "channel": channel, "intent": intent},
            )
            messages.success(request, "Message marked as sent/received in the POC. Receiver can now provide feedback.")
            return redirect("comms:receiver_feedback", message_id=message.id)

        try:
            message = MessageAnalyzer().analyze(
                sender=selected_sender,
                receiver=receiver,
                channel=channel,
                intent=intent,
                original_message=original_message,
            )
        except (NebiusConfigurationError, NebiusRuntimeError, LLMResponseValidationError, ValueError) as exc:
            messages.error(request, f"Message analysis failed: {exc}")
            return render(request, template, {
                "org": org,
                "employees": employees,
                "sender": sender,
                "channels": Message.Channel.choices,
                "intents": Message.Intent.choices,
                "analysis_error": str(exc),
                "form_data": request.POST,
                "current_employee": sender,
            })

        return redirect("comms:message_detail", message_id=message.id)

    return render(request, template, {
        "org": org,
        "employees": employees,
        "sender": sender,
        "channels": Message.Channel.choices,
        "intents": Message.Intent.choices,
        "current_employee": sender,
    })

def message_detail(request: HttpRequest, message_id: int):
    org = _get_current_org(request)
    message = get_object_or_404(
        Message.objects.select_related("sender", "receiver", "organization").prefetch_related("suggestions", "revisions"),
        pk=message_id,
        organization=org,
    )
    recalculate_scores(message)
    apply_accepted_suggestions(message)
    return render(request, "comms/message_detail.html", {
        "org": message.organization,
        "message": message,
        "revisions": message.revisions.all(),
        "current_employee": _get_current_employee(request),
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
    org = _get_current_org(request)
    message = get_object_or_404(Message, pk=message_id, organization=org)
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
    log_event(
        f"suggestion.{suggestion.decision}",
        message=message,
        payload={"suggestion_id": suggestion.id, "decision": suggestion.decision},
    )
    return JsonResponse({
        "ok": True,
        "decision": suggestion.decision,
        "current_scores": message.current_scores,
        "final_text": message.final_text,
    })

@require_POST
def bulk_suggestion_decision(request: HttpRequest, message_id: int):
    org = _get_current_org(request)
    message = get_object_or_404(Message, pk=message_id, organization=org)
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
    log_event(
        f"bulk_suggestions.{decision}",
        message=message,
        payload={"decision": decision, "suggestion_count": message.suggestions.count()},
    )
    return JsonResponse({
        "ok": True,
        "current_scores": message.current_scores,
        "final_text": message.final_text,
    })

@require_POST
def mark_message_sent(request: HttpRequest, message_id: int):
    org = _get_current_org(request)
    message = get_object_or_404(Message, pk=message_id, organization=org)
    apply_accepted_suggestions(message)
    recalculate_scores(message)
    message.status = Message.Status.SENT
    message.sent_at = timezone.now()
    message.save(update_fields=["status", "sent_at", "final_text", "current_scores"])
    log_event("message.sent", message=message, payload={"channel": message.channel, "intent": message.intent})
    messages.success(request, "Message marked as sent/received in the POC. Receiver can now provide feedback.")
    return redirect("comms:receiver_feedback", message_id=message.id)

def receiver_feedback(request: HttpRequest, message_id: int):
    org = _get_current_org(request)
    message = get_object_or_404(
        Message.objects.select_related("sender", "receiver"),
        pk=message_id,
        organization=org,
    )

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
        feedback.prompt_update_summary = "Feedback saved with message. Receiver profile was not updated."
        feedback.save(update_fields=["prompt_update_summary"])
        message.status = Message.Status.FEEDBACK_RECEIVED
        message.save(update_fields=["status"])
        log_event(
            "receiver_feedback.submitted",
            message=message,
            actor=message.receiver,
            receiver=message.receiver,
            payload={"feedback_id": feedback.id},
        )
        messages.success(request, "Receiver feedback saved with the message. Receiver profile was not updated.")
        current_employee = _get_current_employee(request)
        if current_employee and current_employee.id == message.receiver_id:
            return redirect("comms:employee_home")
        return redirect("comms:message_detail", message_id=message.id)

    return render(request, "comms/receiver_feedback.html", {
        "org": message.organization,
        "message": message,
        "current_employee": _get_current_employee(request),
    })


def gmail_demo(request: HttpRequest):
    orgs = _org_options()
    org = _get_current_org(request) or (orgs[0] if orgs else None)
    result = None
    analysis_error = ""

    if request.method == "POST":
        form_data = request.POST.copy()
        try:
            message, result = analyze_gmail_draft(form_data, request=request)
            log_event(
                "gmail.draft_analyzed",
                message=message,
                source="gmail_demo",
                payload={
                    "subject": form_data.get("subject", ""),
                    "sender_email": form_data.get("sender_email", ""),
                    "receiver_email": form_data.get("receiver_email", ""),
                },
            )
            org = message.organization
        except Organization.DoesNotExist:
            analysis_error = "Selected organization was not found."
        except (NebiusConfigurationError, NebiusRuntimeError, LLMResponseValidationError, ValueError) as exc:
            analysis_error = str(exc)
    else:
        form_data = {
            "organization_id": org.id if org else "",
            "sender_email": "",
            "receiver_email": "",
            "receiver_name": "",
            "subject": "",
            "body": "",
            "intent": Message.Intent.REQUEST,
        }

    return render(request, "comms/gmail_demo.html", {
        "org": org,
        "orgs": orgs,
        "intents": Message.Intent.choices,
        "result": result,
        "analysis_error": analysis_error,
        "form_data": form_data,
        "current_employee": _get_current_employee(request),
    })
