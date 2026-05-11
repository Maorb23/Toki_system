from dataclasses import dataclass
from typing import Protocol

@dataclass
class DraftMessage:
    external_id: str
    sender_external_id: str
    receiver_external_id: str
    channel: str
    text: str

@dataclass
class SuggestionResult:
    internal_message_id: int
    final_text: str
    suggestions: list[dict]
    current_scores: dict

class CommunicationAdapter(Protocol):
    """
    Future adapter contract.

    Integrations must never auto-send. They should return suggestions and
    let the human accept/reject them.
    """

    def receive_draft(self, payload: dict) -> DraftMessage:
        ...

    def return_suggestions(self, result: SuggestionResult) -> dict:
        ...

    def collect_receiver_feedback(self, payload: dict) -> dict:
        ...
