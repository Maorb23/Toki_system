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

INLINE_PREVIEW_SYSTEM_PROMPT = """
You are an organizational communication coach providing lightweight inline suggestions while a sender types.

Analyze only the changed text and nearby context provided by the user.
Do not rewrite the full message.
Do not produce subject lines, channel-specific versions, risks, summaries, explanations, or scores before/after.
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
    tool_results: dict | None = None,
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
            "Use retrieved context only when it is directly relevant to the sender's message.",
            "Never add company facts, project facts, or receiver preferences that are not present in the draft or retrieved context.",
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
            "intent_guidance": "Interpret all suggestions through this communication intent and preserve that intent.",
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

    if tool_results:
        payload["retrieved_context"] = tool_results

    return _safe_json(payload)

def build_inline_preview_prompt(
    *,
    organization: Organization,
    sender: Employee,
    receiver: Employee,
    channel: str,
    intent: str,
    full_draft: str,
    changed_text: str,
    surrounding_context: str,
    prior_review_context: list[dict] | None = None,
    tool_results: dict | None = None,
) -> str:
    receiver_team = receiver.team
    org_values = [
        {"name": value.name, "description": value.description}
        for value in organization.values.all()
    ]

    payload = {
        "task": "Provide lightweight receiver-aware inline suggestions for changed text only.",
        "hard_rules": [
            "Analyze only changed_text and surrounding_context.",
            "Use prior_review_context as continuity context. Do not retarget prior reviewed text unless changed_text depends on it.",
            "Return suggestions only when there is a concrete improvement.",
            "Preserve the sender's intent.",
            "Avoid over-polishing. Keep the sender's voice natural.",
            "A comma-ended changed_text can be treated as a complete phrase when the sender paused there.",
            "target_text must be an exact contiguous substring of changed_text.",
            "Use complete words or complete sentence spans. Never target part of a word.",
            "Do not create overlapping suggestions.",
            "Do not rewrite the full draft.",
            "Use retrieved context only when it is directly relevant to changed_text and surrounding_context.",
            "Never add company facts, project facts, or receiver preferences that are not present in the draft or retrieved context.",
            "Do not return subject_line, Slack version, Teams version, full risks, summary, explanation, scores_before, or estimated scores.",
            "Return JSON only.",
        ],
        "organization": {
            "name": organization.name,
            "values": org_values,
        },
        "sender": {
            "id": sender.id,
            "name": sender.name,
            "role": sender.role,
            "team": sender.team.name if sender.team else None,
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
            "intent_guidance": "Use this intent to judge whether the changed text fits the sender's goal.",
            "full_draft": full_draft,
            "changed_text": changed_text,
            "surrounding_context": surrounding_context,
            "prior_review_context": prior_review_context or [],
        },
        "required_json_schema": {
            "inline_suggestions": [
                {
                    "target_text": "exact text from changed_text",
                    "suggested_replacement": "string",
                    "issue": "string",
                    "reason": "string",
                    "affected_scores": {
                        "clarity": "integer delta, usually 0-10",
                        "tone": "integer delta, usually 0-10",
                        "receiver_fit": "integer delta, usually 0-10",
                        "org_values_alignment": "integer delta, usually 0-10"
                    }
                }
            ]
        },
    }

    if tool_results:
        payload["retrieved_context"] = tool_results

    return _safe_json(payload)
