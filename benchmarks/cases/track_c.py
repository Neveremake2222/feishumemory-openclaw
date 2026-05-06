"""Track C (Preference Learning) benchmark test cases.

Covers spec.md 9.3.1.4 PLT-01 through PLT-06:
  PLT-01  Explicit preference expression
  PLT-02  Implicit preference induction
  PLT-03  Preference conflict detection
  PLT-04  Cross-scene preference isolation
  PLT-05  Preference decay
  PLT-06  Preference update / correction
"""

from __future__ import annotations

from benchmarks.structures import (
    BenchmarkCase,
    InterferenceSetup,
    ResultAssertion,
    RecallSpec,
    SetupEvent,
    SetupMemory,
)

# ---------------------------------------------------------------------------
# PLT-01: Explicit preference expression
# ---------------------------------------------------------------------------
# User (pm_zhang) explicitly says "我比较喜欢表格视图". System writes a
# preference card. A subsequent recall for the same user in a matching context
# should return that preference.
# ---------------------------------------------------------------------------

PLT_01_EXPLICIT_PREFERENCE = BenchmarkCase(
    case_id="PLT-01",
    track="C",
    capability="explicit_preference_expression",
    description=(
        "用户 pm_zhang 在飞书群聊中明确表达 '我比较喜欢表格视图'，"
        "系统应将此偏好写入 preference 类型记忆卡片。"
        "后续在需要展示多人数据时，召回该用户的偏好记忆。"
    ),
    direction="C",
    complexity_reasoning="low",
    complexity_tool="low",
    complexity_interaction="low",
    memory_types=["preference"],
    spec_ref="PLT-01",
    setup_events=[
        SetupEvent(
            source_type="message",
            content="我比较喜欢表格视图，看起来更清晰",
            actors=["pm_zhang"],
            scope="user",
            source_ref="msg_plt01_001",
            payload={"chat_id": "chat_project_alpha", "message_type": "text"},
            created_hours_ago=2,
        ),
    ],
    setup_memories=[
        SetupMemory(
            memory_type="preference",
            title="pm_zhang 视图偏好：表格视图",
            summary="用户 pm_zhang 明确表示偏好表格视图，认为表格展示更清晰。",
            content={
                "user_id": "pm_zhang",
                "preference_category": "view_layout",
                "preference_value": "table",
                "trigger_context": "展示多人数据、项目进度、任务列表",
                "expression_type": "explicit",
            },
            importance=0.7,
            confidence=0.9,
            evidence=[{"source_ref": "msg_plt01_001", "snippet": "我比较喜欢表格视图，看起来更清晰"}],
            tags=["preference", "view_layout", "explicit", "pm_zhang"],
            created_hours_ago=2,
            user_id="pm_zhang",
            scope="user",
        ),
    ],
    interference=InterferenceSetup(
        memories=[
            SetupMemory(
                memory_type="preference",
                title="eng_li 通知偏好：邮件优先",
                summary="用户 eng_li 偏好通过邮件接收正式通知。",
                content={
                    "user_id": "eng_li",
                    "preference_category": "notification_channel",
                    "preference_value": "email",
                },
                importance=0.5,
                confidence=0.8,
                tags=["preference", "notification", "eng_li"],
                created_hours_ago=6,
                user_id="eng_li",
                scope="user",
            ),
            SetupMemory(
                memory_type="preference",
                title="design_wang 文档偏好：Figma 链接",
                summary="用户 design_wang 偏好在飞书中直接分享 Figma 链接而非截图。",
                content={
                    "user_id": "design_wang",
                    "preference_category": "document_sharing",
                    "preference_value": "figma_link",
                },
                importance=0.5,
                confidence=0.8,
                tags=["preference", "document_sharing", "design_wang"],
                created_hours_ago=12,
                user_id="design_wang",
                scope="user",
            ),
        ],
        events=[
            SetupEvent(
                source_type="message",
                content="今天天气不错，大家记得喝水",
                actors=["eng_li"],
                scope="user",
                source_ref="msg_noise_plt01_001",
                created_hours_ago=1,
            ),
            SetupEvent(
                source_type="message",
                content="v2.3 的回归测试报告已经出了，没有 P0 问题",
                actors=["eng_li"],
                scope="project",
                source_ref="msg_noise_plt01_002",
                created_hours_ago=1.5,
            ),
        ],
        count=4,
    ),
    recalls=[
        RecallSpec(
            query="pm_zhang 喜欢用什么视图展示数据",
            user_id="pm_zhang",
            scope="user",
            intent="preference_lookup",
            limit=5,
        ),
    ],
    assertions=[
        ResultAssertion(type="contains_title", value="pm_zhang 视图偏好：表格视图"),
        ResultAssertion(type="contains_memory_type", value="preference"),
        ResultAssertion(type="contains_tag", value="pm_zhang"),
        # Should NOT return other users' preferences
        ResultAssertion(type="contains_title", value="eng_li 通知偏好：邮件优先", negates=True),
    ],
    expected_titles=["pm_zhang 视图偏好：表格视图"],
    forbidden_titles=["eng_li 通知偏好：邮件优先", "design_wang 文档偏好：Figma 链接"],
    expected_count_range=(1, 3),
)

