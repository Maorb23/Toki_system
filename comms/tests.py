from django.test import TestCase
from comms.models import Organization, Team, Employee, Message, InlineSuggestion, ReceiverFeedback, MessageRevision
from comms.services.score_engine import recalculate_scores, set_suggestion_decision, apply_accepted_suggestions
from comms.services.feedback_processor import update_receiver_profile_from_feedback
from comms.services.llm_client import NebiusLLMClient, NebiusConfigurationError
from comms.services.inline_preview import validate_inline_preview_response
from comms.services.message_analyzer import validate_analysis_response, LLMResponseValidationError
from django.test import override_settings
from django.core.management import call_command
from django.test import Client
from unittest.mock import patch
import tempfile
import json

class ScoreEngineTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Test Org")
        team = Team.objects.create(organization=org, name="Engineering")
        self.sender = Employee.objects.create(organization=org, team=team, name="Sender", role="PM")
        self.receiver = Employee.objects.create(organization=org, team=team, name="Receiver", role="Engineer")
        self.message = Message.objects.create(
            organization=org,
            sender=self.sender,
            receiver=self.receiver,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            original_text="Fix this today.",
            scores_before={"clarity": 50, "tone": 50, "receiver_fit": 50, "org_values_alignment": 50},
            current_scores={"clarity": 50, "tone": 50, "receiver_fit": 50, "org_values_alignment": 50},
        )
        self.suggestion = InlineSuggestion.objects.create(
            message=self.message,
            target_text="Fix this today.",
            suggested_replacement="Could you look at this today? It is blocking the release.",
            affected_scores={"clarity": 10, "tone": 15, "receiver_fit": 10, "org_values_alignment": 5},
        )

    def test_accepting_suggestion_updates_scores(self):
        set_suggestion_decision(self.suggestion, InlineSuggestion.Decision.ACCEPTED)
        self.message.refresh_from_db()
        self.assertEqual(self.message.current_scores["clarity"], 60)
        self.assertEqual(self.message.current_scores["tone"], 65)
        self.assertIn("Could you look", self.message.final_text)
        self.assertIn(self.suggestion.id, self.message.accepted_suggestion_ids)
        self.assertEqual(self.message.rejected_suggestion_ids, [])

    def test_apply_accepted_suggestions_creates_revision(self):
        apply_accepted_suggestions(self.message)
        self.assertEqual(MessageRevision.objects.filter(message=self.message).count(), 1)

    def test_accept_all_uses_overall_suggested_message(self):
        self.message.overall_suggested_message = "Could you look at this today? It is blocking the release."
        self.message.save(update_fields=["overall_suggested_message"])
        set_suggestion_decision(self.suggestion, InlineSuggestion.Decision.ACCEPTED)
        self.message.refresh_from_db()
        self.assertEqual(self.message.final_text, self.message.overall_suggested_message)

    def test_partial_word_suggestion_expands_to_word_boundaries(self):
        message = Message.objects.create(
            organization=self.sender.organization,
            sender=self.sender,
            receiver=self.receiver,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            original_text="Ship before launch.",
            scores_before={"clarity": 50, "tone": 50, "receiver_fit": 50, "org_values_alignment": 50},
            current_scores={"clarity": 50, "tone": 50, "receiver_fit": 50, "org_values_alignment": 50},
        )
        suggestion = InlineSuggestion.objects.create(
            message=message,
            target_text="for",
            start_index=7,
            end_index=10,
            suggested_replacement="after",
            affected_scores={"clarity": 5},
        )
        set_suggestion_decision(suggestion, InlineSuggestion.Decision.ACCEPTED)
        message.refresh_from_db()
        self.assertEqual(message.final_text, "Ship after launch.")

class MessageSuggestionViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        org = Organization.objects.create(name="Web Org")
        team = Team.objects.create(organization=org, name="Engineering")
        sender = Employee.objects.create(organization=org, team=team, name="Sender", role="PM")
        receiver = Employee.objects.create(organization=org, team=team, name="Receiver", role="Engineer")
        self.message = Message.objects.create(
            organization=org,
            sender=sender,
            receiver=receiver,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            original_text="Fix this today. Send status.",
            final_text="Fix this today. Send status.",
            overall_suggested_message="Could you fix this today? Please send a brief status update.",
            scores_before={"clarity": 40, "tone": 40, "receiver_fit": 40, "org_values_alignment": 40},
            current_scores={"clarity": 40, "tone": 40, "receiver_fit": 40, "org_values_alignment": 40},
        )
        self.first = InlineSuggestion.objects.create(
            message=self.message,
            target_text="Fix this today.",
            start_index=0,
            end_index=15,
            suggested_replacement="Could you fix this today?",
            affected_scores={"clarity": 5, "tone": 10, "receiver_fit": 5, "org_values_alignment": 0},
        )
        self.second = InlineSuggestion.objects.create(
            message=self.message,
            target_text="Send status.",
            start_index=16,
            end_index=28,
            suggested_replacement="Please send a brief status update.",
            affected_scores={"clarity": 10, "tone": 5, "receiver_fit": 5, "org_values_alignment": 5},
        )

    def test_accept_suggestion_response_updates_scores_and_final_text(self):
        response = self.client.post(
            f"/api/messages/{self.message.id}/suggestions/{self.first.id}/decision/",
            data=json.dumps({"decision": InlineSuggestion.Decision.ACCEPTED}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["current_scores"]["tone"], 50)
        self.assertEqual(body["final_text"], "Could you fix this today? Send status.")

    def test_reject_suggestion_response_recalculates_scores(self):
        set_suggestion_decision(self.first, InlineSuggestion.Decision.ACCEPTED)
        response = self.client.post(
            f"/api/messages/{self.message.id}/suggestions/{self.second.id}/decision/",
            data=json.dumps({"decision": InlineSuggestion.Decision.REJECTED}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["current_scores"]["tone"], 50)
        self.assertEqual(body["final_text"], "Could you fix this today? Send status.")

    def test_accept_all_response_uses_overall_llm_message(self):
        response = self.client.post(
            f"/api/messages/{self.message.id}/suggestions/bulk-decision/",
            data=json.dumps({"decision": InlineSuggestion.Decision.ACCEPTED}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["final_text"], self.message.overall_suggested_message)
        self.assertEqual(body["current_scores"]["clarity"], 55)

class ModeSplitTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Northstar Labs")
        team = Team.objects.create(organization=self.org, name="Customer Success")
        self.rina = Employee.objects.create(
            organization=self.org,
            team=team,
            name="Rina Tal",
            role="Customer Success Manager",
        )
        self.dana = Employee.objects.create(
            organization=self.org,
            team=team,
            name="Dana Weiss",
            role="Engineering Lead",
        )

    def test_root_shows_mode_select(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Admin / Org View")
        self.assertContains(response, "Continue as")
        self.assertContains(response, "Rina Tal")

    def test_employee_sign_in_locks_employee_mode(self):
        response = self.client.get(f"/employee/sign-in/{self.rina.id}/")
        self.assertRedirects(response, "/employee/")
        response = self.client.get("/employee/")
        self.assertContains(response, "Employee mode")
        self.assertContains(response, "Rina Tal")

    def test_org_dashboard_moved_to_org_path(self):
        response = self.client.get("/org/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Northstar Labs")

    def test_selecting_org_changes_landing_and_personas(self):
        office = Organization.objects.create(name="The Office", description="Scranton branch")
        team = Team.objects.create(organization=office, name="Regional Management")
        Employee.objects.create(
            organization=office,
            team=team,
            name="Michael Scott",
            role="Regional Manager",
        )

        response = self.client.get(f"/orgs/{office.id}/select/")
        self.assertRedirects(response, "/")

        response = self.client.get("/")
        self.assertContains(response, "The Office")
        self.assertContains(response, "Michael Scott")
        self.assertNotContains(response, "Rina Tal")

    def test_mode_select_allows_sign_in_as_any_employee(self):
        alex = Employee.objects.create(
            organization=self.org,
            team=self.rina.team,
            name="Alex Kim",
            role="Designer",
        )

        response = self.client.get("/")

        self.assertContains(response, "All users")
        self.assertContains(response, "Alex Kim")
        self.assertContains(response, f"/employee/sign-in/{alex.id}/")

    @patch("comms.views.MessageAnalyzer.analyze")
    def test_lightweight_send_creates_sent_message_without_full_analysis(self, analyze):
        response = self.client.post(
            "/workspace/",
            data={
                "sender_id": self.rina.id,
                "receiver_id": self.dana.id,
                "channel": Message.Channel.SLACK,
                "intent": Message.Intent.REQUEST,
                "suggestion_mode": "lightweight",
                "compose_action": "send",
                "original_message": "Could you send the status update today?",
                "lightweight_scores": json.dumps({
                    "clarity": 86,
                    "tone": 92,
                    "receiver_fit": 75,
                    "org_values_alignment": 84,
                }),
            },
        )

        message = Message.objects.get()
        self.assertRedirects(response, f"/messages/{message.id}/receiver-feedback/")
        analyze.assert_not_called()
        self.assertEqual(message.status, Message.Status.SENT)
        self.assertEqual(message.final_text, "Could you send the status update today?")
        self.assertEqual(message.current_scores["clarity"], 86)

    def test_receiver_feedback_save_does_not_update_profile_learning(self):
        message = Message.objects.create(
            organization=self.org,
            sender=self.rina,
            receiver=self.dana,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            original_text="Please send a status update.",
            final_text="Please send a status update.",
            status=Message.Status.SENT,
        )
        before_prompt = self.dana.receiver_prompt
        self.client.get(f"/employee/sign-in/{self.dana.id}/")

        response = self.client.post(
            f"/messages/{message.id}/receiver-feedback/",
            data={
                "clear": "on",
                "free_text": "This was clear enough.",
            },
        )

        self.assertRedirects(response, "/employee/")
        feedback = ReceiverFeedback.objects.get(message=message)
        self.assertEqual(feedback.free_text, "This was clear enough.")
        self.assertIn("not updated", feedback.prompt_update_summary)
        self.dana.refresh_from_db()
        self.assertEqual(self.dana.receiver_prompt, before_prompt)

class FeedbackProcessorTests(TestCase):
    def test_receiver_feedback_appends_prompt_learning(self):
        org = Organization.objects.create(name="Test Org")
        team = Team.objects.create(organization=org, name="People")
        sender = Employee.objects.create(organization=org, team=team, name="Sender", role="Manager")
        receiver = Employee.objects.create(
            organization=org,
            team=team,
            name="Receiver",
            role="People Partner",
            receiver_prompt="Original prompt.",
            communication_preferences={},
        )
        message = Message.objects.create(
            organization=org,
            sender=sender,
            receiver=receiver,
            channel=Message.Channel.EMAIL,
            intent=Message.Intent.FEEDBACK,
            original_text="Message",
        )
        feedback = ReceiverFeedback.objects.create(
            message=message,
            sender=sender,
            receiver=receiver,
            too_direct=True,
            free_text="Add more context before the ask.",
        )

        update_receiver_profile_from_feedback(feedback)
        receiver.refresh_from_db()
        self.assertIn("Recent receiver feedback learning", receiver.receiver_prompt)
        self.assertIn("Avoid overly blunt", receiver.receiver_prompt)
        self.assertIn("Add more context", receiver.receiver_prompt)

class NebiusClientTests(TestCase):
    @override_settings(NEBIUS_API_KEY="", NEBIUS_BASE_URL="https://example.com/v1", NEBIUS_MODEL="model")
    def test_missing_key_raises_clear_error(self):
        with self.assertRaises(NebiusConfigurationError):
            NebiusLLMClient().chat_json(system_prompt="x", user_prompt="y")

class ValidationTests(TestCase):
    def test_invalid_inline_index_raises(self):
        data = {
            "overall_suggested_message": "x",
            "inline_suggestions": [
                {
                    "target_text": "Hi",
                    "suggested_replacement": "Hello",
                    "start_index": 10,
                    "end_index": 2,
                }
            ],
            "scores_before": {
                "clarity": 50,
                "tone": 50,
                "receiver_fit": 50,
                "org_values_alignment": 50,
            },
            "estimated_scores_after_all_suggestions": {
                "clarity": 60,
                "tone": 60,
                "receiver_fit": 60,
                "org_values_alignment": 60,
            },
        }

        with self.assertRaises(LLMResponseValidationError):
            validate_analysis_response(data, "Hi there")

    def test_inline_preview_skips_suggestion_target_outside_changed_text(self):
        data = {
            "inline_suggestions": [
                {
                    "target_text": "outside text",
                    "suggested_replacement": "replacement",
                    "issue": "Bad span",
                    "reason": "The model drifted.",
                    "affected_scores": {"clarity": 5},
                },
                {
                    "target_text": "deadline",
                    "suggested_replacement": "realistic deadline",
                    "issue": "Clarify ask",
                    "reason": "Makes room for constraints.",
                    "affected_scores": {"clarity": 5},
                },
            ]
        }

        suggestions = validate_inline_preview_response(data, "What is the deadline?")

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["target_text"], "deadline")

class OrgImportTests(TestCase):
    def test_import_org_creates_entities(self):
        payload = {
            "organization": {"name": "Test Org", "description": "Demo"},
            "values": [{"name": "Clarity", "description": ""}],
            "teams": [{"name": "Engineering", "description": "", "norms": []}],
            "employees": [
                {
                    "name": "Alex",
                    "role": "Engineer",
                    "team": "Engineering",
                    "manager": None,
                    "seniority_level": "IC",
                    "communication_preferences": {"style": "direct"},
                    "pain_points": [],
                    "receiver_prompt": "Be clear",
                }
            ],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as temp:
            json.dump(payload, temp)
            temp_path = temp.name

        call_command("import_org", temp_path)
        org = Organization.objects.get(name="Test Org")
        self.assertEqual(org.teams.count(), 1)
        self.assertEqual(org.employees.count(), 1)

class ApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Api Org")
        team = Team.objects.create(organization=self.org, name="Engineering")
        self.sender = Employee.objects.create(organization=self.org, team=team, name="Sender", role="PM")
        self.receiver = Employee.objects.create(organization=self.org, team=team, name="Receiver", role="Engineer")

    def _headers(self):
        return {"HTTP_X_API_KEY": "test-key", "HTTP_X_ORG_ID": str(self.org.id)}

    @override_settings(COMMS_API_KEY="test-key")
    def test_api_list_orgs_requires_key(self):
        response = self.client.get("/api/v1/orgs/")
        self.assertEqual(response.status_code, 401)

    @override_settings(COMMS_API_KEY="test-key")
    def test_api_list_orgs(self):
        response = self.client.get("/api/v1/orgs/", **self._headers())
        self.assertEqual(response.status_code, 200)
        self.assertIn("orgs", response.json())

    @override_settings(COMMS_API_KEY="test-key")
    @patch("comms.services.llm_client.NebiusLLMClient.chat_json")
    def test_api_analyze_message(self, chat_json):
        chat_json.return_value = {
            "overall_suggested_message": "Improved",
            "subject_line": "Subject",
            "slack_short_version": "Short",
            "teams_short_version": "Short",
            "inline_suggestions": [
                {
                    "id": "s1",
                    "target_text": "Fix this.",
                    "start_index": 0,
                    "end_index": 9,
                    "issue": "Too blunt",
                    "suggested_replacement": "Could you fix this?",
                    "reason": "Softer",
                    "affected_scores": {"clarity": 5, "tone": 10, "receiver_fit": 5, "org_values_alignment": 5},
                    "org_values_used": ["Respectful disagreement"],
                }
            ],
            "scores_before": {"clarity": 50, "tone": 50, "receiver_fit": 50, "org_values_alignment": 50},
            "estimated_scores_after_all_suggestions": {"clarity": 60, "tone": 60, "receiver_fit": 60, "org_values_alignment": 60},
            "risks": [],
            "summary_of_changes": "Summary",
            "explanation": "Explanation",
        }

        payload = {
            "org_id": self.org.id,
            "sender_id": self.sender.id,
            "receiver_id": self.receiver.id,
            "channel": Message.Channel.SLACK,
            "intent": Message.Intent.REQUEST,
            "original_message": "Fix this.",
        }

        response = self.client.post(
            "/api/v1/messages/analyze/",
            data=json.dumps(payload),
            content_type="application/json",
            **self._headers(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("message", body)

    @patch("comms.services.llm_client.NebiusLLMClient.chat_json")
    def test_inline_preview_endpoint_returns_suggestions_without_creating_message(self, chat_json):
        chat_json.return_value = {
            "inline_suggestions": [
                {
                    "target_text": "what is the deadline?",
                    "suggested_replacement": "what is a realistic deadline for completing this?",
                    "issue": "The ask is clear but could invite a realistic estimate.",
                    "reason": "Keeps the ask while making room for the receiver's constraints.",
                    "affected_scores": {
                        "clarity": 6,
                        "tone": 2,
                        "receiver_fit": 5,
                        "org_values_alignment": 4,
                    },
                }
            ]
        }
        before_count = Message.objects.count()
        payload = {
            "sender_id": self.sender.id,
            "receiver_id": self.receiver.id,
            "channel": Message.Channel.SLACK,
            "intent": Message.Intent.REQUEST,
            "full_draft": "Hey Dana, what is the deadline?",
            "changed_text": "what is the deadline?",
            "surrounding_context": "Hey Dana,",
        }

        response = self.client.post(
            f"/api/orgs/{self.org.id}/inline-suggestions/preview/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("text_hash", body)
        self.assertEqual(len(body["suggestions"]), 1)
        self.assertEqual(body["suggestions"][0]["target_text"], "what is the deadline?")
        self.assertEqual(Message.objects.count(), before_count)
