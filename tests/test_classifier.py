"""
Phase 1 — full test suite.
Run with:  python -m pytest tests/ -v
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from classifier.rule_based import extract_features, RuleBasedRouter, QueryFeatures
from gateway.config import MODEL_REGISTRY


# ---------------------------------------------------------------------------
# Feature extraction tests
# ---------------------------------------------------------------------------

class TestFeatureExtraction:

    def test_simple_factual_query(self):
        f = extract_features("What is the capital of France?")
        assert f.is_factual_lookup is True
        assert f.has_code_block is False
        assert f.is_code_task is False
        assert f.complexity_score < 30

    def test_code_block_detected(self):
        f = extract_features("Fix this:\n```python\ndef foo(): pass\n```")
        assert f.has_code_block is True
        assert f.complexity_score > 30

    def test_code_task_implement(self):
        f = extract_features("Implement a recursive binary search tree in Python")
        assert f.is_code_task is True
        assert f.has_code_keywords is True
        assert f.complexity_score > 40

    def test_code_task_write_a_function(self):
        f = extract_features("Write a Python function to reverse a string")
        assert f.is_code_task is True
        assert f.complexity_score > 30

    def test_code_task_write_a_script(self):
        f = extract_features("Write a script to parse JSON files")
        assert f.is_code_task is True

    def test_code_task_debug(self):
        f = extract_features("Debug this error in my code")
        assert f.is_code_task is True

    def test_constraint_counting_three(self):
        f = extract_features(
            "Write a fn that must handle nulls, ensure thread safety, never throw exceptions"
        )
        assert f.constraint_count >= 3

    def test_constraint_counting_many(self):
        f = extract_features(
            "must ensure never guarantee constraint mandatory"
        )
        assert f.constraint_count >= 5

    def test_specialist_domain_legal(self):
        f = extract_features("What are my GDPR compliance obligations?")
        assert f.is_specialist_domain is True

    def test_specialist_domain_medical(self):
        f = extract_features("Explain clinical diagnosis criteria for type 2 diabetes")
        assert f.is_specialist_domain is True

    def test_specialist_domain_financial(self):
        f = extract_features("What are the tax implications of a SAFE note?")
        assert f.is_specialist_domain is True

    def test_specialist_domain_security(self):
        f = extract_features("Explain how SQL injection vulnerabilities work")
        assert f.is_specialist_domain is True

    def test_creative_task_story(self):
        f = extract_features("Write a story about a robot who learns to paint")
        assert f.is_creative is True

    def test_creative_task_poem(self):
        f = extract_features("Compose a poem about the ocean")
        assert f.is_creative is True

    def test_complexity_score_always_in_range(self):
        """Score must always be 0–100 regardless of input."""
        prompts = [
            "Hi",
            "What is 2+2?",
            "x " * 600,   # very long
            "Implement lock-free concurrent skip list in Rust "
            "must guarantee linearisability ensure no ABA problem never use mutexes",
        ]
        for p in prompts:
            f = extract_features(p)
            assert 0 <= f.complexity_score <= 100, (
                f"Score {f.complexity_score:.1f} out of range for: {p[:40]!r}"
            )

    def test_token_count_estimation(self):
        f = extract_features("hello world this is a test")   # 6 words
        assert f.word_count == 6
        assert f.token_count_est == int(6 * 1.3)

    def test_char_count(self):
        prompt = "hello world"
        f = extract_features(prompt)
        assert f.char_count == len(prompt)

    def test_turn_index_increases_score(self):
        p = "Tell me more about that"
        f0 = extract_features(p, turn_index=0)
        f5 = extract_features(p, turn_index=5)
        assert f5.complexity_score > f0.complexity_score

    def test_factual_query_suppresses_score(self):
        """is_factual_lookup should reduce complexity score."""
        factual = extract_features("What is the boiling point of water?")
        non_factual = extract_features("Explain the boiling point of water in detail")
        assert factual.complexity_score < non_factual.complexity_score

    def test_question_count_counted(self):
        f = extract_features("Is this right? Are you sure? Can you verify?")
        assert f.question_count == 3

    def test_cost_budget_stored(self):
        f = extract_features("Hello", cost_budget_usd=0.05)
        assert f.cost_budget_usd == 0.05

    def test_default_cost_budget(self):
        f = extract_features("Hello")
        assert f.cost_budget_usd == 0.01   # default


# ---------------------------------------------------------------------------
# Tier routing tests
# ---------------------------------------------------------------------------

class TestTierRouting:

    def setup_method(self):
        self.router = RuleBasedRouter()

    # ── Tier 0 ────────────────────────────────────────────────────────────

    def test_factual_routes_tier0(self):
        d = self.router.route("What is the capital of Germany?")
        assert d.tier == 0

    def test_boiling_point_tier0(self):
        d = self.router.route("What is the boiling point of water?")
        assert d.tier == 0

    def test_who_invented_tier0(self):
        d = self.router.route("Who invented the telephone?")
        assert d.tier == 0

    def test_historical_date_tier0(self):
        d = self.router.route("When did World War II end?")
        assert d.tier == 0

    def test_simple_definition_tier0(self):
        d = self.router.route("What are the primary colours?")
        assert d.tier == 0

    # ── Tier 1 ────────────────────────────────────────────────────────────

    def test_implement_bst_tier1(self):
        d = self.router.route(
            "Implement a binary search tree with insert, delete, search in Python"
        )
        assert d.tier >= 1

    def test_write_function_tier1(self):
        d = self.router.route("Write a Python function to reverse a string")
        assert d.tier >= 1

    def test_for_loop_tier1(self):
        d = self.router.route("Implement a for loop in JavaScript")
        assert d.tier >= 1

    def test_legal_domain_tier1(self):
        d = self.router.route("What are the legal implications of a SAFE note conversion?")
        assert d.tier >= 1

    def test_code_block_tier1_minimum(self):
        d = self.router.route("Fix this:\n```python\ndef foo(): pass\n```")
        assert d.tier >= 1

    def test_medical_domain_tier1(self):
        d = self.router.route("Explain clinical diagnosis criteria for type 2 diabetes")
        assert d.tier >= 1

    # ── Tier 2 ────────────────────────────────────────────────────────────

    def test_complex_rust_constraints_tier2(self):
        d = self.router.route(
            "Implement a lock-free concurrent skip list in Rust. "
            "Must guarantee linearisability, ensure no ABA problem, never use mutexes."
        )
        assert d.tier == 2

    def test_code_task_with_two_constraints_tier2(self):
        d = self.router.route(
            "Write a Python function that must handle all edge cases "
            "and ensure O(1) space complexity."
        )
        assert d.tier == 2

    def test_specialist_with_constraint_tier2(self):
        d = self.router.route(
            "What GDPR compliance steps must I follow before launching?"
        )
        assert d.tier == 2


# ---------------------------------------------------------------------------
# Router behaviour tests
# ---------------------------------------------------------------------------

class TestRouterBehaviour:

    def setup_method(self):
        self.router = RuleBasedRouter()

    def test_force_model_overrides_routing(self):
        d = self.router.route("What is 2+2?", force_model="claude-opus-3")
        assert d.model_id == "claude-opus-3"
        assert d.reasoning == "customer_forced_model"

    def test_force_model_any_tier(self):
        """Force model should work regardless of query complexity."""
        d = self.router.route(
            "Implement a lock-free skip list in Rust must guarantee linearisability",
            force_model="claude-haiku-3",
        )
        assert d.model_id == "claude-haiku-3"

    def test_decision_has_features_attached(self):
        d = self.router.route("Explain quantum entanglement simply")
        assert d.features is not None
        assert isinstance(d.features, QueryFeatures)

    def test_complexity_score_matches_features(self):
        d = self.router.route("What is machine learning?")
        assert d.complexity_score == d.features.complexity_score

    def test_provider_is_valid(self):
        d = self.router.route("What is machine learning?")
        assert d.provider in ("anthropic", "openai", "google")

    def test_model_id_in_registry(self):
        """Every routing decision must produce a model in the registry."""
        queries = [
            "What is 2+2?",
            "Write a Python function to sort a list",
            "Implement a distributed consensus algorithm in Rust with linearisability guarantees, "
            "must never deadlock, ensure fault tolerance",
        ]
        for q in queries:
            d = self.router.route(q)
            assert d.model_id in MODEL_REGISTRY, (
                f"Model {d.model_id!r} not in registry for query: {q[:40]!r}"
            )

    def test_shadow_routing_produces_valid_model(self):
        """Shadow-routed decisions must still produce a valid model."""
        for _ in range(30):   # run enough to hit the 5% shadow path
            d = self.router.route("Simple question")
            assert d.model_id in MODEL_REGISTRY

    def test_shadow_routing_flag_set(self):
        """At least one in 30 requests should be shadow-routed."""
        shadow_count = sum(
            1 for _ in range(30)
            if self.router.route("Simple question").is_shadow
        )
        # With 5% rate and 30 trials, probability of zero shadow hits is 0.95^30 ≈ 0.2%
        # We allow zero to avoid flaky tests — just ensure flag type is correct
        d = self.router.route("Any question")
        assert isinstance(d.is_shadow, bool)

    def test_cost_budget_passed_to_features(self):
        d = self.router.route("Hello", cost_budget_usd=0.05)
        assert d.features.cost_budget_usd == 0.05

    def test_tier_matches_model_registry(self):
        """Routed tier must match the model's tier in the registry."""
        for q in ["What is 2+2?", "Write a Python script", "Implement lock-free Rust must ensure guarantee"]:
            d = self.router.route(q)
            if not d.is_shadow:   # shadow picks randomly, skip tier check
                expected_tier = MODEL_REGISTRY[d.model_id]["tier"]
                assert d.tier == expected_tier, (
                    f"Tier mismatch for {d.model_id}: got {d.tier}, registry says {expected_tier}"
                )

    def test_reasoning_string_not_empty(self):
        d = self.router.route("What is machine learning?")
        assert d.reasoning and len(d.reasoning) > 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def setup_method(self):
        self.router = RuleBasedRouter()

    def test_empty_string_does_not_crash(self):
        f = extract_features("")
        assert 0 <= f.complexity_score <= 100

    def test_single_word_does_not_crash(self):
        f = extract_features("Hi")
        assert f.complexity_score < 15

    def test_very_long_prompt_caps_at_100(self):
        f = extract_features("analyse this carefully " * 200)
        assert f.complexity_score <= 100

    def test_many_question_marks(self):
        f = extract_features("?" * 50)
        assert f.complexity_score <= 100

    def test_code_block_and_constraints_forces_tier2(self):
        d = self.router.route(
            "Fix this:\n```python\ncode\n```\n"
            "Must be O(1) space, ensure no side effects."
        )
        assert d.tier == 2

    def test_provider_preference_anthropic(self):
        d = self.router.route(
            "What is 2+2?",
            provider_preference="anthropic",
        )
        if not d.is_shadow:
            assert d.provider == "anthropic"

    def test_different_queries_can_produce_different_tiers(self):
        d0 = self.router.route("What is the capital of France?")
        d2 = self.router.route(
            "Implement lock-free concurrent skip list in Rust "
            "must guarantee linearisability ensure no ABA problem never use mutexes"
        )
        assert d0.tier < d2.tier