# ---------------------------------------------------------------------------
# PLT-02: Implicit preference induction
# ---------------------------------------------------------------------------
# User (design_wang) never explicitly states a preference but repeatedly
# performs the same action (choosing card layout 3 out of 3 times). The system
# should have captured this behavioral pattern as a preference memory.
# ---------------------------------------------------------------------------

PLT_02_IMPLICIT_PREFERENCE = BenchmarkCase(
    case_id="PLT-02",
    track="C",
    capability="implicit_preference_induction",
    description=(
        "用户 design_wang 从未明确表达偏好，但连续 3 次在飞书文档协作中选择"
        "卡片式布局而非列表布局。系统应从行为观察中归纳出偏好并写入 preference 记忆。"
        "后续在文档协作场景下召回 design_wang 的布局偏好。"
    ),
    direction="C",
    complexity_reasoning="medium",
    complexity_tool="low",
    complexity_interaction="medium",
    memory_types=["preference"],
    spec_ref="PLT-02",
    setup_events=[
        SetupEvent(
            source_type="event",
            content="用户 design_wang 在项目 Alpha 文档协作中选择卡片式布局查看设计稿列表",
            actors=["design_wang"],
            scope="user",
            source_ref="evt_plt02_001",
            payload={
                "action": "select_layout",
                "layout_type": "card",
                "context": "design_review",
                "project_id": "proj_alpha",
            },
            created_hours_ago=48,
        ),
        SetupEvent(
            source_type="event",
            content="用户 design_wang 在项目 Beta 文档协作中再次选择卡片式布局",
            actors=["design_wang"],
            scope="user",
            source_ref="evt_plt02_002",
            payload={
                "action": "select_layout",
                "layout_type": "card",
                "context": "design_review",
                "project_id": "proj_beta",
            },
            created_hours_ago=24,
        ),
        SetupEvent(
            source_type="event",
            content="用户 design_wang 在项目 Gamma 设计评审中第三次选择卡片式布局",
            actors=["design_wang"],
            scope="user",
            source_ref="evt_plt02_003",
            payload={
                "action": "select_layout",
                "layout_type": "card",
                "context": "design_review",
                "project_id": "proj_gamma",
            },
            created_hours_ago=4,
        ),
    ],
    setup_memories=[
        # This preference should be induced from the 3 repeated behaviors above
        SetupMemory(
            memory_type="preference",
            title="design_wang 布局偏好：卡片式布局",
            summary=(
                "用户 design_wang 连续 3 次在文档协作和设计评审场景中选择卡片式布局，"
                "系统归纳其偏好为卡片式布局而非列表布局。此偏好为隐式归纳。"
            ),
            content={
                "user_id": "design_wang",
                "preference_category": "view_layout",
                "preference_value": "card",
                "trigger_context": "设计评审、文档协作中查看设计稿列表",
                "expression_type": "implicit",
                "behavior_evidence_count": 3,
                "behavior_evidence_refs": [
                    "evt_plt02_001",
                    "evt_plt02_002",
                    "evt_plt02_003",
                ],
            },
            importance=0.6,
            confidence=0.75,
            evidence=[
                {"source_ref": "evt_plt02_001", "snippet": "选择卡片式布局查看设计稿列表"},
                {"source_ref": "evt_plt02_002", "snippet": "再次选择卡片式布局"},
                {"source_ref": "evt_plt02_003", "snippet": "第三次选择卡片式布局"},
            ],
            tags=["preference", "view_layout", "implicit", "design_wang"],
            created_hours_ago=4,
            user_id="design_wang",
            scope="user",
        ),
    ],
    interference=InterferenceSetup(
        memories=[
            SetupMemory(
                memory_type="preference",
                title="pm_zhang 汇报偏好：先结论后细节",
                summary="用户 pm_zhang 在汇报中偏好先说结论再说细节。",
                content={
                    "user_id": "pm_zhang",
                    "preference_category": "communication_style",
                    "preference_value": "conclusion_first",
                },
                importance=0.5,
                confidence=0.8,
                tags=["preference", "communication", "pm_zhang"],
                created_hours_ago=36,
                user_id="pm_zhang",
                scope="user",
            ),
        ],
        events=[
            SetupEvent(
                source_type="message",
                content="设计系统 v3 的色彩变量已经全部更新了，大家可以看看",
                actors=["design_wang"],
                scope="project",
                source_ref="msg_noise_plt02_001",
                created_hours_ago=3,
            ),
        ],
        count=2,
    ),
    recalls=[
        RecallSpec(
            query="design_wang 布局偏好",
            user_id="design_wang",
            scope="user",
            intent="preference_lookup",
            limit=5,
        ),
    ],
    assertions=[
        ResultAssertion(type="contains_title", value="design_wang 布局偏好：卡片式布局"),
        ResultAssertion(type="contains_memory_type", value="preference"),
        ResultAssertion(type="contains_tag", value="design_wang"),
        # Should not return other users' preferences
        ResultAssertion(type="contains_title", value="pm_zhang 汇报偏好：先结论后细节", negates=True),
    ],
    expected_titles=["design_wang 布局偏好：卡片式布局"],
    forbidden_titles=["pm_zhang 汇报偏好：先结论后细节"],
    expected_count_range=(1, 3),
)

