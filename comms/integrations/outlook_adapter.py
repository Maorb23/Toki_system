from .base import DraftMessage, SuggestionResult


class OutlookAdapter:
    """Stub for future Outlook Office.js add-in integration."""

    def receive_draft(self, payload: dict) -> DraftMessage:
        return DraftMessage(
            external_id=payload.get("item_id", ""),
            sender_external_id=payload.get("sender_email", ""),
            receiver_external_id=payload.get("receiver_email", ""),
            channel="outlook",
            text=payload.get("body", ""),
        )

    def return_suggestions(self, result: SuggestionResult) -> dict:
        return {
            "message_id": result.internal_message_id,
            "final_text": result.final_text,
            "inline_suggestions": result.suggestions,
            "current_scores": result.current_scores,
            "note": "User approval is required before inserting into the Outlook draft.",
        }

    def collect_receiver_feedback(self, payload: dict) -> dict:
        return {"status": "not_implemented", "source": "outlook"}
