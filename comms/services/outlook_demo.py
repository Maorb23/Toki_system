from django.http import HttpRequest
from django.urls import reverse

from comms.models import Employee, Message, Organization
from comms.services.message_analyzer import MessageAnalyzer


class OutlookOrganizationNotFound(ValueError):
    pass


def _unique_demo_receiver_name(org: Organization, base_name: str) -> str:
    candidate = base_name.strip() or "Outlook Demo Receiver"
    if not Employee.objects.filter(organization=org, name=candidate).exists():
        return candidate

    index = 2
    while Employee.objects.filter(organization=org, name=f"{candidate} {index}").exists():
        index += 1
    return f"{candidate} {index}"


def get_outlook_demo_participants(payload: dict) -> tuple[Organization, Employee, Employee, bool]:
    organization_id = payload.get("organization_id") or payload.get("org_id")
    sender_email = (payload.get("sender_email") or "").strip()
    receiver_email = (payload.get("receiver_email") or "").strip()
    receiver_name = (payload.get("receiver_name") or "").strip()

    if not all([organization_id, sender_email, receiver_email]):
        raise ValueError("organization_id, sender_email, and receiver_email are required")

    org = Organization.objects.filter(pk=organization_id).first()
    if org is None:
        raise OutlookOrganizationNotFound("organization_id was not found")

    sender = Employee.objects.filter(organization=org, email__iexact=sender_email).first()
    if sender is None:
        raise ValueError("sender_email does not match an employee in this organization")

    receiver_created = False
    receiver = Employee.objects.filter(organization=org, email__iexact=receiver_email).first()
    if receiver is None:
        safe_name = _unique_demo_receiver_name(
            org,
            receiver_name or receiver_email.split("@")[0] or "Outlook Demo Receiver",
        )
        receiver = Employee.objects.create(
            organization=org,
            name=safe_name,
            email=receiver_email,
            role="Outlook demo receiver",
            communication_preferences={},
            pain_points=[],
            receiver_prompt="Demo receiver created from Outlook integration. Use a clear, respectful, specific email.",
        )
        receiver_created = True

    return org, sender, receiver, receiver_created


def analyze_outlook_draft(payload: dict, *, request: HttpRequest | None = None) -> tuple[Message, dict]:
    subject = (payload.get("subject") or "").strip()
    body = (payload.get("body") or "").strip()
    intent = (payload.get("intent") or "").strip()

    if not all([body, intent]):
        raise ValueError("body and intent are required")

    org, sender, receiver, receiver_created = get_outlook_demo_participants(payload)
    original_message = body
    if subject:
        original_message = f"Subject: {subject}\n\n{body}"

    message = MessageAnalyzer().analyze(
        sender=sender,
        receiver=receiver,
        channel=Message.Channel.OUTLOOK,
        intent=intent,
        original_message=original_message,
    )

    dashboard_path = reverse("comms:message_detail", args=[message.id])
    dashboard_url = request.build_absolute_uri(dashboard_path) if request else dashboard_path
    dashboard_absolute_url = request.build_absolute_uri(dashboard_path) if request else dashboard_path
    suggestions = [
        {
            "id": suggestion.id,
            "target_text": suggestion.target_text,
            "suggested_replacement": suggestion.suggested_replacement,
            "issue": suggestion.issue,
            "reason": suggestion.reason,
            "affected_scores": suggestion.affected_scores,
        }
        for suggestion in message.suggestions.all()
    ]

    return message, {
        "message_id": message.id,
        "organization_id": org.id,
        "sender_id": sender.id,
        "receiver_id": receiver.id,
        "receiver_created": receiver_created,
        "channel": message.channel,
        "scores": {
            "before": message.scores_before,
            "after_all_suggestions": message.estimated_scores_after_all,
            "current": message.current_scores,
        },
        "suggestions": suggestions,
        "improved_version": message.overall_suggested_message,
        "short_version": message.slack_short_version or message.teams_short_version,
        "explanation": message.explanation,
        "dashboard_url": dashboard_url,
        "dashboard_absolute_url": dashboard_absolute_url,
        "metadata": {
            "channel": "outlook",
        },
    }
