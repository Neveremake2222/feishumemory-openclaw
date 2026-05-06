"""Track D (Structured Memory Advantage) benchmark test cases.

These cases demonstrate WHY structured external memory is better than
plain keyword search or basic RAG. Each case shows a concrete advantage.

Advantage dimensions:
  D-01: Version supersession — only current valid decisions returned
  D-02: Scope isolation    — user preferences don't pollute project recall
  D-03: Conflict detection  — numeric contradictions are flagged
  D-04: Evidence trace     — every memory is traceable to source
  D-05: Multi-signal rank  — freshness + importance > keyword frequency
  D-06: Zero-result safety — unambiguous "don't know" (no hallucination)
  D-07: Bigram Chinese     — Chinese phrase matching is stable
"""

from __future__ import annotations

from benchmarks.structures import (
    BenchmarkCase,
    Complexity,
    Direction,
    InterferenceSetup,
    RecallSpec,
    ResultAssertion,
    SetupEvent,
    SetupMemory,
    Track,
)

HOUR = 1.0
DAY = 24.0


def _event(
    source_type: str,
    content: str,
    actors: list[str],
    scope: str,
    created_hours_ago: float = 0.0,
    source_ref: str | None = None,
    payload: dict | None = None,
) -> SetupEvent:
    return SetupEvent(
        source_type=source_type,
        content=content,
        actors=actors,
        scope=scope,
        source_ref=source_ref,
        payload=payload,
        created_hours_ago=created_hours_ago,
    )


