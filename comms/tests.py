from django.test import TestCase, TransactionTestCase
from comms.models import (
    Employee,
    FeedbackReminder,
    InlineSuggestion,
    Message,
    MessageRevision,
    OrgValue,
    OrgValuesDriftCheck,
    Organization,
    ReceiverFeedback,
    ReceiverProfileRefreshProposal,
    SystemEvent,
    Team,
    WebhookDelivery,
    WebhookSubscription,
    WeeklyCommunicationReport,
)
from comms.services.event_log import log_event
from comms.services.onboarding import ensure_employee_onboarding
from comms.services.profile_refresh import approve_profile_refresh_proposal, reject_profile_refresh_proposal
from comms.services.score_engine import recalculate_scores, set_suggestion_decision, apply_accepted_suggestions
from comms.services.webhooks import sign_webhook_payload
from comms.services.feedback_processor import update_receiver_profile_from_feedback
from comms.services.llm_client import NebiusLLMClient, NebiusConfigurationError
from comms.services.inline_preview import validate_inline_preview_response
from comms.services.message_analyzer import validate_analysis_response, LLMResponseValidationError
from django.test import override_settings
from django.core.management import call_command
from django.test import Client
from unittest.mock import patch
from io import StringIO
from django.utils import timezone
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

    def test_analysis_validation_normalizes_mismatched_target_from_valid_indexes(self):
        data = {
            "overall_suggested_message": "Alpha improved Gamma",
            "inline_suggestions": [
                {
                    "target_text": "Beta please",
                    "suggested_replacement": "improved",
                    "start_index": 6,
                    "end_index": 10,
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

        validated = validate_analysis_response(data, "Alpha Beta Gamma")

        self.assertEqual(validated["inline_suggestions"][0]["target_text"], "Beta")
        self.assertEqual(validated["inline_suggestions"][0]["start_index"], 6)
        self.assertEqual(validated["inline_suggestions"][0]["end_index"], 10)
        self.assertEqual(len(validated["_validation_metadata"]["normalized_inline_suggestions"]), 1)

    def test_analysis_validation_skips_unanchorable_suggestion(self):
        data = {
            "overall_suggested_message": "Alpha improved Gamma",
            "inline_suggestions": [
                {
                    "target_text": "outside text",
                    "suggested_replacement": "replacement",
                },
                {
                    "target_text": "Beta",
                    "suggested_replacement": "improved",
                },
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

        validated = validate_analysis_response(data, "Alpha Beta Gamma")

        self.assertEqual(len(validated["inline_suggestions"]), 1)
        self.assertEqual(validated["inline_suggestions"][0]["target_text"], "Beta")
        self.assertEqual(len(validated["_validation_metadata"]["skipped_inline_suggestions"]), 1)

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


class SystemEventTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Event Org")
        team = Team.objects.create(organization=self.org, name="Engineering")
        self.sender = Employee.objects.create(organization=self.org, team=team, name="Sender", role="PM")
        self.receiver = Employee.objects.create(organization=self.org, team=team, name="Receiver", role="Engineer")
        self.message = Message.objects.create(
            organization=self.org,
            sender=self.sender,
            receiver=self.receiver,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            original_text="Message",
        )

    def test_system_event_creation(self):
        event = SystemEvent.objects.create(
            organization=self.org,
            actor=self.sender,
            receiver=self.receiver,
            message=self.message,
            event_type="test.event",
            payload={"ok": True},
        )

        self.assertEqual(event.source, "app")
        self.assertEqual(event.status, "success")
        self.assertEqual(event.payload["ok"], True)

    def test_log_event_does_not_crash(self):
        with patch("comms.services.event_log.SystemEvent.objects.create", side_effect=RuntimeError("db unavailable")), \
             patch("comms.services.event_log.logger.exception"):
            event = log_event("test.event", message=self.message)

        self.assertIsNone(event)


class ScheduledAutomationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Automation Org")
        OrgValue.objects.create(organization=self.org, name="Clarity", description="Be clear")
        OrgValue.objects.create(organization=self.org, name="Ownership", description="Own outcomes")
        self.team = Team.objects.create(organization=self.org, name="Engineering")
        self.sender = Employee.objects.create(
            organization=self.org,
            team=self.team,
            name="Sender",
            email="sender@example.com",
            role="PM",
        )
        self.receiver = Employee.objects.create(
            organization=self.org,
            team=self.team,
            name="Receiver",
            email="receiver@example.com",
            role="Engineer",
        )

    def _message(self, *, days_old=1, status=Message.Status.SENT, text="Private full message text"):
        created_at = timezone.now() - timezone.timedelta(days=days_old)
        message = Message.objects.create(
            organization=self.org,
            sender=self.sender,
            receiver=self.receiver,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            original_text=text,
            final_text=text,
            status=status,
            created_at=created_at,
            current_scores={"clarity": 60, "tone": 80, "receiver_fit": 75, "org_values_alignment": 65},
        )
        return message

    def test_weekly_report_command_creates_report(self):
        message = self._message()
        InlineSuggestion.objects.create(
            message=message,
            target_text="Private",
            suggested_replacement="Could you",
            decision=InlineSuggestion.Decision.ACCEPTED,
        )

        out = StringIO()
        call_command("weekly_team_communication_report", "--organization-id", str(self.org.id), stdout=out)

        report = WeeklyCommunicationReport.objects.get(organization=self.org)
        self.assertEqual(report.metrics["message_count"], 1)
        self.assertEqual(report.metrics["accepted_suggestion_count"], 1)

    def test_weekly_report_does_not_include_private_full_message_text(self):
        private_text = "Private full message text with sensitive customer details"
        self._message(text=private_text)

        call_command("weekly_team_communication_report", "--organization-id", str(self.org.id), stdout=StringIO())

        report = WeeklyCommunicationReport.objects.get(organization=self.org)
        serialized = json.dumps({"metrics": report.metrics, "summary": report.summary})
        self.assertNotIn(private_text, serialized)

    def test_org_values_drift_check_creates_check(self):
        self._message()

        call_command("org_values_drift_check", "--organization-id", str(self.org.id), stdout=StringIO())

        check = OrgValuesDriftCheck.objects.get(organization=self.org)
        self.assertEqual(check.metrics["message_count"], 1)
        self.assertEqual(check.metrics["average_scores"]["org_values_alignment"], 65)
        self.assertEqual(check.warnings[0]["type"], "low_org_values_alignment")

    def test_stale_feedback_reminder_creates_reminder(self):
        message = self._message(days_old=9, status=Message.Status.SENT)

        call_command("stale_feedback_reminder", "--organization-id", str(self.org.id), "--days", "7", stdout=StringIO())

        reminder = FeedbackReminder.objects.get(message=message)
        self.assertEqual(reminder.status, FeedbackReminder.Status.PENDING)
        self.assertIn(f":{message.id}:", reminder.reminder_key)

    def test_stale_feedback_reminder_deduplicates_reminders(self):
        self._message(days_old=9, status=Message.Status.SENT)

        call_command("stale_feedback_reminder", "--organization-id", str(self.org.id), "--days", "7", stdout=StringIO())
        call_command("stale_feedback_reminder", "--organization-id", str(self.org.id), "--days", "7", stdout=StringIO())

        self.assertEqual(FeedbackReminder.objects.count(), 1)

    def test_onboarding_creates_default_receiver_prompt_only_when_missing(self):
        employee = Employee.objects.create(
            organization=self.org,
            team=self.team,
            name="New Hire",
            role="Designer",
            seniority_level="IC",
        )

        created = ensure_employee_onboarding(employee)
        employee.refresh_from_db()

        self.assertTrue(created)
        self.assertIn("New Hire", employee.receiver_prompt)
        self.assertIn("Designer", employee.receiver_prompt)
        self.assertIn("Clarity", employee.receiver_prompt)

    def test_onboarding_does_not_overwrite_existing_receiver_prompt(self):
        employee = Employee.objects.create(
            organization=self.org,
            team=self.team,
            name="Existing",
            role="Designer",
            receiver_prompt="Keep this exact prompt.",
        )

        created = ensure_employee_onboarding(employee)
        employee.refresh_from_db()

        self.assertFalse(created)
        self.assertEqual(employee.receiver_prompt, "Keep this exact prompt.")

    def test_import_employees_csv_is_idempotent(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8") as temp:
            temp.write("name,email,role,team,manager_email,seniority_level\n")
            temp.write("Alex Kim,alex@example.com,Designer,Design,,IC\n")
            path = temp.name

        call_command("import_employees_csv", path, "--organization-id", str(self.org.id), stdout=StringIO())
        call_command("import_employees_csv", path, "--organization-id", str(self.org.id), stdout=StringIO())

        self.assertEqual(Employee.objects.filter(organization=self.org, email="alex@example.com").count(), 1)

    def test_import_employees_csv_creates_teams_if_missing(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8") as temp:
            temp.write("name,email,role,team,manager_email,seniority_level\n")
            temp.write("Casey Lee,casey@example.com,Analyst,Data Science,,IC\n")
            path = temp.name

        call_command("import_employees_csv", path, "--organization-id", str(self.org.id), stdout=StringIO())

        self.assertTrue(Team.objects.filter(organization=self.org, name="Data Science").exists())
        self.assertEqual(Employee.objects.get(email="casey@example.com").team.name, "Data Science")


class WebhookTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Webhook Org")
        self.team = Team.objects.create(organization=self.org, name="Engineering")
        self.sender = Employee.objects.create(organization=self.org, team=self.team, name="Sender", role="PM")
        self.receiver = Employee.objects.create(organization=self.org, team=self.team, name="Receiver", role="Engineer")
        self.message = Message.objects.create(
            organization=self.org,
            sender=self.sender,
            receiver=self.receiver,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            original_text="Message",
            status=Message.Status.SENT,
            created_at=timezone.now() - timezone.timedelta(days=10),
            current_scores={"clarity": 80, "tone": 80, "receiver_fit": 80, "org_values_alignment": 80},
        )

    def _event(self, event_type="feedback.missing"):
        return SystemEvent.objects.create(
            organization=self.org,
            message=self.message,
            receiver=self.receiver,
            event_type=event_type,
            source="scheduler",
            payload={"ok": True},
        )

    def _subscription(self, *, event_types=None, is_active=True):
        return WebhookSubscription.objects.create(
            organization=self.org,
            name="n8n",
            target_url="https://example.com/webhook",
            secret="secret",
            event_types=event_types or ["feedback.missing"],
            is_active=is_active,
        )

    def test_sign_webhook_payload_is_deterministic(self):
        payload = {"b": 2, "a": 1}

        first = sign_webhook_payload(payload, "secret")
        second = sign_webhook_payload({"a": 1, "b": 2}, "secret")

        self.assertEqual(first, second)

    @patch("comms.services.webhooks.requests.post")
    def test_inactive_webhook_does_not_send(self, post):
        self._subscription(is_active=False)

        from comms.services.webhooks import deliver_event_to_webhooks
        deliveries = deliver_event_to_webhooks(self._event())

        self.assertEqual(deliveries, [])
        post.assert_not_called()

    @patch("comms.services.webhooks.requests.post")
    def test_unmatched_event_type_does_not_send(self, post):
        self._subscription(event_types=["weekly_report.generated"])

        from comms.services.webhooks import deliver_event_to_webhooks
        deliveries = deliver_event_to_webhooks(self._event("feedback.missing"))

        self.assertEqual(deliveries, [])
        post.assert_not_called()

    @patch("comms.services.webhooks.requests.post")
    def test_matching_active_webhook_sends_post(self, post):
        self._subscription()
        post.return_value.status_code = 200
        post.return_value.text = "ok"

        from comms.services.webhooks import deliver_event_to_webhooks
        deliveries = deliver_event_to_webhooks(self._event())

        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0].status, WebhookDelivery.Status.SUCCESS)
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["headers"]["X-ReceiverAware-Event"], "feedback.missing")
        self.assertIn("X-ReceiverAware-Signature", post.call_args.kwargs["headers"])

    @patch("comms.services.webhooks.logger.warning")
    @patch("comms.services.webhooks.requests.post")
    def test_failed_webhook_creates_failed_delivery(self, post, warning):
        self._subscription()
        post.side_effect = RuntimeError("network down")

        from comms.services.webhooks import deliver_event_to_webhooks
        deliveries = deliver_event_to_webhooks(self._event())

        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0].status, WebhookDelivery.Status.FAILED)
        self.assertIn("network down", deliveries[0].error_message)

    @patch("comms.services.webhooks.logger.warning")
    @patch("comms.services.webhooks.requests.post")
    def test_scheduled_command_still_succeeds_when_webhook_fails(self, post, warning):
        self._subscription(event_types=["org_values_drift.checked"])
        post.side_effect = RuntimeError("network down")

        call_command("org_values_drift_check", "--organization-id", str(self.org.id), stdout=StringIO())

        self.assertTrue(OrgValuesDriftCheck.objects.filter(organization=self.org).exists())
        self.assertEqual(WebhookDelivery.objects.get().status, WebhookDelivery.Status.FAILED)

    @patch("comms.services.webhooks.requests.post")
    def test_stale_feedback_reminder_triggers_delivery_for_feedback_missing(self, post):
        self._subscription(event_types=["feedback.missing"])
        post.return_value.status_code = 200
        post.return_value.text = "ok"

        call_command("stale_feedback_reminder", "--organization-id", str(self.org.id), "--days", "7", stdout=StringIO())

        self.assertEqual(WebhookDelivery.objects.count(), 1)
        self.assertEqual(WebhookDelivery.objects.get().event.event_type, "feedback.missing")

    @patch("comms.services.webhooks.requests.post")
    def test_weekly_report_triggers_delivery_for_weekly_report_generated(self, post):
        self._subscription(event_types=["weekly_report.generated"])
        post.return_value.status_code = 200
        post.return_value.text = "ok"

        call_command("weekly_team_communication_report", "--organization-id", str(self.org.id), stdout=StringIO())

        self.assertEqual(WebhookDelivery.objects.count(), 1)
        self.assertEqual(WebhookDelivery.objects.get().event.event_type, "weekly_report.generated")


class ReceiverProfileRefreshTests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Profile Org")
        self.team = Team.objects.create(organization=self.org, name="Engineering")
        self.sender = Employee.objects.create(organization=self.org, team=self.team, name="Sender", role="PM")
        self.receiver = Employee.objects.create(
            organization=self.org,
            team=self.team,
            name="Receiver",
            role="Engineer",
            receiver_prompt="Original prompt.",
            communication_preferences={"style": "direct"},
            pain_points=["unclear requirements"],
        )
        self.message = Message.objects.create(
            organization=self.org,
            sender=self.sender,
            receiver=self.receiver,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            original_text="Message",
            current_scores={"clarity": 70, "tone": 70, "receiver_fit": 70, "org_values_alignment": 70},
        )

    def _create_evidence(self):
        ReceiverFeedback.objects.create(
            message=self.message,
            sender=self.sender,
            receiver=self.receiver,
            too_long=True,
            unclear_ask=True,
            free_text="Please make asks clearer.",
        )
        InlineSuggestion.objects.create(
            message=self.message,
            target_text="Message",
            suggested_replacement="Clear message",
            decision=InlineSuggestion.Decision.ACCEPTED,
            decided_at=timezone.now(),
        )

    def _proposal(self):
        return ReceiverProfileRefreshProposal.objects.create(
            organization=self.org,
            receiver=self.receiver,
            proposed_changes={
                "receiver_prompt_additions": "Use concise requests with explicit next steps.",
                "communication_preferences_updates": {"structure": "clear ask and next step"},
                "pain_points_updates": ["Requests may be unclear without explicit next steps."],
            },
            explanation="Test proposal",
            evidence_summary={"feedback_count": 1},
        )

    def test_monthly_command_creates_pending_receiver_profile_refresh_proposal(self):
        self._create_evidence()

        call_command("monthly_receiver_profile_refresh", "--organization-id", str(self.org.id), stdout=StringIO())

        proposal = ReceiverProfileRefreshProposal.objects.get(receiver=self.receiver)
        self.assertEqual(proposal.status, ReceiverProfileRefreshProposal.Status.PENDING)
        self.assertEqual(proposal.evidence_summary["feedback_count"], 1)

    def test_monthly_command_does_not_overwrite_receiver_prompt(self):
        self._create_evidence()

        call_command("monthly_receiver_profile_refresh", "--organization-id", str(self.org.id), stdout=StringIO())

        self.receiver.refresh_from_db()
        self.assertEqual(self.receiver.receiver_prompt, "Original prompt.")

    def test_monthly_command_avoids_duplicate_pending_proposal_for_same_receiver_month(self):
        self._create_evidence()

        call_command("monthly_receiver_profile_refresh", "--organization-id", str(self.org.id), stdout=StringIO())
        call_command("monthly_receiver_profile_refresh", "--organization-id", str(self.org.id), stdout=StringIO())

        self.assertEqual(ReceiverProfileRefreshProposal.objects.filter(receiver=self.receiver).count(), 1)

    def test_approval_appends_prompt_additions_once(self):
        proposal = self._proposal()

        approve_profile_refresh_proposal(proposal, reviewed_by=self.sender)
        approve_profile_refresh_proposal(proposal, reviewed_by=self.sender)

        self.receiver.refresh_from_db()
        self.assertEqual(self.receiver.receiver_prompt.count("Use concise requests"), 1)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, ReceiverProfileRefreshProposal.Status.APPROVED)

    def test_approval_updates_communication_preferences_safely(self):
        proposal = self._proposal()

        approve_profile_refresh_proposal(proposal, reviewed_by=self.sender)

        self.receiver.refresh_from_db()
        self.assertEqual(self.receiver.communication_preferences["style"], "direct")
        self.assertEqual(self.receiver.communication_preferences["structure"], "clear ask and next step")

    def test_approval_logs_receiver_profile_refresh_approved(self):
        proposal = self._proposal()

        approve_profile_refresh_proposal(proposal, reviewed_by=self.sender)

        self.assertTrue(SystemEvent.objects.filter(event_type="receiver_profile.refresh_approved").exists())

    def test_approval_is_idempotent(self):
        proposal = self._proposal()

        first = approve_profile_refresh_proposal(proposal, reviewed_by=self.sender)
        second = approve_profile_refresh_proposal(first, reviewed_by=self.sender)

        self.assertEqual(first.id, second.id)
        self.receiver.refresh_from_db()
        self.assertEqual(self.receiver.receiver_prompt.count("[Receiver profile refresh proposal"), 1)

    def test_rejection_does_not_change_employee(self):
        proposal = self._proposal()
        before_prompt = self.receiver.receiver_prompt

        reject_profile_refresh_proposal(proposal, reviewed_by=self.sender)

        self.receiver.refresh_from_db()
        self.assertEqual(self.receiver.receiver_prompt, before_prompt)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, ReceiverProfileRefreshProposal.Status.REJECTED)

    def test_cannot_reject_approved_proposal(self):
        proposal = self._proposal()
        approve_profile_refresh_proposal(proposal, reviewed_by=self.sender)

        with self.assertRaises(ValueError):
            reject_profile_refresh_proposal(proposal, reviewed_by=self.sender)

    @patch("comms.management.commands.monthly_receiver_profile_refresh.deliver_event_to_webhooks")
    def test_webhook_delivery_called_for_receiver_profile_refresh_proposed(self, deliver):
        self._create_evidence()
        deliver.return_value = []

        call_command("monthly_receiver_profile_refresh", "--organization-id", str(self.org.id), stdout=StringIO())

        deliver.assert_called_once()


