from .base import DraftMessage, SuggestionResult

class TeamsAdapter:
    """Stub for future Microsoft Teams messaging extension integration."""

    def receive_draft(self, payload: dict) -> DraftMessage:
        return DraftMessage(
            external_id=payload.get("activity_id", ""),
            sender_external_id=payload.get("from_id", ""),
            receiver_external_id=payload.get("receiver_id", ""),
            channel="teams",
            text=payload.get("text", ""),
        )

    def return_suggestions(self, result: SuggestionResult) -> dict:
        return {
            "message_id": result.internal_message_id,
            "final_text": result.final_text,
            "inline_suggestions": result.suggestions,
            "current_scores": result.current_scores,
            "note": "User approval is required before sending.",
        }

    def collect_receiver_feedback(self, payload: dict) -> dict:
        return {"status": "not_implemented", "source": "teams"}
