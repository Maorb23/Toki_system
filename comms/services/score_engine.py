from typing import Dict
from django.utils import timezone
from comms.models import Message, InlineSuggestion, MessageRevision

SCORE_KEYS = ["clarity", "tone", "receiver_fit", "org_values_alignment"]


def clamp_score(value: int | float) -> int:
    return max(0, min(100, int(round(value))))


def normalize_scores(scores: dict | None) -> Dict[str, int]:
    scores = scores or {}
    return {key: clamp_score(scores.get(key, 0)) for key in SCORE_KEYS}


def recalculate_scores(message: Message) -> Dict[str, int]:
    """
    Deterministic score update.

    Base = LLM scores_before.
    Add affected_scores deltas for accepted suggestions only.
    Rejected/pending suggestions do not improve scores.
    """
    current = normalize_scores(message.scores_before)
    accepted = message.suggestions.filter(decision=InlineSuggestion.Decision.ACCEPTED)

    for suggestion in accepted:
        deltas = suggestion.affected_scores or {}
        for key in SCORE_KEYS:
            current[key] = clamp_score(current.get(key, 0) + int(deltas.get(key, 0) or 0))

    message.current_scores = current
    message.save(update_fields=["current_scores"])
    return current


def sync_suggestion_decisions(message: Message) -> None:
    """
    Sync accepted/rejected suggestion IDs onto the Message record.

    Keep this function because views.py already imports and uses it.
    """
    accepted = list(
        message.suggestions.filter(decision=InlineSuggestion.Decision.ACCEPTED)
        .values_list("id", flat=True)
    )
    rejected = list(
        message.suggestions.filter(decision=InlineSuggestion.Decision.REJECTED)
        .values_list("id", flat=True)
    )

    message.accepted_suggestion_ids = accepted
    message.rejected_suggestion_ids = rejected
    message.save(update_fields=["accepted_suggestion_ids", "rejected_suggestion_ids"])


def _record_revision_if_changed(message: Message, text: str, note: str) -> None:
    last = message.revisions.order_by("-version_index").first()
    if last and last.text == text:
        return

    next_index = 1 if not last else last.version_index + 1
    MessageRevision.objects.create(
        message=message,
        version_index=next_index,
        text=text,
        note=note,
    )


def _safe_index_span(original_text: str, suggestion: InlineSuggestion) -> tuple[int, int] | None:
    """
    Use LLM-provided indexes only if they exactly match target_text.

    This prevents corrupted text when the LLM gives slightly wrong start/end indexes.
    """
    start = suggestion.start_index
    end = suggestion.end_index
    target = suggestion.target_text or ""

    if start is None or end is None:
        return None

    if not isinstance(start, int) or not isinstance(end, int):
        return None

    if not (0 <= start < end <= len(original_text)):
        return None

    if original_text[start:end] != target:
        return None

    return _expand_to_word_boundaries(original_text, start, end)


def _safe_target_text_span(original_text: str, suggestion: InlineSuggestion) -> tuple[int, int] | None:
    """
    Fallback to target_text only if it appears exactly once.

    If it appears multiple times, replacement is ambiguous and should be skipped.
    """
    target = suggestion.target_text or ""
    if not target.strip():
        return None

    first = original_text.find(target)
    if first < 0:
        return None

    second = original_text.find(target, first + len(target))
    if second >= 0:
        return None

    return _expand_to_word_boundaries(original_text, first, first + len(target))


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _is_boundary(text: str, index: int) -> bool:
    if index < 0 or index >= len(text):
        return True
    return not _is_word_char(text[index])


def _expand_to_word_boundaries(original_text: str, start: int, end: int) -> tuple[int, int]:
    while start > 0 and not _is_boundary(original_text, start - 1):
        start -= 1

    while end < len(original_text) and not _is_boundary(original_text, end):
        end += 1

    return start, end


def _collect_safe_replacements(
    original_text: str,
    accepted_suggestions: list[InlineSuggestion],
) -> list[tuple[int, int, str]]:
    replacements: list[tuple[int, int, str]] = []

    for suggestion in accepted_suggestions:
        span = _safe_index_span(original_text, suggestion)

        if span is None:
            span = _safe_target_text_span(original_text, suggestion)

        if span is None:
            # Safer to skip this suggestion than corrupt the final message.
            continue

        start, end = span
        replacement = suggestion.suggested_replacement or ""
        replacements.append((start, end, replacement))

    # Sort and remove overlaps.
    replacements.sort(key=lambda item: item[0])
    clean: list[tuple[int, int, str]] = []
    last_end = -1

    for start, end, replacement in replacements:
        if start < last_end:
            continue
        clean.append((start, end, replacement))
        last_end = end

    return clean


def _apply_replacements(original_text: str, replacements: list[tuple[int, int, str]]) -> str:
    """
    Apply replacements from right to left so earlier indexes do not shift.
    """
    text = original_text

    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        text = text[:start] + replacement + text[end:]

    return text


def apply_accepted_suggestions(message: Message) -> str:
    """
    Build final text from accepted suggestions.

    Important POC rule:
    - If all suggestions are accepted, use overall_suggested_message when available.
      This avoids corrupted span-by-span reconstruction.
    - If only some suggestions are accepted, apply only verified safe spans.
    - Never apply LLM indexes unless they exactly match target_text.
    """
    original_text = message.original_text or ""
    overall_suggested_message = (message.overall_suggested_message or "").strip()

    all_suggestions = list(message.suggestions.all())
    accepted = [
        suggestion
        for suggestion in all_suggestions
        if suggestion.decision == InlineSuggestion.Decision.ACCEPTED
    ]

    if not accepted:
        final_text = original_text

    elif len(accepted) == len(all_suggestions) and overall_suggested_message:
        # Accept All should use the LLM's coherent full-message rewrite.
        final_text = overall_suggested_message

    else:
        replacements = _collect_safe_replacements(original_text, accepted)

        if replacements:
            final_text = _apply_replacements(original_text, replacements)
        else:
            # Better to keep original than create broken/corrupted output.
            final_text = original_text

    if message.final_text != final_text:
        message.final_text = final_text
        message.save(update_fields=["final_text"])
        _record_revision_if_changed(message, final_text, "Accepted suggestions update")

    elif not message.revisions.exists():
        _record_revision_if_changed(message, final_text, "Initial message")

    return final_text


def set_suggestion_decision(suggestion: InlineSuggestion, decision: str) -> None:
    if decision not in {
        InlineSuggestion.Decision.ACCEPTED,
        InlineSuggestion.Decision.REJECTED,
        InlineSuggestion.Decision.PENDING,
    }:
        raise ValueError(f"Invalid suggestion decision: {decision}")

    suggestion.decision = decision
    suggestion.decided_at = timezone.now() if decision != InlineSuggestion.Decision.PENDING else None
    suggestion.save(update_fields=["decision", "decided_at"])

    recalculate_scores(suggestion.message)
    apply_accepted_suggestions(suggestion.message)
    sync_suggestion_decisions(suggestion.message)