# ---------------------------------------------------------------------------
# PLT-03: Preference conflict detection
# ---------------------------------------------------------------------------
# User (eng_li) explicitly says "我喜欢详细版" but their actual behavior shows
# selecting the concise/简版 version multiple times. System should detect and
# mark this as a preference conflict.
# ---------------------------------------------------------------------------

PLT_03_PREFERENCE_CONFLICT = BenchmarkCase(
    case_id="PLT-03",
    track="C",
    capability="preference_conflict_detection",
    description=(
        "用户 eng_li 明确说 '我喜欢详细版的周报'，但后续行为连续 2 次选择简版周报。"
        "系统应检测到显式偏好与隐式行为之间的冲突，并标记偏好冲突状态。"
        "召回时应能返回冲突标记信息。"
    ),
    direction="C",
    complexity_reasoning="high",
    complexity_tool="medium",
    complexity_interaction="high",
    memory_types=["preference"],
    spec_ref="PLT-03",
    setup_events=[
        # Explicit preference expression
        SetupEvent(
            source_type="message",
            content="我喜欢详细版的周报，信息量更大，能更好地了解项目全貌",
            actors=["eng_li"],
            scope="user",
            source_ref="msg_plt03_001",
            payload={"chat_id": "chat_team_weekly", "message_type": "text"},
            created_hours_ago=72,
        ),
        # Conflicting behavior 1
        SetupEvent(
            source_type="event",
            content="用户 eng_li 在周报生成时选择简版而非详细版",
            actors=["eng_li"],
            scope="user",
            source_ref="evt_plt03_001",
            payload={
                "action": "select_report_format",
                "format": "brief",
                "context": "weekly_report",
            },
            created_hours_ago=48,
        ),
        # Conflicting behavior 2
        SetupEvent(
            source_type="event",
            content="用户 eng_li 再次在周报生成时选择简版",
            actors=["eng_li"],
            scope="user",
            source_ref="evt_plt03_002",
            payload={
                "action": "select_report_format",
                "format": "brief",
                "context": "weekly_report",
            },
            created_hours_ago=24,
        ),
    ],
    setup_memories=[
        # The original explicit preference (should still be retrievable)
        SetupMemory(
            memory_type="preference",
            title="eng_li 周报格式偏好：详细版（显式表达）",
            summary="用户 eng_li 明确表示喜欢详细版周报，认为信息量更大。",
            content={
                "user_id": "eng_li",
                "preference_category": "report_format",
                "preference_value": "detailed",
                "expression_type": "explicit",
                "trigger_context": "周报生成",
                "conflict_detected": True,
                "conflict_detail": (
                    "显式偏好为详细版，但行为连续 2 次选择简版。"
                    "建议以最新行为为准或提示用户确认。"
                ),
                "behavior_evidence_count": 2,
                "behavior_evidence_refs": ["evt_plt03_001", "evt_plt03_002"],
            },
            importance=0.7,
            confidence=0.6,  # Lowered due to conflict
            evidence=[
                {"source_ref": "msg_plt03_001", "snippet": "我喜欢详细版的周报"},
                {"source_ref": "evt_plt03_001", "snippet": "选择简版而非详细版"},
                {"source_ref": "evt_plt03_002", "snippet": "再次选择简版"},
            ],
            tags=["preference", "report_format", "conflict", "eng_li"],
            created_hours_ago=24,
            user_id="eng_li",
            scope="user",
        ),
    ],
    interference=InterferenceSetup(
        memories=[
            SetupMemory(
                memory_type="preference",
                title="pm_zhang 会议纪要偏好：行动项按负责人分组",
                summary="用户 pm_zhang 偏好会议纪要将行动项按负责人分组展示。",
                content={
                    "user_id": "pm_zhang",
                    "preference_category": "meeting_notes_format",
                    "preference_value": "grouped_by_owner",
                },
                importance=0.5,
                confidence=0.85,
                tags=["preference", "meeting_notes", "pm_zhang"],
                created_hours_ago=10,
                user_id="pm_zhang",
                scope="user",
            ),
        ],
        events=[
            SetupEvent(
                source_type="message",
                content="这周的迭代要赶一下进度，大家辛苦了",
                actors=["pm_zhang"],
                scope="project",
                source_ref="msg_noise_plt03_001",
                created_hours_ago=5,
            ),
        ],
        count=2,
    ),
    recalls=[
        RecallSpec(
            query="eng_li 周报格式偏好",
            user_id="eng_li",
            scope="user",
            intent="preference_lookup",
            limit=5,
        ),
    ],
    assertions=[
        ResultAssertion(type="contains_title", value="eng_li 周报格式偏好：详细版（显式表达）"),
        ResultAssertion(type="contains_memory_type", value="preference"),
        ResultAssertion(type="contains_tag", value="conflict"),
        # Should not return other users' preferences
        ResultAssertion(
            type="contains_title",
            value="pm_zhang 会议纪要偏好：行动项按负责人分组",
            negates=True,
        ),
    ],
    expected_titles=["eng_li 周报格式偏好：详细版（显式表达）"],
    forbidden_titles=["pm_zhang 会议纪要偏好：行动项按负责人分组"],
    expected_count_range=(1, 3),
    notes=(
        "PLT-03 的关键验证点：召回的偏好记忆应包含 conflict 标记，"
        "且 confidence 因冲突而降低。runner 应检查 memory content 中"
        "conflict_detected=True 以及 conflict_detail 字段。"
    ),
)