def _memory(
    memory_type: str,
    title: str,
    summary: str,
    content: dict,
    importance: float = 0.5,
    confidence: float = 0.8,
    evidence: list[dict] | None = None,
    tags: list[str] | None = None,
    created_hours_ago: float = 0.0,
    scope: str = "project",
    project_id: str | None = None,
    task_id: str | None = None,
    user_id: str | None = None,
) -> SetupMemory:
    return SetupMemory(
        memory_type=memory_type,
        title=title,
        summary=summary,
        content=content,
        importance=importance,
        confidence=confidence,
        evidence=evidence,
        tags=tags,
        created_hours_ago=created_hours_ago,
        scope=scope,
        project_id=project_id,
        task_id=task_id,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# D-01: Version supersession advantage
# ---------------------------------------------------------------------------
# Plain search: returns both RabbitMQ (old) and Kafka (new) because both match
# Structured memory: returns only the current valid Kafka decision
# ---------------------------------------------------------------------------
D01_VERSION_SUPERSESSION = BenchmarkCase(
    case_id="D-01",
    track="D",
    capability="version_supersession",
    description=(
        "【版本替代优势】项目先选 RabbitMQ，后改选 Kafka。"
        "普通搜索两条都返回（都含消息队列选型关键词）；"
        "本系统通过 fact_override 冲突检测，自动 supersede 旧决策，只返回当前有效的 Kafka。"
    ),
    direction=Direction.B,
    complexity_reasoning=Complexity.LOW,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["decision"],
    setup_events=[
        _event(
            source_type="message",
            content="技术选型结论：使用 RabbitMQ 作为消息队列，团队熟悉，维护成本低。",
            actors=["eng_li"],
            scope="project",
            source_ref="msg://tech_rabbitmq",
            created_hours_ago=48.0,
        ),
        _event(
            source_type="message",
            content="最终决定：因高吞吐需求，升级为 Kafka，RabbitMQ 方案废弃。",
            actors=["eng_li", "pm_zhang"],
            scope="project",
            source_ref="msg://tech_kafka",
            created_hours_ago=1.0,
        ),
    ],
    setup_memories=[
        _memory(
            memory_type="decision",
            title="项目消息队列选型决定：RabbitMQ",
            summary="项目消息队列选型决定：RabbitMQ 满足中等吞吐量需求。",
            content={"scope": "project", "project_id": "proj_alpha", "technology": "RabbitMQ"},
            importance=0.7,
            confidence=0.8,
            evidence=[{"source_type": "message", "source_ref": "msg://tech_rabbitmq"}],
            tags=["decision", "technology", "rabbitmq"],
            created_hours_ago=48.0,
            scope="project",
            project_id="proj_alpha",
        ),
        _memory(
            memory_type="decision",
            title="消息队列选型最终决定：Kafka",
            summary="消息队列选型最终决定：Kafka 满足高吞吐量需求。",
            content={
                "scope": "project",
                "project_id": "proj_alpha",
                "technology": "Kafka",
            },
            importance=0.9,
            confidence=0.9,
            evidence=[{"source_type": "message", "source_ref": "msg://tech_kafka"}],
            tags=["decision", "technology", "kafka"],
            created_hours_ago=1.0,
            scope="project",
            project_id="proj_alpha",
        ),
    ],
    recalls=[
        RecallSpec(
            query="项目消息队列技术选型",
            project_id="proj_alpha",
            intent="decision",
            limit=5,
            assertions=[
                ResultAssertion(type="contains_title", value="Kafka"),
                ResultAssertion(type="contains_title", value="RabbitMQ", negates=True),
                ResultAssertion(type="contains_memory_type", value="decision"),
            ],
        ),
    ],
    expected_titles=["Kafka"],
    forbidden_titles=["RabbitMQ"],
    expected_count_range=(1, 2),
    notes="D-01: Plain search returns both. Structured memory returns only the current valid decision.",
)


# ---------------------------------------------------------------------------
# D-02: Scope isolation advantage
# ---------------------------------------------------------------------------
# Plain search: mixes user preference with project decision (both contain "数据库")
# Structured memory: project_id filter excludes user-scoped memories
# ---------------------------------------------------------------------------
D02_SCOPE_ISOLATION = BenchmarkCase(
    case_id="D-02",
    track="D",
    capability="scope_isolation",
    description=(
        "【作用域隔离优势】用户A个人偏好 PostgreSQL，但项目决策用 SQLite。"
        "查询项目记忆时，普通搜索会混入个人偏好；"
        "本系统通过 scope + project_id 过滤，只返回项目决策。"
    ),
    direction=Direction.B_PLUS_C,
    complexity_reasoning=Complexity.LOW,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["preference", "decision"],
    setup_memories=[
        _memory(
            memory_type="preference",
            title="个人偏好：PostgreSQL",
            summary="我个人的项目习惯用 PostgreSQL，配置灵活，生态成熟。",
            content={"scope": "user", "user_id": "u1", "database": "PostgreSQL"},
            importance=0.6,
            confidence=0.8,
            evidence=[],
            tags=["preference", "database"],
            created_hours_ago=24.0,
            scope="user",
            user_id="u1",
        ),
        _memory(
            memory_type="decision",
            title="项目决策：SQLite",
            summary="本项目因轻量、无依赖需求，采用 SQLite 作为主数据库，不引入额外服务。",
            content={"scope": "project", "project_id": "proj_alpha", "database": "SQLite"},
            importance=0.8,
            confidence=0.8,
            evidence=[],
            tags=["decision", "database"],
            created_hours_ago=12.0,
            scope="project",
            project_id="proj_alpha",
        ),
    ],
    recalls=[
        RecallSpec(
            query="数据库选型",
            project_id="proj_alpha",
            scope="project",
            intent="decision",
            limit=5,
            assertions=[
                ResultAssertion(type="contains_title", value="SQLite"),
                ResultAssertion(type="contains_title", value="PostgreSQL", negates=True),
                ResultAssertion(type="contains_memory_type", value="decision"),
            ],
        ),
    ],
    expected_titles=["SQLite"],
    forbidden_titles=["PostgreSQL"],
    expected_count_range=(1, 1),
    notes="D-02: Plain search mixes scope. Structured memory filters by project scope.",
)


# ---------------------------------------------------------------------------
# D-03: Conflict detection advantage
# ---------------------------------------------------------------------------
# Plain search / basic RAG: cannot detect numeric contradiction
# Structured memory: detects evidence_conflict (3 days vs 5 days), keeps both
# but flags for review and lowers confidence — verified by both existing in recall
# ---------------------------------------------------------------------------
D03_CONFLICT_DETECTION = BenchmarkCase(
    case_id="D-03",
    track="D",
    capability="conflict_detection",
    description=(
        "【冲突检测优势】同一任务出现矛盾进度数据（预计3天 vs 预计5天）。"
        "普通搜索/RAG 无法检测数字矛盾，只会返回多条结果；"
        "本系统检测 evidence_conflict，两条都保留但标记 review，降低置信度。"
        "验证：两条冲突记忆都被召回，说明系统没有静默覆盖任何一条。"
    ),
    direction=Direction.B,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["task_status"],
    setup_memories=[
        _memory(
            memory_type="task_status",
            title="API 联调预计3天完成",
            summary="预计3天完成 API 联调测试，资源充足。",
            content={"scope": "project", "project_id": "proj_alpha", "task_id": "task_api", "duration_days": 3},
            importance=0.7,
            confidence=0.8,
            evidence=[{"source_type": "message", "source_ref": "msg://task_est3"}],
            tags=["task_status", "api"],
            created_hours_ago=2.0,
            scope="project",
            project_id="proj_alpha",
            task_id="task_api",
        ),
        # Second estimate triggers evidence_conflict (numeric 3 vs 5)
        _memory(
            memory_type="task_status",
            title="API 联调预计5天完成",
            summary="预计5天完成 API 联调测试，因为发现接口不兼容问题需要额外调试。",
            content={"scope": "project", "project_id": "proj_alpha", "task_id": "task_api", "duration_days": 5},
            importance=0.7,
            confidence=0.8,
            evidence=[{"source_type": "message", "source_ref": "msg://task_est5"}],
            tags=["task_status", "api"],
            created_hours_ago=1.0,
            scope="project",
            project_id="proj_alpha",
            task_id="task_api",
        ),
    ],
    recalls=[
        RecallSpec(
            query="API 联调 预计完成时间",
            project_id="proj_alpha",
            intent="task_status",
            limit=5,
            assertions=[
                ResultAssertion(type="contains_title", value="预计3天完成"),
                ResultAssertion(type="contains_title", value="预计5天完成"),
                ResultAssertion(type="contains_memory_type", value="task_status"),
            ],
        ),
    ],
    expected_titles=["预计3天完成", "预计5天完成"],
    forbidden_titles=[],
    expected_count_range=(2, 2),
    notes=(
        "D-03: Plain search can't detect numeric contradiction. "
        "Structured memory detects evidence_conflict at write-time, keeps both, flags review, lowers confidence."
    ),
)


# ---------------------------------------------------------------------------
# D-04: Evidence traceability advantage
# ---------------------------------------------------------------------------
# Plain search: returns text with no source reference
# Structured memory: every memory has evidence[] with source_ref + source_type
# ---------------------------------------------------------------------------
D04_EVIDENCE_TRACEABILITY = BenchmarkCase(
    case_id="D-04",
    track="D",
    capability="evidence_traceability",
    description=(
        "【证据溯源优势】每条记忆都有 evidence[] 字段，指向飞书 source_ref。"
        "普通搜索只返回匹配文本，无来源追溯；"
        "本系统返回的记忆包含完整 evidence 链，可审计、可溯源。"
    ),
    direction=Direction.B,
    complexity_reasoning=Complexity.LOW,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["decision"],
    setup_events=[
        _event(
            source_type="message",
            content="决定本项目采用 SQLite + BM25 做轻量记忆检索。",
            actors=["eng_li"],
            scope="project",
            source_ref="msg://om_feishu_abc123",
            created_hours_ago=1.0,
        ),
    ],
    setup_memories=[
        _memory(
            memory_type="decision",
            title="技术选型：SQLite + BM25",
            summary="决定本项目默认使用 SQLite + BM25 做轻量记忆检索。",
            content={"scope": "project", "project_id": "proj_alpha"},
            importance=0.8,
            confidence=0.8,
            evidence=[
                {"source_type": "message", "source_ref": "msg://om_feishu_abc123", "content_hash": "abc123"}
            ],
            tags=["decision", "technology", "sqlite", "bm25"],
            created_hours_ago=1.0,
            scope="project",
            project_id="proj_alpha",
        ),
    ],
    recalls=[
        RecallSpec(
            query="SQLite BM25 记忆检索",
            project_id="proj_alpha",
            intent="decision",
            limit=3,
            assertions=[
                ResultAssertion(type="contains_evidence_source_ref", value="msg://om_feishu_abc123"),
                ResultAssertion(type="contains_memory_type", value="decision"),
            ],
        ),
    ],
    expected_titles=["SQLite", "BM25"],
    forbidden_titles=[],
    expected_count_range=(1, 1),
    notes="D-04: Plain search has no source reference. Structured memory has full evidence chain.",
)


# ---------------------------------------------------------------------------
# D-05: Multi-signal ranking advantage
# ---------------------------------------------------------------------------
# Plain keyword search: ranks by term frequency, old low-quality content floods results
# Structured memory: BM25 + freshness + importance + confidence → key result rises to top
# ---------------------------------------------------------------------------
D05_MULTI_SIGNAL_RANKING = BenchmarkCase(
    case_id="D-05",
    track="D",
    capability="multi_signal_ranking",
    description=(
        "【多信号召回优势】60条噪声记忆（含关键词但旧、低重要度），"
        "1条关键新决策（高重要度、高置信度）。"
        "普通搜索被噪声淹没；本系统靠四维加权，关键记忆排入 top-3。"
    ),
    direction=Direction.B,
    complexity_reasoning=Complexity.MEDIUM,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["decision", "task_status", "preference"],
    setup_memories=[
        _memory(
            memory_type="decision",
            title="技术方案：采用分布式架构",
            summary="核心决策：本项目采用分布式微服务架构，提升可扩展性。",
            content={"scope": "project", "project_id": "proj_alpha", "architecture": "distributed"},
            importance=0.9,
            confidence=0.9,
            evidence=[],
            tags=["decision", "architecture", "microservice"],
            created_hours_ago=1.0,
            scope="project",
            project_id="proj_alpha",
        ),
    ],
    interference=InterferenceSetup(
        count=60,
        memories=[
            _memory(
                memory_type="task_status",
                title="任务进行中",
                summary="分布式架构调研任务进行中，进展顺利。",
                content={"scope": "project", "project_id": "proj_alpha"},
                importance=0.3,
                confidence=0.6,
                evidence=[],
                tags=["task_status", "architecture", "microservice"],
                created_hours_ago=72.0,
                scope="project",
                project_id="proj_alpha",
            ),
        ],
    ),
    recalls=[
        RecallSpec(
            query="分布式架构 微服务 项目方案",
            project_id="proj_alpha",
            intent="decision",
            limit=3,
            assertions=[
                ResultAssertion(type="contains_title", value="分布式架构"),
                ResultAssertion(type="contains_memory_type", value="decision"),
            ],
        ),
    ],
    expected_titles=["分布式架构"],
    forbidden_titles=[],
    expected_count_range=(1, 3),
    notes="D-05: Keyword search drowns in noise. Multi-signal ranking surfaces high-importance decisions.",
)


# ---------------------------------------------------------------------------
# D-06: Zero-result safety advantage
# ---------------------------------------------------------------------------
# Plain search: may hallucinate or return irrelevant results
# LLM without memory: fabricates answers
# Structured memory: returns empty for unrelated queries (no hallucination)
# ---------------------------------------------------------------------------
D06_ZERO_RESULT_SAFETY = BenchmarkCase(
    case_id="D-06",
    track="D",
    capability="zero_result_safety",
    description=(
        "【零结果可靠性优势】项目 Alpha 有记忆，但查询项目 Beta（无记忆）。"
        "本系统返回空结果，明确表示无相关记忆；"
        "不返回不相关结果，不产生幻觉。"
    ),
    direction=Direction.B,
    complexity_reasoning=Complexity.LOW,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["decision"],
    setup_memories=[
        _memory(
            memory_type="decision",
            title="Alpha 项目技术栈",
            summary="Alpha 项目使用 Python + FastAPI 技术栈。",
            content={"scope": "project", "project_id": "proj_alpha"},
            importance=0.8,
            confidence=0.8,
            evidence=[],
            tags=["decision", "tech_stack"],
            created_hours_ago=6.0,
            scope="project",
            project_id="proj_alpha",
        ),
    ],
    recalls=[
        RecallSpec(
            query="Beta 项目技术栈 数据库选型",
            project_id="proj_beta",  # Different from proj_alpha — no memories exist
            intent="decision",
            limit=5,
            assertions=[],
        ),
    ],
    expected_titles=[],
    forbidden_titles=["Alpha"],
    expected_count_range=(0, 0),
    expect_zero_results=True,
    notes="D-06: Structured memory returns zero for unrelated scope — no hallucination risk.",
)


# ---------------------------------------------------------------------------
# D-07: Chinese bigram recall advantage
# ---------------------------------------------------------------------------
# Single-character tokenization: "BM25" breaks into ['b', 'm', '2', '5']
#   → Chinese phrases like "检索方案" split to individual chars → poor phrase matching
# Bigram tokenization: "BM25检索方案" → ['b','m','2','5','检','索','方','案','BM25','M25','25检','检索','索方','方案']
#   → bigrams like "检索" and "方案" match stably even when query order differs
# ---------------------------------------------------------------------------
D07_CHINESE_BIGRAM = BenchmarkCase(
    case_id="D-07",
    track="D",
    capability="chinese_bigram_recall",
    description=(
        "【中文 Bigram 召回优势】写入 检索方案采用BM25，查询 BM25检索方案。"
        "单字切分时 检索方案 被拆成单字，短语匹配不稳定；"
        "bigram 切分后生成 检索 和 方案 等二元组，顺序颠倒也能匹配。"
    ),
    direction=Direction.B,
    complexity_reasoning=Complexity.LOW,
    complexity_tool=Complexity.LOW,
    complexity_interaction=Complexity.LOW,
    memory_types=["decision"],
    setup_memories=[
        _memory(
            memory_type="decision",
            title="技术选型：SQLite + BM25 检索方案",
            summary="决定本项目默认使用 SQLite + BM25 做轻量记忆检索，平衡性能和简单性。",
            content={"scope": "project", "project_id": "proj_alpha", "technology": "SQLite+BM25"},
            importance=0.8,
            confidence=0.8,
            evidence=[],
            tags=["decision", "technology", "sqlite", "bm25", "检索"],
            created_hours_ago=1.0,
            scope="project",
            project_id="proj_alpha",
        ),
    ],
    recalls=[
        RecallSpec(
            query="BM25 检索方案",
            project_id="proj_alpha",
            intent="decision",
            limit=3,
            assertions=[
                ResultAssertion(type="contains_title", value="检索方案"),
                ResultAssertion(type="contains_title", value="BM25"),
            ],
        ),
    ],
    expected_titles=["BM25", "检索方案"],
    forbidden_titles=[],
    expected_count_range=(1, 1),
    notes="D-07: Single-char tokenization fails phrase matching. Bigram stable across word order.",
)


# ---------------------------------------------------------------------------
# All Track D cases
# ---------------------------------------------------------------------------
TRACK_D_CASES: list[BenchmarkCase] = [
    D01_VERSION_SUPERSESSION,
    D02_SCOPE_ISOLATION,
    D03_CONFLICT_DETECTION,
    D04_EVIDENCE_TRACEABILITY,
    D05_MULTI_SIGNAL_RANKING,
    D06_ZERO_RESULT_SAFETY,
    D07_CHINESE_BIGRAM,
]
