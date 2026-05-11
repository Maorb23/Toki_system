from typing import Any
from django.db import transaction
from comms.models import Employee, Message, InlineSuggestion, MessageRevision
from comms.services.llm_client import NebiusLLMClient
from comms.services.prompt_builder import SYSTEM_PROMPT, build_message_analysis_prompt
from comms.services.score_engine import normalize_scores


def _safe_text(value: Any) -> str:
    """
    Convert optional LLM string fields into DB-safe strings.

    LLMs may return null for optional fields like subject_line.
    Django CharField/TextField with blank=True still does not allow NULL
    unless null=True is set, so we store empty string instead.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)

class LLMResponseValidationError(ValueError):
    pass

def _require_dict(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise LLMResponseValidationError(f"{name} must be an object")
    return value

def _require_list(value: Any, name: str) -> list:
    if not isinstance(value, list):
        raise LLMResponseValidationError(f"{name} must be a list")
    return value

def _require_int_range(value: Any, name: str, min_value: int, max_value: int) -> int:
    if not isinstance(value, int):
        raise LLMResponseValidationError(f"{name} must be an integer")
    if value < min_value or value > max_value:
        raise LLMResponseValidationError(f"{name} must be between {min_value} and {max_value}")
    return value

def _normalize_suggestion_span(original_message: str, item: dict) -> tuple[int | None, int | None, str | None]:
    target_text = item.get("target_text") or ""
    start_index = item.get("start_index")
    end_index = item.get("end_index")

    if isinstance(start_index, int) and isinstance(end_index, int):
        if 0 <= start_index < end_index <= len(original_message):
            normalized = original_message[start_index:end_index]
            if normalized == target_text:
                return start_index, end_index, normalized
            return start_index, end_index, normalized

    if target_text:
        start = original_message.find(target_text)
        if start != -1:
            return start, start + len(target_text), target_text

    return None, None, None

def validate_analysis_response(data: dict, original_message: str) -> dict:
    _require_dict(data, "root")
    required = [
        "overall_suggested_message",
        "inline_suggestions",
        "scores_before",
        "estimated_scores_after_all_suggestions",
    ]
    missing = [key for key in required if key not in data]
    if missing:
        raise LLMResponseValidationError(f"Missing required LLM fields: {', '.join(missing)}")

    _require_list(data["inline_suggestions"], "inline_suggestions")
    _require_dict(data["scores_before"], "scores_before")
    _require_dict(data["estimated_scores_after_all_suggestions"], "estimated_scores_after_all_suggestions")

    for index, item in enumerate(data["inline_suggestions"]):
        _require_dict(item, f"inline_suggestions[{index}]")
        for field in ["target_text", "suggested_replacement"]:
            if not item.get(field):
                raise LLMResponseValidationError(f"inline_suggestions[{index}].{field} is required")

        target_text = item.get("target_text")
        if target_text and target_text not in original_message:
            raise LLMResponseValidationError(
                f"inline_suggestions[{index}].target_text was not found in original_message"
            )

        start_index = item.get("start_index")
        end_index = item.get("end_index")
        if start_index is not None or end_index is not None:
            if not isinstance(start_index, int) or not isinstance(end_index, int):
                raise LLMResponseValidationError(
                    f"inline_suggestions[{index}].start_index/end_index must be integers"
                )
            if start_index < 0 or end_index < 0 or start_index >= end_index:
                raise LLMResponseValidationError(
                    f"inline_suggestions[{index}].start_index/end_index are invalid"
                )
            if end_index > len(original_message):
                raise LLMResponseValidationError(
                    f"inline_suggestions[{index}].end_index is out of bounds"
                )

    for key in ["clarity", "tone", "receiver_fit", "org_values_alignment"]:
        _require_int_range(data["scores_before"].get(key), f"scores_before.{key}", 0, 100)
        _require_int_range(
            data["estimated_scores_after_all_suggestions"].get(key),
            f"estimated_scores_after_all_suggestions.{key}",
            0,
            100,
        )

    return data

class MessageAnalyzer:
    def __init__(self, llm_client: NebiusLLMClient | None = None) -> None:
        self.llm_client = llm_client or NebiusLLMClient()

    @transaction.atomic
    def analyze(
        self,
        *,
        sender: Employee,
        receiver: Employee,
        channel: str,
        intent: str,
        original_message: str,
    ) -> Message:
        if sender.organization_id != receiver.organization_id:
            raise ValueError("Sender and receiver must belong to the same organization")

        prompt = build_message_analysis_prompt(
            organization=sender.organization,
            sender=sender,
            receiver=receiver,
            channel=channel,
            intent=intent,
            original_message=original_message,
        )

        raw = self.llm_client.chat_json(system_prompt=SYSTEM_PROMPT, user_prompt=prompt)
        data = validate_analysis_response(raw, original_message)

        message = Message.objects.create(
            organization=sender.organization,
            sender=sender,
            receiver=receiver,
            channel=channel,
            intent=intent,
            original_text=original_message,
            final_text=original_message,
            overall_suggested_message=_safe_text(data.get("overall_suggested_message", "")),
            subject_line=_safe_text(data.get("subject_line", "")),
            slack_short_version=_safe_text(data.get("slack_short_version", "")),
            teams_short_version=_safe_text(data.get("teams_short_version", "")),
            scores_before=normalize_scores(data.get("scores_before")),
            estimated_scores_after_all=normalize_scores(data.get("estimated_scores_after_all_suggestions")),
            current_scores=normalize_scores(data.get("scores_before")),
            risks=data.get("risks") or [],
            summary_of_changes=_safe_text(data.get("summary_of_changes", "")),
            explanation=_safe_text(data.get("explanation", "")),
            raw_llm_response=raw,
            status=Message.Status.ANALYZED,
        )

        MessageRevision.objects.create(
            message=message,
            version_index=1,
            text=original_message,
            note="Original draft",
        )

        for item in data["inline_suggestions"]:
            start_index, end_index, normalized_text = _normalize_suggestion_span(original_message, item)
            InlineSuggestion.objects.create(
                message=message,
                external_id=item.get("id", ""),
                target_text=normalized_text or item.get("target_text", ""),
                start_index=start_index,
                end_index=end_index,
                issue=item.get("issue", ""),
                suggested_replacement=item.get("suggested_replacement", ""),
                reason=item.get("reason", ""),
                affected_scores=item.get("affected_scores") or {},
                org_values_used=item.get("org_values_used") or [],
            )

        return message