# ---------------------------------------------------------------------------
# PLT-04: Cross-scene preference isolation
# ---------------------------------------------------------------------------
# User (pm_zhang) prefers table view in project Alpha but list view in
# project Beta. The two project-scoped preferences must not mix.
# ---------------------------------------------------------------------------

PLT_04_CROSS_SCENE_ISOLATION = BenchmarkCase(
    case_id="PLT-04",
    track="C",
    capability="cross_scene_preference_isolation",
    description=(
        "用户 pm_zhang 在项目 Alpha 中偏好表格视图展示任务进度，"
        "但在项目 Beta 中偏好列表视图。系统应区分场景记录偏好，不混用。"
        "在项目 Alpha 上下文中召回时返回表格视图偏好；"
        "在项目 Beta 上下文中召回时返回列表视图偏好。"
    ),
    direction="C",
    complexity_reasoning="high",
    complexity_tool="medium",
    complexity_interaction="medium",
    memory_types=["preference"],
    spec_ref="PLT-04",
    setup_events=[
        SetupEvent(
            source_type="message",
            content="在这个项目里我还是喜欢用表格看任务进度",
            actors=["pm_zhang"],
            scope="project",
            source_ref="msg_plt04_001",
            payload={"chat_id": "chat_proj_alpha", "project_id": "proj_alpha"},
            created_hours_ago=48,
        ),
        SetupEvent(
            source_type="message",
            content="Beta 项目的事务比较简单，列表视图就够了",
            actors=["pm_zhang"],
            scope="project",
            source_ref="msg_plt04_002",
            payload={"chat_id": "chat_proj_beta", "project_id": "proj_beta"},
            created_hours_ago=24,
        ),
    ],
    setup_memories=[
        SetupMemory(
            memory_type="preference",
            title="pm_zhang 项目 Alpha 视图偏好：表格视图",
            summary="用户 pm_zhang 在项目 Alpha 中偏好表格视图展示任务进度。",
            content={
                "user_id": "pm_zhang",
                "preference_category": "view_layout",
                "preference_value": "table",
                "trigger_context": "项目 Alpha 中的任务进度展示",
                "expression_type": "explicit",
                "project_scope": "proj_alpha",
            },
            importance=0.7,
            confidence=0.9,
            evidence=[
                {
                    "source_ref": "msg_plt04_001",
                    "snippet": "在这个项目里我还是喜欢用表格看任务进度",
                }
            ],
            tags=["preference", "view_layout", "explicit", "pm_zhang", "proj_alpha"],
            created_hours_ago=48,
            user_id="pm_zhang",
            scope="project",
            project_id="proj_alpha",
        ),
        SetupMemory(
            memory_type="preference",
            title="pm_zhang 项目 Beta 视图偏好：列表视图",
            summary="用户 pm_zhang 在项目 Beta 中偏好列表视图。",
            content={
                "user_id": "pm_zhang",
                "preference_category": "view_layout",
                "preference_value": "list",
                "trigger_context": "项目 Beta 中的任务进度展示",
                "expression_type": "explicit",
                "project_scope": "proj_beta",
            },
            importance=0.7,
            confidence=0.9,
            evidence=[
                {
                    "source_ref": "msg_plt04_002",
                    "snippet": "Beta 项目的事务比较简单，列表视图就够了",
                }
            ],
            tags=["preference", "view_layout", "explicit", "pm_zhang", "proj_beta"],
            created_hours_ago=24,
            user_id="pm_zhang",
            scope="project",
            project_id="proj_beta",
        ),
    ],
    interference=InterferenceSetup(
        memories=[
            SetupMemory(
                memory_type="preference",
                title="design_wang 设计工具偏好：Figma",
                summary="用户 design_wang 偏好使用 Figma 进行设计协作。",
                content={
                    "user_id": "design_wang",
                    "preference_category": "design_tool",
                    "preference_value": "figma",
                },
                importance=0.5,
                confidence=0.8,
                tags=["preference", "design_tool", "design_wang"],
                created_hours_ago=8,
                user_id="design_wang",
                scope="user",
            ),
        ],
        count=1,
    ),
    recalls=[
        # Single recall with proj_alpha: should only return proj_alpha's preference
        RecallSpec(
            query="pm_zhang 视图偏好",
            user_id="pm_zhang",
            project_id="proj_alpha",
            scope="project",
            intent="preference_lookup",
            limit=5,
        ),
    ],
    assertions=[
        ResultAssertion(type="contains_title", value="pm_zhang 项目 Alpha 视图偏好：表格视图"),
        ResultAssertion(
            type="contains_title",
            value="pm_zhang 项目 Beta 视图偏好：列表视图",
            negates=True,
        ),
    ],
    expected_titles=["pm_zhang 项目 Alpha 视图偏好：表格视图"],
    forbidden_titles=["pm_zhang 项目 Beta 视图偏好：列表视图"],
    expected_count_range=(1, 1),
    notes=(
        "PLT-04: 验证以 proj_alpha 查询时只返回 Alpha 偏好，不返回 Beta 偏好。"
        "Runner 当前不支持 per-recall 断言，因此使用单次 recall + forbidden_titles。"
    ),
)

