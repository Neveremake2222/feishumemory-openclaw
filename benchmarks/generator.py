"""Template-based benchmark case generator.

Generates thousands of parameterized benchmark cases from template functions
and parameter tables. Each case tests a specific memory capability with
STRICT assertions — failures reveal real engine defects.

Usage:
    python -m benchmarks.generator --count
    python -m benchmarks.generator --track J --limit 100
    python -m benchmarks.generator --track K
    python -m benchmarks.generator --track L
    python -m benchmarks.generator --all --export benchmarks_runtime/generated_cases.jsonl
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

sys.path.insert(0, str(Path(__file__).parent.parent))

from benchmarks.structures import (
    BenchmarkCase,
    Complexity,
    Direction,
    InterferenceSetup,
    RecallSpec,
    ResultAssertion,
    SetupEvent,
    SetupMemory,
    SetupWorkflowOutcome,
    _standard_ground_truth,
    _standard_rubric,
)


# ---------------------------------------------------------------------------
# Parameter Tables
# ---------------------------------------------------------------------------

PROJECTS = [
    "alpha", "beta", "gamma", "delta", "epsilon",
    "zeta", "eta", "theta", "iota", "kappa",
]

MEMBERS = [
    "zhang", "li", "wang", "zhao", "chen",
    "liu", "wu", "lin", "huang", "zhou",
    "xu", "sun", "ma", "zhu", "hu",
]

# Fact domains with templates and value pools
FACT_DOMAINS = [
    {
        "key": "deadline",
        "values": ["2026-06-30", "2026-09-15", "2026-12-01", "2027-03-15", "2027-06-30"],
        "title_tpl": "{project} deadline: {value}",
        "summary_tpl": "{project} deadline confirmed as {value}.",
        "content_key": "deadline",
    },
    {
        "key": "tech_stack",
        "values": ["FastAPI+React", "Django+Vue", "Flask+Angular", "Go+Next.js", "Spring+React"],
        "title_tpl": "{project} tech stack: {value}",
        "summary_tpl": "{project} uses {value} for backend and frontend.",
        "content_key": "tech_stack",
    },
    {
        "key": "architecture",
        "values": ["Modular Monolith", "Microservices", "Event-Driven", "Layered", "Hexagonal"],
        "title_tpl": "{project} architecture: {value}",
        "summary_tpl": "{project} selected {value} architecture.",
        "content_key": "architecture",
    },
    {
        "key": "database",
        "values": ["PostgreSQL 15", "MySQL 8.0", "MongoDB 6.0", "Redis 7", "Cassandra 4.1"],
        "title_tpl": "{project} database: {value}",
        "summary_tpl": "{project} uses {value} for primary storage.",
        "content_key": "database",
    },
    {
        "key": "deployment",
        "values": ["Kubernetes+Helm", "Docker Swarm", "AWS ECS", "Vercel", "Cloudflare Pages"],
        "title_tpl": "{project} deployment: {value}",
        "summary_tpl": "{project} deploys via {value}.",
        "content_key": "deployment",
    },
    {
        "key": "security_constraint",
        "values": ["OAuth2+API Gateway", "mTLS Required", "JWT+RBAC", "SAML SSO", "Zero Trust"],
        "title_tpl": "{project} security: {value}",
        "summary_tpl": "{project} enforces {value} security model.",
        "content_key": "security",
    },
    {
        "key": "rate_limit",
        "values": ["1000 req/min", "5000 req/min", "10000 req/min", "Unlimited", "100 req/min"],
        "title_tpl": "{project} rate limit: {value}",
        "summary_tpl": "{project} rate limit set to {value}.",
        "content_key": "rate_limit",
    },
    {
        "key": "ci_cd",
        "values": ["GitHub Actions", "GitLab CI", "Jenkins", "CircleCI", "ArgoCD"],
        "title_tpl": "{project} CI/CD: {value}",
        "summary_tpl": "{project} uses {value} for CI/CD pipeline.",
        "content_key": "cicd",
    },
    {
        "key": "monitoring",
        "values": ["Prometheus+Grafana", "Datadog", "New Relic", "Sentry", "ELK Stack"],
        "title_tpl": "{project} monitoring: {value}",
        "summary_tpl": "{project} monitors via {value}.",
        "content_key": "monitoring",
    },
    {
        "key": "cache",
        "values": ["Redis 7", "Memcached", "Varnish", "CDN Edge", "No Cache"],
        "title_tpl": "{project} cache: {value}",
        "summary_tpl": "{project} caching strategy: {value}.",
        "content_key": "cache",
    },
    {
        "key": "api_style",
        "values": ["REST+OpenAPI", "GraphQL", "gRPC", "WebSocket", "REST+JSON:API"],
        "title_tpl": "{project} API style: {value}",
        "summary_tpl": "{project} exposes {value} API.",
        "content_key": "api_style",
    },
    {
        "key": "auth_method",
        "values": ["JWT+Refresh", "Session+Cookie", "API Key", "OAuth2 PKCE", "SAML"],
        "title_tpl": "{project} auth: {value}",
        "summary_tpl": "{project} authentication: {value}.",
        "content_key": "auth",
    },
    {
        "key": "queue",
        "values": ["RabbitMQ", "Kafka", "Redis Streams", "AWS SQS", "NATS"],
        "title_tpl": "{project} queue: {value}",
        "summary_tpl": "{project} uses {value} for message queue.",
        "content_key": "queue",
    },
    {
        "key": "search",
        "values": ["Elasticsearch 8", "Meilisearch", "Typesense", "Algolia", "PostgreSQL FTS"],
        "title_tpl": "{project} search: {value}",
        "summary_tpl": "{project} search powered by {value}.",
        "content_key": "search",
    },
    {
        "key": "storage",
        "values": ["S3-compatible", "GCS", "Azure Blob", "MinIO", "Backblaze B2"],
        "title_tpl": "{project} storage: {value}",
        "summary_tpl": "{project} stores blobs in {value}.",
        "content_key": "storage",
    },
    {
        "key": "cdn",
        "values": ["Cloudflare", "AWS CloudFront", "Fastly", "Akamai", "BunnyCDN"],
        "title_tpl": "{project} CDN: {value}",
        "summary_tpl": "{project} CDN provided by {value}.",
        "content_key": "cdn",
    },
    {
        "key": "testing",
        "values": ["Pytest+Playwright", "Jest+Cypress", "Go test+Selenium", "JUnit+Selenide", "PHPUnit"],
        "title_tpl": "{project} testing: {value}",
        "summary_tpl": "{project} testing stack: {value}.",
        "content_key": "testing",
    },
    {
        "key": "container",
        "values": ["Docker", "Podman", "containerd", "RKT", "LXD"],
        "title_tpl": "{project} container: {value}",
        "summary_tpl": "{project} containers via {value}.",
        "content_key": "container",
    },
    {
        "key": "infrastructure",
        "values": ["AWS EC2", "GCP Compute", "Azure VM", "Hetzner", "DigitalOcean"],
        "title_tpl": "{project} infra: {value}",
        "summary_tpl": "{project} runs on {value}.",
        "content_key": "infra",
    },
    {
        "key": "logging",
        "values": ["ELK Stack", "Loki+Grafana", "CloudWatch", "Splunk", "Datadog Logs"],
        "title_tpl": "{project} logging: {value}",
        "summary_tpl": "{project} logging: {value}.",
        "content_key": "logging",
    },
]

# Decision types for decision recall templates
DECISION_TYPES = [
    "architecture", "tech_stack", "security", "performance", "cost",
    "process", "api", "database", "deployment", "team_struct",
]

# Preference dimensions
PREFERENCE_DIMENSIONS = [
    "notification_channel", "code_review_format", "meeting_schedule",
    "documentation_style", "tool_preference", "communication_style",
    "break_frequency", "async_vs_sync", "time_zone", "meeting_length",
]

# Scale levels for Track K
# Small scales for regression gate (fast), large scales for extended suite
SCALE_LEVELS_REGRESSION = [
    (100, "K-100"),
    (500, "K-500"),
    (1000, "K-1k"),
]
SCALE_LEVELS_EXTENDED = [
    (2000, "K-2k"),
    (5000, "K-5k"),
    (10000, "K-10k"),
]
SCALE_LEVELS = SCALE_LEVELS_REGRESSION + SCALE_LEVELS_EXTENDED

# Noise levels for interference tests
NOISE_LEVELS = [0, 5, 15, 30, 50, 100]

# Stale age hours
STALE_AGES = [24, 72, 168, 720, 2160]

# Agent task types for Track L
AGENT_TASK_TYPES = [
    "experience_reuse", "decision_making", "preference_application",
    "constraint_lookup", "error_recovery", "workflow_execution",
    "governance_compliance", "scope_isolation", "multi_hop_reasoning",
    "knowledge_synthesis",
]


# ---------------------------------------------------------------------------
# Noise Memory Templates
# ---------------------------------------------------------------------------

NOISE_TOPICS = [
    "team lunch planning", "office supply order", "birthday celebration",
    "parking permit renewal", "coffee machine repair", "meeting room booking",
    "holiday schedule", "equipment return", "welcome new hire",
    "parking lot incident", "HVAC request", "security badge issue",
    "catering order", "transportation logistics", "team building event",
    "training session signup", "certificate renewal", "insurance claim",
    "vendor meeting setup", "client visit preparation",
]


def _make_noise_memory(idx: int, project_id: str = "proj_noise") -> SetupMemory:
    """Generate a noise memory that should NOT be recalled."""
    topic = NOISE_TOPICS[idx % len(NOISE_TOPICS)]
    member = MEMBERS[idx % len(MEMBERS)]
    return SetupMemory(
        memory_type="task_status",
        title=f"[Noise] {topic.title()} #{idx}",
        summary=f"Internal note about {topic} from {member}. Item {idx} in the noise set.",
        content={"kind": "noise", "topic": topic, "item": idx},
        importance=0.2 + (idx % 3) * 0.05,
        confidence=0.4 + (idx % 4) * 0.05,
        evidence=[{"source_ref": f"noise://{idx}"}],
        tags=["noise", topic.replace(" ", "_")],
        project_id=project_id,
        user_id=member,
    )


# ---------------------------------------------------------------------------
# Case ID Counter
# ---------------------------------------------------------------------------

@dataclass
class CaseCounter:
    """Thread-safe counter for generating unique case IDs."""
    value: int = 0

    def next(self, prefix: str = "GEN") -> str:
        self.value += 1
        return f"{prefix}-{self.value:05d}"


_counter = CaseCounter()


def _reset_counter(start: int = 0) -> None:
    """Reset the case counter for deterministic generation."""
    _counter.value = start


def _new_id(prefix: str = "GEN") -> str:
    """Generate a new unique case ID."""
    return _counter.next(prefix)


# ---------------------------------------------------------------------------
# Core Template Helpers
# ---------------------------------------------------------------------------

def _m(
    memory_type: str,
    title: str,
    summary: str,
    content: dict,
    *,
    importance: float = 0.7,
    confidence: float = 0.85,
    evidence: list[dict] | None = None,
    tags: list[str] | None = None,
    created_hours_ago: float = 0.0,
    scope: str = "project",
    project_id: str = "proj_alpha",
    task_id: str | None = None,
    user_id: str | None = None,
) -> SetupMemory:
    """Shorthand for creating SetupMemory objects."""
    return SetupMemory(
        memory_type=memory_type,
        title=title,
        summary=summary,
        content=content,
        importance=importance,
        confidence=confidence,
        evidence=evidence or [{"source_ref": "agent://generator"}],
        tags=tags or [],
        created_hours_ago=created_hours_ago,
        scope=scope,
        project_id=project_id,
        task_id=task_id,
        user_id=user_id,
    )


def _recall(
    query: str,
    project_id: str = "proj_alpha",
    user_id: str | None = None,
    task_id: str | None = None,
    intent: str = "general",
    limit: int = 5,
    assertions: list[ResultAssertion] | None = None,
) -> RecallSpec:
    """Shorthand for creating RecallSpec objects."""
    return RecallSpec(
        query=query,
        project_id=project_id,
        user_id=user_id,
        task_id=task_id,
        intent=intent,
        limit=limit,
        assertions=assertions or [],
    )


def _infer_difficulty(noise_count: int, stale: bool = False) -> str:
    """Infer difficulty from noise count and staleness."""
    if noise_count >= 50:
        return "adversarial"
    if noise_count >= 15 or stale:
        return "hard"
    if noise_count >= 5:
        return "medium"
    return "easy"


# ---------------------------------------------------------------------------
# Template: Fact Recall
# Tests basic fact recall across domains, projects, and noise levels
# ---------------------------------------------------------------------------

def generate_fact_recall_cases(
    *,
    domains: list[dict] | None = None,
    projects: list[str] | None = None,
    noise_levels: list[int] | None = None,
    track: str = "J",
) -> list[BenchmarkCase]:
    """Generate fact recall cases: fact × project × noise combinations."""
    domains = domains or FACT_DOMAINS
    projects = projects or PROJECTS
    noise_levels = noise_levels or NOISE_LEVELS

    cases: list[BenchmarkCase] = []
    for domain in domains:
        for project in projects:
            for noise_count in noise_levels:
                case_id = _new_id(f"{track}-F")
                project_id = f"proj_{project}"
                value = domain["values"][0]  # Use first value for simplicity
                title = domain["title_tpl"].format(project=project.upper(), value=value)
                summary = domain["summary_tpl"].format(project=project.upper(), value=value)

                setup_memories: list[SetupMemory] = [
                    _m(
                        "decision",
                        title,
                        summary,
                        {domain["content_key"]: value, "project": project},
                        importance=0.85,
                        confidence=0.92,
                        tags=[f"project_{project}", domain["key"]],
                        project_id=project_id,
                    ),
                ]

                # Add noise memories
                for ni in range(noise_count):
                    setup_memories.append(_make_noise_memory(ni, f"proj_noise_{project}"))

                query = f"{project.upper()} {domain['key'].replace('_', ' ')}"

                cases.append(BenchmarkCase(
                    case_id=case_id,
                    track=track,
                    capability=f"fact_recall_{domain['key']}",
                    description=f"Recall {domain['key']} for project {project} with {noise_count} noise memories.",
                    direction=Direction.B,
                    complexity_reasoning=Complexity.LOW,
                    complexity_tool=Complexity.LOW,
                    complexity_interaction=Complexity.LOW,
                    memory_types=["decision"],
                    memory_target="fact",
                    memory_type_dimension="fact",
                    evaluation_task=f"Recall the {domain['key']} for project {project}.",
                    expected_behavior=f"Return the {domain['key']} fact: {value}.",
                    difficulty=_infer_difficulty(noise_count),
                    source_anchor=f"generator:{case_id}",
                    setup_memories=setup_memories,
                    recalls=[_recall(query, project_id=project_id, limit=5)],
                    assertions=[
                        ResultAssertion(type="contains_title", value=value),
                        ResultAssertion(type="contains_tag", value=f"project_{project}"),
                    ],
                    expected_titles=[title],
                    forbidden_titles=[],
                    expected_count_range=(1, min(3, noise_count + 1)),
                    ground_truth=_standard_ground_truth(
                        expected_titles=[title],
                        forbidden_titles=[],
                        expected_count_range=(1, min(3, noise_count + 1)),
                        required_content=[value],
                    ),
                    scoring_rubric=_standard_rubric(
                        expected_titles=[title],
                        bounded_context=True,
                        retrieval_only=True,
                    ),
                ))

    return cases


# ---------------------------------------------------------------------------
# Template: Decision Version Chain
# Tests that the current (latest) decision is recalled, not superseded ones
# ---------------------------------------------------------------------------

def generate_decision_version_cases(
    *,
    decision_types: list[str] | None = None,
    projects: list[str] | None = None,
    version_depths: list[int] | None = None,
    track: str = "J",
) -> list[BenchmarkCase]:
    """Generate decision version chain cases: tests stale vs current discrimination."""
    decision_types = decision_types or DECISION_TYPES
    projects = projects or PROJECTS
    version_depths = version_depths or [2, 3, 4, 5]

    cases: list[BenchmarkCase] = []
    for decision_type in decision_types:
        for project in projects:
            for depth in version_depths:
                case_id = _new_id(f"{track}-V")
                project_id = f"proj_{project}"
                values = [f"{decision_type}_v{i}" for i in range(1, depth + 1)]
                current_value = values[-1]

                setup_memories: list[SetupMemory] = []
                for i, val in enumerate(values):
                    is_current = (i == depth - 1)
                    is_stale = not is_current
                    age_hours = (depth - i) * 168.0  # Each version 1 week apart
                    setup_memories.append(_m(
                        "decision",
                        f"{project.upper()} {decision_type} {val}",
                        f"{project.upper()} {decision_type} decision: {val}."
                            + (" Current." if is_current else " Superseded."),
                        {"decision": decision_type, "version": val, "current": is_current},
                        importance=0.95 if is_current else 0.5 - (i * 0.1),
                        confidence=0.98 if is_current else 0.6 - (i * 0.1),
                        tags=[f"project_{project}", decision_type, "superseded" if is_stale else "current"],
                        created_hours_ago=age_hours,
                        project_id=project_id,
                    ))

                query = f"{project.upper()} current {decision_type}"

                # STRICT: forbidden_titles includes ALL superseded versions
                forbidden = [f"{project.upper()} {decision_type} {v}" for v in values[:-1]]

                cases.append(BenchmarkCase(
                    case_id=case_id,
                    track=track,
                    capability=f"decision_version_chain_{depth}v",
                    description=f"Recall the current {decision_type} among {depth} versions for {project}.",
                    direction=Direction.B,
                    complexity_reasoning=Complexity.MEDIUM,
                    complexity_tool=Complexity.LOW,
                    complexity_interaction=Complexity.LOW,
                    memory_types=["decision"],
                    memory_target="decision",
                    memory_type_dimension="decision",
                    evaluation_task=f"Find the current {decision_type} for {project}.",
                    expected_behavior=f"Return the latest {decision_type}: {current_value}, not superseded versions.",
                    difficulty="hard" if depth >= 4 else "medium",
                    source_anchor=f"generator:{case_id}",
                    setup_memories=setup_memories,
                    recalls=[_recall(query, project_id=project_id, limit=3)],
                    assertions=[
                        ResultAssertion(type="contains_title", value=current_value),
                        # STRICT: Check that superseded titles are NOT in results
                        *[
                            ResultAssertion(type="contains_title", value=v, negates=True)
                            for v in values[:-1]
                        ],
                    ],
                    expected_titles=[f"{project.upper()} {decision_type} {current_value}"],
                    forbidden_titles=forbidden,
                    expected_count_range=(1, 1),
                    ground_truth=_standard_ground_truth(
                        expected_titles=[f"{project.upper()} {decision_type} {current_value}"],
                        forbidden_titles=forbidden,
                        expected_count_range=(1, 1),
                        required_content=[current_value],
                    ),
                    scoring_rubric=_standard_rubric(
                        expected_titles=[f"{project.upper()} {decision_type} {current_value}"],
                        forbidden_titles=forbidden,
                        bounded_context=True,
                        retrieval_only=True,
                    ),
                ))

    return cases


# ---------------------------------------------------------------------------
# Template: Preference Recall
# Tests preference recall for different users, dimensions, and states
# ---------------------------------------------------------------------------

def generate_preference_cases(
    *,
    dimensions: list[str] | None = None,
    members: list[str] | None = None,
    track: str = "J",
) -> list[BenchmarkCase]:
    """Generate preference recall cases: user × dimension × stale/current."""
    dimensions = dimensions or PREFERENCE_DIMENSIONS
    members = members or MEMBERS

    cases: list[BenchmarkCase] = []

    for dim in dimensions:
        for member in members:
            case_id = _new_id(f"{track}-P")
            user_id = member

            # Stale preference (old)
            stale_value = f"stale_{dim}_old"
            stale_title = f"{member.upper()} prefers {dim}: {stale_value}"

            # Current preference (updated)
            current_value = f"current_{dim}_best"
            current_title = f"{member.upper()} prefers {dim}: {current_value}"

            setup_memories = [
                _m(
                    "preference",
                    stale_title,
                    f"{member.upper()} previously preferred {stale_value} for {dim}.",
                    {dim: stale_value, "status": "stale"},
                    importance=0.2,
                    confidence=0.3,
                    tags=[member, "preference", dim, "stale"],
                    user_id=user_id,
                    created_hours_ago=720.0,  # 30 days old
                ),
                _m(
                    "preference",
                    current_title,
                    f"{member.upper()} updated preference for {dim} to {current_value}.",
                    {dim: current_value, "status": "current"},
                    importance=0.9,
                    confidence=0.95,
                    tags=[member, "preference", dim, "current"],
                    user_id=user_id,
                    created_hours_ago=24.0,  # 1 day old
                ),
            ]

            query = f"{member.upper()} {dim.replace('_', ' ')} preference"

            cases.append(BenchmarkCase(
                case_id=case_id,
                track=track,
                capability=f"preference_recall_{dim}",
                description=f"Recall the current {dim} preference for {member}.",
                direction=Direction.C,
                complexity_reasoning=Complexity.MEDIUM,
                complexity_tool=Complexity.LOW,
                complexity_interaction=Complexity.LOW,
                memory_types=["preference"],
                memory_target="preference",
                memory_type_dimension="preference",
                evaluation_task=f"Find {member}'s current {dim} preference.",
                expected_behavior=f"Return the current {dim} preference: {current_value}, not the stale one.",
                difficulty="medium",
                source_anchor=f"generator:{case_id}",
                setup_memories=setup_memories,
                recalls=[_recall(query, user_id=user_id, limit=3)],
                assertions=[
                    ResultAssertion(type="contains_title", value="current"),
                    ResultAssertion(type="contains_title", value=dim),
                    ResultAssertion(type="contains_tag", value=dim),
                    ResultAssertion(type="contains_tag", value="current"),
                    # STRICT: stale title should NOT be in top results
                    ResultAssertion(type="contains_title", value="stale", negates=True),
                    ResultAssertion(type="contains_tag", value="stale", negates=True),
                ],
                expected_titles=[current_title],
                forbidden_titles=[stale_title],
                expected_count_range=(1, 2),
                ground_truth=_standard_ground_truth(
                    expected_titles=[current_title],
                    forbidden_titles=[stale_title],
                    expected_count_range=(1, 2),
                    required_content=[current_value],
                ),
                scoring_rubric=_standard_rubric(
                    expected_titles=[current_title],
                    forbidden_titles=[stale_title],
                    bounded_context=True,
                    retrieval_only=True,
                ),
            ))

    return cases


# ---------------------------------------------------------------------------
# Template: Scope Isolation
# Tests that memories from one project/user/task don't leak to others
# ---------------------------------------------------------------------------

def generate_scope_isolation_cases(
    *,
    projects: list[str] | None = None,
    members: list[str] | None = None,
    track: str = "J",
) -> list[BenchmarkCase]:
    """Generate scope isolation cases: cross-project, cross-user isolation."""
    projects = projects or PROJECTS
    members = members or MEMBERS

    cases: list[BenchmarkCase] = []
    topic_counter = 0

    for i, project_a in enumerate(projects):
        for j, project_b in enumerate(projects):
            if i >= j:
                continue  # Only test A→B where A != B

            for member_a in members[:3]:  # Limit combinations
                for member_b in members[:3]:
                    if member_a == member_b:
                        continue

                    case_id = _new_id(f"{track}-S")
                    topic_counter += 1

                    # Memory from project_a, user_a
                    mem_a_title = f"Project {project_a.upper()} secret decision by {member_a}"
                    # Memory from project_b, user_b
                    mem_b_title = f"Project {project_b.upper()} confidential decision by {member_b}"

                    setup_memories = [
                        _m(
                            "decision",
                            mem_a_title,
                            f"Confidential decision for project {project_a} by {member_a}.",
                            {"secret": True, "owner": member_a},
                            importance=0.9,
                            confidence=0.95,
                            tags=[f"project_{project_a}", member_a],
                            project_id=f"proj_{project_a}",
                            user_id=member_a,
                        ),
                        _m(
                            "decision",
                            mem_b_title,
                            f"Confidential decision for project {project_b} by {member_b}.",
                            {"secret": True, "owner": member_b},
                            importance=0.9,
                            confidence=0.95,
                            tags=[f"project_{project_b}", member_b],
                            project_id=f"proj_{project_b}",
                            user_id=member_b,
                        ),
                    ]

                    # Query for project_a only → should NOT return project_b's memory
                    query_a = f"Project {project_a.upper()} confidential decision"

                    cases.append(BenchmarkCase(
                        case_id=case_id,
                        track=track,
                        capability="scope_isolation_project",
                        description=f"Query for {project_a} should not return {project_b}'s memory.",
                        direction=Direction.C,
                        complexity_reasoning=Complexity.LOW,
                        complexity_tool=Complexity.LOW,
                        complexity_interaction=Complexity.LOW,
                        memory_types=["decision"],
                        memory_target="decision",
                        memory_type_dimension="decision",
                        evaluation_task=f"Find confidential decisions for project {project_a}.",
                        expected_behavior=f"Return {project_a}'s memory, not {project_b}'s.",
                        difficulty="easy",
                        source_anchor=f"generator:{case_id}",
                        setup_memories=setup_memories,
                        recalls=[_recall(query_a, project_id=f"proj_{project_a}", limit=5)],
                        assertions=[
                            ResultAssertion(type="contains_tag", value=f"project_{project_a}"),
                            # STRICT: project_b memory should NOT be returned
                            ResultAssertion(type="contains_title", value=f"Project {project_b.upper()}", negates=True),
                        ],
                        expected_titles=[mem_a_title],
                        forbidden_titles=[mem_b_title],
                        expected_count_range=(1, 1),
                        ground_truth=_standard_ground_truth(
                            expected_titles=[mem_a_title],
                            forbidden_titles=[mem_b_title],
                            expected_count_range=(1, 1),
                        ),
                        scoring_rubric=_standard_rubric(
                            expected_titles=[mem_a_title],
                            forbidden_titles=[mem_b_title],
                            bounded_context=True,
                            retrieval_only=True,
                        ),
                    ))

    return cases


# ---------------------------------------------------------------------------
# Template: Stale Memory Exclusion
# Tests that stale memories (low confidence/importance) are properly excluded
# ---------------------------------------------------------------------------

def generate_stale_exclusion_cases(
    *,
    domains: list[dict] | None = None,
    projects: list[str] | None = None,
    stale_ages: list[float] | None = None,
    memory_types: list[str] | None = None,
    track: str = "J",
) -> list[BenchmarkCase]:
    """Generate stale exclusion cases: tests freshness ranking."""
    domains = domains or FACT_DOMAINS
    projects = projects or PROJECTS
    stale_ages = stale_ages or STALE_AGES
    memory_types = memory_types or ["decision", "semantic", "preference", "procedural"]

    cases: list[BenchmarkCase] = []
    for domain in domains:
        for project in projects:
            for age_hours in stale_ages:
                for mem_type in memory_types[:2]:  # Limit to 2 types per domain for scale
                    case_id = _new_id(f"{track}-ST")

                    current_title = f"{project.upper()} current {domain['key']}: current_value"
                    stale_title = f"{project.upper()} stale {domain['key']}: stale_value"

                    setup_memories = [
                        # Current memory (fresh, high importance/confidence)
                        _m(
                            mem_type,
                            current_title,
                            f"Current {domain['key']} for {project}: current_value.",
                            {"key": domain["key"], "value": "current_value", "status": "current"},
                            importance=0.9,
                            confidence=0.95,
                            tags=[f"project_{project}", domain["key"], "current"],
                            created_hours_ago=1.0,
                            project_id=f"proj_{project}",
                        ),
                        # Stale memory (old, low importance/confidence)
                        _m(
                            mem_type,
                            stale_title,
                            f"Stale {domain['key']} for {project}: stale_value. This is outdated.",
                            {"key": domain["key"], "value": "stale_value", "status": "stale"},
                            importance=0.2,
                            confidence=0.25,
                            tags=[f"project_{project}", domain["key"], "stale"],
                            created_hours_ago=age_hours,
                            project_id=f"proj_{project}",
                        ),
                    ]

                    query = f"{project.upper()} {domain['key']}"

                    cases.append(BenchmarkCase(
                        case_id=case_id,
                        track=track,
                        capability=f"stale_exclusion_{mem_type}_{int(age_hours)}h",
                        description=f"Recall current {domain['key']} (age {age_hours}h) vs stale.",
                        direction=Direction.B,
                        complexity_reasoning=Complexity.MEDIUM,
                        complexity_tool=Complexity.LOW,
                        complexity_interaction=Complexity.LOW,
                        memory_types=[mem_type],
                        memory_target=mem_type,
                        memory_type_dimension=mem_type,
                        evaluation_task=f"Find the current {domain['key']} for {project}.",
                        expected_behavior="Return current value, exclude stale value from top results.",
                        difficulty="hard" if age_hours >= 168 else "medium",
                        source_anchor=f"generator:{case_id}",
                        setup_memories=setup_memories,
                        recalls=[_recall(query, project_id=f"proj_{project}", limit=3)],
                        assertions=[
                            ResultAssertion(type="contains_title", value="current"),
                            # STRICT: stale title should NOT appear
                            ResultAssertion(type="contains_title", value="stale", negates=True),
                        ],
                        expected_titles=[current_title],
                        forbidden_titles=[stale_title],
                        expected_count_range=(1, 1),
                        ground_truth=_standard_ground_truth(
                            expected_titles=[current_title],
                            forbidden_titles=[stale_title],
                            expected_count_range=(1, 1),
                            required_content=["current_value"],
                        ),
                        scoring_rubric=_standard_rubric(
                            expected_titles=[current_title],
                            forbidden_titles=[stale_title],
                            bounded_context=True,
                            retrieval_only=True,
                        ),
                    ))

    return cases


# ---------------------------------------------------------------------------
# Template: Zero Result / Refusal
# Tests that the engine returns zero results for truly unknown queries
# ---------------------------------------------------------------------------

def generate_zero_result_cases(
    *,
    projects: list[str] | None = None,
    memory_types: list[str] | None = None,
    track: str = "J",
) -> list[BenchmarkCase]:
    """Generate zero-result cases: tests refusal to hallucinate."""
    projects = projects or PROJECTS
    memory_types = memory_types or ["decision", "semantic", "preference", "procedural", "workflow_trace"]

    # Query topics that are NOT in any memory
    ABSENT_TOPICS = [
        "quantum computing budget allocation",
        "fusion reactor maintenance schedule",
        "teleportation protocol security audit",
        "alien contact response procedures",
        "time travel approval workflow",
        "telepathy standardization committee",
        "dimensional rift monitoring dashboard",
        "antimatter storage compliance",
        "consciousness transfer backup policy",
        "parallel universe conflict resolution",
        "warp drive deployment roadmap",
        "synthetic emotion regulation framework",
        "psionic shield procurement process",
        "dimensional stability metric tracking",
        "hyperlight communication protocols",
        "sentient AI rights charter drafting",
        "wormhole navigation certification",
        "temporal paradox mitigation strategy",
        "multiverse ethics review board",
        "gravity manipulation safety standards",
    ]

    cases: list[BenchmarkCase] = []

    for project in projects:
        for mem_type in memory_types:
            engine_memory_type = "procedural" if mem_type == "workflow_trace" else mem_type
            content_kind = "workflow_trace" if mem_type == "workflow_trace" else "legitimate"
            # Set up a few unrelated memories
            unrelated_memories = [
                _m(
                    engine_memory_type,
                    f"{project.upper()} legitimate {mem_type} {i}",
                    f"Unrelated legitimate {mem_type} #{i} for {project}.",
                    {"kind": content_kind, "index": i},
                    importance=0.7,
                    confidence=0.85,
                    tags=[f"project_{project}", "legitimate", mem_type],
                    project_id=f"proj_{project}",
                )
                for i in range(3)
            ]

            for topic in ABSENT_TOPICS[:10]:  # Limit to 10 topics per project
                case_id = _new_id(f"{track}-Z")

                cases.append(BenchmarkCase(
                    case_id=case_id,
                    track=track,
                    capability="zero_result_refusal",
                    description=f"Query '{topic}' should return zero results (never recorded).",
                    direction=Direction.B,
                    complexity_reasoning=Complexity.LOW,
                    complexity_tool=Complexity.LOW,
                    complexity_interaction=Complexity.LOW,
                    memory_types=[mem_type],
                    memory_target=mem_type,
                    memory_type_dimension=mem_type,
                    evaluation_task=f"Check if '{topic}' exists — it does not.",
                    expected_behavior="Return zero results. Do not hallucinate a memory for this topic.",
                    difficulty="medium",
                    source_anchor=f"generator:{case_id}",
                    setup_memories=unrelated_memories,
                    recalls=[_recall(topic, project_id=f"proj_{project}", limit=5)],
                    expect_zero_results=True,
                    expected_titles=[],
                    forbidden_titles=[],
                    expected_count_range=(0, 0),
                    ground_truth=_standard_ground_truth(
                        expected_titles=[],
                        forbidden_titles=[],
                        expected_count_range=(0, 0),
                        expect_zero_results=True,
                    ),
                    scoring_rubric=_standard_rubric(
                        expected_titles=None,
                        forbidden_titles=None,
                        bounded_context=True,
                        answer_required=False,
                    ),
                ))

    return cases


# ---------------------------------------------------------------------------
# Template: Interference Resistance
# Tests recall quality under increasing noise
# ---------------------------------------------------------------------------

def generate_interference_cases(
    *,
    domains: list[dict] | None = None,
    projects: list[str] | None = None,
    noise_levels: list[int] | None = None,
    track: str = "J",
) -> list[BenchmarkCase]:
    """Generate interference resistance cases: target recall under noise."""
    domains = domains or FACT_DOMAINS
    projects = projects or PROJECTS
    noise_levels = noise_levels or [10, 25, 50, 100, 200]

    cases: list[BenchmarkCase] = []
    for domain in domains:
        for project in projects[:5]:  # Limit projects for scale
            for noise_count in noise_levels:
                case_id = _new_id(f"{track}-I")
                project_id = f"proj_{project}"
                value = domain["values"][0]

                setup_memories: list[SetupMemory] = [
                    _m(
                        "decision",
                        domain["title_tpl"].format(project=project.upper(), value=value),
                        domain["summary_tpl"].format(project=project.upper(), value=value),
                        {domain["content_key"]: value},
                        importance=0.9,
                        confidence=0.95,
                        tags=[f"project_{project}", domain["key"]],
                        project_id=project_id,
                    ),
                ]

                # Add noise memories
                for ni in range(noise_count):
                    setup_memories.append(_make_noise_memory(ni, f"proj_noise_{project}"))

                query = f"{project.upper()} {domain['key'].replace('_', ' ')}"

                cases.append(BenchmarkCase(
                    case_id=case_id,
                    track=track,
                    capability=f"interference_resistance_{noise_count}",
                    description=f"Recall {domain['key']} for {project} under {noise_count} noise memories.",
                    direction=Direction.B,
                    complexity_reasoning=Complexity.MEDIUM,
                    complexity_tool=Complexity.LOW,
                    complexity_interaction=Complexity.LOW,
                    memory_types=["decision"],
                    memory_target="fact",
                    memory_type_dimension="fact",
                    evaluation_task=f"Find the {domain['key']} for project {project} despite noise.",
                    expected_behavior=f"Return the target {domain['key']} fact: {value}.",
                    difficulty="adversarial" if noise_count >= 100 else "hard" if noise_count >= 50 else "medium",
                    source_anchor=f"generator:{case_id}",
                    setup_memories=setup_memories,
                    recalls=[_recall(query, project_id=project_id, limit=5)],
                    assertions=[
                        ResultAssertion(type="contains_title", value=value),
                    ],
                    expected_titles=[domain["title_tpl"].format(project=project.upper(), value=value)],
                    forbidden_titles=[],
                    expected_count_range=(1, min(3, noise_count // 20 + 2)),
                    ground_truth=_standard_ground_truth(
                        expected_titles=[domain["title_tpl"].format(project=project.upper(), value=value)],
                        expected_count_range=(1, min(3, noise_count // 20 + 2)),
                        required_content=[value],
                    ),
                    scoring_rubric=_standard_rubric(
                        expected_titles=[domain["title_tpl"].format(project=project.upper(), value=value)],
                        bounded_context=True,
                        retrieval_only=True,
                    ),
                ))

    return cases


# ---------------------------------------------------------------------------
# Template: Multi-Hop Reasoning
# Tests recall requiring synthesis of 2+ memories
# ---------------------------------------------------------------------------

def generate_multi_hop_cases(
    *,
    projects: list[str] | None = None,
    track: str = "J",
) -> list[BenchmarkCase]:
    """Generate multi-hop reasoning cases: requires 2+ memories."""
    projects = projects or PROJECTS

    cases: list[BenchmarkCase] = []

    for project in projects:
        case_id = _new_id(f"{track}-M")
        project_id = f"proj_{project}"

        # Memory 1: Decision
        mem1_title = f"{project.upper()} tech stack decision"
        # Memory 2: Constraint (related)
        mem2_title = f"{project.upper()} security constraint"
        # Memory 3: Schedule (related)
        mem3_title = f"{project.upper()} deployment schedule"

        setup_memories = [
            _m(
                "decision",
                mem1_title,
                f"{project.upper()} selected FastAPI+React as tech stack.",
                {"decision": "tech_stack", "value": "FastAPI+React"},
                importance=0.9,
                confidence=0.95,
                tags=[f"project_{project}", "tech_stack"],
                project_id=project_id,
            ),
            _m(
                "decision",
                mem2_title,
                f"{project.upper()} requires OAuth2+API Gateway security.",
                {"decision": "security", "value": "OAuth2+API Gateway"},
                importance=0.95,
                confidence=0.98,
                tags=[f"project_{project}", "security"],
                project_id=project_id,
            ),
            _m(
                "decision",
                mem3_title,
                f"{project.upper()} deployment scheduled for June 30, 2026.",
                {"decision": "schedule", "value": "2026-06-30"},
                importance=0.8,
                confidence=0.9,
                tags=[f"project_{project}", "schedule"],
                project_id=project_id,
            ),
        ]

        # Query requires understanding all 3 decisions
        query = f"What is the full technical setup for {project.upper()}?"

        cases.append(BenchmarkCase(
            case_id=case_id,
            track=track,
            capability="multi_hop_3_way",
            description=f"Recall all 3 related decisions for {project} in one query.",
            direction=Direction.B_PLUS_C,
            complexity_reasoning=Complexity.HIGH,
            complexity_tool=Complexity.LOW,
            complexity_interaction=Complexity.LOW,
            memory_types=["decision", "decision", "decision"],
            memory_target="decision",
            memory_type_dimension="decision",
            evaluation_task=f"Synthesize all technical decisions for {project}.",
            expected_behavior="Return all 3 related decisions: tech stack, security, and schedule.",
            difficulty="hard",
            source_anchor=f"generator:{case_id}",
            setup_memories=setup_memories,
            recalls=[_recall(query, project_id=project_id, intent="multi_hop", limit=5)],
            assertions=[
                ResultAssertion(type="contains_title", value="tech stack"),
                ResultAssertion(type="contains_title", value="security"),
                ResultAssertion(type="contains_title", value="schedule"),
            ],
            expected_titles=[mem1_title, mem2_title, mem3_title],
            forbidden_titles=[],
            expected_count_range=(2, 3),
            ground_truth=_standard_ground_truth(
                expected_titles=[mem1_title, mem2_title, mem3_title],
                expected_count_range=(2, 3),
                required_content=["FastAPI+React", "OAuth2", "2026-06-30"],
            ),
            scoring_rubric=_standard_rubric(
                expected_titles=[mem1_title, mem2_title, mem3_title],
                bounded_context=True,
                retrieval_only=True,
            ),
        ))

    return cases


# ---------------------------------------------------------------------------
# Track K: Scale Benchmark Cases
# Tests retrieval at 100/500/1k/2k/5k/10k scale
# ---------------------------------------------------------------------------

def generate_scale_cases(
    *,
    scale_levels: list[tuple[int, str]] | None = None,
    projects: list[str] | None = None,
    domains: list[dict] | None = None,
    track: str = "K",
) -> list[BenchmarkCase]:
    """Generate scale benchmark cases: tests recall at different memory counts.

    Default scale levels are regression-safe (100/500/1k).
    Use SCALE_LEVELS_EXTENDED or SCALE_LEVELS for larger scales.
    """
    scale_levels = scale_levels or SCALE_LEVELS_REGRESSION
    projects = projects or PROJECTS
    domains = domains or FACT_DOMAINS

    cases: list[BenchmarkCase] = []
    for scale, label in scale_levels:
        for project in projects[:3]:  # 3 representative projects
            for domain in domains[:5]:  # 5 representative domains
                case_id = _new_id(f"{track}-{label}")
                project_id = f"proj_{project}"
                value = domain["values"][0]
                target_title = domain["title_tpl"].format(project=project.upper(), value=value)

                # Target memory
                target_memory = _m(
                    "decision",
                    target_title,
                    domain["summary_tpl"].format(project=project.upper(), value=value),
                    {domain["content_key"]: value, "project": project},
                    importance=0.9,
                    confidence=0.95,
                    tags=[f"project_{project}", domain["key"]],
                    project_id=project_id,
                )

                # Noise memories to reach scale
                noise_count = scale - 1  # -1 for target memory
                noise_memories = [
                    _make_noise_memory(i, f"proj_noise_{project}")
                    for i in range(noise_count)
                ]

                query = f"{project.upper()} {domain['key'].replace('_', ' ')}"

                cases.append(BenchmarkCase(
                    case_id=case_id,
                    track=track,
                    capability=f"scale_recall_{label}",
                    description=f"Recall {domain['key']} for {project} at {scale} memories.",
                    direction=Direction.B,
                    complexity_reasoning=Complexity.LOW,
                    complexity_tool=Complexity.LOW,
                    complexity_interaction=Complexity.LOW,
                    memory_types=["decision"],
                    memory_target="fact",
                    memory_type_dimension="fact",
                    scale_level=scale,
                    evaluation_task=f"Recall {domain['key']} for {project} among {scale} memories.",
                    expected_behavior=f"Return the {domain['key']} fact: {value}.",
                    difficulty="hard" if scale >= 5000 else "medium" if scale >= 1000 else "easy",
                    source_anchor=f"generator:{case_id}",
                    setup_memories=[target_memory] + noise_memories,
                    recalls=[_recall(query, project_id=project_id, limit=5)],
                    assertions=[
                        ResultAssertion(type="contains_title", value=value),
                    ],
                    expected_titles=[target_title],
                    forbidden_titles=[],
                    expected_count_range=(1, min(3, scale // 100 + 2)),
                    ground_truth=_standard_ground_truth(
                        expected_titles=[target_title],
                        expected_count_range=(1, min(3, scale // 100 + 2)),
                        required_content=[value],
                    ),
                    scoring_rubric=_standard_rubric(
                        expected_titles=[target_title],
                        bounded_context=True,
                        retrieval_only=True,
                    ),
                ))

    return cases


# ---------------------------------------------------------------------------
# Track L: Agent Task Benchmark Cases
# Tests end-to-end agent task completion with memory vs baseline
# ---------------------------------------------------------------------------

def generate_agent_task_cases(
    *,
    task_types: list[str] | None = None,
    projects: list[str] | None = None,
    members: list[str] | None = None,
    track: str = "L",
) -> list[BenchmarkCase]:
    """Generate agent task cases: end-to-end task completion with memory."""
    task_types = task_types or AGENT_TASK_TYPES
    projects = projects or PROJECTS
    members = members or MEMBERS

    cases: list[BenchmarkCase] = []
    for task_type in task_types:
        for project in projects[:5]:  # 5 representative projects
            for member in members[:3]:  # 3 representative members
                case_id = _new_id(f"{track}")
                project_id = f"proj_{project}"

                # Build a rich memory context for the task
                setup_memories: list[SetupMemory] = [
                    # Decision memory
                    _m(
                        "decision",
                        f"{project.upper()} tech decision by {member}",
                        f"{member} decided on FastAPI+React for {project}.",
                        {"decision": "tech_stack", "decided_by": member},
                        importance=0.9,
                        confidence=0.95,
                        tags=[f"project_{project}", member, "decision"],
                        project_id=project_id,
                        user_id=member,
                    ),
                    # Preference memory
                    _m(
                        "preference",
                        f"{member} code review preference",
                        f"{member} prefers bullet points in code review.",
                        {"preference": "code_review_format", "value": "bullet_points"},
                        importance=0.85,
                        confidence=0.92,
                        tags=[member, "preference", "code_review"],
                        user_id=member,
                    ),
                    # Fact memory
                    _m(
                        "semantic",
                        f"{project.upper()} deadline",
                        f"{project} deadline is June 30, 2026.",
                        {"fact": "deadline", "value": "2026-06-30"},
                        importance=0.8,
                        confidence=0.9,
                        tags=[f"project_{project}", "deadline"],
                        project_id=project_id,
                    ),
                    # Procedure memory
                    _m(
                        "procedural",
                        f"{project.upper()} deployment workflow",
                        f"{project} deployment: build, test, stage, prod.",
                        {"procedure": "deployment", "steps": ["build", "test", "stage", "prod"]},
                        importance=0.75,
                        confidence=0.88,
                        tags=[f"project_{project}", "deployment"],
                        project_id=project_id,
                    ),
                ]

                # Task-specific query and assertions
                if task_type == "experience_reuse":
                    query = f"Apply past tech decisions for {project}"
                    capability = f"experience_reuse_{member}"
                    eval_task = f"Complete a new task in {project} using past decisions."
                    expected_behavior = "Recall and apply the tech stack decision for the task."
                    assertions = [
                        ResultAssertion(type="contains_title", value="tech decision"),
                    ]
                    expected_titles = [f"{project.upper()} tech decision by {member}"]

                elif task_type == "decision_making":
                    query = f"What constraints apply to {project}?"
                    capability = f"decision_making_{project}"
                    eval_task = f"Make a decision for {project} respecting all constraints."
                    expected_behavior = "Identify and apply all relevant constraints."
                    assertions = [
                        ResultAssertion(type="contains_title", value="tech decision"),
                        ResultAssertion(type="contains_title", value="deadline"),
                    ]
                    expected_titles = [f"{project.upper()} tech decision by {member}", f"{project.upper()} deadline"]

                elif task_type == "preference_application":
                    query = f"Apply {member}'s preferences to the review"
                    capability = f"preference_application_{member}"
                    eval_task = f"Complete code review respecting {member}'s preferences."
                    expected_behavior = "Recall and apply the code review format preference."
                    assertions = [
                        ResultAssertion(type="contains_title", value="code review preference"),
                    ]
                    expected_titles = [f"{member} code review preference"]

                elif task_type == "constraint_lookup":
                    query = f"Find all constraints for {project} deployment"
                    capability = f"constraint_lookup_{project}"
                    eval_task = f"Verify deployment constraints for {project}."
                    expected_behavior = "Recall all constraint memories for the deployment."
                    assertions = [
                        ResultAssertion(type="contains_title", value="tech decision"),
                    ]
                    expected_titles = [f"{project.upper()} tech decision by {member}"]

                elif task_type == "error_recovery":
                    query = f"Similar error happened before in {project}?"
                    capability = f"error_recovery_{project}"
                    eval_task = f"Recover from error using past {project} experience."
                    expected_behavior = "Recall relevant past decisions to guide recovery."
                    assertions = [
                        ResultAssertion(type="contains_title", value="tech decision"),
                    ]
                    expected_titles = [f"{project.upper()} tech decision by {member}"]

                elif task_type == "workflow_execution":
                    query = f"How to deploy to {project}?"
                    capability = f"workflow_execution_{project}"
                    eval_task = f"Execute the {project} deployment workflow."
                    expected_behavior = "Recall the deployment procedure and execute correctly."
                    assertions = [
                        ResultAssertion(type="contains_title", value="deployment workflow"),
                    ]
                    expected_titles = [f"{project.upper()} deployment workflow"]

                elif task_type == "governance_compliance":
                    query = f"Check governance for {project} decisions"
                    capability = f"governance_compliance_{project}"
                    eval_task = f"Verify {project} decisions comply with governance."
                    expected_behavior = "Recall decisions and verify they follow governance rules."
                    assertions = [
                        ResultAssertion(type="contains_title", value="tech decision"),
                    ]
                    expected_titles = [f"{project.upper()} tech decision by {member}"]

                elif task_type == "scope_isolation":
                    query = f"{member}'s private preferences"
                    capability = f"scope_isolation_{member}"
                    eval_task = f"Find {member}'s preferences without leaking to others."
                    expected_behavior = "Return only {member}'s personal preferences."
                    assertions = [
                        ResultAssertion(type="contains_title", value="code review preference"),
                        ResultAssertion(type="contains_tag", value=member),
                    ]
                    expected_titles = [f"{member} code review preference"]

                elif task_type == "multi_hop_reasoning":
                    query = f"Full context for {project} from {member}"
                    capability = f"multi_hop_{project}_{member}"
                    eval_task = f"Synthesize all context from {member} about {project}."
                    expected_behavior = "Recall all 4 memories: decision, preference, fact, procedure."
                    assertions = [
                        ResultAssertion(type="contains_title", value="tech decision"),
                        ResultAssertion(type="contains_title", value="preference"),
                        ResultAssertion(type="contains_title", value="deadline"),
                        ResultAssertion(type="contains_title", value="deployment"),
                    ]
                    expected_titles = [
                        f"{project.upper()} tech decision by {member}",
                        f"{member} code review preference",
                        f"{project.upper()} deadline",
                        f"{project.upper()} deployment workflow",
                    ]

                else:  # knowledge_synthesis
                    query = f"Everything about {project}"
                    capability = f"knowledge_synthesis_{project}"
                    eval_task = f"Synthesize complete knowledge about {project}."
                    expected_behavior = "Recall all relevant memories about the project."
                    assertions = [
                        ResultAssertion(type="contains_tag", value=f"project_{project}"),
                    ]
                    expected_titles = [
                        f"{project.upper()} tech decision by {member}",
                        f"{project.upper()} deadline",
                        f"{project.upper()} deployment workflow",
                    ]

                cases.append(BenchmarkCase(
                    case_id=case_id,
                    track=track,
                    capability=capability,
                    description=f"Agent task: {task_type} for {project} by {member}.",
                    direction=Direction.B_PLUS_C,
                    complexity_reasoning=Complexity.HIGH,
                    complexity_tool=Complexity.MEDIUM,
                    complexity_interaction=Complexity.MEDIUM,
                    memory_types=["decision", "preference", "semantic", "procedural"],
                    memory_target="mixed",
                    agent_config={
                        "task_type": task_type,
                        "project": project,
                        "user": member,
                    },
                    baseline_mode="memory_enabled",
                    evaluation_task=eval_task,
                    expected_behavior=expected_behavior,
                    difficulty="medium",
                    source_anchor=f"generator:{case_id}",
                    setup_memories=setup_memories,
                    recalls=[_recall(query, project_id=project_id, user_id=member, limit=5)],
                    assertions=assertions,
                    expected_titles=expected_titles,
                    forbidden_titles=[],
                    expected_count_range=(1, len(expected_titles)),
                    ground_truth=_standard_ground_truth(
                        expected_titles=expected_titles,
                        expected_count_range=(1, len(expected_titles)),
                    ),
                    scoring_rubric=_standard_rubric(
                        expected_titles=expected_titles,
                        bounded_context=True,
                    ),
                ))

    return cases


# ---------------------------------------------------------------------------
# Main Generation Functions
# ---------------------------------------------------------------------------

def generate_track_j_cases(
    *,
    limit: int | None = None,
    track: str = "J",
) -> list[BenchmarkCase]:
    """Generate all Track J (Retrieval Quality) cases."""
    _reset_counter(0)
    cases: list[BenchmarkCase] = []

    # Add hand-crafted cases from existing track_j.py
    # (These are imported separately in runner)

    # Fact recall cases (STRICT: includes stale exclusion)
    cases.extend(generate_fact_recall_cases(track=track))

    # Decision version chains (STRICT: forbids superseded)
    cases.extend(generate_decision_version_cases(track=track))

    # Preference recall (STRICT: stale exclusion)
    cases.extend(generate_preference_cases(track=track))

    # Scope isolation
    cases.extend(generate_scope_isolation_cases(track=track))

    # Stale exclusion (STRICT: no weakening)
    cases.extend(generate_stale_exclusion_cases(track=track))

    # Zero result
    cases.extend(generate_zero_result_cases(track=track))

    # Interference resistance
    cases.extend(generate_interference_cases(track=track))

    # Multi-hop
    cases.extend(generate_multi_hop_cases(track=track))

    if limit:
        cases = cases[:limit]

    return cases


def generate_track_k_cases(track: str = "K") -> list[BenchmarkCase]:
    """Generate all Track K (Scale) cases."""
    _reset_counter(0)
    return generate_scale_cases(track=track)


def generate_track_l_cases(track: str = "L") -> list[BenchmarkCase]:
    """Generate all Track L (Agent Task) cases."""
    _reset_counter(0)
    return generate_agent_task_cases(track=track)


def generate_all_cases() -> dict[str, list[BenchmarkCase]]:
    """Generate all benchmark cases organized by track."""
    return {
        "J": generate_track_j_cases(),
        "K": generate_track_k_cases(),
        "L": generate_track_l_cases(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Template-based benchmark case generator.")
    parser.add_argument("--track", choices=["J", "K", "L", "all"], default="all")
    parser.add_argument("--limit", type=int, default=None, help="Limit cases per track")
    parser.add_argument("--count", action="store_true", help="Only print counts")
    parser.add_argument("--export", metavar="PATH", help="Export cases to JSONL")
    args = parser.parse_args(argv)

    if args.track == "J":
        cases = generate_track_j_cases(limit=args.limit)
        track_cases = {"J": cases}
    elif args.track == "K":
        cases = generate_track_k_cases()
        track_cases = {"K": cases}
    elif args.track == "L":
        cases = generate_track_l_cases()
        track_cases = {"L": cases}
    else:
        track_cases = generate_all_cases()
        if args.limit:
            for t in track_cases:
                track_cases[t] = track_cases[t][:args.limit]

    total = sum(len(c) for c in track_cases.values())
    print(f"Generated {total} benchmark cases:")
    for track, cases in sorted(track_cases.items()):
        print(f"  Track {track}: {len(cases)} cases")

    if args.count:
        return 0

    if args.export:
        path = Path(args.export)
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            for track, cases in sorted(track_cases.items()):
                for case in cases:
                    # Convert to dict for JSON serialization
                    from dataclasses import asdict
                    record = asdict(case)
                    # Remove non-serializable fields for export
                    record.pop("setup_events", None)
                    record.pop("interference", None)
                    record.pop("workflow_outcomes", None)
                    fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
                    fh.write("\n")
                    count += 1
        print(f"Exported {count} cases to {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
