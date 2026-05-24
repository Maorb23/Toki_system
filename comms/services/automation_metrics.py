from django.db.models import QuerySet

from comms.services.score_engine import SCORE_KEYS


def average_scores(messages: QuerySet) -> dict:
    totals = {key: [] for key in SCORE_KEYS}
    for message in messages:
        scores = message.current_scores or message.scores_before or {}
        for key in SCORE_KEYS:
            value = scores.get(key)
            if isinstance(value, (int, float)):
                totals[key].append(value)

    return {
        key: round(sum(values) / len(values), 1) if values else 0
        for key, values in totals.items()
    }


def low_score_areas(scores: dict, *, threshold: int = 70) -> list[str]:
    return [
        key
        for key, value in scores.items()
        if isinstance(value, (int, float)) and value and value < threshold
    ]
