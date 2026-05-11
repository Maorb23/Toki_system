import json
from comms.models import Employee, Organization, Team

SYSTEM_PROMPT = """
You are an organizational communication coach.

Your job is to analyze a sender's draft message and provide inline suggestions.
You must not auto-send, autocomplete, impersonate the sender, or replace the human.
The sender will decide which suggestions to accept.

Return structured JSON only.
Do not include markdown.
Do not include commentary outside JSON.
"""

def _safe_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)

def build_message_analysis_prompt(
    *,
    organization: Organization,
    sender: Employee,
    receiver: Employee,
    channel: str,
    intent: str,
    original_message: str,
) -> str:
    org_values = [
        {"name": value.name, "description": value.description}
        for value in organization.values.all()
    ]

    sender_team = sender.team
    receiver_team = receiver.team

    payload = {
        "task": "Analyze a draft message and produce receiver-aware inline suggestions.",
        "hard_rules": [
            "Suggest changes only. Do not send the message.",
            "Preserve the sender's intent.",
            "Avoid over-polishing. Keep it natural.",
            "Map suggestions to specific text spans when possible.",
            "Use exact target_text snippets from the original message.",
            "Inline suggestions must use complete, self-contained spans.",
            "If a suggestion changes the object, deadline, context, or meaning of a sentence, target the whole sentence, not only a prefix.",
            "Never replace only the beginning of a sentence if the replacement repeats or changes words that remain later in the same sentence.",
            "target_text must be an exact contiguous substring from original_message.",
            "start_index and end_index must match target_text exactly.",
            "Prefer one complete sentence-level suggestion over a partial phrase suggestion when the change affects sentence meaning.",
            "Do not create overlapping suggestions; each suggestion must target a unique span.",
            "Return JSON only.",
        ],
        "organization": {
            "name": organization.name,
            "description": organization.description,
            "values": org_values,
        },
        "sender": {
            "id": sender.id,
            "name": sender.name,
            "role": sender.role,
            "team": sender_team.name if sender_team else None,
            "communication_preferences": sender.communication_preferences,
        },
        "receiver": {
            "id": receiver.id,
            "name": receiver.name,
            "role": receiver.role,
            "team": receiver_team.name if receiver_team else None,
            "communication_preferences": receiver.communication_preferences,
            "pain_points": receiver.pain_points,
            "receiver_prompt": receiver.receiver_prompt,
        },
        "receiver_team_context": {
            "name": receiver_team.name if receiver_team else None,
            "description": receiver_team.description if receiver_team else "",
            "norms": receiver_team.norms if receiver_team else [],
        },
        "message_context": {
            "channel": channel,
            "intent": intent,
            "original_message": original_message,
        },
        "required_json_schema": {
            "overall_suggested_message": "string",
            "subject_line": "string, optional; useful for email/gmail",
            "slack_short_version": "string, optional; short Slack-ready version",
            "teams_short_version": "string, optional; short Teams-ready version",
            "inline_suggestions": [
                {
                    "id": "string unique id",
                    "target_text": "exact text from the original message",
                    "start_index": "integer character index if possible, otherwise null",
                    "end_index": "integer character index if possible, otherwise null",
                    "issue": "string",
                    "suggested_replacement": "string",
                    "reason": "string",
                    "affected_scores": {
                        "clarity": "integer delta, usually 0-20",
                        "tone": "integer delta, usually 0-20",
                        "receiver_fit": "integer delta, usually 0-20",
                        "org_values_alignment": "integer delta, usually 0-20"
                    },
                    "org_values_used": ["string"]
                }
            ],
            "scores_before": {
                "clarity": "integer 0-100",
                "tone": "integer 0-100",
                "receiver_fit": "integer 0-100",
                "org_values_alignment": "integer 0-100"
            },
            "estimated_scores_after_all_suggestions": {
                "clarity": "integer 0-100",
                "tone": "integer 0-100",
                "receiver_fit": "integer 0-100",
                "org_values_alignment": "integer 0-100"
            },
            "risks": ["string"],
            "summary_of_changes": "string",
            "explanation": "string"
        },
    }

    return _safe_json(payload)