# ---------------------------------------------------------------------------
# PLT-05: Preference decay
# ---------------------------------------------------------------------------
# A preference was written 35 days ago and has not been recalled since.
# The system should have reduced its weight / rank it lower due to temporal
# decay. Verify the preference is still retrievable but with reduced score.
# ---------------------------------------------------------------------------

PLT_05_PREFERENCE_DECAY = BenchmarkCase(
    case_id="PLT-05",
    track="C",
    capability="preference_decay",
    description=(
        "用户 eng_li 的通知偏好记忆写入于 35 天前，此后从未被召回。"
        "偏好类型的半衰期配置为 90 天，但 35 天无触发应导致 freshness 降低。"
        "验证该偏好仍然可召回但排名靠后，且权重低于近期创建的同类偏好。"
    ),
    direction="C",
    complexity_reasoning="medium",
    complexity_tool="low",
    complexity_interaction="low",
    memory_types=["preference"],
    spec_ref="PLT-05",
    setup_events=[
        SetupEvent(
            source_type="message",
            content="我比较习惯在飞书群里直接沟通，不太喜欢发邮件",
            actors=["eng_li"],
            scope="user",
            source_ref="msg_plt05_001",
            created_hours_ago=35 * 24,  # 35 days ago
        ),
    ],
    setup_memories=[
        # Old preference -- 35 days ago, never recalled
        SetupMemory(
            memory_type="preference",
            title="eng_li 沟通渠道偏好：飞书群聊",
            summary="用户 eng_li 偏好在飞书群聊中直接沟通，不太喜欢发邮件。",
            content={
                "user_id": "eng_li",
                "preference_category": "communication_channel",
                "preference_value": "feishu_group_chat",
                "expression_type": "explicit",
                "trigger_context": "日常工作沟通",
            },
            importance=0.6,
            confidence=0.85,
            evidence=[
                {
                    "source_ref": "msg_plt05_001",
                    "snippet": "我比较习惯在飞书群里直接沟通，不太喜欢发邮件",
                }
            ],
            tags=["preference", "communication_channel", "explicit", "eng_li"],
            created_hours_ago=35 * 24,  # 35 days = 840 hours
            user_id="eng_li",
            scope="user",
        ),
        # Recent preference for the same user -- should rank higher
        SetupMemory(
            memory_type="preference",
            title="eng_li 代码审查偏好：精简评论",
            summary="用户 eng_li 在代码审查中偏好精简的评论风格。",
            content={
                "user_id": "eng_li",
                "preference_category": "code_review_style",
                "preference_value": "concise",
                "expression_type": "explicit",
                "trigger_context": "代码审查",
            },
            importance=0.7,
            confidence=0.9,
            evidence=[
                {
                    "source_ref": "msg_plt05_002",
                    "snippet": "代码审查的评论尽量精简，不用写太多",
                }
            ],
            tags=["preference", "code_review", "explicit", "eng_li"],
            created_hours_ago=4,  # Very recent
            user_id="eng_li",
            scope="user",
        ),
    ],
    interference=InterferenceSetup(
        memories=[
            SetupMemory(
                memory_type="preference",
                title="design_wang 设计评审偏好：线上进行",
                summary="用户 design_wang 偏好线上设计评审而非线下。",
                content={
                    "user_id": "design_wang",
                    "preference_category": "review_format",
                    "preference_value": "online",
                },
                importance=0.5,
                confidence=0.8,
                tags=["preference", "design_review", "design_wang"],
                created_hours_ago=6,
                user_id="design_wang",
                scope="user",
            ),
        ],
        events=[
            SetupEvent(
                source_type="message",
                content="周五下午三点有项目复盘会，请准时参加",
                actors=["pm_zhang"],
                scope="project",
                source_ref="msg_noise_plt05_001",
                created_hours_ago=2,
            ),
            SetupEvent(
                source_type="message",
                content="服务端监控看板已经搭好了，大家看看有没有要调整的",
                actors=["eng_li"],
                scope="project",
                source_ref="msg_noise_plt05_002",
                created_hours_ago=3,
            ),
        ],
        count=3,
    ),
    recalls=[
        RecallSpec(
            query="eng_li 偏好",
            user_id="eng_li",
            scope="user",
            intent="preference_lookup",
            limit=10,
        ),
    ],
    assertions=[
        # Both preferences should be present for eng_li
        ResultAssertion(type="contains_title", value="eng_li 代码审查偏好：精简评论"),
        ResultAssertion(type="contains_title", value="eng_li 沟通渠道偏好：飞书群聊"),
        ResultAssertion(type="contains_memory_type", value="preference"),
        # Should not return other users' preferences
        ResultAssertion(
            type="contains_title",
            value="design_wang 设计评审偏好：线上进行",
            negates=True,
        ),
    ],
    expected_titles=[
        "eng_li 代码审查偏好：精简评论",
        "eng_li 沟通渠道偏好：飞书群聊",
    ],
    forbidden_titles=["design_wang 设计评审偏好：线上进行"],
    expected_count_range=(2, 4),
    notes=(
        "PLT-05 的关键验证点：两条 eng_li 偏好都应被召回，但近期偏好"
        "（代码审查偏好）的排序应高于 35 天前的旧偏好（沟通渠道偏好）。"
        "runner 应检查返回列表中 '代码审查偏好' 的 rank < '沟通渠道偏好' 的 rank。"
        "preference 类型的半衰期为 90 天，35 天衰减约 freshness = 0.5^(35*24/(90*24)) "
        "= 0.5^0.389 ≈ 0.76，不会完全消失但分数应明显低于近期记忆。"
    ),
)

