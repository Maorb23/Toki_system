import hashlib
from typing import Any

from comms.models import Employee
from comms.services.llm_client import NebiusLLMClient
from comms.services.message_analyzer import LLMResponseValidationError
from comms.services.prompt_builder import INLINE_PREVIEW_SYSTEM_PROMPT, build_inline_preview_prompt
from comms.services.score_engine import SCORE_KEYS, clamp_score


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _require_dict(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise LLMResponseValidationError(f"{name} must be an object")
    return value


def _require_list(value: Any, name: str) -> list:
    if not isinstance(value, list):
        raise LLMResponseValidationError(f"{name} must be a list")
    return value


def _normalize_affected_scores(value: Any) -> dict:
    scores = value if isinstance(value, dict) else {}
    return {key: clamp_score(scores.get(key, 0)) for key in SCORE_KEYS}


def validate_inline_preview_response(data: dict, changed_text: str) -> list[dict]:
    _require_dict(data, "root")
    suggestions = _require_list(data.get("inline_suggestions"), "inline_suggestions")

    clean = []
    for index, item in enumerate(suggestions):
        _require_dict(item, f"inline_suggestions[{index}]")
        target_text = (item.get("target_text") or "").strip()
        suggested_replacement = (item.get("suggested_replacement") or "").strip()
        if not target_text or not suggested_replacement:
            raise LLMResponseValidationError(
                f"inline_suggestions[{index}].target_text and suggested_replacement are required"
            )
        if target_text not in changed_text:
            raise LLMResponseValidationError(
                f"inline_suggestions[{index}].target_text was not found in changed_text"
            )

        clean.append({
            "target_text": target_text,
            "suggested_replacement": suggested_replacement,
            "issue": str(item.get("issue") or ""),
            "reason": str(item.get("reason") or ""),
            "affected_scores": _normalize_affected_scores(item.get("affected_scores")),
        })

    return clean


class InlineSuggestionPreviewer:
    def __init__(self, llm_client: NebiusLLMClient | None = None) -> None:
        self.llm_client = llm_client or NebiusLLMClient()

    def preview(
        self,
        *,
        sender: Employee,
        receiver: Employee,
        channel: str,
        intent: str,
        full_draft: str,
        changed_text: str,
        surrounding_context: str,
    ) -> dict:
        if sender.organization_id != receiver.organization_id:
            raise ValueError("Sender and receiver must belong to the same organization")

        changed_text = changed_text.strip()
        if not changed_text:
            return {"text_hash": text_hash(""), "suggestions": []}

        prompt = build_inline_preview_prompt(
            organization=sender.organization,
            sender=sender,
            receiver=receiver,
            channel=channel,
            intent=intent,
            full_draft=full_draft,
            changed_text=changed_text,
            surrounding_context=surrounding_context,
        )
        raw = self.llm_client.chat_json(
            system_prompt=INLINE_PREVIEW_SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.1,
        )
        suggestions = validate_inline_preview_response(raw, changed_text)
        return {
            "text_hash": text_hash(changed_text),
            "suggestions": suggestions,
        }
