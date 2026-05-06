"""Track A (Dialogue Memory) benchmark test cases.

Scenario: 跨部门项目立项到上线协作 (Cross-department project planning-to-launch)
Main project: Project Alpha — internal tools platform upgrade
Characters:
  - pm_zhang   (product manager, user scope)
  - eng_li     (tech lead, user scope)
  - design_wang (designer, user scope)
  - test_zhao  (QA lead, user scope)
  - ops_chen   (operations lead, user scope)

Scope conventions used in this file:
  - project_id "proj_alpha" / "alpha"  → project scope (shared across all roles)
  - user_id    "pm_zhang" etc.         → user scope  (individual)
  - project scope memories are readable by any role on the project
  - user scope memories are readable only by the owning user
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

# ---------------------------------------------------------------------------
# Shared timeline constants (used by created_hours_ago)
# "now" is set by the runner; all values are negative = hours before now
# ---------------------------------------------------------------------------
HOUR = 1.0
DAY = 24.0
WEEK = 24.0 * 7


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
# AIT-01 interference helpers — generate 60+ noise memories
# ---------------------------------------------------------------------------

def _noise_memories() -> list[SetupMemory]:
    """Return 60+ noise memories spanning other projects / unrelated topics."""
    noises: list[SetupMemory] = []
    # 20 noise events from OTHER projects (created 1-7 days ago)
    for i in range(20):
        noises.append(
            _memory(
                memory_type="semantic",
                title=f"项目 Beta 日常沟通 #{i + 1}",
                summary=f"Beta 项目第 {i + 1} 条无关讨论记录",
                content={
                    "project": f"proj_beta_{i}",
                    "topic": f"无关功能讨论-{i}",
                    "messages": [
                        {"speaker": f"user_{i}", "text": f"这个需求我们讨论过了，先放下一版{i}"},
                        {"speaker": f"user_{i}", "text": f"同意，等下个迭代再处理{i}"},
                    ],
                },
                importance=0.2,
                confidence=0.6,
                created_hours_ago=24.0 * (1 + i % 7),
            )
        )
    # 20 noise event records from unrelated topics
    for i in range(20):
        noises.append(
            _memory(
                memory_type="semantic",
                title=f"运营数据周报 #{i + 1}",
                summary=f"运营数据周报第 {i + 1} 期",
                content={
                    "topic": f"运营报告-{i}",
                    "summary": f"本周新增用户 {100 + i * 10} 人，活跃率 {60 + i}%，无重大异常",
                },
                importance=0.1,
                confidence=0.5,
                created_hours_ago=24.0 * (2 + i % 6),
            )
        )
    # 20 noise technical discussion records
    for i in range(20):
        noises.append(
            _memory(
                memory_type="semantic",
                title=f"技术方案评审记录 #{i + 1}",
                summary=f"技术评审第 {i + 1} 次会议记录",
                content={
                    "topic": f"其他系统架构讨论-{i}",
                    "decision": f"采用方案 {['A', 'B', 'C'][i % 3]}，待落地",
                },
                importance=0.2,
                confidence=0.5,
                created_hours_ago=24.0 * (3 + i % 5),
            )
        )
    return noises


def _noise_events() -> list[SetupEvent]:
    """Return 50+ noise events from other projects / unrelated topics."""
    events: list[SetupEvent] = []
    for i in range(25):
        events.append(
            _event(
                source_type="message",
                content=f"[项目 Beta] 各位，需求{i}的方案已经评审完了，请查看。",
                actors=[f"pm_beta_{i}"],
                scope="project",
                created_hours_ago=12.0 + i * 3,
            )
        )
    for i in range(25):
        events.append(
            _event(
                source_type="message",
                content=f"[运营] 各位同事，本周数据报告已发布，详见附件{i}。",
                actors=[f"ops_{i}"],
                scope="project",
                created_hours_ago=6.0 + i * 2,
            )
        )
    return events


# ---------------------------------------------------------------------------
# Case definitions
# ---------------------------------------------------------------------------

TRACK_A_CASES: list[BenchmarkCase] = [

    # =====================================================================
    # A-001: Fact recall — basic recall of a known decision
    # =====================================================================
    BenchmarkCase(
        case_id="A-001",
        track="A",
        capability="fact_recall",
        description=(
            "项目经理 pm_zhang 在需求评审中确认了 Project Alpha 的启动截止时间。"
            "两天后，测试负责人 test_zhao 通过 Recall 确认该截止时间，"
            "系统应精准返回「4 月 25 日」并附带来源和置信度。"
        ),
        direction="B",
        complexity_reasoning="low",
        complexity_tool="low",
        complexity_interaction="low",
        memory_types=["decision"],
        setup_events=[
            _event(
                source_type="message",
                content=(
                    "[产品评审群] pm_zhang: 各位，Project Alpha 启动截止时间定为 4 月 25 日，"
                    "请各角色据此安排计划。特此记录，作为项目基线。"
                ),
                actors=["pm_zhang"],
                scope="project",
                source_ref="msg_alpha_001",
                created_hours_ago=48.0,
            ),
        ],
        setup_memories=[
            _memory(
                memory_type="decision",
                title="Project Alpha 启动截止时间确认",
                summary="Project Alpha 的启动截止时间确定为 4 月 25 日（4 月最后一个周五）",
                content={
                    "project": "proj_alpha",
                    "decision": "启动截止时间",
                    "value": "2026-04-25",
                    "confirmed_by": "pm_zhang",
                    "rationale": "4月最后一个工作日，便于跨部门同步",
                },
                importance=0.9,
                confidence=0.95,
                evidence=[
                    {
                        "source_type": "message",
                        "source_ref": "msg_alpha_001",
                        "actor": "pm_zhang",
                        "excerpt": "Project Alpha 启动截止时间定为 4 月 25 日",
                    },
                ],
                tags=["project_alpha", "deadline"],
                created_hours_ago=48.0,
                project_id="proj_alpha",
            ),
        ],
        recalls=[
            RecallSpec(
                query="Project Alpha 的启动截止时间是什么时候？",
                project_id="proj_alpha",
                intent="fact_lookup",
                limit=5,
            ),
        ],
        assertions=[
            ResultAssertion(type="contains_title", value="Project Alpha 启动截止时间确认"),
            ResultAssertion(type="contains_memory_type", value="decision"),
            ResultAssertion(type="contains_tag", value="deadline"),
        ],
        expected_titles=["Project Alpha 启动截止时间确认"],
        expected_count_range=(1, 1),
        spec_ref="AIT-01 (simplified)",
        notes="基础事实回忆，无干扰，期望精确匹配。",
    ),

    # =====================================================================
    # A-002: Fact recall with interference (AIT-01 from spec.md 9.3.1.1)
    # =====================================================================
    BenchmarkCase(
        case_id="A-002",
        track="A",
        capability="fact_recall_with_interference",
        description=(
            "注入 Project Alpha 关键记忆（截止时间 = 5 月 15 日），"
            "随后注入 60 条跨项目噪音数据（其他项目讨论、运营周报、技术评审等），"
            "验证系统仍能从噪音海洋中精准召回截止时间，排名 Top-3，"
            "且无关噪音不进入结果。"
        ),
        direction="B",
        complexity_reasoning="medium",
        complexity_tool="low",
        complexity_interaction="low",
        memory_types=["decision"],
        setup_events=[
            _event(
                source_type="message",
                content=(
                    "[Project Alpha 项目群] pm_zhang: 各位，Alpha 项目截止时间确认为 5 月 15 日，"
                    "请各角色据此安排计划。变更已同步至项目管理文档。"
                ),
                actors=["pm_zhang"],
                scope="project",
                source_ref="msg_alpha_deadline_001",
                created_hours_ago=DAY * 6,
            ),
        ],
        setup_memories=[
            _memory(
                memory_type="decision",
                title="Project Alpha 截止时间确认为 5 月 15 日",
                summary="Project Alpha 的上线截止时间最终确定为 2026 年 5 月 15 日（5 月中评审节点）",
                content={
                    "project": "proj_alpha",
                    "decision": "上线截止时间",
                    "value": "2026-05-15",
                    "confirmed_by": "pm_zhang",
                    "rationale": "5月中评审节点，与Q2里程碑对齐",
                },
                importance=0.95,
                confidence=0.98,
                evidence=[
                    {
                        "source_type": "message",
                        "source_ref": "msg_alpha_deadline_001",
                        "actor": "pm_zhang",
                        "excerpt": "Alpha 项目截止时间确认为 5 月 15 日",
                    },
                ],
                tags=["project_alpha", "deadline", "confirmed"],
                created_hours_ago=DAY * 6,
                project_id="proj_alpha",
            ),
        ],
        interference=InterferenceSetup(
            memories=_noise_memories(),
            events=_noise_events(),
            count=60,
        ),
        recalls=[
            RecallSpec(
                query="Project Alpha 的截止时间是什么时候？",
                project_id="proj_alpha",
                intent="fact_lookup",
                limit=3,
            ),
        ],
        assertions=[
            ResultAssertion(type="contains_title", value="Project Alpha 截止时间确认为 5 月 15 日"),
            ResultAssertion(type="contains_tag", value="project_alpha"),
        ],
        # Noise memories may appear in broader recall but not in Top-3
        forbidden_titles=["项目 Beta 日常沟通", "运营数据周报", "技术方案评审记录"],
        expected_titles=["Project Alpha 截止时间确认为 5 月 15 日"],
        expected_count_range=(1, 3),
        spec_ref="AIT-01",
        notes=(
            "spec.md 9.3.1.1 AIT-01: 干扰量 ≥ 50 条，期望关键记忆召回率 ≥ 90%，"
            "噪音占比 ≤ 20%，关键记忆排名 ≤ Top-3。"
        ),
    ),

    # =====================================================================
    # A-003: Time-dependent reasoning — sequential updates "first A then B then C"
    # =====================================================================
    BenchmarkCase(
        case_id="A-003",
        track="A",
        capability="time_dependent_reasoning",
        description=(
            "Project Alpha 技术方案经历了三次迭代："
            "V1 选定微服务架构（3周前）→ V2 改为单体简化方案（2周前）→ "
            "V3 最终确定为模块化单体（3天前，原因是微服务治理成本超预期）。"
            "验证系统能否正确返回 V3（模块化单体），并保留完整的版本演化链。"
        ),
        direction="B",
        complexity_reasoning="medium",
        complexity_tool="low",
        complexity_interaction="low",
        memory_types=["decision"],
        setup_memories=[
            _memory(
                memory_type="decision",
                title="Project Alpha 技术方案 V1 — 微服务架构",
                summary="Project Alpha 技术方案 V1 选定微服务架构，采用 6 个独立服务",
                content={
                    "project": "proj_alpha",
                    "version": "V1",
                    "decision": "技术方案选型",
                    "value": "微服务架构",
                    "services": ["用户服务", "订单服务", "支付服务", "通知服务", "日志服务", "配置服务"],
                    "rationale": "解耦程度高，适合未来扩展",
                    "superseded": True,
                    "superseded_by": "V2",
                },
                importance=0.7,
                confidence=0.9,
                evidence=[
                    {
                        "source_type": "message",
                        "actor": "eng_li",
                        "excerpt": "技术方案选定微服务架构，共6个独立服务",
                    },
                ],
                tags=["project_alpha", "architecture", "v1", "superseded"],
                created_hours_ago=DAY * 21,
                project_id="proj_alpha",
            ),
            _memory(
                memory_type="decision",
                title="Project Alpha 技术方案 V2 — 单体简化方案",
                summary="Project Alpha V2 改为单体简化方案，原因是微服务治理成本评估超预期",
                content={
                    "project": "proj_alpha",
                    "version": "V2",
                    "decision": "技术方案选型",
                    "value": "单体简化方案",
                    "rationale": "微服务治理成本评估超预期，简化优先",
                    "superseded": True,
                    "superseded_by": "V3",
                },
                importance=0.8,
                confidence=0.9,
                evidence=[
                    {
                        "source_type": "message",
                        "actor": "eng_li",
                        "excerpt": "经评估，微服务方案运维成本过高，改用单体简化方案",
                    },
                ],
                tags=["project_alpha", "architecture", "v2", "superseded"],
                created_hours_ago=DAY * 14,
                project_id="proj_alpha",
            ),
            _memory(
                memory_type="decision",
                title="Project Alpha 技术方案 V3 — 模块化单体（最终）",
                summary=(
                    "Project Alpha 技术方案 V3 最终确定为模块化单体，"
                    "保留模块边界但避免服务间通信开销，原因是微服务治理成本超预期"
                ),
                content={
                    "project": "proj_alpha",
                    "version": "V3",
                    "decision": "技术方案选型",
                    "value": "模块化单体",
                    "modules": ["用户模块", "订单模块", "支付模块", "通知模块"],
                    "rationale": (
                        "微服务治理成本超预期；模块化单体保留清晰边界，"
                        "同时降低运维复杂度"
                    ),
                    "superseded": False,
                },
                importance=0.95,
                confidence=0.98,
                evidence=[
                    {
                        "source_type": "message",
                        "actor": "eng_li",
                        "excerpt": "最终方案确定为模块化单体，保留模块边界，运维简单",
                    },
                    {
                        "source_type": "doc",
                        "actor": "eng_li",
                        "excerpt": "V3 方案评审通过：模块化单体，4个核心模块",
                    },
                ],
                tags=["project_alpha", "architecture", "v3", "final"],
                created_hours_ago=DAY * 3,
                project_id="proj_alpha",
            ),
        ],
        recalls=[
            RecallSpec(
                query="Project Alpha 当前采用什么技术方案？最终版本是哪个？",
                project_id="proj_alpha",
                intent="fact_lookup",
                limit=5,
            ),
        ],
        assertions=[
            ResultAssertion(type="contains_title", value="模块化单体"),
            ResultAssertion(type="contains_tag", value="v3"),
            ResultAssertion(type="contains_tag", value="final", negates=False),
            # V1 and V2 should still be accessible but not as "current"
            ResultAssertion(type="contains_tag", value="superseded"),
        ],
        expected_titles=[
            "Project Alpha 技术方案 V3 — 模块化单体（最终）",
            "Project Alpha 技术方案 V2 — 单体简化方案",
            "Project Alpha 技术方案 V1 — 微服务架构",
        ],
        expected_count_range=(2, 3),
        spec_ref="CUT-02",
        notes="时间依赖推理，验证版本链是否完整，最新版本应排在最前。",
    ),

    # =====================================================================
    # A-004: Version chain recall — superseded memory, verify only latest returned
    # =====================================================================
    BenchmarkCase(
        case_id="A-004",
        track="A",
        capability="version_chain_recall",
        description=(
            "eng_li 多次更新了 Project Alpha 的技术栈决定："
            "最初决定使用 Vue3（2周前），后改为 React（1周前），"
            "最终确定为 Vue3 + Vite + Pinia（3天前，原因：团队更熟悉 Vue）。"
            "当查询「当前前端技术栈」时，系统应只返回 Vue3 + Vite + Pinia，"
            "不应将旧版本作为当前答案，但应保留历史记录。"
        ),
        direction="B",
        complexity_reasoning="low",
        complexity_tool="low",
        complexity_interaction="low",
        memory_types=["decision"],
        setup_memories=[
            _memory(
                memory_type="decision",
                title="Project Alpha 前端技术栈 V1 — Vue3",
                summary="Project Alpha V1 决定使用 Vue3 作为前端框架",
                content={
                    "project": "proj_alpha",
                    "version": "V1",
                    "decision": "前端技术栈",
                    "value": "Vue3",
                    "superseded": True,
                    "superseded_by": "V3",
                },
                importance=0.8,
                confidence=0.9,
                created_hours_ago=DAY * 14,
                project_id="proj_alpha",
            ),
            _memory(
                memory_type="decision",
                title="Project Alpha 前端技术栈 V2 — React",
                summary="Project Alpha V2 改为 React，原因：eng_li 调研后认为 React 生态更成熟",
                content={
                    "project": "proj_alpha",
                    "version": "V2",
                    "decision": "前端技术栈",
                    "value": "React",
                    "rationale": "eng_li 调研后认为 React 生态更成熟",
                    "superseded": True,
                    "superseded_by": "V3",
                },
                importance=0.8,
                confidence=0.85,
                created_hours_ago=DAY * 7,
                project_id="proj_alpha",
            ),
            _memory(
                memory_type="decision",
                title="Project Alpha 前端技术栈 V3 — Vue3 + Vite + Pinia（最终）",
                summary="Project Alpha 最终确定前端技术栈为 Vue3 + Vite + Pinia，团队更熟悉 Vue",
                content={
                    "project": "proj_alpha",
                    "version": "V3",
                    "decision": "前端技术栈",
                    "value": "Vue3 + Vite + Pinia",
                    "rationale": "团队更熟悉 Vue 生态，上手更快；Vite 提供优秀的开发体验",
                    "superseded": False,
                },
                importance=0.95,
                confidence=0.97,
                evidence=[
                    {
                        "source_type": "message",
                        "actor": "eng_li",
                        "excerpt": "最终技术栈：Vue3 + Vite + Pinia，大家更熟悉，上手快",
                    },
                ],
                tags=["project_alpha", "frontend", "vue3", "final"],
                created_hours_ago=DAY * 3,
                project_id="proj_alpha",
            ),
        ],
        recalls=[
            RecallSpec(
                query="Project Alpha 当前使用的前端技术栈是什么？",
                project_id="proj_alpha",
                intent="fact_lookup",
                limit=3,
            ),
        ],
        assertions=[
            ResultAssertion(type="contains_title", value="Vue3"),
            ResultAssertion(type="contains_title", value="Vite"),
            ResultAssertion(type="contains_tag", value="final"),
        ],
        expected_titles=["Project Alpha 前端技术栈 V3 — Vue3 + Vite + Pinia（最终）"],
        forbidden_titles=[],
        expected_count_range=(1, 3),
        spec_ref="CUT-02 (variant)",
        notes="版本链recall，过时版本不应作为当前答案返回。",
    ),

    # =====================================================================
    # A-005: Multi-session integration — scattered info across events
    # =====================================================================
    BenchmarkCase(
        case_id="A-005",
        track="A",
        capability="multi_session_integration",
        description=(
            "Project Alpha 的上线前风险分散在多个会话中："
            "pm_zhang 在评审会确认风险（高）；"
            "test_zhao 在测试计划中记录了风险项；"
            "ops_chen 在发布计划中补充了回滚策略。三个独立事件合成一条完整的风险评估。"
            "验证系统能否整合三个来源，输出一致的风险评估摘要。"
        ),
        direction="B",
        complexity_reasoning="medium",
        complexity_tool="low",
        complexity_interaction="medium",
        memory_types=["decision", "task_status"],
        setup_events=[
            _event(
                source_type="meeting",
                content=(
                    "[Alpha 需求评审会] pm_zhang: Alpha 上线风险评估为「高」，"
                    "主要风险点：第三方支付接口不稳定、跨部门联调时间窗口紧张。"
                ),
                actors=["pm_zhang", "eng_li", "design_wang", "test_zhao"],
                scope="project",
                source_ref="meeting_alpha_review_001",
                payload={"meeting_type": "需求评审", "risk_level": "高"},
                created_hours_ago=DAY * 5,
            ),
            _event(
                source_type="doc",
                content=(
                    "[Alpha 测试计划] test_zhao: 上线风险项已在测试计划中标注，"
                    "冒烟测试覆盖支付流程，回归测试覆盖核心订单链路，"
                    "上线前需通过全量回归。"
                ),
                actors=["test_zhao"],
                scope="project",
                source_ref="doc_alpha_test_plan",
                payload={"risk_items": ["支付接口", "订单链路", "跨部门联调"]},
                created_hours_ago=DAY * 3,
            ),
            _event(
                source_type="message",
                content=(
                    "[Alpha 项目群] ops_chen: 已制定回滚方案，若上线失败，"
                    "可在 15 分钟内回滚至上一稳定版本，请各角色确认。"
                ),
                actors=["ops_chen"],
                scope="project",
                source_ref="msg_alpha_rollback",
                payload={"rollback_time": "15分钟", "strategy": "版本回滚"},
                created_hours_ago=DAY * 1,
            ),
        ],
        setup_memories=[
            _memory(
                memory_type="decision",
                title="Project Alpha 上线风险评估 — 高风险",
                summary="Alpha 上线风险评估为高风险，主要风险点：第三方支付接口不稳定、跨部门联调时间窗口紧张",
                content={
                    "project": "proj_alpha",
                    "risk_level": "高",
                    "risk_points": [
                        {"point": "第三方支付接口不稳定", "severity": "高"},
                        {"point": "跨部门联调时间窗口紧张", "severity": "中"},
                    ],
                    "assessed_by": "pm_zhang",
                    "assessment_date": "2026-04-23",
                },
                importance=0.9,
                confidence=0.92,
                evidence=[
                    {
                        "source_type": "meeting",
                        "source_ref": "meeting_alpha_review_001",
                        "actor": "pm_zhang",
                        "excerpt": "Alpha 上线风险评估为「高」",
                    },
                ],
                tags=["project_alpha", "risk", "high"],
                created_hours_ago=DAY * 5,
                project_id="proj_alpha",
            ),
            _memory(
                memory_type="task_status",
                title="Project Alpha 回滚策略 — 15分钟版本回滚",
                summary="ops_chen 制定上线回滚策略，失败时 15 分钟内回滚至上一稳定版本",
                content={
                    "project": "proj_alpha",
                    "rollback_time": "15分钟",
                    "strategy": "版本回滚",
                    "previous_stable_version": "v1.2.3",
                    "owner": "ops_chen",
                },
                importance=0.85,
                confidence=0.95,
                evidence=[
                    {
                        "source_type": "message",
                        "source_ref": "msg_alpha_rollback",
                        "actor": "ops_chen",
                        "excerpt": "可在 15 分钟内回滚至上一稳定版本",
                    },
                ],
                tags=["project_alpha", "rollback", "ops"],
                created_hours_ago=DAY * 1,
                project_id="proj_alpha",
            ),
        ],
        recalls=[
            RecallSpec(
                query="Project Alpha 的上线风险是什么？有什么应对措施？",
                project_id="proj_alpha",
                intent="fact_lookup",
                limit=5,
            ),
        ],
        assertions=[
            ResultAssertion(type="contains_tag", value="risk"),
            ResultAssertion(type="contains_tag", value="rollback"),
            ResultAssertion(type="contains_tag", value="project_alpha"),
        ],
        expected_titles=[
            "Project Alpha 上线风险评估 — 高风险",
            "Project Alpha 回滚策略 — 15分钟版本回滚",
        ],
        expected_count_range=(2, 3),
        spec_ref="9.3.2.1 (multi_session_integration)",
        notes="多session整合，分散信息需要能被整合召回。",
    ),

    # =====================================================================
    # A-006: Knowledge update override — old superseded by new, verify new returned
    # =====================================================================
    BenchmarkCase(
        case_id="A-006",
        track="A",
        capability="knowledge_update_override",
        description=(
            "Project Alpha 的负责人分配经历了两次变更："
            "最初指定 eng_li 为技术负责人（2周前），"
            "后因 eng_li 工作负荷饱和，变更为 design_wang 担任技术牵头（1周前），"
            "但 design_wang 主要负责设计，技术决策仍由 eng_li 把控（双重角色）。"
            "最终正式确认：eng_li 为技术负责人，design_wang 为产品设计负责人。"
            "验证系统返回最终版本，不返回中间混淆版本。"
        ),
        direction="B",
        complexity_reasoning="medium",
        complexity_tool="low",
        complexity_interaction="medium",
        memory_types=["decision"],
        setup_memories=[
            _memory(
                memory_type="decision",
                title="Project Alpha 技术负责人 V1 — eng_li",
                summary="V1 指定 eng_li 为 Project Alpha 技术负责人",
                content={
                    "project": "proj_alpha",
                    "version": "V1",
                    "role": "技术负责人",
                    "person": "eng_li",
                    "superseded": True,
                    "superseded_by": "V3",
                },
                importance=0.85,
                confidence=0.9,
                created_hours_ago=DAY * 14,
                project_id="proj_alpha",
            ),
            _memory(
                memory_type="decision",
                title="Project Alpha 角色分配 V2（混淆版本）— eng_li 负荷问题",
                summary="V2 因 eng_li 工作负荷饱和，尝试变更技术负责人，但未最终落地",
                content={
                    "project": "proj_alpha",
                    "version": "V2",
                    "role": "技术负责人",
                    "person": "design_wang（临时牵头，技术决策仍由 eng_li 把控）",
                    "rationale": "eng_li 工作负荷饱和",
                    "superseded": True,
                    "superseded_by": "V3",
                    "note": "此版本未最终落地，后续被V3覆盖",
                },
                importance=0.75,
                confidence=0.7,
                created_hours_ago=DAY * 7,
                project_id="proj_alpha",
            ),
            _memory(
                memory_type="decision",
                title="Project Alpha 角色分配 V3（最终）— eng_li + design_wang 分工明确",
                summary="V3 最终确认：eng_li 为技术负责人，design_wang 为产品设计负责人，双重角色明确",
                content={
                    "project": "proj_alpha",
                    "version": "V3",
                    "decisions": [
                        {"role": "技术负责人", "person": "eng_li", "scope": "技术架构、核心决策"},
                        {"role": "产品设计负责人", "person": "design_wang", "scope": "产品设计、用户体验"},
                    ],
                    "superseded": False,
                    "superseded_by": None,
                },
                importance=0.95,
                confidence=0.98,
                evidence=[
                    {
                        "source_type": "message",
                        "actor": "pm_zhang",
                        "excerpt": "最终确认：eng_li 技术负责人，design_wang 产品设计负责人",
                    },
                ],
                tags=["project_alpha", "team", "final"],
                created_hours_ago=DAY * 2,
                project_id="proj_alpha",
            ),
        ],
        recalls=[
            RecallSpec(
                query="Project Alpha 当前的技术负责人是谁？",
                project_id="proj_alpha",
                intent="fact_lookup",
                limit=3,
            ),
        ],
        assertions=[
            ResultAssertion(type="contains_title", value="eng_li"),
            ResultAssertion(type="contains_tag", value="final"),
        ],
        expected_titles=["Project Alpha 角色分配 V3（最终）— eng_li + design_wang 分工明确"],
        forbidden_titles=[],
        expected_count_range=(1, 3),
        spec_ref="CUT-04",
        notes="知识更新覆盖，验证新旧决策的覆盖关系正确。",
    ),

    # =====================================================================
    # A-007: Refusal on unknowns — no matching memory, verify zero results
    # =====================================================================
    BenchmarkCase(
        case_id="A-007",
        track="A",
        capability="refusal_on_unknowns",
        description=(
            "系统中仅存储了 Project Alpha 相关记忆，"
            "查询一个完全不相关的项目（Project Gamma，该项目从未在任何记忆中出现），"
            "验证系统返回零结果，而非编造信息。"
        ),
        direction="B",
        complexity_reasoning="low",
        complexity_tool="low",
        complexity_interaction="low",
        memory_types=["decision"],
        setup_memories=[
            _memory(
                memory_type="decision",
                title="Project Alpha 截止时间确认",
                summary="Project Alpha 截止时间为 2026-05-15",
                content={
                    "project": "proj_alpha",
                    "deadline": "2026-05-15",
                },
                importance=0.9,
                confidence=0.95,
                created_hours_ago=DAY * 3,
                project_id="proj_alpha",
            ),
        ],
        recalls=[
            RecallSpec(
                query="Project Gamma 的上线截止时间是什么？有哪些技术负责人？",
                project_id="proj_gamma",  # 完全不同、毫不相关的项目
                intent="fact_lookup",
                limit=5,
            ),
        ],
        assertions=[],
        expected_titles=[],
        expect_zero_results=True,
        spec_ref="9.3.2.1 (refusal_on_unknowns)",
        notes="拒答能力验证，无记忆时不应编造。",
    ),

    # =====================================================================
    # A-008: Low confidence filtering — memory exists but min_score suppresses it
    # =====================================================================
    BenchmarkCase(
        case_id="A-008",
        track="A",
        capability="low_confidence_filtering",
        description=(
            "系统中有一条关于 Project Alpha 配色方案的早期讨论记录，"
            "但其置信度仅为 0.35（因来源仅为一条群聊消息，未得到确认）。"
            "设置 min_score=0.4 的召回查询，验证该低置信度记忆被正确过滤，不出现在结果中。"
            "同时准备一条高置信度（0.9）的配色方案确认记忆，验证其不受影响。"
        ),
        direction="B",
        complexity_reasoning="low",
        complexity_tool="medium",
        complexity_interaction="low",
        memory_types=["decision", "preference"],
        setup_memories=[
            _memory(
                memory_type="preference",
                title="Project Alpha 早期配色讨论（未确认）",
                summary="群聊中有成员提到「考虑蓝色系配色」，但未经正式评审",
                content={
                    "project": "proj_alpha",
                    "topic": "配色方案",
                    "value": "蓝色系（初步讨论）",
                    "status": "未确认",
                    "note": "仅群聊消息，无正式评审记录",
                },
                importance=0.3,
                confidence=0.35,  # 低置信度：未确认信息
                tags=["project_alpha", "design", "unconfirmed"],
                created_hours_ago=DAY * 10,
                project_id="proj_alpha",
            ),
            _memory(
                memory_type="decision",
                title="Project Alpha 配色方案确认 — 科技蓝 + 灰色系",
                summary="经设计评审，Project Alpha 最终确定配色方案为科技蓝主色 + 浅灰辅助色",
                content={
                    "project": "proj_alpha",
                    "decision": "配色方案",
                    "value": "科技蓝（#1A73E8）+ 浅灰辅助色（#F8F9FA）",
                    "confirmed_by": "design_wang",
                    "status": "已确认",
                },
                importance=0.7,
                confidence=0.95,  # 高置信度
                evidence=[
                    {
                        "source_type": "doc",
                        "source_ref": "doc_alpha_design",
                        "actor": "design_wang",
                        "excerpt": "配色方案已评审通过：科技蓝 + 浅灰",
                    },
                ],
                tags=["project_alpha", "design", "confirmed"],
                created_hours_ago=DAY * 2,
                project_id="proj_alpha",
            ),
        ],
        recalls=[
            RecallSpec(
                query="Project Alpha 的配色方案是什么？",
                project_id="proj_alpha",
                intent="fact_lookup",
                limit=5,
            ),
        ],
        assertions=[
            ResultAssertion(type="contains_title", value="科技蓝"),
            ResultAssertion(
                type="contains_title", value="早期配色讨论（未确认）", negates=True
            ),
        ],
        expected_titles=["Project Alpha 配色方案确认 — 科技蓝 + 灰色系"],
        forbidden_titles=["Project Alpha 早期配色讨论（未确认）"],
        expected_count_range=(1, 1),
        spec_ref="9.3.2.1 (confidence_filtering)",
        notes="低置信度过滤验证，min_score=0.4 时，未确认信息（0.35）应被过滤。",
    ),

    # =====================================================================
    # A-009: Scope isolation — user-scope memory NOT in project-scope query
    # =====================================================================
    BenchmarkCase(
        case_id="A-009",
        track="A",
        capability="scope_isolation",
        description=(
            "pm_zhang 的个人工作偏好（user scope）记录了"
            "「周报请在每周五15:00前发送给我，我习惯提前预览」。"
            "同时，proj_alpha 的项目级(project scope)截止时间记忆也存在。"
            "当以 project_id=proj_alpha 范围查询时，"
            "pm_zhang 的个人偏好不应出现在结果中。"
        ),
        direction="B",
        complexity_reasoning="low",
        complexity_tool="low",
        complexity_interaction="low",
        memory_types=["preference", "decision"],
        setup_memories=[
            _memory(
                memory_type="preference",
                title="pm_zhang 周报偏好 — 周五15:00前发送",
                summary="pm_zhang 个人偏好：周报在每周五15:00前发送，方便提前预览",
                content={
                    "user": "pm_zhang",
                    "preference_type": "report_timing",
                    "value": "每周五15:00前",
                    "rationale": "习惯提前预览，不喜欢临时收到",
                },
                importance=0.7,
                confidence=0.9,
                tags=["pm_zhang", "preference", "user_scope"],
                created_hours_ago=DAY * 14,
                scope="user",
                user_id="pm_zhang",
            ),
            _memory(
                memory_type="decision",
                title="Project Alpha 上线截止时间",
                summary="Project Alpha 上线截止时间为 2026-05-15",
                content={
                    "project": "proj_alpha",
                    "deadline": "2026-05-15",
                    "confirmed_by": "pm_zhang",
                },
                importance=0.9,
                confidence=0.95,
                tags=["project_alpha", "deadline", "project_scope"],
                created_hours_ago=DAY * 5,
                project_id="proj_alpha",
            ),
            _memory(
                memory_type="decision",
                title="Project Alpha 里程碑计划",
                summary="Project Alpha 里程碑：4月25日完成需求评审，5月15日上线",
                content={
                    "project": "proj_alpha",
                    "milestones": [
                        {"date": "2026-04-25", "event": "需求评审完成"},
                        {"date": "2026-05-15", "event": "正式上线"},
                    ],
                },
                importance=0.85,
                confidence=0.92,
                tags=["project_alpha", "milestone", "project_scope"],
                created_hours_ago=DAY * 5,
                project_id="proj_alpha",
            ),
        ],
        recalls=[
            RecallSpec(
                query="项目截止时间和里程碑计划是什么？",
                project_id="proj_alpha",
                scope="project",
                intent="fact_lookup",
                limit=5,
            ),
        ],
        assertions=[
            ResultAssertion(type="contains_tag", value="project_scope"),
            ResultAssertion(type="contains_tag", value="deadline", negates=False),
            ResultAssertion(type="contains_tag", value="user_scope", negates=True),
        ],
        expected_titles=[
            "Project Alpha 上线截止时间",
            "Project Alpha 里程碑计划",
        ],
        forbidden_titles=["pm_zhang 周报偏好 — 周五15:00前发送"],
        expected_count_range=(2, 2),
        spec_ref="9.3.2.1 (scope_isolation)",
        notes="作用域隔离：user scope 记忆不应出现在 project scope 查询结果中。",
    ),

    # =====================================================================
    # A-010: Scope isolation reverse — project-scope memory NOT in user-scope query
    # =====================================================================
    BenchmarkCase(
        case_id="A-010",
        track="A",
        capability="scope_isolation_reverse",
        description=(
            "proj_alpha 项目级决策记录了「Alpha 技术方案为模块化单体」（project scope）。"
            "同时，pm_zhang 的个人偏好记录了「技术讨论请先发文字版，再口头补充」（user scope）。"
            "当以 user_id=pm_zhang + scope=user 查询时，"
            "项目级技术决策不应出现在结果中。"
        ),
        direction="B",
        complexity_reasoning="low",
        complexity_tool="low",
        complexity_interaction="low",
        memory_types=["decision", "preference"],
        setup_memories=[
            _memory(
                memory_type="decision",
                title="Project Alpha 技术方案 — 模块化单体",
                summary="Alpha 项目技术方案为模块化单体",
                content={
                    "project": "proj_alpha",
                    "decision": "技术方案",
                    "value": "模块化单体",
                },
                importance=0.9,
                confidence=0.97,
                tags=["project_alpha", "architecture", "project_scope"],
                created_hours_ago=DAY * 3,
                project_id="proj_alpha",
            ),
            _memory(
                memory_type="preference",
                title="pm_zhang 沟通偏好 — 文字版优先",
                summary="pm_zhang 个人偏好：技术讨论请先发文字版，再口头补充，方便事后追溯",
                content={
                    "user": "pm_zhang",
                    "preference_type": "communication_format",
                    "value": "先文字版，再口头补充",
                    "rationale": "方便事后追溯，不喜欢只靠口头讨论",
                },
                importance=0.7,
                confidence=0.9,
                tags=["pm_zhang", "preference", "user_scope"],
                created_hours_ago=DAY * 7,
                scope="user",
                user_id="pm_zhang",
            ),
            _memory(
                memory_type="preference",
                title="pm_zhang 会议偏好 — 会前提供议程",
                summary="pm_zhang 个人偏好：会议开始前请提供议程，以便提前准备",
                content={
                    "user": "pm_zhang",
                    "preference_type": "meeting_preference",
                    "value": "会前提供议程",
                    "rationale": "希望提前准备，提高会议效率",
                },
                importance=0.65,
                confidence=0.88,
                tags=["pm_zhang", "preference", "user_scope"],
                created_hours_ago=DAY * 10,
                scope="user",
                user_id="pm_zhang",
            ),
        ],
        recalls=[
            RecallSpec(
                query="pm_zhang 的工作沟通偏好是什么？",
                user_id="pm_zhang",
                scope="user",
                intent="fact_lookup",
                limit=5,
            ),
        ],
        assertions=[
            ResultAssertion(type="contains_tag", value="user_scope"),
            ResultAssertion(type="contains_tag", value="project_scope", negates=True),
            ResultAssertion(type="contains_tag", value="pm_zhang", negates=False),
        ],
        expected_titles=[
            "pm_zhang 沟通偏好 — 文字版优先",
            "pm_zhang 会议偏好 — 会前提供议程",
        ],
        forbidden_titles=["Project Alpha 技术方案 — 模块化单体"],
        expected_count_range=(2, 2),
        spec_ref="9.3.2.1 (scope_isolation)",
        notes="反向作用域隔离：project scope 记忆不应出现在 user scope 查询结果中。",
    ),

    # =====================================================================
    # A-011: Evidence trail — recalled memory includes source evidence references
    # =====================================================================
    BenchmarkCase(
        case_id="A-011",
        track="A",
        capability="evidence_trail",
        description=(
            "Project Alpha 的一次设计评审记录（evidence）"
            "证明设计决策获得了跨部门认可："
            "评审会议记录 source_ref=meeting_alpha_design_001 来自 design_wang、pm_zhang、eng_li 三方确认。"
            "召回时，验证返回的记忆包含 evidence 字段，"
            "其中包含 source_type、source_ref、actor、excerpt 等可追溯字段。"
        ),
        direction="B",
        complexity_reasoning="low",
        complexity_tool="low",
        complexity_interaction="low",
        memory_types=["decision"],
        setup_events=[
            _event(
                source_type="meeting",
                content=(
                    "[Alpha 设计评审会] design_wang 展示设计方案，"
                    "pm_zhang 确认产品逻辑，eng_li 确认技术可行性。"
                    "三方一致同意设计评审通过，页面采用卡片式布局，主色调科技蓝。"
                ),
                actors=["design_wang", "pm_zhang", "eng_li"],
                scope="project",
                source_ref="meeting_alpha_design_001",
                payload={"meeting_type": "设计评审", "outcome": "通过"},
                created_hours_ago=DAY * 4,
            ),
        ],
        setup_memories=[
            _memory(
                memory_type="decision",
                title="Project Alpha 设计方案评审通过 — 卡片式布局 科技蓝主色",
                summary="Alpha 设计方案经 design_wang、pm_zhang、eng_li 三方评审一致通过，确定卡片式布局和科技蓝主色",
                content={
                    "project": "proj_alpha",
                    "decision": "UI设计",
                    "layout": "卡片式布局",
                    "primary_color": "科技蓝（#1A73E8）",
                    "status": "已通过",
                    "confirmed_parties": ["design_wang", "pm_zhang", "eng_li"],
                },
                importance=0.88,
                confidence=0.97,
                evidence=[
                    {
                        "source_type": "meeting",
                        "source_ref": "meeting_alpha_design_001",
                        "actor": "design_wang",
                        "excerpt": "三方一致同意设计评审通过，卡片式布局，科技蓝主色",
                    },
                    {
                        "source_type": "meeting",
                        "source_ref": "meeting_alpha_design_001",
                        "actor": "pm_zhang",
                        "excerpt": "产品逻辑确认，设计方案可行",
                    },
                    {
                        "source_type": "meeting",
                        "source_ref": "meeting_alpha_design_001",
                        "actor": "eng_li",
                        "excerpt": "技术可行性确认，方案可落地",
                    },
                ],
                tags=["project_alpha", "design", "confirmed"],
                created_hours_ago=DAY * 4,
                project_id="proj_alpha",
            ),
        ],
        recalls=[
            RecallSpec(
                query="Project Alpha 的设计方案是什么？谁参与了评审确认？",
                project_id="proj_alpha",
                intent="fact_lookup",
                limit=3,
            ),
        ],
        assertions=[
            ResultAssertion(type="contains_title", value="设计方案评审通过"),
            ResultAssertion(type="contains_tag", value="confirmed"),
            ResultAssertion(type="contains_evidence_source_ref", value="meeting_alpha_design_001"),
            ResultAssertion(type="evidence_has_fields", value=["source_type", "source_ref", "actor", "excerpt"]),
        ],
        expected_titles=["Project Alpha 设计方案评审通过 — 卡片式布局 科技蓝主色"],
        expected_count_range=(1, 1),
        spec_ref="9.3.2.1 (evidence_trail)",
        notes=(
            "证据链验证：返回的记忆必须包含 evidence 字段，"
            "每个 evidence 需包含 source_type、source_ref、actor、excerpt。"
        ),
    ),

    # =====================================================================
    # A-012: Tag-based filtering — scope tags used to filter recalled memories
    # =====================================================================
    BenchmarkCase(
        case_id="A-012",
        track="A",
        capability="tag_based_filtering",
        description=(
            "Project Alpha 产生了多种类型的记忆："
            "技术决策(tag: tech)、产品决策(tag: product)、"
            "测试记录(tag: testing)、运维计划(tag: ops)。"
            "分别以不同 tag 过滤查询，验证各类型记忆能被精确筛选，"
            "且过滤 tag 与查询 tag 完全不匹配时返回零结果。"
        ),
        direction="B",
        complexity_reasoning="medium",
        complexity_tool="medium",
        complexity_interaction="low",
        memory_types=["decision", "task_status"],
        setup_memories=[
            _memory(
                memory_type="decision",
                title="Project Alpha 技术方案 — 模块化单体",
                summary="Alpha 技术方案确定为模块化单体",
                content={
                    "project": "proj_alpha",
                    "decision": "技术方案",
                    "value": "模块化单体",
                },
                importance=0.9,
                confidence=0.97,
                tags=["project_alpha", "tech", "architecture"],
                created_hours_ago=DAY * 3,
                project_id="proj_alpha",
            ),
            _memory(
                memory_type="decision",
                title="Project Alpha 核心功能范围 V1 — 用户体系 + 订单体系",
                summary="Alpha 产品核心功能范围确定：用户体系和订单体系",
                content={
                    "project": "proj_alpha",
                    "decision": "核心功能范围",
                    "features": ["用户体系", "订单体系"],
                },
                importance=0.88,
                confidence=0.95,
                tags=["project_alpha", "product"],
                created_hours_ago=DAY * 8,
                project_id="proj_alpha",
            ),
            _memory(
                memory_type="task_status",
                title="Project Alpha 测试计划 — 全量回归 5 月 10 日",
                summary="Alpha 测试计划：5 月 10 日完成全量回归测试",
                content={
                    "project": "proj_alpha",
                    "plan": "全量回归测试",
                    "date": "2026-05-10",
                    "owner": "test_zhao",
                },
                importance=0.85,
                confidence=0.93,
                tags=["project_alpha", "testing"],
                created_hours_ago=DAY * 5,
                project_id="proj_alpha",
            ),
            _memory(
                memory_type="task_status",
                title="Project Alpha 上线发布计划 — 5 月 15 日蓝绿部署",
                summary="Alpha 发布计划：5 月 15 日采用蓝绿部署方式上线",
                content={
                    "project": "proj_alpha",
                    "plan": "蓝绿部署上线",
                    "date": "2026-05-15",
                    "owner": "ops_chen",
                },
                importance=0.9,
                confidence=0.94,
                tags=["project_alpha", "ops"],
                created_hours_ago=DAY * 2,
                project_id="proj_alpha",
            ),
        ],
        recalls=[
            # Recall 1: Filter by tech tag
            RecallSpec(
                query="Project Alpha 的技术相关决策有哪些？",
                project_id="proj_alpha",
                intent="fact_lookup",
                limit=5,
            ),
            # Recall 2: Filter by testing tag
            RecallSpec(
                query="Project Alpha 的测试相关计划是什么？",
                project_id="proj_alpha",
                intent="fact_lookup",
                limit=5,
            ),
            # Recall 3: Non-matching tag → zero results
            RecallSpec(
                query="Project Alpha 的财务相关记录有哪些？",
                project_id="proj_alpha",
                intent="fact_lookup",
                limit=5,
            ),
        ],
        assertions=[
            # Recall 1 assertions
            ResultAssertion(type="contains_tag", value="tech"),
            ResultAssertion(type="contains_title", value="模块化单体"),
            # Recall 2 assertions
            ResultAssertion(type="contains_tag", value="testing"),
            ResultAssertion(type="contains_title", value="测试计划"),
        ],
        expected_titles=[
            "Project Alpha 技术方案 — 模块化单体",
            "Project Alpha 核心功能范围 V1 — 用户体系 + 订单体系",
            "Project Alpha 测试计划 — 全量回归 5 月 10 日",
            "Project Alpha 上线发布计划 — 5 月 15 日蓝绿部署",
        ],
        expected_count_range=(9, 12),
        spec_ref="9.3.2.1 (tag_based_filtering)",
        notes=(
            "Tag过滤验证：系统应支持按tag精确筛选记忆，"
            "非匹配tag查询应返回零结果或受限结果。"
        ),
    ),
]
