from django.db import transaction
from comms.models import ReceiverFeedback, Employee

FEEDBACK_GUIDANCE = {
    "too_direct": "Avoid overly blunt phrasing for this receiver. Keep clarity, but add respectful framing and context.",
    "too_soft": "This receiver is comfortable with more direct language when the ask and rationale are clear.",
    "too_long": "This receiver prefers shorter messages, concise bullets, and a clear ask near the top.",
    "too_short": "This receiver prefers enough context to understand why the message matters.",
    "missed_context": "Include relevant background context before asking this receiver to decide or act.",
    "too_much_context": "Reduce background detail. Lead with the point, then add only essential context.",
    "unclear_ask": "Make the ask explicit, including owner, action, and timing.",
    "unclear_ownership": "Make ownership clear by naming the person or team responsible for the next step.",
    "not_aligned_with_preferences": "Review this receiver's stated preferences before sending. Adapt structure, detail level, and tone.",
    "good_message": "This message style worked well for the receiver. Preserve similar structure and tone in future messages.",
}

def _selected_feedback_flags(feedback: ReceiverFeedback) -> list[str]:
    return [
        flag for flag in FEEDBACK_GUIDANCE.keys()
        if getattr(feedback, flag, False)
    ]

@transaction.atomic
def update_receiver_profile_from_feedback(feedback: ReceiverFeedback) -> ReceiverFeedback:
    """
    Update receiver prompt/preferences based on receiver feedback after receiving the message.

    This is transparent and append-oriented: do not wipe the full profile.
    """
    receiver: Employee = feedback.receiver
    before_prompt = receiver.receiver_prompt or ""

    selected_flags = _selected_feedback_flags(feedback)
    guidance_lines = [FEEDBACK_GUIDANCE[flag] for flag in selected_flags]

    if feedback.free_text.strip():
        guidance_lines.append(f"Receiver free-text feedback: {feedback.free_text.strip()}")

    if not guidance_lines:
        guidance_lines.append("No specific negative feedback was selected. Preserve the current communication guidance.")

    update_block = "\n".join(f"- {line}" for line in guidance_lines)
    new_prompt = before_prompt.rstrip() + "\n\nRecent receiver feedback learning:\n" + update_block

    prefs = dict(receiver.communication_preferences or {})
    learned = list(prefs.get("learned_from_receiver_feedback", []))
    learned.append(
        {
            "message_id": feedback.message_id,
            "feedback_id": feedback.id,
            "flags": selected_flags,
            "free_text": feedback.free_text,
        }
    )
    prefs["learned_from_receiver_feedback"] = learned[-20:]

    receiver.receiver_prompt = new_prompt.strip()
    receiver.communication_preferences = prefs
    receiver.save(update_fields=["receiver_prompt", "communication_preferences", "updated_at"])

    feedback.receiver_prompt_before = before_prompt
    feedback.receiver_prompt_after = receiver.receiver_prompt
    feedback.prompt_update_summary = "Updated receiver profile with: " + ", ".join(selected_flags or ["general feedback"])
    feedback.save(
        update_fields=[
            "receiver_prompt_before",
            "receiver_prompt_after",
            "prompt_update_summary",
        ]
    )

    feedback.message.status = feedback.message.Status.FEEDBACK_RECEIVED
    feedback.message.save(update_fields=["status"])

    return feedback
