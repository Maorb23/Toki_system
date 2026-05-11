from .base import DraftMessage, SuggestionResult

class SlackAdapter:
    """Stub for future Slack shortcuts/modals integration."""

    def receive_draft(self, payload: dict) -> DraftMessage:
        return DraftMessage(
            external_id=payload.get("message_ts", ""),
            sender_external_id=payload.get("user_id", ""),
            receiver_external_id=payload.get("receiver_id", ""),
            channel="slack",
            text=payload.get("text", ""),
        )

    def return_suggestions(self, result: SuggestionResult) -> dict:
        return {
            "type": "modal",
            "message_id": result.internal_message_id,
            "final_text": result.final_text,
            "inline_suggestions": result.suggestions,
            "current_scores": result.current_scores,
            "note": "User approval is required before posting.",
        }

    def collect_receiver_feedback(self, payload: dict) -> dict:
        return {"status": "not_implemented", "source": "slack"}