# ---------------------------------------------------------------------------
# PLT-06: Preference update / correction
# ---------------------------------------------------------------------------
# User (pm_zhang) first says they prefer table view, then later corrects
# themselves: "之前说的不对，其实我更喜欢列表视图". The old preference should
# be superseded, and the new one should take its place.
# ---------------------------------------------------------------------------

PLT_06_PREFERENCE_UPDATE = BenchmarkCase(
    case_id="PLT-06",
    track="C",
    capability="preference_update_correction",
    description=(
        "用户 pm_zhang 之前说偏好表格视图，后来纠正说 '之前说的不对，"
        "其实我更喜欢列表视图'。系统应将旧偏好标记为 superseded，"
        "写入新的列表视图偏好。召回时只返回最新的列表视图偏好。"
    ),
    direction="C",
    complexity_reasoning="medium",
    complexity_tool="low",
    complexity_interaction="medium",
    memory_types=["preference"],
    spec_ref="PLT-06",
    setup_events=[
        # Original preference expression
        SetupEvent(
            source_type="message",
            content="我觉得表格视图挺好的，信息密度高",
            actors=["pm_zhang"],
            scope="user",
            source_ref="msg_plt06_001",
            payload={"chat_id": "chat_project_alpha", "message_type": "text"},
            created_hours_ago=168,  # 7 days ago
        ),
        # Correction / update
        SetupEvent(
            source_type="message",
            content="之前说的不对，其实我更喜欢列表视图，表格太密了看着累",
            actors=["pm_zhang"],
            scope="user",
            source_ref="msg_plt06_002",
            payload={"chat_id": "chat_project_alpha", "message_type": "text"},
            created_hours_ago=24,  # 1 day ago
        ),
    ],
    setup_memories=[
        # Old preference -- should be superseded
        SetupMemory(
            memory_type="preference",
            title="pm_zhang 视图偏好：表格视图（已更新）",
            summary="用户 pm_zhang 之前偏好表格视图，后自行纠正为列表视图。",
            content={
                "user_id": "pm_zhang",
                "preference_category": "view_layout",
                "preference_value": "table",
                "expression_type": "explicit",
                "trigger_context": "数据展示",
                "superseded": True,
                "superseded_reason": "用户主动纠正：'之前说的不对，其实我更喜欢列表视图'",
            },
            importance=0.3,  # Lowered after supersession
            confidence=0.9,
            evidence=[
                {
                    "source_ref": "msg_plt06_001",
                    "snippet": "我觉得表格视图挺好的，信息密度高",
                }
            ],
            tags=["preference", "view_layout", "superseded", "pm_zhang"],
            created_hours_ago=168,
            user_id="pm_zhang",
            scope="user",
        ),
        # New preference -- the current active one
        SetupMemory(
            memory_type="preference",
            title="pm_zhang 视图偏好：列表视图（最新）",
            summary="用户 pm_zhang 纠正了之前的偏好，当前偏好列表视图，认为表格太密。",
            content={
                "user_id": "pm_zhang",
                "preference_category": "view_layout",
                "preference_value": "list",
                "expression_type": "explicit",
                "trigger_context": "数据展示",
                "update_reason": "用户主动纠正旧偏好",
                "supersedes": "pm_zhang 视图偏好：表格视图（已更新）",
            },
            importance=0.7,
            confidence=0.9,
            evidence=[
                {
                    "source_ref": "msg_plt06_002",
                    "snippet": "之前说的不对，其实我更喜欢列表视图，表格太密了看着累",
                }
            ],
            tags=["preference", "view_layout", "explicit", "pm_zhang"],
            created_hours_ago=24,
            user_id="pm_zhang",
            scope="user",
        ),
    ],
    interference=InterferenceSetup(
        memories=[
            SetupMemory(
                memory_type="preference",
                title="eng_li 代码风格偏好：类型注解",
                summary="用户 eng_li 偏好在 Python 代码中使用完整的类型注解。",
                content={
                    "user_id": "eng_li",
                    "preference_category": "code_style",
                    "preference_value": "type_hints",
                },
                importance=0.5,
                confidence=0.8,
                tags=["preference", "code_style", "eng_li"],
                created_hours_ago=12,
                user_id="eng_li",
                scope="user",
            ),
            SetupMemory(
                memory_type="preference",
                title="design_wang 颜色偏好：暖色调",
                summary="用户 design_wang 在设计作品中偏好暖色调。",
                content={
                    "user_id": "design_wang",
                    "preference_category": "design_color",
                    "preference_value": "warm_tone",
                },
                importance=0.5,
                confidence=0.75,
                tags=["preference", "design_color", "design_wang"],
                created_hours_ago=8,
                user_id="design_wang",
                scope="user",
            ),
        ],
        events=[
            SetupEvent(
                source_type="message",
                content="下个迭代需要重新排优先级，我整理一下需求池",
                actors=["pm_zhang"],
                scope="project",
                source_ref="msg_noise_plt06_001",
                created_hours_ago=10,
            ),
            SetupEvent(
                source_type="message",
                content="性能优化的方案已经提 PR 了，等 review",
                actors=["eng_li"],
                scope="project",
                source_ref="msg_noise_plt06_002",
                created_hours_ago=6,
            ),
        ],
        count=4,
    ),
    recalls=[
        RecallSpec(
            query="pm_zhang 视图偏好",
            user_id="pm_zhang",
            scope="user",
            intent="preference_lookup",
            limit=5,
        ),
    ],
    assertions=[
        # Active (new) preference must be present
        ResultAssertion(type="contains_title", value="pm_zhang 视图偏好：列表视图（最新）"),
        ResultAssertion(type="contains_memory_type", value="preference"),
        # Other users' preferences should not appear
        ResultAssertion(
            type="contains_title",
            value="eng_li 代码风格偏好：类型注解",
            negates=True,
        ),
        ResultAssertion(
            type="contains_title",
            value="design_wang 颜色偏好：暖色调",
            negates=True,
        ),
    ],
    expected_titles=["pm_zhang 视图偏好：列表视图（最新）"],
    forbidden_titles=[
        "eng_li 代码风格偏好：类型注解",
        "design_wang 颜色偏好：暖色调",
    ],
    expected_count_range=(1, 2),
    notes=(
        "PLT-06 的关键验证点：旧偏好（表格视图）应标记为 superseded，"
        "召回结果中不应出现已 superseded 的旧偏好，只返回新的列表视图偏好。"
        "runner 应检查旧偏好的 status=superseded 且不参与正常召回。"
    ),
)

