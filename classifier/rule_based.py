"""
Phase 1 rule-based classifier.

Extracts 12 features from a query and applies weighted rules to
choose the appropriate model tier. No ML — deterministic logic
that ships fast and starts collecting the training data we need
for Phase 2.

Every feature extracted here will become a column in the training
dataset. The extract_features() function is intentionally reused
in Phase 2's XGBoost pipeline.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional
from gateway.config import MODEL_REGISTRY, get_settings


# ---------------------------------------------------------------------------
# Feature vector — also becomes Phase 2 training columns
# ---------------------------------------------------------------------------

@dataclass
class QueryFeatures:
    # Raw signals
    token_count_est: int = 0          # estimated tokens (words * 1.3)
    char_count: int = 0
    word_count: int = 0

    # Structural signals
    has_code_block: bool = False       # ``` or <code> present
    has_code_keywords: bool = False    # function, class, import, algorithm…
    constraint_count: int = 0         # must, ensure, never, only if…
    question_count: int = 0           # number of ? marks
    sentence_count: int = 0

    # Domain signals
    is_specialist_domain: bool = False # legal, medical, financial…
    is_factual_lookup: bool = False    # "what is", "who is", "when did"…
    is_creative: bool = False          # write, compose, story, poem…
    is_code_task: bool = False         # implement, debug, refactor, write code

    # Session context (filled by gateway, not classifier)
    turn_index: int = 0               # 0 = first message
    cost_budget_usd: float = 0.01

    # Derived
    complexity_score: float = 0.0     # 0–100, computed from above


# ---------------------------------------------------------------------------
# Routing decision
# ---------------------------------------------------------------------------

@dataclass
class RoutingDecision:
    model_id: str
    tier: int
    provider: str
    is_shadow: bool = False           # True = A/B shadow override
    complexity_score: float = 0.0
    features: Optional[QueryFeatures] = None
    reasoning: str = ""               # human-readable explanation (for logs)
    estimated_cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

CODE_KEYWORDS = re.compile(
    r'\b(function|def |class |import |async |await |algorithm|implement|'
    r'recursive|recursion|loop|array|pointer|malloc|struct|interface|'
    r'refactor|debug|compile|runtime|exception|stack|queue|tree|graph|'
    r'database|sql|api|http|endpoint|regex|parse|serialize)\b',
    re.IGNORECASE,
)

CONSTRAINT_KEYWORDS = re.compile(
    r'\b(must|ensure|without|only if|guarantee|exactly|never|always|'
    r'constraint|requirement|strict|mandatory|forbidden|prohibited|'
    r'at most|at least|no more than|no less than)\b',
    re.IGNORECASE,
)

SPECIALIST_DOMAIN = re.compile(
    r'\b(legal|liability|contract|statute|regulation|compliance|gdpr|hipaa|'
    r'medical|clinical|diagnosis|treatment|drug|patient|symptom|'
    r'financial|tax|investment|portfolio|derivative|audit|'
    r'security|vulnerabilit\w*|exploit\w*|penetration|malware|'
    r'injection|ransomware|phishing|zero.?day|cyber)\b',
    re.IGNORECASE,
)

FACTUAL_LOOKUP = re.compile(
    r'^(what is|what are|who is|who are|when did|when was|where is|'
    r'where are|how many|how much|what year|what date|define |'
    r'meaning of|tell me about)\b',
    re.IGNORECASE,
)

CREATIVE_KEYWORDS = re.compile(
    r'\b(write a|compose|create a story|poem|essay|blog post|screenplay|'
    r'fiction|narrative|character|plot|creative|imagine|brainstorm)\b',
    re.IGNORECASE,
)

CODE_TASK_KEYWORDS = re.compile(
    r'\b('
    r'implement|write code|code for|build a|'
    r'create a (function|class|module|script|app|service)|'
    r'write (?:a |an |the )(?:\w+ ){0,3}'
    r'(?:function|script|program|class|module|algorithm|solution|snippet|method)|'
    r'debug|fix (the|this) (bug|error|issue)|'
    r'refactor|optimise|optimize'
    r')\b',
    re.IGNORECASE,
)


def extract_features(
    prompt: str,
    turn_index: int = 0,
    cost_budget_usd: float = 0.01,
) -> QueryFeatures:
    """
    Extract the 12-signal feature vector from a raw prompt string.
    Pure function — no side effects, fully testable.
    """
    f = QueryFeatures()
    f.turn_index = turn_index
    f.cost_budget_usd = cost_budget_usd

    # Basic counts
    f.char_count = len(prompt)
    f.word_count = len(prompt.split())
    f.token_count_est = int(f.word_count * 1.3)
    f.sentence_count = max(1, len(re.split(r'[.!?]+', prompt)))
    f.question_count = prompt.count('?')

    # Structural
    f.has_code_block = bool(re.search(r'```|<code>', prompt))
    f.has_code_keywords = bool(CODE_KEYWORDS.search(prompt))
    f.constraint_count = len(CONSTRAINT_KEYWORDS.findall(prompt))

    # Domain
    f.is_specialist_domain = bool(SPECIALIST_DOMAIN.search(prompt))
    f.is_factual_lookup = bool(FACTUAL_LOOKUP.match(prompt.strip()))
    f.is_creative = bool(CREATIVE_KEYWORDS.search(prompt))
    f.is_code_task = bool(CODE_TASK_KEYWORDS.search(prompt))

    # Complexity score: weighted sum, capped at 100
    score = 0.0
    score += min(f.token_count_est * 0.06, 20)   # length: max 20 pts
    score += 25 if f.has_code_block else 0
    score += 15 if f.has_code_keywords else 0
    score += 25 if f.is_code_task else 0
    score += f.constraint_count * 6               # each constraint: 6 pts
    score += 12 if f.is_specialist_domain else 0
    score += f.turn_index * 3                     # later turns harder
    score -= 18 if f.is_factual_lookup else 0     # factual = simpler
    score -= 8  if f.is_creative else 0           # creative ≠ hard reasoning
    score += f.question_count * 2

    f.complexity_score = max(0.0, min(100.0, score))
    return f


# ---------------------------------------------------------------------------
# Rule-based router
# ---------------------------------------------------------------------------

import random


class RuleBasedRouter:
    """
    Maps feature vectors to routing decisions using explicit thresholds.

    Thresholds were set by manually inspecting 500 example queries
    across difficulty levels. In Phase 2 these will be replaced by
    XGBoost's learned decision boundaries.

    Tier logic:
        Tier 0 (Haiku/Flash/GPT-4o-mini):  score < 30 AND no hard signals
        Tier 1 (Sonnet/GPT-4o):            30 <= score < 60 OR hard signals
        Tier 2 (Opus):                      score >= 60 OR extreme hard signals
    """

    TIER0_THRESHOLD = 30.0
    TIER2_THRESHOLD = 60.0

    # Prefer these model IDs within each tier (ordered by preference)
    TIER_PREFERENCES = {
        0: ["gemini-2.0-flash-lite", "gemini-2.0-flash", "claude-haiku-3", "gpt-4o-mini"],
        1: ["gemini-2.5-flash", "gemini-2.0-flash", "claude-sonnet-3-5", "gpt-4o"],
        2: ["gemini-2.5-pro", "claude-opus-3", "gemini-2.5-flash"],
    }

    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    def select_tier(self, features: QueryFeatures) -> tuple[int, str]:
        """
        Returns (tier, reasoning_string).
        Hard signals can force Tier 1/2 regardless of score.
        """
        score = features.complexity_score

        # Hard upgrades — certain signals always bump tier
        hard_upgrade_to_1 = (
            features.has_code_block or
            features.is_code_task or
            features.is_specialist_domain
        )
        hard_upgrade_to_2 = (
            (features.has_code_block and features.constraint_count >= 2) or
            (features.is_code_task and features.constraint_count >= 2) or
            (features.is_specialist_domain and features.constraint_count >= 1) or
            features.token_count_est > 1200
        )

        if hard_upgrade_to_2 or score >= self.TIER2_THRESHOLD:
            return 2, (
                f"score={score:.0f} | "
                f"code_block={features.has_code_block} | "
                f"constraints={features.constraint_count} | "
                f"specialist={features.is_specialist_domain} | "
                f"tokens={features.token_count_est}"
            )

        if hard_upgrade_to_1 or score >= self.TIER0_THRESHOLD:
            return 1, (
                f"score={score:.0f} | "
                f"code_keywords={features.has_code_keywords} | "
                f"code_task={features.is_code_task} | "
                f"specialist={features.is_specialist_domain}"
            )

        return 0, (
            f"score={score:.0f} | "
            f"factual={features.is_factual_lookup} | "
            f"creative={features.is_creative} | "
            f"tokens={features.token_count_est}"
        )

    def select_model(self, tier: int, provider_preference: str = "google") -> str:
        """
        Pick the best available model for the tier.
        Respects provider preference if multiple tier options exist.
        Falls back gracefully if the preferred provider has no model in this tier.
        """
        candidates = self.TIER_PREFERENCES[tier]
        # Try preferred provider first
        for model_id in candidates:
            if MODEL_REGISTRY[model_id]["provider"] == provider_preference:
                return model_id
        # Preferred provider not available in this tier — use first available
        return candidates[0]

    def is_shadow_request(self) -> bool:
        """5% of traffic goes to random model for counterfactual data."""
        return random.random() < self.settings.shadow_routing_fraction

    def available_models(self, provider_preference: str = None) -> list:
        """
        Return models filtered to only providers with real API keys set.
        Prevents shadow routing from hitting providers with no key.
        """
        from gateway.config import get_settings
        s = get_settings()
        available_providers = set()
        if s.anthropic_api_key and len(s.anthropic_api_key) > 10:
            available_providers.add("anthropic")
        if s.openai_api_key and len(s.openai_api_key) > 10:
            available_providers.add("openai")
        if s.google_api_key and len(s.google_api_key) > 10:
            available_providers.add("google")
        return [
            model_id for model_id, meta in MODEL_REGISTRY.items()
            if meta["provider"] in available_providers
        ]

    def route(
        self,
        prompt: str,
        turn_index: int = 0,
        cost_budget_usd: float = None,
        provider_preference: str = "google",
        force_model: str = None,
    ) -> RoutingDecision:
        """
        Main entry point. Returns a RoutingDecision with full context.
        This is what the gateway calls on every request.
        """
        budget = cost_budget_usd or self.settings.default_cost_budget_usd
        features = extract_features(prompt, turn_index, budget)

        # Shadow routing override — only pick from providers with real keys
        is_shadow = self.is_shadow_request()
        if is_shadow:
            all_models = self.available_models(provider_preference)
            if not all_models:
                all_models = list(MODEL_REGISTRY.keys())
            model_id = random.choice(all_models)
            tier = MODEL_REGISTRY[model_id]["tier"]
            return RoutingDecision(
                model_id=model_id,
                tier=tier,
                provider=MODEL_REGISTRY[model_id]["provider"],
                is_shadow=True,
                complexity_score=features.complexity_score,
                features=features,
                reasoning="shadow_routing_override",
            )

        # Forced model (for testing / customer override)
        if force_model and force_model in MODEL_REGISTRY:
            model_id = force_model
            tier = MODEL_REGISTRY[model_id]["tier"]
            return RoutingDecision(
                model_id=model_id,
                tier=tier,
                provider=MODEL_REGISTRY[model_id]["provider"],
                is_shadow=False,
                complexity_score=features.complexity_score,
                features=features,
                reasoning="customer_forced_model",
            )

        tier, reasoning = self.select_tier(features)
        model_id = self.select_model(tier, provider_preference)
        meta = MODEL_REGISTRY[model_id]

        return RoutingDecision(
            model_id=model_id,
            tier=tier,
            provider=meta["provider"],
            is_shadow=False,
            complexity_score=features.complexity_score,
            features=features,
            reasoning=reasoning,
        )
