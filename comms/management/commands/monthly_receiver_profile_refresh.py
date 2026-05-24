from collections import Counter

from django.core.management.base import BaseCommand
from django.utils import timezone

from comms.models import Employee, InlineSuggestion, ReceiverFeedback, ReceiverProfileRefreshProposal
from comms.services.event_log import log_event
from comms.services.webhooks import deliver_event_to_webhooks, delivery_summary


FEEDBACK_FLAGS = [
    "too_direct",
    "too_soft",
    "too_long",
    "too_short",
    "missed_context",
    "too_much_context",
    "unclear_ask",
    "unclear_ownership",
    "not_aligned_with_preferences",
]


def _month_bounds(now):
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1)
    return month_start, next_month


def _proposal_payload(receiver, feedback_qs, accepted_count, rejected_count):
    flag_counter = Counter()
    for feedback in feedback_qs:
        for flag in FEEDBACK_FLAGS:
            if getattr(feedback, flag):
                flag_counter[flag] += 1

    common_flags = dict(flag_counter.most_common())
    prompt_lines = [
        f"Recent profile evidence for {receiver.name}:",
        f"- Receiver feedback entries reviewed: {feedback_qs.count()}",
        f"- Accepted suggestions for messages to this receiver: {accepted_count}",
        f"- Rejected suggestions for messages to this receiver: {rejected_count}",
    ]
    if common_flags:
        prompt_lines.append(f"- Common feedback flags: {', '.join(common_flags.keys())}")

    preference_updates = {}
    pain_points_updates = []
    if common_flags.get("too_long"):
        preference_updates["detail"] = "prefers concise messages"
        pain_points_updates.append("Messages may include too much detail.")
    if common_flags.get("unclear_ask"):
        preference_updates["structure"] = "clear ask and next step"
        pain_points_updates.append("Requests may be unclear without explicit next steps.")
    if common_flags.get("too_direct"):
        preference_updates["style"] = "direct but respectful"
        pain_points_updates.append("Tone may feel too direct without context.")
    if common_flags.get("missed_context"):
        preference_updates["context"] = "include relevant background before the ask"
        pain_points_updates.append("Messages may miss context the receiver needs.")

    evidence_summary = {
        "feedback_count": feedback_qs.count(),
        "accepted_suggestion_count": accepted_count,
        "rejected_suggestion_count": rejected_count,
        "common_feedback_flags": common_flags,
    }
    proposed_changes = {
        "receiver_prompt_additions": "\n".join(prompt_lines),
        "communication_preferences_updates": preference_updates,
        "pain_points_updates": pain_points_updates,
    }
    explanation = (
        f"Pending deterministic refresh for {receiver.name} based on recent feedback and suggestion decisions. "
        "Review before applying; no receiver profile fields have been changed."
    )
    return evidence_summary, proposed_changes, explanation


class Command(BaseCommand):
    help = "Create pending receiver profile refresh proposals from recent feedback and suggestion history."

    def add_arguments(self, parser):
        parser.add_argument("--organization-id", type=int, help="Limit proposals to one organization")

    def handle(self, *args, **options):
        now = timezone.now()
        period_start = now - timezone.timedelta(days=30)
        month_start, next_month = _month_bounds(now)
        receivers = Employee.objects.select_related("organization", "team").order_by("organization__name", "name")
        if options.get("organization_id"):
            receivers = receivers.filter(organization_id=options["organization_id"])

        created = 0
        duplicates = 0
        webhook_deliveries = []
        for receiver in receivers:
            duplicate_exists = ReceiverProfileRefreshProposal.objects.filter(
                receiver=receiver,
                status=ReceiverProfileRefreshProposal.Status.PENDING,
                created_at__gte=month_start,
                created_at__lt=next_month,
            ).exists()
            if duplicate_exists:
                duplicates += 1
                continue

            feedback_qs = ReceiverFeedback.objects.filter(receiver=receiver, created_at__gte=period_start)
            suggestions = InlineSuggestion.objects.filter(message__receiver=receiver, decided_at__gte=period_start)
            accepted_count = suggestions.filter(decision=InlineSuggestion.Decision.ACCEPTED).count()
            rejected_count = suggestions.filter(decision=InlineSuggestion.Decision.REJECTED).count()

            if not feedback_qs.exists() and accepted_count == 0 and rejected_count == 0:
                continue

            evidence_summary, proposed_changes, explanation = _proposal_payload(
                receiver,
                feedback_qs,
                accepted_count,
                rejected_count,
            )
            proposal = ReceiverProfileRefreshProposal.objects.create(
                organization=receiver.organization,
                receiver=receiver,
                proposed_changes=proposed_changes,
                explanation=explanation,
                evidence_summary=evidence_summary,
            )
            event = log_event(
                "receiver_profile.refresh_proposed",
                organization=receiver.organization,
                receiver=receiver,
                payload={"proposal_id": proposal.id, "receiver_id": receiver.id},
            )
            if event:
                webhook_deliveries.extend(deliver_event_to_webhooks(event))
            created += 1
            self.stdout.write(f"proposal created: receiver={receiver.name} id={proposal.id}")

        self.stdout.write(f"proposals created: {created}")
        self.stdout.write(f"duplicates skipped: {duplicates}")
        self.stdout.write(delivery_summary(webhook_deliveries))