class GmailAppsScriptDemoTests(TestCase):
    def test_seed_gmail_demo_org_is_idempotent(self):
        call_command("seed_gmail_demo_org", stdout=StringIO())
        call_command("seed_gmail_demo_org", stdout=StringIO())

        org = Organization.objects.get(name="Acme Demo Org")
        self.assertEqual(Employee.objects.filter(organization=org, email="sender@acme.test").count(), 1)
        self.assertEqual(Employee.objects.filter(organization=org, email="receiver@acme.test").count(), 1)
        self.assertEqual(org.values.count(), 3)

class ApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.org = Organization.objects.create(name="Api Org")
        team = Team.objects.create(organization=self.org, name="Engineering")
        self.sender = Employee.objects.create(organization=self.org, team=team, name="Sender", email="sender@example.com", role="PM")
        self.receiver = Employee.objects.create(organization=self.org, team=team, name="Receiver", email="receiver@example.com", role="Engineer")

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

    @patch("comms.api.InlineSuggestionPreviewer.preview")
    def test_gmail_inline_preview_maps_emails_and_uses_gmail_channel(self, preview):
        preview.return_value = {
            "text_hash": "abc123",
            "suggestions": [
                {
                    "target_text": "Fix this.",
                    "suggested_replacement": "Could you fix this?",
                    "issue": "Too blunt",
                    "reason": "Softer",
                    "affected_scores": {"tone": 8},
                }
            ],
        }
        payload = {
            "organization_id": self.org.id,
            "sender_email": "sender@example.com",
            "receiver_email": "receiver@example.com",
            "receiver_name": "Receiver",
            "intent": Message.Intent.REQUEST,
            "full_draft": "Fix this.",
            "changed_text": "Fix this.",
            "surrounding_context": "Fix this.",
        }

        response = self.client.post(
            "/api/integrations/gmail/inline-suggestions/preview/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["sender_id"], self.sender.id)
        self.assertEqual(body["receiver_id"], self.receiver.id)
        preview.assert_called_once()
        self.assertEqual(preview.call_args.kwargs["channel"], Message.Channel.GMAIL)

    @override_settings(COMMS_GMAIL_INTEGRATION_TOKEN="gmail-token")
    def test_gmail_inline_preview_v1_rejects_invalid_token(self):
        response = self.client.post(
            "/api/v1/integrations/gmail/inline-suggestions/preview/",
            data=json.dumps({}),
            content_type="application/json",
            **self._gmail_headers("wrong-token"),
        )

        self.assertEqual(response.status_code, 401)

    @override_settings(COMMS_GMAIL_INTEGRATION_TOKEN="gmail-token")
    @patch("comms.api.InlineSuggestionPreviewer.preview")
    def test_gmail_inline_preview_v1_is_csrf_exempt_for_apps_script(self, preview):
        preview.return_value = {"text_hash": "abc123", "suggestions": []}
        csrf_client = Client(enforce_csrf_checks=True)
        payload = {
            "organization_id": self.org.id,
            "sender_email": "sender@example.com",
            "receiver_email": "receiver@example.com",
            "receiver_name": "Receiver",
            "intent": Message.Intent.REQUEST,
            "full_draft": "Could you review this today?",
            "changed_text": "Could you review this today?",
            "surrounding_context": "Could you review this today?",
        }

        response = csrf_client.post(
            "/api/v1/integrations/gmail/inline-suggestions/preview/",
            data=json.dumps(payload),
            content_type="application/json",
            **self._gmail_headers(),
        )

        self.assertEqual(response.status_code, 200)
        preview.assert_called_once()

    def _gmail_headers(self, token="gmail-token"):
        return {"HTTP_X_GMAIL_INTEGRATION_TOKEN": token}

    @override_settings(COMMS_GMAIL_INTEGRATION_TOKEN="gmail-token")
    def test_gmail_health_endpoint(self):
        response = self.client.get("/api/v1/integrations/gmail/health/", **self._gmail_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["integration"], "gmail")

    @override_settings(COMMS_GMAIL_INTEGRATION_TOKEN="gmail-token")
    def test_gmail_analyze_rejects_invalid_token(self):
        response = self.client.post(
            "/api/v1/integrations/gmail/analyze-draft/",
            data=json.dumps({}),
            content_type="application/json",
            **self._gmail_headers("wrong-token"),
        )

        self.assertEqual(response.status_code, 401)

    @override_settings(COMMS_GMAIL_INTEGRATION_TOKEN="gmail-token")
    @patch("comms.services.gmail_demo.MessageAnalyzer.analyze")
    def test_gmail_analyze_maps_receiver_by_email_uses_gmail_calls_analyzer_and_returns_absolute_dashboard_url(self, analyze):
        def create_message(*, sender, receiver, channel, intent, original_message):
            message = Message.objects.create(
                organization=self.org,
                sender=sender,
                receiver=receiver,
                channel=channel,
                intent=intent,
                original_text=original_message,
                final_text=original_message,
                overall_suggested_message="Improved Gmail draft",
                slack_short_version="Short draft",
                scores_before={"clarity": 70, "tone": 70, "receiver_fit": 70, "org_values_alignment": 70},
                estimated_scores_after_all={"clarity": 80, "tone": 80, "receiver_fit": 80, "org_values_alignment": 80},
                current_scores={"clarity": 70, "tone": 70, "receiver_fit": 70, "org_values_alignment": 70},
                explanation="Explanation",
                status=Message.Status.ANALYZED,
            )
            InlineSuggestion.objects.create(
                message=message,
                target_text="Fix this.",
                suggested_replacement="Could you fix this?",
            )
            return message

        analyze.side_effect = create_message
        payload = {
            "organization_id": self.org.id,
            "sender_email": "sender@example.com",
            "receiver_email": "receiver@example.com",
            "receiver_name": "Receiver",
            "subject": "Status",
            "body": "Fix this.",
            "intent": Message.Intent.REQUEST,
        }

        response = self.client.post(
            "/api/v1/integrations/gmail/analyze-draft/",
            data=json.dumps(payload),
            content_type="application/json",
            **self._gmail_headers(),
        )

        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(channel=Message.Channel.GMAIL)
        body = response.json()
        self.assertEqual(message.receiver, self.receiver)
        self.assertEqual(body["receiver_id"], self.receiver.id)
        self.assertEqual(body["channel"], Message.Channel.GMAIL)
        self.assertIn("dashboard_absolute_url", body)
        self.assertTrue(body["dashboard_absolute_url"].startswith("http://testserver/messages/"))
        analyze.assert_called_once()
        self.assertEqual(analyze.call_args.kwargs["channel"], Message.Channel.GMAIL)

    @override_settings(COMMS_GMAIL_INTEGRATION_TOKEN="gmail-token")
    @patch("comms.services.gmail_demo.MessageAnalyzer.analyze")
    def test_gmail_analyze_is_csrf_exempt_for_server_to_server_addon_calls(self, analyze):
        def create_message(*, sender, receiver, channel, intent, original_message):
            return Message.objects.create(
                organization=self.org,
                sender=sender,
                receiver=receiver,
                channel=channel,
                intent=intent,
                original_text=original_message,
                final_text=original_message,
                scores_before={"clarity": 70, "tone": 70, "receiver_fit": 70, "org_values_alignment": 70},
                estimated_scores_after_all={"clarity": 80, "tone": 80, "receiver_fit": 80, "org_values_alignment": 80},
                current_scores={"clarity": 70, "tone": 70, "receiver_fit": 70, "org_values_alignment": 70},
                status=Message.Status.ANALYZED,
            )

        analyze.side_effect = create_message
        csrf_client = Client(enforce_csrf_checks=True)
        payload = {
            "organization_id": self.org.id,
            "sender_email": "sender@example.com",
            "receiver_email": "receiver@example.com",
            "receiver_name": "Receiver",
            "subject": "Status",
            "body": "Fix this.",
            "intent": Message.Intent.REQUEST,
        }

        response = csrf_client.post(
            "/api/v1/integrations/gmail/analyze-draft/",
            data=json.dumps(payload),
            content_type="application/json",
            **self._gmail_headers(),
        )

        self.assertNotEqual(response.status_code, 403)
        self.assertEqual(response.status_code, 200)