def _implicit_preference_observation(index: int, source_ref: str) -> SetupMemory:
    return SetupMemory(
        memory_type="preference",
        title=f"Implicit observation: structured output {index}",
        summary=f"[implicit positive observation] User requested structured output {index}.",
        content={
            "scope": "project",
            "kind": "implicit_preference_observation",
            "preference_kind": "output_format",
            "pattern_key": "pref.output.structured_format",
            "signal": "structured_output_requested",
            "polarity": "positive",
            "risk_level": "low",
            "needs_confirmation": "true",
            "confirmed": "false",
            "observed_at": "2026-05-04T00:00:00+00:00",
            "source_text": "please use markdown bullet list",
        },
        importance=0.3,
        confidence=0.45,
        evidence=[{"source_ref": source_ref}],
        tags=["implicit_preference", "output_format", "pref.output.structured_format"],
        scope="project",
    )


def _stable_preference_under_review(title: str, source_ref: str) -> SetupMemory:
    return SetupMemory(
        memory_type="preference",
        title=title,
        summary="Stable preference was marked for review after contrary evidence.",
        content={
            "scope": "project",
            "kind": "stable_preference",
            "preference_kind": "output_format",
            "pattern_key": "pref.output.structured_format",
            "confirmed": "true",
            "needs_confirmation": "false",
            "needs_review": "true",
            "review_reason": "negative implicit preference evidence observed",
        },
        importance=0.65,
        confidence=0.55,
        evidence=[{"source_ref": source_ref}],
        tags=["implicit_preference", "stable_preference", "output_format", "pref.output.structured_format"],
        scope="project",
        logical_layer="L2",
    )


def _stale_stable_preference(title: str, source_ref: str) -> SetupMemory:
    return SetupMemory(
        memory_type="preference",
        title=title,
        summary="Stable preference is old enough to require conservative review.",
        content={
            "scope": "project",
            "kind": "stable_preference",
            "preference_kind": "output_format",
            "pattern_key": "pref.output.structured_format",
            "confirmed": "true",
            "needs_confirmation": "false",
            "confirmed_at": "2026-01-01T00:00:00+00:00",
        },
        importance=0.65,
        confidence=0.85,
        evidence=[{"source_ref": source_ref}],
        tags=["implicit_preference", "stable_preference", "output_format", "pref.output.structured_format"],
        created_hours_ago=100 * 24,
        scope="project",
        logical_layer="L2",
    )


