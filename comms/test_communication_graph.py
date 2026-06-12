import json
import os
import tempfile
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from comms.models import (
    Employee,
    InlineSuggestion,
    MeetingContext,
    Message,
    MessageRevision,
    OrgValue,
    Organization,
    OrganizationContext,
    ProjectContext,
    Team,
)
from comms.services.communication_graph import CommunicationGraphRunner, export_graph_visualization
from comms.services.context_tools import suggest_meeting_context, suggest_related_projects
from comms.services.message_analyzer import MessageAnalyzer
from comms.services.score_engine import normalize_scores
from comms.services.inline_preview import (
    InlineSuggestionPreviewer,
    deterministic_inline_suggestions,
    validate_inline_preview_response,
)
from comms.services.weave_monitor import (
    _is_duplicate_project_init_error,
    clear_weave_cache,
    content_trace_fields,
    trace_operation,
)


class CommunicationGraphTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Graph Org", description="A graph-enabled organization")
        OrgValue.objects.create(organization=self.org, name="Clarity", description="Make asks clear.")
        self.team = Team.objects.create(
            organization=self.org,
            name="Product",
            description="Roadmap and delivery",
            norms=["Prefer concise bullet points."],
        )
        self.sender = Employee.objects.create(
            organization=self.org,
            team=self.team,
            name="Sender",
            role="PM",
        )
        self.receiver = Employee.objects.create(
            organization=self.org,
            team=self.team,
            name="Dan",
            role="Engineer",
            receiver_prompt="Dan prefers concise context and clear asks.",
            communication_preferences={"style": "concise"},
        )
        OrganizationContext.objects.create(
            organization=self.org,
            operating_context={"planning_horizon": "Q3 roadmap"},
            current_priorities=["Align roadmap delivery with customer risk"],
            communication_patterns=["Use owner, deadline, and customer impact."],
        )
        ProjectContext.objects.create(
            organization=self.org,
            name="Q3 Roadmap Alignment",
            description="Align roadmap tradeoffs and customer-facing commitments.",
            status=ProjectContext.Status.ACTIVE,
            priority="high",
            quarter="Q3",
            team=self.team,
            owner=self.receiver,
            goals=["Prepare roadmap narrative"],
            stakeholders=["Dan", "Sender"],
        )
        MeetingContext.objects.create(
            organization=self.org,
            title="Q3 Roadmap Review",
            meeting_type="roadmap",
            cadence="Weekly",
            status=MeetingContext.Status.RECURRING,
            team=self.team,
            owner=self.receiver,
            participants=["Dan", "Sender"],
            related_projects=["Q3 Roadmap Alignment"],
            summary="Review Q3 roadmap tradeoffs and action items.",
            decisions=["Separate committed and exploratory roadmap items."],
        )
        self.legacy_calls = 0
        self.force_bad_rewrite = False
        self.last_legacy_message = ""

    def _legacy(self, message_text: str, tool_results: dict | None = None) -> Message:
        self.legacy_calls += 1
        self.last_legacy_message = message_text
        overall = "Bananas only." if self.force_bad_rewrite else self._rewrite(message_text)
        scores_before = normalize_scores({
            "clarity": 50,
            "tone": 50,
            "receiver_fit": 50,
            "org_values_alignment": 50,
        })
        scores_after = normalize_scores({
            "clarity": 70,
            "tone": 70,
            "receiver_fit": 70,
            "org_values_alignment": 70,
        })
        message = Message.objects.create(
            organization=self.org,
            sender=self.sender,
            receiver=self.receiver,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            original_text=message_text,
            final_text=message_text,
            overall_suggested_message=overall,
            scores_before=scores_before,
            estimated_scores_after_all=scores_after,
            current_scores=scores_before,
            raw_llm_response={"tool_results_used": bool(tool_results)},
            status=Message.Status.ANALYZED,
        )
        MessageRevision.objects.create(
            message=message,
            version_index=1,
            text=message_text,
            note="Original draft",
        )
        InlineSuggestion.objects.create(
            message=message,
            target_text=message_text,
            start_index=0,
            end_index=len(message_text),
            issue="Needs receiver-aware rewrite",
            suggested_replacement=overall,
            affected_scores={"clarity": 10, "tone": 10, "receiver_fit": 10, "org_values_alignment": 10},
        )
        return message

    def _rewrite(self, message_text: str) -> str:
        lowered = message_text.lower()
        if "roadmap" in lowered:
            return "Dan, could we discuss the Q3 roadmap and align on next steps?"
        if "fix" in lowered:
            return "Could you do what I asked and fix it as soon as possible?"
        return message_text

    def _run(self, text: str):
        runner = CommunicationGraphRunner(
            sender=self.sender,
            receiver=self.receiver,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            legacy_analyze=self._legacy,
        )
        return runner.invoke(text)

    def test_ordinary_messages_bypass_llm_and_tools(self):
        for text in ["Hi Tal,", "Thanks"]:
            state = self._run(text)
            message = Message.objects.get(pk=state["message_id"])
            metadata = state["metadata"]

            self.assertEqual(metadata["route"], "bypass")
            self.assertFalse(metadata["used_llm"])
            self.assertFalse(metadata["used_tools"])
            self.assertEqual(message.final_text, text)

        self.assertEqual(self.legacy_calls, 0)

    def test_typo_cleanup_without_full_rewrite(self):
        state = self._run("Can you send me the reciever file?")
        message = Message.objects.get(pk=state["message_id"])

        self.assertIn(state["metadata"]["route"], {"validate_only", "bypass"})
        self.assertFalse(state["metadata"]["used_llm"])
        self.assertEqual(message.final_text, "Can you send me the receiver file?")

    def test_message_needing_improvement_calls_communication_agent(self):
        state = self._run("you didnt do what i asked fix it asap")

        self.assertEqual(state["metadata"]["route"], "rewrite")
        self.assertTrue(state["metadata"]["used_llm"])
        self.assertEqual(self.legacy_calls, 1)

    def test_typo_cleanup_and_communication_agent_can_coexist(self):
        state = self._run("We wsnt to have it asap")

        self.assertEqual(state["metadata"]["route"], "rewrite")
        self.assertIn("typo_cleanup", state["metadata"]["nodes_executed"])
        self.assertIn("communication_agent", state["metadata"]["nodes_executed"])
        self.assertEqual(self.last_legacy_message, "We want to have it asap")
        self.assertEqual(self.legacy_calls, 1)

    def test_context_tools_are_called_only_when_needed(self):
        simple = self._run("Can you send me the file?")
        contextual = self._run("lets talk about q3 roadmap with dan")

        self.assertFalse(simple["metadata"]["used_tools"])
        self.assertEqual(contextual["metadata"]["route"], "rewrite_with_context")
        self.assertTrue(contextual["metadata"]["used_tools"])
        self.assertIn("suggest_related_projects", contextual["metadata"]["tools_called"])
        self.assertIn("suggest_meeting_context", contextual["metadata"]["tools_called"])
        self.assertIn("get_receiver_profile", contextual["metadata"]["tools_called"])
        self.assertNotIn("get_company_context", contextual["metadata"]["tools_called"])
        self.assertNotIn("retrieve_company_patterns", contextual["metadata"]["tools_called"])
        self.assertEqual(contextual["tool_results"]["suggest_related_projects"][0]["name"], "Q3 Roadmap Alignment")
        self.assertEqual(contextual["tool_results"]["suggest_meeting_context"]["relevant_meetings"][0]["title"], "Q3 Roadmap Review")

    def test_typo_cleanup_and_specific_context_tools_can_coexist(self):
        state = self._run("What about the prokject roadmap meeting with Dan?")

        self.assertEqual(state["metadata"]["route"], "rewrite_with_context")
        self.assertIn("typo_cleanup", state["metadata"]["nodes_executed"])
        self.assertEqual(self.last_legacy_message, "What about the project roadmap meeting with Dan?")
        self.assertIn("suggest_related_projects", state["metadata"]["tools_called"])
        self.assertIn("suggest_meeting_context", state["metadata"]["tools_called"])
        self.assertIn("get_receiver_profile", state["metadata"]["tools_called"])
        self.assertNotIn("get_company_context", state["metadata"]["tools_called"])
        self.assertNotIn("retrieve_company_patterns", state["metadata"]["tools_called"])

    def test_plain_typo_does_not_use_context_tools(self):
        state = self._run("abboput")

        self.assertFalse(state["metadata"]["used_tools"])
        self.assertNotIn("context_tools", state["metadata"]["nodes_executed"])

    def test_entity_name_typo_uses_lookup_only_when_context_is_requested(self):
        ProjectContext.objects.create(
            organization=self.org,
            name="Strategic Operations",
            description="Cross-functional operations initiative for strategic planning.",
            status=ProjectContext.Status.ACTIVE,
            priority="high",
            team=self.team,
            owner=self.receiver,
        )

        bare_entity = self._run("Stratigic Operations")
        contextual_entity = self._run("What's next for Stratigic Operations?")

        self.assertFalse(bare_entity["metadata"]["used_tools"])
        self.assertEqual(contextual_entity["metadata"]["route"], "rewrite_with_context")
        self.assertIn("suggest_related_projects", contextual_entity["metadata"]["tools_called"])
        self.assertNotIn("get_company_context", contextual_entity["metadata"]["tools_called"])
        self.assertNotIn("retrieve_company_patterns", contextual_entity["metadata"]["tools_called"])
        self.assertEqual(contextual_entity["tool_results"]["suggest_related_projects"][0]["name"], "Strategic Operations")

    def test_weave_traces_nodes_tools_and_execution_summary(self):
        trace_calls = []

        def fake_trace_operation(name, inputs, operation, *, output=None):
            result = operation()
            trace_calls.append({
                "name": name,
                "inputs": inputs,
                "output": output(result) if output else None,
            })
            return result

        with patch("comms.services.communication_graph.trace_operation", side_effect=fake_trace_operation):
            self._run("lets talk about q3 roadmap with dan")

        names = [call["name"] for call in trace_calls]
        self.assertIn("communication_agent.graph_run", names)
        self.assertIn("communication_agent.node.input_normalizer", names)
        self.assertIn("communication_agent.node.context_tools", names)
        self.assertIn("communication_agent.tool.suggest_related_projects", names)
        self.assertIn("communication_agent.tool.suggest_meeting_context", names)
        self.assertIn("communication_agent.execution_summary", names)

        summary_call = next(call for call in trace_calls if call["name"] == "communication_agent.execution_summary")
        self.assertEqual(summary_call["output"]["route"], "rewrite_with_context")
        self.assertIn("context_tools", summary_call["output"]["nodes_executed"])
        self.assertIn("suggest_related_projects", summary_call["output"]["tool_result_summaries"])

    def test_write_tools_require_explicit_preference_intent(self):
        contextual = self._run("lets talk about q3 roadmap with dan")
        explicit = self._run("Please remember that Dan prefers concise bullet points")

        self.assertNotIn("update_receiver_preference", contextual["metadata"]["tools_called"])
        self.assertIn("update_receiver_preference", explicit["metadata"]["tools_called"])
        self.receiver.refresh_from_db()
        self.assertIn("concise bullet points", self.receiver.communication_preferences["agent_saved_preferences"])

    def test_company_context_is_available_when_selected(self):
        state = self._run("How does this align with our company values and customer standards?")

        company_context = state["tool_results"]["get_company_context"]
        self.assertEqual(company_context["name"], "Graph Org")
        self.assertEqual(company_context["values"][0]["name"], "Clarity")
        self.assertIn("current_priorities", company_context["context"])

    def test_context_tools_tolerate_project_typo_and_vague_meeting_reference(self):
        projects = suggest_related_projects("what is next for the projerc roadmap?", self.org.id)
        meeting_context = suggest_meeting_context("what about our meeting on the day?", self.receiver.id, self.org.id)

        self.assertEqual(projects[0]["name"], "Q3 Roadmap Alignment")
        self.assertEqual(meeting_context["relevant_meetings"][0]["title"], "Q3 Roadmap Review")

    def test_project_context_uses_receiver_for_vague_project_reference(self):
        ProjectContext.objects.create(
            organization=self.org,
            name="Enterprise Onboarding Reliability",
            description="Improve onboarding reliability for strategic customers.",
            status=ProjectContext.Status.ACTIVE,
            priority="high",
            team=self.team,
            owner=self.receiver,
            goals=["Reduce onboarding failures"],
            stakeholders=[self.receiver.name],
        )

        projects = suggest_related_projects(
            "Regarding our project, how can we improve the onboarding reliability?",
            self.org.id,
            self.receiver.id,
        )

        self.assertEqual(projects[0]["name"], "Enterprise Onboarding Reliability")

    def test_final_validator_flags_bad_rewrite(self):
        self.force_bad_rewrite = True
        state = self._run("you didnt do what i asked fix it asap")

        self.assertFalse(state["metadata"]["validator_passed"])
        self.assertIn("final_validator", [error["node"] for error in state["metadata"]["errors"]])

    def test_latency_metadata_is_returned(self):
        state = self._run("Hi Tal,")
        metadata = state["metadata"]

        self.assertIn("input_normalizer", metadata["nodes_executed"])
        self.assertIn("latency_ms_by_node", metadata)
        self.assertIn("weave_tracing", metadata)
        self.assertGreaterEqual(metadata["total_latency_ms"], 0)

    def test_graph_visualization_artifact_is_created(self):
        runner = CommunicationGraphRunner(
            sender=self.sender,
            receiver=self.receiver,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            legacy_analyze=self._legacy,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = export_graph_visualization(runner.compiled_graph, output_dir=temp_dir)

            self.assertTrue(paths["png"].endswith("communication_agent_graph.png"))
            self.assertTrue(paths["mermaid"].endswith("communication_agent_graph.mmd"))


class MessageAnalyzerCompatibilityTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Compatibility Org")
        self.team = Team.objects.create(organization=self.org, name="Engineering")
        self.sender = Employee.objects.create(organization=self.org, team=self.team, name="Sender", role="PM")
        self.receiver = Employee.objects.create(organization=self.org, team=self.team, name="Receiver", role="Engineer")

    def test_public_analyzer_entrypoint_still_returns_message(self):
        client = FakeLLMClient()
        analyzer = MessageAnalyzer(llm_client=client)

        message = analyzer.analyze(
            sender=self.sender,
            receiver=self.receiver,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            original_message="you didnt do what i asked fix it asap",
        )

        self.assertIsInstance(message, Message)
        self.assertEqual(client.calls, 1)
        self.assertEqual(analyzer.last_metadata["route"], "rewrite")
        self.assertEqual(message.suggestions.count(), 1)
        self.assertIn("agent_metadata", message.raw_llm_response)

    def test_analyze_with_metadata_returns_backward_compatible_tuple(self):
        analyzer = MessageAnalyzer(llm_client=FakeLLMClient())

        message, metadata = analyzer.analyze_with_metadata(
            sender=self.sender,
            receiver=self.receiver,
            channel=Message.Channel.SLACK,
            intent=Message.Intent.REQUEST,
            original_message="you didnt do what i asked fix it asap",
        )

        self.assertEqual(message.id, Message.objects.get().id)
        self.assertEqual(metadata["route"], "rewrite")


class BenchmarkCommandTests(TestCase):
    def test_benchmark_command_outputs_required_metrics(self):
        stdout = StringIO()
        call_command("benchmark_communication_agent", "--iterations", "1", stdout=stdout)

        metrics = json.loads(stdout.getvalue())
        for key in [
            "old_avg_latency_ms",
            "new_avg_latency_ms",
            "old_p95_latency_ms",
            "new_p95_latency_ms",
            "llm_calls_per_100_requests",
            "tool_calls_per_100_requests",
            "bypass_rate",
        ]:
            self.assertIn(key, metrics)


class WeaveMonitorTests(SimpleTestCase):
    def tearDown(self):
        clear_weave_cache()

    def test_disabled_weave_tracing_runs_operation_once(self):
        calls = []

        def operation():
            calls.append("called")
            return {"ok": True}

        with patch.dict(os.environ, {"WEAVE_TRACING": "false"}):
            clear_weave_cache()
            result = trace_operation("test.noop", {"message_length": 4}, operation)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls, ["called"])

    def test_duplicate_project_init_error_is_recoverable(self):
        exc = RuntimeError(
            "{'message': \"Error 1062 (23000): Duplicate entry "
            "'communication-agent-2950600' for key 'projects.ix_projects_name_entity_id'\", "
            "'path': ['upsertModel']}"
        )

        self.assertTrue(_is_duplicate_project_init_error(exc))

    def test_content_trace_fields_are_env_gated_and_truncated(self):
        with patch.dict(os.environ, {"WEAVE_LOG_CONTENT": "false"}):
            self.assertEqual(content_trace_fields(message="secret"), {})

        with patch.dict(os.environ, {"WEAVE_LOG_CONTENT": "true", "WEAVE_LOG_CONTENT_MAX_CHARS": "100"}, clear=False):
            payload = content_trace_fields(message="x" * 140)

        self.assertIn("content", payload)
        self.assertTrue(payload["content"]["message"].endswith("...[truncated]"))

    def test_trace_operation_still_emits_row_when_content_logging_is_off(self):
        calls = []
        outputs = []

        class FakeWeave:
            def op(self, *, name):
                def decorator(func):
                    def wrapped(payload):
                        calls.append({"name": name, "payload": payload})
                        result = func(payload)
                        outputs.append(result)
                        return result

                    return wrapped

                return decorator

        with patch.dict(os.environ, {"WEAVE_LOG_CONTENT": "false"}):
            with patch("comms.services.weave_monitor._weave_module", return_value=FakeWeave()):
                result = trace_operation(
                    "test.no_content_row",
                    {
                        "message_length": 6,
                        **content_trace_fields(message="secret"),
                    },
                    lambda: {"ok": True},
                    output=lambda value: {
                        "ok": value["ok"],
                        **content_trace_fields(message="secret"),
                    },
                )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls[0]["name"], "test.no_content_row")
        self.assertNotIn("content", calls[0]["payload"])
        self.assertNotIn("content", outputs[0]["result"])


class InlinePreviewWeaveTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Inline Weave Org")
        self.team = Team.objects.create(organization=self.org, name="Engineering")
        self.sender = Employee.objects.create(organization=self.org, team=self.team, name="Sender", role="PM")
        self.receiver = Employee.objects.create(organization=self.org, team=self.team, name="Receiver", role="Engineer")

    def test_inline_preview_tracing_noop_does_not_double_call_llm(self):
        client = FakeInlineLLMClient()
        previewer = InlineSuggestionPreviewer(llm_client=client)

        with patch.dict(os.environ, {"WEAVE_TRACING": "false"}):
            clear_weave_cache()
            result = previewer.preview(
                sender=self.sender,
                receiver=self.receiver,
                channel=Message.Channel.SLACK,
                intent=Message.Intent.REQUEST,
                full_draft="Can you review the meeting deadline?",
                changed_text="meeting deadline",
                surrounding_context="Can you review the",
            )

        self.assertEqual(client.calls, 1)
        self.assertEqual(result["text_hash"], "c1f4b6ab7ffd")
        self.assertEqual(len(result["suggestions"]), 1)

    def test_inline_preview_weave_traces_nodes_tools_and_execution_summary(self):
        trace_calls = []
        client = FakeInlineLLMClient()
        previewer = InlineSuggestionPreviewer(llm_client=client)

        def fake_trace_operation(name, inputs, operation, *, output=None):
            result = operation()
            trace_calls.append({
                "name": name,
                "inputs": inputs,
                "output": output(result) if output else None,
            })
            return result

        with patch.dict(os.environ, {"WEAVE_LOG_CONTENT": "false"}):
            with patch("comms.services.inline_preview.trace_operation", side_effect=fake_trace_operation):
                result = previewer.preview(
                    sender=self.sender,
                    receiver=self.receiver,
                    channel=Message.Channel.SLACK,
                    intent=Message.Intent.REQUEST,
                    full_draft="Can you review the meeting deadline?",
                    changed_text="meeting deadline",
                    surrounding_context="Can you review the",
                )

        names = [call["name"] for call in trace_calls]
        self.assertIn("communication_agent.inline_preview", names)
        self.assertIn("communication_agent.inline_preview.node.input_normalizer", names)
        self.assertIn("communication_agent.inline_preview.node.router", names)
        self.assertIn("communication_agent.inline_preview.node.context_tools", names)
        self.assertIn("communication_agent.inline_preview.node.prompt_builder", names)
        self.assertIn("communication_agent.inline_preview.node.llm_preview", names)
        self.assertIn("communication_agent.inline_preview.node.final_validator", names)
        self.assertIn("communication_agent.inline_preview.node.final_response", names)
        self.assertIn("communication_agent.inline_preview.execution_summary", names)
        self.assertIn("communication_agent.inline_preview.tool.suggest_meeting_context", names)
        self.assertNotIn("communication_agent.inline_preview.tool.get_company_context", names)
        self.assertNotIn("communication_agent.inline_preview.tool.retrieve_company_patterns", names)

        outer_call = next(call for call in trace_calls if call["name"] == "communication_agent.inline_preview")
        final_call = next(call for call in trace_calls if call["name"] == "communication_agent.inline_preview.node.final_response")
        self.assertIn("suggest_meeting_context", outer_call["output"]["tools_called"])
        self.assertTrue(outer_call["output"]["used_tools"])
        self.assertIn("llm_preview", outer_call["output"]["nodes_executed"])
        self.assertIn("steps", outer_call["output"])
        self.assertNotIn("content", outer_call["inputs"])
        self.assertNotIn("content", outer_call["output"])
        self.assertNotIn("content", final_call["output"])
        self.assertEqual(result["text_hash"], "c1f4b6ab7ffd")
        self.assertEqual(client.calls, 1)

    def test_inline_preview_routes_only_relevant_tools_for_mixed_typo_and_context(self):
        trace_calls = []
        previewer = InlineSuggestionPreviewer(llm_client=FakeEmptyInlineLLMClient())

        def fake_trace_operation(name, inputs, operation, *, output=None):
            result = operation()
            trace_calls.append({
                "name": name,
                "inputs": inputs,
                "output": output(result) if output else None,
            })
            return result

        with patch("comms.services.inline_preview.trace_operation", side_effect=fake_trace_operation):
            previewer.preview(
                sender=self.sender,
                receiver=self.receiver,
                channel=Message.Channel.SLACK,
                intent=Message.Intent.REQUEST,
                full_draft="What about the prokject roadmap meeting with Dana?",
                changed_text="What about the prokject roadmap meeting with Dana?",
                surrounding_context="",
            )

        outer_call = next(call for call in trace_calls if call["name"] == "communication_agent.inline_preview")
        tools_called = outer_call["output"]["tools_called"]
        self.assertIn("suggest_related_projects", tools_called)
        self.assertIn("suggest_meeting_context", tools_called)
        self.assertIn("get_receiver_profile", tools_called)
        self.assertNotIn("get_company_context", tools_called)
        self.assertNotIn("retrieve_company_patterns", tools_called)

    def test_inline_preview_plain_typo_stays_tool_free_but_entity_context_routes(self):
        ProjectContext.objects.create(
            organization=self.org,
            name="Strategic Operations",
            description="Cross-functional operations initiative for strategic planning.",
            status=ProjectContext.Status.ACTIVE,
            priority="high",
            team=self.team,
            owner=self.receiver,
        )
        previewer = InlineSuggestionPreviewer(llm_client=FakeEmptyInlineLLMClient())

        def run_and_collect_tools(changed_text):
            trace_calls = []

            def fake_trace_operation(name, inputs, operation, *, output=None):
                result = operation()
                trace_calls.append({
                    "name": name,
                    "inputs": inputs,
                    "output": output(result) if output else None,
                })
                return result

            with patch("comms.services.inline_preview.trace_operation", side_effect=fake_trace_operation):
                previewer.preview(
                    sender=self.sender,
                    receiver=self.receiver,
                    channel=Message.Channel.SLACK,
                    intent=Message.Intent.REQUEST,
                    full_draft=changed_text,
                    changed_text=changed_text,
                    surrounding_context="",
                )
            outer_call = next(call for call in trace_calls if call["name"] == "communication_agent.inline_preview")
            return outer_call["output"]["tools_called"]

        self.assertEqual(run_and_collect_tools("abboput"), [])
        contextual_tools = run_and_collect_tools("What's next for Stratigic Operations?")
        self.assertIn("suggest_related_projects", contextual_tools)
        self.assertNotIn("get_company_context", contextual_tools)
        self.assertNotIn("retrieve_company_patterns", contextual_tools)

    def test_inline_preview_trace_content_is_opt_in_and_project_name_is_suggested(self):
        ProjectContext.objects.create(
            organization=self.org,
            name="Enterprise Onboarding Reliability",
            description="Improve onboarding reliability for strategic customers.",
            status=ProjectContext.Status.ACTIVE,
            priority="high",
            team=self.team,
            owner=self.receiver,
            goals=["Reduce onboarding failures"],
            stakeholders=[self.receiver.name],
        )
        trace_calls = []
        previewer = InlineSuggestionPreviewer(llm_client=FakeEmptyInlineLLMClient())

        def fake_trace_operation(name, inputs, operation, *, output=None):
            result = operation()
            trace_calls.append({
                "name": name,
                "inputs": inputs,
                "output": output(result) if output else None,
            })
            return result

        with patch.dict(os.environ, {"WEAVE_LOG_CONTENT": "true"}):
            with patch("comms.services.inline_preview.trace_operation", side_effect=fake_trace_operation):
                result = previewer.preview(
                    sender=self.sender,
                    receiver=self.receiver,
                    channel=Message.Channel.SLACK,
                    intent=Message.Intent.REQUEST,
                    full_draft="Hey Dana, Regarding our project, how can we improve the onboarding reliability?",
                    changed_text="Regarding our project, how can we improve the onboarding reliability?",
                    surrounding_context="Hey Dana,",
                )

        targets = [suggestion["target_text"] for suggestion in result["suggestions"]]
        outer_call = next(call for call in trace_calls if call["name"] == "communication_agent.inline_preview")
        final_call = next(call for call in trace_calls if call["name"] == "communication_agent.inline_preview.node.final_response")

        self.assertIn("our project", targets)
        self.assertIn("content", outer_call["inputs"])
        self.assertEqual(outer_call["inputs"]["content"]["changed_text"], "Regarding our project, how can we improve the onboarding reliability?")
        self.assertIn("content", final_call["output"])
        self.assertEqual(final_call["output"]["content"]["suggestions"][0]["suggested_replacement"], "Enterprise Onboarding Reliability")

    def test_inline_preview_validator_skips_malformed_suggestion_items(self):
        result = validate_inline_preview_response(
            {
                "inline_suggestions": [
                    "not an object",
                    {"target_text": "deadline", "suggested_replacement": ""},
                    {
                        "target_text": "deadline",
                        "suggested_replacement": "realistic deadline",
                        "issue": "Clarify timing",
                        "reason": "Makes the ask actionable.",
                    },
                ]
            },
            "Can you confirm the deadline?",
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["target_text"], "deadline")

    def test_inline_preview_validator_tolerates_non_list_suggestions_field(self):
        singleton = validate_inline_preview_response(
            {
                "inline_suggestions": {
                    "target_text": "alos",
                    "suggested_replacement": "also",
                    "issue": "Typo",
                }
            },
            "alos can we make it shiny?",
        )
        scalar = validate_inline_preview_response(
            {"inline_suggestions": "not a list"},
            "alos can we make it shiny?",
        )

        self.assertEqual(len(singleton), 1)
        self.assertEqual(singleton[0]["target_text"], "alos")
        self.assertEqual(scalar, [])

    def test_inline_preview_validator_preserves_correction_and_overlapping_rewrite(self):
        result = validate_inline_preview_response(
            {
                "inline_suggestions": [
                    {
                        "target_text": "Regarding the projct we discussed",
                        "suggested_replacement": "Regarding the project we discussed, could you share the next phase?",
                        "issue": "Clarify ask",
                        "reason": "Broad communication rewrite.",
                    },
                    {
                        "target_text": "projct",
                        "suggested_replacement": "project",
                        "issue": "Typo",
                        "reason": "Corrects spelling without changing the ask.",
                    },
                ]
            },
            "Regarding the projct we discussed",
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(sorted(item["target_text"] for item in result), sorted([
            "projct",
            "Regarding the projct we discussed",
        ]))

    def test_deterministic_inline_suggestions_catch_vague_polish_and_blunt_fetch(self):
        result = deterministic_inline_suggestions(
            "Let's do it good and great and shiny! now we need it to have interactive scheme right? go fetch the data right now?"
        )
        targets = [suggestion["target_text"] for suggestion in result]

        self.assertIn("Let's do it good and great and shiny!", targets)
        self.assertIn("go fetch the data right now?", targets)

    def test_inline_preview_adds_deterministic_suggestions_when_llm_misses_obvious_phrases(self):
        previewer = InlineSuggestionPreviewer(llm_client=FakeEmptyInlineLLMClient())

        with patch.dict(os.environ, {"WEAVE_TRACING": "false"}):
            clear_weave_cache()
            result = previewer.preview(
                sender=self.sender,
                receiver=self.receiver,
                channel=Message.Channel.SLACK,
                intent=Message.Intent.REQUEST,
                full_draft=(
                    "Hey Dana, Let's do it good and great and shiny! "
                    "go fetch the data right now?"
                ),
                changed_text=(
                    "Let's do it good and great and shiny! "
                    "go fetch the data right now?"
                ),
                surrounding_context="Hey Dana,",
            )

        targets = [suggestion["target_text"] for suggestion in result["suggestions"]]
        self.assertIn("Let's do it good and great and shiny!", targets)
        self.assertIn("go fetch the data right now?", targets)


class OrganizationContextSeedImportTests(TestCase):
    def test_seed_pseudo_org_creates_projects_meetings_and_context(self):
        call_command("seed_pseudo_org", stdout=StringIO())

        northstar = Organization.objects.get(name="Northstar Labs")
        office = Organization.objects.get(name="The Office")

        self.assertTrue(northstar.projects.filter(name="Q3 Roadmap Alignment").exists())
        self.assertTrue(northstar.meeting_contexts.filter(title="Q3 Roadmap Review").exists())
        self.assertTrue(northstar.context.current_priorities)
        self.assertTrue(office.projects.filter(name="Branch Forecast Refresh").exists())
        self.assertTrue(office.meeting_contexts.filter(title="Monday Branch Priorities").exists())

    def test_import_org_creates_projects_meetings_and_context(self):
        payload = {
            "organization": {"name": "Imported Context Org", "description": "Demo"},
            "teams": [{"name": "Engineering", "description": "", "norms": []}],
            "employees": [
                {
                    "name": "Alex",
                    "role": "Engineer",
                    "team": "Engineering",
                    "manager": None,
                    "seniority_level": "IC",
                    "communication_preferences": {},
                    "pain_points": [],
                    "receiver_prompt": "Be clear.",
                }
            ],
            "context": {
                "operating_context": {"stage": "pilot"},
                "current_priorities": ["Ship pilot"],
                "communication_patterns": ["Name owners"],
            },
            "projects": [
                {
                    "name": "Pilot Launch",
                    "team": "Engineering",
                    "owner": "Alex",
                    "status": "active",
                    "goals": ["Launch"],
                }
            ],
            "meetings": [
                {
                    "title": "Pilot Standup",
                    "team": "Engineering",
                    "owner": "Alex",
                    "participants": ["Alex"],
                    "related_projects": ["Pilot Launch"],
                }
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as temp:
            json.dump(payload, temp)
            temp_path = temp.name

        call_command("import_org", temp_path, stdout=StringIO())

        org = Organization.objects.get(name="Imported Context Org")
        self.assertEqual(org.context.current_priorities, ["Ship pilot"])
        self.assertEqual(org.projects.get().owner.name, "Alex")
        self.assertEqual(org.meeting_contexts.get().related_projects, ["Pilot Launch"])


class FakeLLMClient:
    def __init__(self):
        self.calls = 0

    def chat_json(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> dict:
        self.calls += 1
        return {
            "overall_suggested_message": "Could you do what I asked and fix it as soon as possible?",
            "subject_line": "",
            "slack_short_version": "Could you fix this as soon as possible?",
            "teams_short_version": "Could you fix this as soon as possible?",
            "inline_suggestions": [
                {
                    "id": "s1",
                    "target_text": "fix it",
                    "start_index": None,
                    "end_index": None,
                    "issue": "Too blunt",
                    "suggested_replacement": "fix this as soon as possible",
                    "reason": "Keeps the ask while improving tone.",
                    "affected_scores": {"clarity": 5, "tone": 10, "receiver_fit": 5, "org_values_alignment": 5},
                    "org_values_used": [],
                }
            ],
            "scores_before": {"clarity": 50, "tone": 50, "receiver_fit": 50, "org_values_alignment": 50},
            "estimated_scores_after_all_suggestions": {
                "clarity": 70,
                "tone": 70,
                "receiver_fit": 70,
                "org_values_alignment": 70,
            },
            "risks": [],
            "summary_of_changes": "Improved tone and actionability.",
            "explanation": "Receiver-aware rewrite.",
        }


class FakeInlineLLMClient:
    def __init__(self):
        self.calls = 0

    def chat_json(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> dict:
        self.calls += 1
        return {
            "inline_suggestions": [
                {
                    "target_text": "deadline",
                    "suggested_replacement": "realistic deadline",
                    "issue": "Clarify timing",
                    "reason": "Makes the ask more actionable.",
                    "affected_scores": {"clarity": 5},
                }
            ]
        }


class FakeEmptyInlineLLMClient:
    def __init__(self):
        self.calls = 0

    def chat_json(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> dict:
        self.calls += 1
        return {"inline_suggestions": []}
