import tempfile
import unittest
from pathlib import Path

from agent_runtime.core.communication import (
    CapabilityProfileManagerLite,
    CapabilityRouterLite,
)
from agent_runtime.memory.context_views import (
    build_minimal_context_view,
    consumer_context_from_profile,
)
from agent_runtime.state.state_pool import StatePoolLite, StateRef


class DynamicCapabilityProfileTest(unittest.TestCase):
    def test_arbitrary_agent_names_derive_profiles_without_fixed_roles(self) -> None:
        profiles = CapabilityProfileManagerLite([])
        profile = profiles.register_or_update(
            agent_id="EvidenceScout42",
            role="KnowledgeSpecialist",
            role_description="Research sources, retrieve evidence, and rank citations.",
            system_prompt="Verify claims against reliable evidence before responding.",
        )

        self.assertIsNotNone(profile)
        self.assertIn("retrieval", profile.capabilities)
        self.assertIn("evidence_ranking", profile.capabilities)
        self.assertIn("RETRIEVE_EVIDENCE", profile.preferred_actions)
        self.assertIn("retrieval_state", profile.accepted_state_types)
        self.assertIsNone(profiles.get("planner"))
        self.assertIsNone(profiles.get("writer"))
        self.assertIsNone(profiles.get("reviewer"))

        chinese_profile = profiles.register_or_update(
            agent_id="证据专员",
            role="研究专家",
            role_description="负责检索证据、核验来源并整理引用。",
        )
        self.assertIn("retrieval", chinese_profile.capabilities)
        self.assertIn("validation", chinese_profile.capabilities)

    def test_tool_synchronizer_adds_and_removes_tool_capabilities(self) -> None:
        profiles = CapabilityProfileManagerLite([])
        profile = profiles.register_or_update(
            agent_id="SandboxWorker",
            role="Worker",
            tools=[
                {
                    "tool_id": "python_runner",
                    "description": "Execute and debug Python code",
                    "capability_tags": ["coding", "execution"],
                    "supported_actions": ["RUN_CODE"],
                    "cost_level": 0.25,
                }
            ],
        )
        initial_version = profile.profile_version

        self.assertIn("python_runner", profile.available_tools)
        self.assertIn("coding", profile.tool_capabilities)
        self.assertIn("RUN_CODE", profile.preferred_actions)
        tool_rows = profile.to_dict()["tool_capabilities"]
        self.assertTrue(
            any(row["source"] == "tool:python_runner" for row in tool_rows)
        )
        self.assertEqual(profile.to_dict()["tool_cost_levels"]["python_runner"], 0.25)

        profile = profiles.sync_tools("SandboxWorker", [])

        self.assertGreater(profile.profile_version, initial_version)
        self.assertEqual(profile.available_tools, set())
        self.assertEqual(profile.tool_capabilities, {})
        self.assertEqual(profile.tool_capability_sources, {})
        self.assertNotIn("RUN_CODE", profile.preferred_actions)

    def test_runtime_instance_alias_does_not_replace_business_profile(self) -> None:
        profiles = CapabilityProfileManagerLite([])
        profile = profiles.register_or_update(
            agent_id="DeliveryComposer",
            role="SynthesisSpecialist",
            role_description="Synthesize evidence and write the final deliverable.",
            registry_scope="business",
        )
        initial_version = profile.profile_version

        aliased = profiles.register_or_update(
            agent_id="DeliveryComposer",
            role="ChatAgentContainer",
            declared_capabilities=["orchestration"],
            registry_scope="business",
            instance_aliases=[
                "DeliveryComposer_12345678-1234-1234-1234-123456789abc_"
                "12345678-1234-1234-1234-123456789abc"
            ],
            alias_only=True,
        )
        profiles.register_or_update(
            agent_id="SingleThreadedAgentRuntime",
            role="SingleThreadedAgentRuntime",
            registry_scope="system",
        )

        self.assertIs(aliased, profile)
        self.assertEqual(aliased.role, "SynthesisSpecialist")
        self.assertEqual(aliased.profile_version, initial_version)
        self.assertIn("synthesis", aliased.capabilities)
        self.assertNotIn("orchestration", aliased.capabilities)
        self.assertEqual(
            profiles.agent_ids(registry_scope="business"),
            ["DeliveryComposer"],
        )
        self.assertEqual(
            profiles.agent_ids(registry_scope="system"),
            ["SingleThreadedAgentRuntime"],
        )

    def test_runtime_feedback_updates_reliability_cost_and_learned_action(self) -> None:
        profiles = CapabilityProfileManagerLite([])
        profile = profiles.register_or_update(
            agent_id="MetricInspector",
            role="Data specialist",
            role_description="Analyze metrics and statistics.",
        )
        initial_version = profile.profile_version

        profiles.record_execution(
            "MetricInspector",
            success=True,
            schema_valid=True,
            action="ANALYZE_DATA",
            cost_tokens=320,
            latency_ms=1250,
        )
        profile = profiles.record_execution(
            "MetricInspector",
            success=False,
            schema_valid=False,
            action="ANALYZE_DATA",
            cost_tokens=80,
            latency_ms=250,
        )

        self.assertEqual(profile.success_count, 1)
        self.assertEqual(profile.failure_count, 1)
        self.assertEqual(profile.total_cost_tokens, 400)
        self.assertEqual(profile.average_latency_ms, 750)
        self.assertEqual(profile.schema_reliability, 0.5)
        self.assertIn("ANALYZE_DATA", profile.runtime_preferred_actions)
        self.assertGreater(profile.profile_version, initial_version)

    def test_execution_load_and_memory_locality_drive_equivalent_tie_break(self) -> None:
        profiles = CapabilityProfileManagerLite([])
        for agent_id in ("ComposerEast", "ComposerWest"):
            profiles.register_or_update(
                agent_id=agent_id,
                role="Delivery specialist",
                role_description="Write and synthesize a final deliverable.",
            )
        profiles.record_execution_start(
            "ComposerEast",
            memory_keys=["memory_shared_context"],
        )
        profiles.record_execution_start("ComposerWest")
        profiles.record_execution_start("ComposerWest")
        router = CapabilityRouterLite(profiles)

        decision = router.resolve_tie(
            ["ComposerEast", "ComposerWest"],
            task_id="T-locality",
            action="WRITE_OUTPUT",
            memory_locality={"ComposerEast": 1.0, "ComposerWest": 0.0},
            current_load={"ComposerEast": 1.0, "ComposerWest": 2.0},
        )

        self.assertEqual(decision.receiver, "ComposerEast")
        self.assertEqual(profiles.get("ComposerEast").current_load, 1)
        profiles.record_execution("ComposerEast", success=True)
        self.assertEqual(profiles.get("ComposerEast").current_load, 0)

    def test_semantic_uncertainty_uses_dynamic_planning_capability(self) -> None:
        profiles = CapabilityProfileManagerLite([])
        profiles.register_or_update(
            agent_id="FlowConductor",
            role="Workflow coordinator",
            role_description="Decompose unclear tasks and coordinate the workflow.",
        )
        profiles.register_or_update(
            agent_id="MediaWorker",
            role="Media worker",
            role_description="Process media artifacts.",
        )
        router = CapabilityRouterLite(profiles)

        active = router.route(
            sender="MediaWorker",
            declared_receiver="MediaWorker",
            state_refs=[],
            readiness="ready",
            required_action="TRANSCODE_VIDEO",
            candidates=["MediaWorker", "FlowConductor"],
            routing_mode="active",
        )
        advisory = router.route(
            sender="MediaWorker",
            declared_receiver="MediaWorker",
            state_refs=[],
            readiness="ready",
            required_action="TRANSCODE_VIDEO",
            candidates=["MediaWorker", "FlowConductor"],
            routing_mode="advisory",
        )

        self.assertTrue(active.planner_fallback_required)
        self.assertIn("action_not_in_routing_table", active.planner_fallback_reasons)
        self.assertEqual(active.receiver, "FlowConductor")
        self.assertEqual(active.recommended_receiver, "FlowConductor")
        self.assertEqual(advisory.receiver, "MediaWorker")
        self.assertEqual(advisory.recommended_receiver, "FlowConductor")
        self.assertIn("planner_fallback_advisory", advisory.reasons)

    def test_equivalent_resources_do_not_trigger_planner_fallback(self) -> None:
        profiles = CapabilityProfileManagerLite([])
        for agent_id in ("DraftNodeA", "DraftNodeB"):
            profiles.register_or_update(
                agent_id=agent_id,
                role="Writer",
                role_description="Write and synthesize the final deliverable.",
            )
        decision = CapabilityRouterLite(profiles).route(
            sender="DraftNodeA",
            declared_receiver="DraftNodeA",
            state_refs=[],
            readiness="ready",
            required_action="WRITE_OUTPUT",
            candidates=["DraftNodeA", "DraftNodeB"],
            task_id="T-equivalent",
        )

        self.assertFalse(decision.planner_fallback_required)
        self.assertIn(decision.receiver, {"DraftNodeA", "DraftNodeB"})

    def test_router_scores_custom_agents_but_advisory_mode_preserves_framework(self) -> None:
        profiles = CapabilityProfileManagerLite([])
        profiles.register_or_update(
            agent_id="EvidenceScout",
            role="Research specialist",
            role_description="Retrieve and rank evidence.",
        )
        profiles.register_or_update(
            agent_id="DeliveryComposer",
            role="Synthesis specialist",
            role_description="Integrate evidence and write the final deliverable.",
        )
        router = CapabilityRouterLite(profiles)
        state = StateRef(
            state_id="state_retrieval",
            state_type="retrieval_state",
            version=1,
            payload_kind="structured_non_text",
            contains_embedding_refs=True,
            usage_hint="summary_context_selection",
            tier="hot",
        )

        active = router.route(
            sender="EvidenceScout",
            declared_receiver="EvidenceScout",
            state_refs=[state],
            readiness="ready",
            required_action="WRITE_OUTPUT",
            candidates=["EvidenceScout", "DeliveryComposer"],
        )
        advisory = router.route(
            sender="EvidenceScout",
            declared_receiver="EvidenceScout",
            state_refs=[state],
            readiness="ready",
            required_action="WRITE_OUTPUT",
            candidates=["EvidenceScout", "DeliveryComposer"],
            routing_mode="advisory",
        )

        self.assertEqual(active.receiver, "DeliveryComposer")
        self.assertEqual(advisory.receiver, "EvidenceScout")
        self.assertFalse(advisory.route_changed)
        self.assertGreater(
            advisory.candidate_scores["DeliveryComposer"],
            advisory.candidate_scores["EvidenceScout"],
        )

    def test_route_score_matches_final_design_formula_and_neutral_prior(self) -> None:
        profiles = CapabilityProfileManagerLite([])
        profile = profiles.register_or_update(
            agent_id="DraftEngine",
            role="Delivery engine",
            declared_capabilities=[
                "synthesis",
                "writing",
                "final_deliverable_draft",
            ],
            preferred_actions=["WRITE_OUTPUT"],
        )
        decision = CapabilityRouterLite(profiles).route(
            sender="DraftEngine",
            declared_receiver="DraftEngine",
            state_refs=[],
            readiness="ready",
            required_action="WRITE_OUTPUT",
            candidates=["DraftEngine"],
        )

        self.assertEqual(profile.history_success, 0.5)
        self.assertEqual(profile.schema_reliability, 0.5)
        self.assertAlmostEqual(decision.candidate_scores["DraftEngine"], 0.8)

    def test_same_consumer_gets_action_specific_minimal_views(self) -> None:
        profiles = CapabilityProfileManagerLite([])
        profile = profiles.register_or_update(
            agent_id="EvidenceAuditor",
            role="Research and validation specialist",
            role_description="Retrieve evidence, verify claims, and review risks.",
        )
        consumer = consumer_context_from_profile(
            profile,
            consumer_id="EvidenceAuditor",
        )
        source = """
objective: compare two designs
evidence: source S1 supports design A
scores: S1=0.92
risks: design A has a stale dependency
deliverable: choose one design
revisions: verify the dependency before acceptance
"""

        retrieval = build_minimal_context_view(
            query="retrieve and rank evidence",
            prompt_views=[source],
            consumer=consumer,
            action="RETRIEVE_EVIDENCE",
        )
        review = build_minimal_context_view(
            query="review risks before acceptance",
            prompt_views=[source],
            consumer=consumer,
            action="REVIEW_OUTPUT",
        )

        self.assertEqual(retrieval.consumer_id, "EvidenceAuditor")
        self.assertIn("evidence", retrieval.information_fields)
        self.assertIn("coverage", retrieval.information_fields)
        self.assertIn("risks", review.information_fields)
        self.assertIn("revisions", review.information_fields)
        self.assertNotEqual(retrieval.information_fields, review.information_fields)
        self.assertNotIn("semantic_role", retrieval.text)

    def test_state_access_uses_capability_action_not_agent_class_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = StatePoolLite(Path(tmp))
            state_ref, _ = pool.write_state(
                task_id="T1",
                source_agent="EvidenceScout",
                state_type="retrieval_state",
                payload={
                    "chunk_ids": ["c1", "c2", "c3"],
                    "source_ids": ["s1", "s2", "s3"],
                    "score_map": {"c1": 0.9, "c2": 0.8, "c3": 0.7},
                    "evidence_rank": ["c1", "c2", "c3"],
                    "chunks": {
                        "c1": {"text": "evidence one"},
                        "c2": {"text": "evidence two"},
                        "c3": {"text": "evidence three"},
                    },
                },
                summary="retrieval state",
                usage_hint="summary_context_selection",
            )

            metadata, metadata_report = pool.render_prompt_view_with_report(
                state_ref,
                "CoordinatorX",
                capabilities=["orchestration"],
                action="ROUTE_TASK",
            )
            evidence, evidence_report = pool.render_prompt_view_with_report(
                state_ref,
                "ResearcherY",
                capabilities=["evidence_ranking"],
                action="VERIFY_CLAIM",
            )

            self.assertEqual(metadata_report.access_level, "metadata")
            self.assertNotIn("evidence one", metadata)
            self.assertEqual(evidence_report.access_level, "evidence_snippets")
            self.assertIn("evidence three", evidence)


if __name__ == "__main__":
    unittest.main()