PLT_07_PREFERENCE_CANDIDATE_EVENT_ENTRY = BenchmarkCase(
    case_id="PLT-07",
    track="C",
    capability="preference_candidate_event_entry",
    description="Review-generated preference candidates should create event_entries for evidence reconstruction.",
    direction="C",
    complexity_reasoning="medium",
    complexity_tool="low",
    complexity_interaction="low",
    memory_types=["preference"],
    setup_memories=[
        _implicit_preference_observation(1, "bench://pref-event-1"),
        _implicit_preference_observation(2, "bench://pref-event-2"),
        _implicit_preference_observation(3, "bench://pref-event-3"),
    ],
    run_review=True,
    event_assertions=[
        ResultAssertion(type="memory_content_kind_count", value=["preference_candidate", 1]),
        ResultAssertion(type="event_entry_relation_count", value=["synthesized_preference_candidate", 1]),
    ],
    spec_ref="PLT-07",
)


PLT_08_STABLE_PREFERENCE_CONFIRMATION_EVENT_ENTRY = BenchmarkCase(
    case_id="PLT-08",
    track="C",
    capability="stable_preference_confirmation_event_entry",
    description="Confirming a preference candidate should create a stable-preference event entry.",
    direction="C",
    complexity_reasoning="medium",
    complexity_tool="low",
    complexity_interaction="low",
    memory_types=["preference"],
    setup_memories=[
        _implicit_preference_observation(1, "bench://pref-confirm-1"),
        _implicit_preference_observation(2, "bench://pref-confirm-2"),
        _implicit_preference_observation(3, "bench://pref-confirm-3"),
    ],
    run_review=True,
    event_assertions=[
        ResultAssertion(
            type="preference_candidate_confirm_creates_event_entry",
            value=["Possible preference: output_format", "confirmed_stable_preference"],
        ),
    ],
    spec_ref="PLT-08",
)


PLT_09_STABLE_PREFERENCE_RECONFIRM_EVENT_ENTRY = BenchmarkCase(
    case_id="PLT-09",
    track="C",
    capability="stable_preference_reconfirm_event_entry",
    description="Re-confirming a stable preference under review should clear review and record an event entry.",
    direction="C",
    complexity_reasoning="medium",
    complexity_tool="low",
    complexity_interaction="medium",
    memory_types=["preference"],
    setup_memories=[
        _stable_preference_under_review("Confirmed preference: output_format review reconfirm", "bench://pref-reconfirm"),
    ],
    event_assertions=[
        ResultAssertion(
            type="stable_preference_review_action_creates_event_entry",
            value=[
                "Confirmed preference: output_format review reconfirm",
                "reconfirm",
                "reconfirmed_stable_preference",
            ],
        ),
    ],
    spec_ref="PLT-09",
)


PLT_10_STABLE_PREFERENCE_REJECT_EVENT_ENTRY = BenchmarkCase(
    case_id="PLT-10",
    track="C",
    capability="stable_preference_reject_event_entry",
    description="Rejecting a stable preference under review should archive it and record an event entry.",
    direction="C",
    complexity_reasoning="medium",
    complexity_tool="low",
    complexity_interaction="medium",
    memory_types=["preference"],
    setup_memories=[
        _stable_preference_under_review("Confirmed preference: output_format review reject", "bench://pref-reject"),
    ],
    event_assertions=[
        ResultAssertion(
            type="stable_preference_review_action_creates_event_entry",
            value=[
                "Confirmed preference: output_format review reject",
                "reject",
                "rejected_stable_preference",
            ],
        ),
    ],
    spec_ref="PLT-10",
)


PLT_11_STALE_STABLE_PREFERENCE_REVIEW = BenchmarkCase(
    case_id="PLT-11",
    track="C",
    capability="stale_stable_preference_review",
    description="Long-unused stable preferences should be marked for review instead of being silently archived.",
    direction="C",
    complexity_reasoning="medium",
    complexity_tool="low",
    complexity_interaction="low",
    memory_types=["preference"],
    setup_memories=[
        _stale_stable_preference("Confirmed preference: output_format stale review", "bench://pref-stale-review"),
    ],
    run_review=True,
    event_assertions=[
        ResultAssertion("memory_content_field_equals", ["stable_preference", "needs_review", "true"]),
        ResultAssertion(
            "memory_content_field_equals",
            ["stable_preference", "review_reason", "stable preference stale or long unused"],
        ),
        ResultAssertion("event_entry_relation_count", ["stable_preference_marked_stale_for_review", 1]),
    ],
    spec_ref="PLT-11",
)


# ---------------------------------------------------------------------------
# Aggregate list
# ---------------------------------------------------------------------------

TRACK_C_CASES: list[BenchmarkCase] = [
    PLT_01_EXPLICIT_PREFERENCE,
    PLT_02_IMPLICIT_PREFERENCE,
    PLT_03_PREFERENCE_CONFLICT,
    PLT_04_CROSS_SCENE_ISOLATION,
    PLT_05_PREFERENCE_DECAY,
    PLT_06_PREFERENCE_UPDATE,
    PLT_07_PREFERENCE_CANDIDATE_EVENT_ENTRY,
    PLT_08_STABLE_PREFERENCE_CONFIRMATION_EVENT_ENTRY,
    PLT_09_STABLE_PREFERENCE_RECONFIRM_EVENT_ENTRY,
    PLT_10_STABLE_PREFERENCE_REJECT_EVENT_ENTRY,
    PLT_11_STALE_STABLE_PREFERENCE_REVIEW,
]
