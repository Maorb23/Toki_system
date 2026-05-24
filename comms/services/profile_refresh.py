from django.db import transaction
from django.utils import timezone

from comms.models import ReceiverProfileRefreshProposal
from comms.services.event_log import log_event


def _append_unique(existing: list, additions: list) -> list:
    values = list(existing or [])
    seen = {str(value).strip().lower() for value in values}
    for addition in additions or []:
        normalized = str(addition).strip()
        if normalized and normalized.lower() not in seen:
            values.append(normalized)
            seen.add(normalized.lower())
    return values


@transaction.atomic
def approve_profile_refresh_proposal(proposal: ReceiverProfileRefreshProposal, reviewed_by=None) -> ReceiverProfileRefreshProposal:
    proposal = ReceiverProfileRefreshProposal.objects.select_for_update().select_related("receiver", "organization").get(pk=proposal.pk)
    if proposal.status == ReceiverProfileRefreshProposal.Status.APPROVED:
        return proposal
    if proposal.status == ReceiverProfileRefreshProposal.Status.REJECTED:
        raise ValueError("Rejected profile refresh proposals cannot be approved.")

    receiver = proposal.receiver
    changes = proposal.proposed_changes or {}
    old_prompt = receiver.receiver_prompt
    old_preferences = dict(receiver.communication_preferences or {})
    old_pain_points = list(receiver.pain_points or [])

    additions = (changes.get("receiver_prompt_additions") or "").strip()
    marker = f"[Receiver profile refresh proposal #{proposal.id}]"
    if additions and marker not in receiver.receiver_prompt:
        section = f"\n\n{marker}\n{additions}"
        receiver.receiver_prompt = f"{receiver.receiver_prompt}{section}" if receiver.receiver_prompt else f"{marker}\n{additions}"

    preference_updates = changes.get("communication_preferences_updates") or {}
    if isinstance(preference_updates, dict):
        prefs = dict(receiver.communication_preferences or {})
        prefs.update(preference_updates)
        receiver.communication_preferences = prefs

    pain_updates = changes.get("pain_points_updates") or []
    if isinstance(pain_updates, list):
        receiver.pain_points = _append_unique(receiver.pain_points or [], pain_updates)

    receiver.save(update_fields=["receiver_prompt", "communication_preferences", "pain_points", "updated_at"])

    proposal.status = ReceiverProfileRefreshProposal.Status.APPROVED
    proposal.reviewed_at = timezone.now()
    proposal.reviewed_by = reviewed_by
    proposal.applied_payload = {
        "old_receiver_prompt": old_prompt,
        "new_receiver_prompt": receiver.receiver_prompt,
        "old_communication_preferences": old_preferences,
        "new_communication_preferences": receiver.communication_preferences,
        "old_pain_points": old_pain_points,
        "new_pain_points": receiver.pain_points,
    }
    proposal.save(update_fields=["status", "reviewed_at", "reviewed_by", "applied_payload"])
    log_event(
        "receiver_profile.refresh_approved",
        organization=proposal.organization,
        actor=reviewed_by,
        receiver=receiver,
        payload={"proposal_id": proposal.id},
    )
    return proposal


@transaction.atomic
def reject_profile_refresh_proposal(proposal: ReceiverProfileRefreshProposal, reviewed_by=None) -> ReceiverProfileRefreshProposal:
    proposal = ReceiverProfileRefreshProposal.objects.select_for_update().select_related("receiver", "organization").get(pk=proposal.pk)
    if proposal.status == ReceiverProfileRefreshProposal.Status.APPROVED:
        raise ValueError("Approved profile refresh proposals cannot be rejected.")
    if proposal.status == ReceiverProfileRefreshProposal.Status.REJECTED:
        return proposal

    proposal.status = ReceiverProfileRefreshProposal.Status.REJECTED
    proposal.reviewed_at = timezone.now()
    proposal.reviewed_by = reviewed_by
    proposal.save(update_fields=["status", "reviewed_at", "reviewed_by"])
    log_event(
        "receiver_profile.refresh_rejected",
        organization=proposal.organization,
        actor=reviewed_by,
        receiver=proposal.receiver,
        payload={"proposal_id": proposal.id},
    )
    return proposal
