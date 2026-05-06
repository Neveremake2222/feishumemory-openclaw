"""Track B (Task Decision Memory) benchmark test cases.

Scenario: 跨部门项目立项到上线协作 (Project Alpha)
Characters: pm_zhang (产品经理张三), eng_li (技术负责人李四),
            design_wang (设计师王五), test_zhao (测试负责人赵六)
Tasks: tech-selection, api-integration, launch-prep, regression-test

Each case targets one capability from spec.md 9.3.2.2:
  - 经验复用 (experience reuse)
  - 跨任务迁移 (cross-task transfer)
  - 减少重复探索 (reduced repeated exploration)
  - 策略延续 (strategy continuity)
  - 风险识别 (risk identification)
  - 任务交接上下文 (task handover context)
"""

from __future__ import annotations

from benchmarks.structures import (
    BenchmarkCase,
    InterferenceSetup,
    RecallSpec,
    ResultAssertion,
    SetupEvent,
    SetupMemory,
)

# ---------------------------------------------------------------------------
# Case B-01: Experience reuse — similar tech-selection problem solved before
# ---------------------------------------------------------------------------
_B01_EXPERIENCE_REUSE = BenchmarkCase(
    case_id="B-01",
    track="B",
    capability="experience_reuse",
    description=(
        "Project Alpha 需要进行技术选型（消息队列），系统应召回 Project Beta "
        "中类似选型的决策经验（选择 Kafka 而非 RabbitMQ，理由是高吞吐），"
        "从而加速当前任务的决策过程。"
    ),
    direction="B",

    # Complexity (spec 9.3.2.4)
    complexity_reasoning="medium",   # 需要跨项目关联相似决策场景
    complexity_tool="low",           # 单次召回即可
    complexity_interaction="low",    # 无需多轮交互

    memory_types=["decision", "semantic"],

    setup_events=[
        SetupEvent(
            source_type="message",
            content=(
                "Project Beta 技术选型讨论：经过对比 Kafka 和 RabbitMQ，"
                "团队确定使用 Kafka 作为消息队列方案，主要因为日均消息量超过 "
                "500 万条，Kafka 的吞吐性能优于 RabbitMQ 约 3 倍。"
                "参与人：eng_li、pm_zhang。"
            ),
            actors=["eng_li", "pm_zhang"],
            scope="project",
            source_ref="msg_beta_tech_selection_001",
            created_hours_ago=720,  # 30 days ago
        ),
        SetupEvent(
            source_type="message",
            content=(
                "Project Alpha 需要选择消息中间件，日均消息量预计 800 万条，"
                "需要支持顺序消费和至少一次投递语义。eng_li 提议评估 Kafka。"
            ),
            actors=["eng_li", "pm_zhang"],
            scope="task",
            source_ref="msg_alpha_tech_selection_001",
            created_hours_ago=2,
        ),
    ],
    setup_memories=[
        SetupMemory(
            memory_type="decision",
            title="Project Beta 消息队列技术选型决策：确定使用 Kafka",
            summary=(
                "Project Beta 消息队列选型确定使用 Kafka 而非 RabbitMQ。"
                "决策依据：日均消息量超 500 万条，Kafka 吞吐性能约为 RabbitMQ 的 3 倍，"
                "且支持顺序消费和至少一次投递语义。参与人：eng_li、pm_zhang。"
            ),
            content={
                "project": "Project Beta",
                "decision": "使用 Kafka 作为消息队列",
                "alternatives": ["RabbitMQ"],
                "reason": "高吞吐场景（日均500万+消息量）下 Kafka 性能优势显著",
                "metrics": "Kafka 吞吐约为 RabbitMQ 的 3 倍",
            },
            importance=0.8,
            confidence=0.9,
            tags=["tech-selection", "kafka", "message-queue", "high-throughput"],
            created_hours_ago=720,
            project_id="project_alpha",
            task_id="tech-selection",
            user_id="eng_li",
        ),
    ],

    interference=InterferenceSetup(
        memories=[
            SetupMemory(
                memory_type="semantic",
                title="Project Gamma 日志框架选型：确定使用 Log4j2",
                summary="Project Gamma 日志框架选型确定使用 Log4j2，因异步日志性能优于 Logback。",
                content={
                    "project": "Project Gamma",
                    "decision": "使用 Log4j2",
                    "reason": "异步日志性能优于 Logback",
                },
                importance=0.5,
                confidence=0.8,
                tags=["tech-selection", "logging", "log4j2"],
                created_hours_ago=480,
            ),
            SetupMemory(
                memory_type="decision",
                title="Project Delta 数据库选型：确定使用 PostgreSQL",
                summary="Project Delta 数据库选型确定使用 PostgreSQL，因需要 JSONB 和全文搜索能力。",
                content={
                    "project": "Project Delta",
                    "decision": "使用 PostgreSQL",
                    "reason": "需要 JSONB 和全文搜索能力",
                },
                importance=0.5,
                confidence=0.8,
                tags=["tech-selection", "database", "postgresql"],
                created_hours_ago=360,
            ),
        ],
        events=[
            SetupEvent(
                source_type="message",
                content="Project Alpha 前端框架升级到 React 18，性能提升约 20%。",
                actors=["eng_li"],
                scope="task",
                source_ref="msg_alpha_frontend_001",
                created_hours_ago=24,
            ),
            SetupEvent(
                source_type="message",
                content="Project Alpha UI 设计评审通过，design_wang 确认视觉规范。",
                actors=["design_wang", "pm_zhang"],
                scope="task",
                source_ref="msg_alpha_design_001",
                created_hours_ago=48,
            ),
        ],
        count=4,
    ),

    recalls=[
        RecallSpec(
            query="Project Alpha 消息队列技术选型，之前类似场景怎么决定的？",
            user_id="eng_li",
            project_id="project_alpha",
            task_id="tech-selection",
            intent="decision_support",
            limit=5,
        ),
    ],

    assertions=[
        ResultAssertion(type="contains_title", value="Project Beta 消息队列技术选型决策"),
        ResultAssertion(type="contains_tag", value="kafka"),
        # Should NOT return unrelated tech selections
        ResultAssertion(type="contains_title", value="Log4j2", negates=True),
    ],

    expected_titles=["Project Beta 消息队列技术选型决策：确定使用 Kafka"],
    forbidden_titles=[
        "Project Gamma 日志框架选型：确定使用 Log4j2",
        "Project Delta 数据库选型：确定使用 PostgreSQL",
    ],
    expected_count_range=(1, 3),

    spec_ref="9.3.2.2 经验复用",
    notes=(
        "验证系统能否从历史项目中召回与当前任务相似的决策经验，"
        "并在存在干扰记忆（其他项目的技术选型决策）的情况下精准匹配。"
    ),
)


# ---------------------------------------------------------------------------
# Case B-02: Cross-task transfer — security compliance constraint transfer
# ---------------------------------------------------------------------------
_B02_CROSS_TASK_TRANSFER = BenchmarkCase(
    case_id="B-02",
    track="B",
    capability="cross_task_transfer",
    description=(
        "在 tech-selection 任务中，团队确立了'所有对外 API 必须通过 API Gateway "
        "并启用 OAuth2 鉴权'的安全合规约束。当 api-integration 任务开始时，"
        "系统应主动召回该约束，使其在新任务中自动生效，无需重新讨论。"
    ),
    direction="B",

    complexity_reasoning="high",     # 需要跨任务迁移约束，涉及合规判断
    complexity_tool="medium",        # 需要关联两个任务的上下文
    complexity_interaction="medium", # 需要理解隐式约束的适用范围

    memory_types=["decision", "semantic"],

    setup_events=[
        SetupEvent(
            source_type="meeting",
            content=(
                "Project Alpha 安全评审会议纪要：pm_zhang 和 eng_li 确认，"
                "所有对外暴露的 API 必须通过统一的 API Gateway 接入，"
                "并强制启用 OAuth2.0 鉴权。不接受直连后端服务的方式。"
                "此要求适用于本项目所有涉及外部接口的任务。"
            ),
            actors=["pm_zhang", "eng_li"],
            scope="project",
            source_ref="meeting_alpha_security_review_001",
            created_hours_ago=168,  # 7 days ago
        ),
    ],
    setup_memories=[
        SetupMemory(
            memory_type="decision",
            title="Project Alpha 安全合规要求：对外 API 必须通过 Gateway + OAuth2",
            summary=(
                "Project Alpha 安全评审结论：所有对外暴露的 API 必须通过统一 API Gateway "
                "接入，并强制启用 OAuth2.0 鉴权，不接受直连后端服务。"
                "此约束适用于本项目所有涉及外部接口的任务。决策人：pm_zhang、eng_li。"
            ),
            content={
                "project": "Project Alpha",
                "constraint_type": "security_compliance",
                "requirement": "对外 API 必须经过 API Gateway + OAuth2 鉴权",
                "deciders": ["pm_zhang", "eng_li"],
            },
            importance=0.9,
            confidence=0.95,
            tags=["security", "compliance", "api-gateway", "oauth2", "constraint"],
            created_hours_ago=168,
            project_id="project_alpha",
            task_id="api-integration",
            user_id="eng_li",
        ),
    ],

    interference=InterferenceSetup(
        memories=[
            SetupMemory(
                memory_type="decision",
                title="Project Alpha 部署方案：确定使用 K8s + Helm",
                summary="Project Alpha 部署方案确定为 Kubernetes + Helm Chart 方式。",
                content={
                    "project": "Project Alpha",
                    "decision": "使用 K8s + Helm 部署",
                    "reason": "团队有 K8s 经验，运维成熟度高",
                },
                importance=0.6,
                confidence=0.85,
                tags=["deployment", "kubernetes", "helm"],
                created_hours_ago=120,
            ),
            SetupMemory(
                memory_type="semantic",
                title="Project Alpha 代码规范：统一使用 TypeScript strict 模式",
                summary="前端代码规范要求统一使用 TypeScript strict 模式编译。",
                content={
                    "project": "Project Alpha",
                    "standard": "TypeScript strict mode",
                },
                importance=0.5,
                confidence=0.8,
                tags=["coding-standard", "typescript"],
                created_hours_ago=96,
            ),
        ],
        count=2,
    ),

    recalls=[
        RecallSpec(
            query="api-integration 任务开始，需要确认对外 API 的安全合规要求",
            user_id="eng_li",
            project_id="project_alpha",
            task_id="api-integration",
            intent="constraint_lookup",
            limit=5,
        ),
    ],

    assertions=[
        ResultAssertion(type="contains_title", value="安全合规要求"),
        ResultAssertion(type="contains_tag", value="security"),
        ResultAssertion(type="contains_tag", value="compliance"),
        ResultAssertion(type="contains_memory_type", value="decision"),
    ],

    expected_titles=["Project Alpha 安全合规要求：对外 API 必须通过 Gateway + OAuth2"],
    forbidden_titles=[],
    expected_count_range=(1, 3),

    spec_ref="9.3.2.2 跨任务迁移",
    notes=(
        "验证 A 任务确立的安全合规约束能否在 B 任务中被召回并生效。"
        "这是 spec 4.4.2 检索触发条件中'智能体准备执行新一轮任务规划'的典型场景。"
    ),
)


# ---------------------------------------------------------------------------
# Case B-03: Reduced repeated exploration — confirmed info not re-asked
# ---------------------------------------------------------------------------
_B03_REDUCED_REPETITION = BenchmarkCase(
    case_id="B-03",
    track="B",
    capability="reduced_repeated_exploration",
    description=(
        "Project Alpha 的上线截止日期已在会议中确认为 6 月 30 日。"
        "在后续 launch-prep 任务讨论中，系统应召回已确认的截止时间，"
        "智能体不再重复询问该信息。"
    ),
    direction="B",

    complexity_reasoning="low",      # 单一事实确认，推理简单
    complexity_tool="low",           # 单次检索
    complexity_interaction="medium", # 需要验证系统是否"知道不再问"

    memory_types=["decision", "task_status"],

    setup_events=[
        SetupEvent(
            source_type="meeting",
            content=(
                "Project Alpha 项目排期确认会议：pm_zhang 与 eng_li、design_wang、"
                "test_zhao 共同确认，项目上线截止日期为 6 月 30 日，不接受延期。"
                "各模块联调完成时间不得晚于 6 月 20 日。"
            ),
            actors=["pm_zhang", "eng_li", "design_wang", "test_zhao"],
            scope="project",
            source_ref="meeting_alpha_schedule_001",
            created_hours_ago=336,  # 14 days ago
        ),
        SetupEvent(
            source_type="message",
            content=(
                "pm_zhang 在群里确认：Alpha 项目上线日就是 6 月 30 日，"
                "请大家按这个时间点倒排开发计划。"
            ),
            actors=["pm_zhang"],
            scope="project",
            source_ref="msg_alpha_deadline_confirm_001",
            created_hours_ago=330,
        ),
    ],
    setup_memories=[
        SetupMemory(
            memory_type="decision",
            title="Project Alpha 上线截止日期确认为 6 月 30 日",
            summary=(
                "Project Alpha 上线截止日期经全员确认定为 6 月 30 日，不接受延期。"
                "各模块联调完成时间不得晚于 6 月 20 日。"
                "确认人：pm_zhang、eng_li、design_wang、test_zhao。"
            ),
            content={
                "project": "Project Alpha",
                "deadline": "2026-06-30",
                "integration_deadline": "2026-06-20",
                "extension_allowed": False,
                "confirmed_by": ["pm_zhang", "eng_li", "design_wang", "test_zhao"],
            },
            importance=0.9,
            confidence=0.95,
            tags=["deadline", "schedule", "milestone", "confirmed"],
            created_hours_ago=336,
            project_id="project_alpha",
            task_id="launch-prep",
            user_id="pm_zhang",
        ),
    ],

    interference=InterferenceSetup(
        memories=[
            SetupMemory(
                memory_type="task_status",
                title="Project Alpha UI 组件库版本升级完成",
                summary="前端 UI 组件库从 v2.1 升级到 v3.0 完成，design_wang 确认视觉一致。",
                content={"task": "ui-upgrade", "status": "completed"},
                importance=0.4,
                confidence=0.8,
                tags=["ui", "frontend"],
                created_hours_ago=72,
            ),
            SetupMemory(
                memory_type="semantic",
                title="Project Alpha 技术栈确认：Spring Boot 3.x + React 18",
                summary="后端使用 Spring Boot 3.x，前端使用 React 18，数据库使用 MySQL 8.0。",
                content={"backend": "Spring Boot 3.x", "frontend": "React 18", "database": "MySQL 8.0"},
                importance=0.6,
                confidence=0.85,
                tags=["tech-stack", "backend", "frontend"],
                created_hours_ago=480,
            ),
            SetupMemory(
                memory_type="task_status",
                title="Project Alpha 接口文档 v2 已提交评审",
                summary="api-integration 任务的接口文档 v2 已由 eng_li 提交评审。",
                content={"task": "api-integration", "status": "in_review"},
                importance=0.5,
                confidence=0.8,
                tags=["api", "documentation"],
                created_hours_ago=48,
            ),
        ],
        count=3,
    ),

    recalls=[
        RecallSpec(
            query="Project Alpha 上线准备，项目的截止时间是什么时候？",
            user_id="pm_zhang",
            project_id="project_alpha",
            task_id="launch-prep",
            intent="fact_lookup",
            limit=5,
        ),
    ],

    assertions=[
        ResultAssertion(type="contains_title", value="上线截止日期确认为 6 月 30 日"),
        # The tech-stack memory is related to the project but NOT the deadline query
        ResultAssertion(type="contains_title", value="UI 组件库版本升级完成", negates=True),
    ],

    expected_titles=["Project Alpha 上线截止日期确认为 6 月 30 日"],
    forbidden_titles=[
        "Project Alpha UI 组件库版本升级完成",
    ],
    expected_count_range=(1, 2),

    spec_ref="9.3.2.2 减少重复探索",
    notes=(
        "验证系统在已确认信息存在时能精准召回，使智能体不再重复询问截止时间。"
        "对应 spec 9.2.1 指标'重复确认次数下降比例'。"
    ),
)


# ---------------------------------------------------------------------------
# Case B-04: Strategy continuity — long-running task resumes after pause
# ---------------------------------------------------------------------------
_B04_STRATEGY_CONTINUITY = BenchmarkCase(
    case_id="B-04",
    track="B",
    capability="strategy_continuity",
    description=(
        "Project Alpha 的 regression-test 任务在执行到 50% 时因 team city "
        "服务器故障暂停。一周后恢复，系统应提供完整的上下文摘要：已通过/失败的"
        "测试用例、待重跑项、阻塞原因和当前策略，使任务能无缝继续。"
    ),
    direction="B",

    complexity_reasoning="high",     # 需要整合多条记忆恢复完整任务状态
    complexity_tool="medium",        # 需要加载任务级别的多条上下文
    complexity_interaction="high",   # 涉及任务中断恢复的多步信息重组

    memory_types=["task_status", "episodic", "decision"],

    setup_events=[
        SetupEvent(
            source_type="message",
            content=(
                "test_zhao 汇报：regression-test 第一轮执行完成，"
                "200 个用例中 185 个通过，15 个失败。"
                "失败用例中 12 个与支付模块相关，3 个与权限模块相关。"
                "支付模块失败疑似与上游 mock 服务配置错误有关。"
            ),
            actors=["test_zhao"],
            scope="task",
            source_ref="msg_alpha_regression_round1_001",
            created_hours_ago=192,  # 8 days ago
        ),
        SetupEvent(
            source_type="message",
            content=(
                "eng_li 确认：TeamCity 构建服务器磁盘故障，当前不可用。"
                "regression-test 暂停，等基础设施恢复后继续。"
                "策略：先修复 mock 配置，然后重跑支付模块用例。"
            ),
            actors=["eng_li", "test_zhao"],
            scope="task",
            source_ref="msg_alpha_regression_pause_001",
            created_hours_ago=168,  # 7 days ago
        ),
    ],
    setup_memories=[
        SetupMemory(
            memory_type="task_status",
            title="regression-test 第一轮结果：185/200 通过，15 失败",
            summary=(
                "Project Alpha regression-test 第一轮：200 个用例中 185 通过，15 失败。"
                "失败分布：12 个支付模块（疑似 mock 服务配置错误），3 个权限模块。"
                "执行人：test_zhao。"
            ),
            content={
                "project": "Project Alpha",
                "task": "regression-test",
                "round": 1,
                "total": 200,
                "passed": 185,
                "failed": 15,
                "failed_modules": {"payment": 12, "permission": 3},
                "suspected_cause": "上游 mock 服务配置错误",
            },
            importance=0.85,
            confidence=0.9,
            tags=["regression-test", "test-results", "payment", "permission"],
            created_hours_ago=192,
            scope="task",
            project_id="project_alpha",
            task_id="regression-test",
            user_id="test_zhao",
        ),
        SetupMemory(
            memory_type="decision",
            title="regression-test 暂停策略：先修 mock 配置再重跑支付模块",
            summary=(
                "因 TeamCity 磁盘故障，regression-test 暂停。恢复策略："
                "（1）eng_li 修复 mock 服务配置错误；"
                "（2）优先重跑 12 个支付模块失败用例；"
                "（3）然后重跑 3 个权限模块用例；"
                "（4）最终全量回归确认。决策人：eng_li、test_zhao。"
            ),
            content={
                "project": "Project Alpha",
                "task": "regression-test",
                "status": "paused",
                "pause_reason": "TeamCity 磁盘故障",
                "resume_strategy": [
                    "修复 mock 服务配置",
                    "重跑支付模块 12 个失败用例",
                    "重跑权限模块 3 个失败用例",
                    "全量回归确认",
                ],
                "deciders": ["eng_li", "test_zhao"],
            },
            importance=0.9,
            confidence=0.9,
            tags=["regression-test", "pause", "resume-strategy", "teamcity"],
            created_hours_ago=168,
            scope="task",
            project_id="project_alpha",
            task_id="regression-test",
            user_id="test_zhao",
        ),
    ],

    interference=InterferenceSetup(
        memories=[
            SetupMemory(
                memory_type="episodic",
                title="Project Alpha 周例会：各模块进度同步",
                summary="周例会中各模块同步进度，前端完成 90%，后端完成 85%，测试待恢复。",
                content={"meeting": "weekly-sync", "progress": {"frontend": "90%", "backend": "85%"}},
                importance=0.4,
                confidence=0.8,
                tags=["weekly-sync", "progress"],
                created_hours_ago=48,
            ),
            SetupMemory(
                memory_type="task_status",
                title="Project Beta 数据迁移任务已完成",
                summary="Project Beta 数据迁移任务已完成，共迁移 120 万条记录。",
                content={"project": "Project Beta", "task": "data-migration", "status": "completed"},
                importance=0.3,
                confidence=0.8,
                tags=["data-migration"],
                created_hours_ago=72,
            ),
        ],
        count=2,
    ),

    recalls=[
        RecallSpec(
            query="regression-test 恢复执行，当前进展到哪里了？下一步做什么？",
            user_id="test_zhao",
            project_id="project_alpha",
            task_id="regression-test",
            intent="context_recovery",
            limit=10,
        ),
    ],

    assertions=[
        ResultAssertion(type="contains_title", value="regression-test 第一轮结果"),
        ResultAssertion(type="contains_title", value="regression-test 暂停策略"),
        ResultAssertion(type="contains_tag", value="regression-test"),
        # Should NOT return other project's tasks
        ResultAssertion(type="contains_title", value="Project Beta 数据迁移", negates=True),
    ],

    expected_titles=[
        "regression-test 第一轮结果：185/200 通过，15 失败",
        "regression-test 暂停策略：先修 mock 配置再重跑支付模块",
    ],
    forbidden_titles=["Project Beta 数据迁移任务已完成"],
    expected_count_range=(2, 4),

    spec_ref="9.3.2.2 策略延续",
    notes=(
        "验证系统在中断恢复场景下能否提供完整的任务上下文："
        "已完成的进度、失败的原因、暂停时的策略决策。"
        "对应 spec 6.2.3 恢复子系统设计和 spec 8.4 任务视图。"
    ),
)


# ---------------------------------------------------------------------------
# Case B-05: Risk identification — system surfaces risk from past experience
# ---------------------------------------------------------------------------
_B05_RISK_IDENTIFICATION = BenchmarkCase(
    case_id="B-05",
    track="B",
    capability="risk_identification",
    description=(
        "Project Alpha 准备进行 api-integration 与第三方支付平台的对接。"
        "系统应召回历史经验：Project Beta 中类似对接曾因沙箱环境与生产环境数据"
        "不一致导致上线延迟一周。系统应在任务启动时主动提示该风险。"
    ),
    direction="B",

    complexity_reasoning="high",     # 需要跨项目识别相似风险模式
    complexity_tool="medium",        # 需要关联两个项目的上下文
    complexity_interaction="medium", # 风险识别需要推理而非直接事实匹配

    memory_types=["decision", "episodic"],

    setup_events=[
        SetupEvent(
            source_type="message",
            content=(
                "Project Beta 上线复盘：与 XX 支付平台对接时，沙箱环境的模拟数据"
                "与生产环境真实数据存在差异（沙箱返回固定金额，生产返回动态金额），"
                "导致金额校验逻辑在生产环境全部失败，上线回滚，延迟一周修复。"
                "教训：对接第三方平台时，沙箱验证通过后必须在预生产环境做额外验证。"
            ),
            actors=["eng_li", "pm_zhang"],
            scope="project",
            source_ref="msg_beta_payment_postmortem_001",
            created_hours_ago=1440,  # 60 days ago
        ),
        SetupEvent(
            source_type="message",
            content=(
                "Project Alpha api-integration 任务启动，需要对接 YY 支付平台的 API。"
                "eng_li 已获取沙箱环境账号，计划下周开始联调。"
            ),
            actors=["eng_li"],
            scope="task",
            source_ref="msg_alpha_api_integration_start_001",
            created_hours_ago=4,
        ),
    ],
    setup_memories=[
        SetupMemory(
            memory_type="decision",
            title="Project Beta 支付对接教训：沙箱与生产数据不一致导致上线延迟",
            summary=(
                "Project Beta 与 XX 支付平台对接的经验教训：沙箱环境模拟数据"
                "（固定金额）与生产环境真实数据（动态金额）不一致，导致金额校验逻辑"
                "在生产环境全部失败，上线回滚并延迟一周修复。"
                "决策：后续对接第三方平台时，沙箱验证通过后必须在预生产环境额外验证。"
                "记录人：eng_li、pm_zhang。"
            ),
            content={
                "project": "Project Beta",
                "risk_type": "third_party_integration",
                "issue": "沙箱与生产环境数据不一致",
                "impact": "上线回滚，延迟一周",
                "lesson": "沙箱验证通过后必须在预生产环境做额外验证",
                "affected_module": "金额校验逻辑",
            },
            importance=0.85,
            confidence=0.9,
            tags=["risk", "payment", "third-party", "sandbox", "postmortem", "integration"],
            created_hours_ago=1440,
            project_id="project_alpha",
            task_id="api-integration",
            user_id="eng_li",
        ),
    ],

    interference=InterferenceSetup(
        memories=[
            SetupMemory(
                memory_type="episodic",
                title="Project Alpha 团建活动安排",
                summary="下周五下午团队建设活动，地点待定。",
                content={"event": "team-building", "date": "next-friday"},
                importance=0.2,
                confidence=0.9,
                tags=["team", "social"],
                created_hours_ago=24,
            ),
            SetupMemory(
                memory_type="decision",
                title="Project Alpha 日志级别规范：生产环境使用 WARN 级别",
                summary="生产环境日志级别统一设置为 WARN，减少日志量。",
                content={"project": "Project Alpha", "log_level": "WARN", "env": "production"},
                importance=0.4,
                confidence=0.85,
                tags=["logging", "standard"],
                created_hours_ago=240,
            ),
            SetupMemory(
                memory_type="semantic",
                title="Project Alpha 监控方案：Prometheus + Grafana",
                summary="项目监控使用 Prometheus 采集 + Grafana 展示。",
                content={"monitoring": "Prometheus + Grafana"},
                importance=0.5,
                confidence=0.8,
                tags=["monitoring", "prometheus"],
                created_hours_ago=200,
            ),
        ],
        count=3,
    ),

    recalls=[
        RecallSpec(
            query="api-integration 对接支付平台，之前有类似的经验教训或风险吗？",
            user_id="eng_li",
            project_id="project_alpha",
            task_id="api-integration",
            intent="risk_assessment",
            limit=5,
        ),
    ],

    assertions=[
        ResultAssertion(type="contains_title", value="支付对接教训"),
        ResultAssertion(type="contains_tag", value="risk"),
        ResultAssertion(type="contains_tag", value="payment"),
        # Irrelevant memories should not appear
        ResultAssertion(type="contains_title", value="团建活动", negates=True),
    ],

    expected_titles=[
        "Project Beta 支付对接教训：沙箱与生产数据不一致导致上线延迟",
    ],
    forbidden_titles=[
        "Project Alpha 团建活动安排",
        "Project Alpha 日志级别规范：生产环境使用 WARN 级别",
    ],
    expected_count_range=(1, 2),

    spec_ref="9.3.2.2 风险识别",
    notes=(
        "验证系统能否在任务启动时主动召回历史风险经验。"
        "这是 spec 7.6 记忆应用模块中'为任务规划提供历史目标、约束和依赖'的核心场景。"
    ),
)


# ---------------------------------------------------------------------------
# Case B-06: Task handover context — new assignee gets full context summary
# ---------------------------------------------------------------------------
_B06_TASK_HANDOVER = BenchmarkCase(
    case_id="B-06",
    track="B",
    capability="task_handover_context",
    description=(
        "api-integration 任务原由 eng_li 负责，因 eng_li 调离项目，"
        "任务交接给新加入的技术负责人 eng_chen。系统应在 eng_chen 接手时"
        "提供完整的任务上下文：目标、已完成内容、待办项、已知风险和决策历史。"
    ),
    direction="B",

    complexity_reasoning="high",     # 需要整合多条记忆生成完整交接上下文
    complexity_tool="high",          # 需要跨多个任务和事件的关联检索
    complexity_interaction="high",   # 交接场景天然需要多维度信息重组

    memory_types=["task_status", "decision", "episodic", "semantic"],

    setup_events=[
        SetupEvent(
            source_type="message",
            content=(
                "eng_li 在群里通知：因个人原因调离 Project Alpha，"
                "api-integration 任务将交接给 eng_chen。请 eng_chen 联系我了解详情。"
            ),
            actors=["eng_li"],
            scope="task",
            source_ref="msg_alpha_handover_001",
            created_hours_ago=48,
        ),
        SetupEvent(
            source_type="message",
            content=(
                "pm_zhang 确认：eng_chen 接手 api-integration 任务，"
                "请 test_zhao 配合更新相关测试计划。"
            ),
            actors=["pm_zhang", "test_zhao"],
            scope="task",
            source_ref="msg_alpha_handover_confirm_001",
            created_hours_ago=47,
        ),
    ],
    setup_memories=[
        SetupMemory(
            memory_type="task_status",
            title="api-integration 任务状态：接口设计完成，联调进行中",
            summary=(
                "Project Alpha api-integration 任务当前状态："
                "（1）接口文档 v2 已完成评审；"
                "（2）核心支付接口已实现并通过单元测试；"
                "（3）与 YY 支付平台的联调正在进行，已完成 3/8 个接口；"
                "（4）待办：完成剩余 5 个接口联调、错误处理、重试机制；"
                "负责人：eng_li（即将交接给 eng_chen）。"
            ),
            content={
                "project": "Project Alpha",
                "task": "api-integration",
                "status": "in_progress",
                "completed": ["接口文档 v2 评审通过", "核心支付接口实现及单测通过", "3/8 接口联调完成"],
                "pending": ["完成剩余 5 个接口联调", "错误处理机制", "重试机制实现"],
                "owner": "eng_li",
                "next_owner": "eng_chen",
            },
            importance=0.85,
            confidence=0.9,
            tags=["api-integration", "task-status", "handover"],
            created_hours_ago=48,
            scope="task",
            project_id="project_alpha",
            task_id="api-integration",
            user_id="eng_li",
        ),
        SetupMemory(
            memory_type="decision",
            title="api-integration 技术方案：REST + 异步回调模式",
            summary=(
                "api-integration 技术方案确定：对外接口使用 RESTful 风格，"
                "第三方回调使用异步消息队列接收。参与人：eng_li、pm_zhang。"
                "理由：REST 易于调试和维护，异步回调避免长连接占用线程池。"
            ),
            content={
                "project": "Project Alpha",
                "task": "api-integration",
                "decision": "REST + 异步回调",
                "reason": "REST 易于调试维护，异步回调避免长连接",
                "deciders": ["eng_li", "pm_zhang"],
            },
            importance=0.75,
            confidence=0.9,
            tags=["api-integration", "tech-decision", "rest", "async"],
            created_hours_ago=240,
            scope="task",
            project_id="project_alpha",
            task_id="api-integration",
            user_id="eng_li",
        ),
        SetupMemory(
            memory_type="decision",
            title="api-integration 安全约束：对外 API 必须通过 Gateway + OAuth2",
            summary=(
                "Project Alpha 安全评审结论适用于 api-integration："
                "所有对外暴露的 API 必须通过统一 API Gateway 接入，"
                "并强制启用 OAuth2.0 鉴权。决策人：pm_zhang、eng_li。"
            ),
            content={
                "project": "Project Alpha",
                "task": "api-integration",
                "constraint_type": "security",
                "requirement": "API Gateway + OAuth2",
            },
            importance=0.9,
            confidence=0.95,
            tags=["api-integration", "security", "compliance", "constraint"],
            created_hours_ago=168,
            scope="task",
            project_id="project_alpha",
            task_id="api-integration",
            user_id="eng_chen",
        ),
        SetupMemory(
            memory_type="episodic",
            title="api-integration 已知风险：YY 支付平台沙箱限流",
            summary=(
                "联调过程中发现 YY 支付平台沙箱环境对每分钟请求有限流（60 次/分钟），"
                "eng_li 已联系对方申请提额但尚未获批。需在联调计划中考虑限流影响。"
            ),
            content={
                "project": "Project Alpha",
                "task": "api-integration",
                "risk": "沙箱限流 60次/分钟，提额申请中",
                "mitigation": "控制联调节奏，单次批量不超过 50 条",
            },
            importance=0.7,
            confidence=0.85,
            tags=["api-integration", "risk", "rate-limit"],
            created_hours_ago=72,
            scope="task",
            project_id="project_alpha",
            task_id="api-integration",
            user_id="eng_chen",
        ),
    ],

    interference=InterferenceSetup(
        memories=[
            SetupMemory(
                memory_type="episodic",
                title="Project Alpha 办公用品采购清单",
                summary="行政部催促提交办公用品采购清单。",
                content={"category": "office-supplies"},
                importance=0.1,
                confidence=0.9,
                tags=["admin"],
                created_hours_ago=12,
            ),
            SetupMemory(
                memory_type="task_status",
                title="Project Gamma 前端重构任务进行中",
                summary="Project Gamma 前端重构完成 60%，预计下周完成。",
                content={"project": "Project Gamma", "task": "frontend-refactor", "status": "in_progress"},
                importance=0.3,
                confidence=0.8,
                tags=["frontend", "refactor"],
                created_hours_ago=36,
            ),
        ],
        count=2,
    ),

    recalls=[
        RecallSpec(
            query=(
                "我刚接手 api-integration 任务，请给我完整的任务上下文："
                "目标、进展、技术方案、已知风险和约束"
            ),
            user_id=None,
            project_id="project_alpha",
            task_id="api-integration",
            intent="context_recovery",
            limit=10,
        ),
    ],

    assertions=[
        ResultAssertion(type="contains_title", value="api-integration 任务状态"),
        ResultAssertion(type="contains_title", value="api-integration 技术方案"),
        ResultAssertion(type="contains_title", value="安全约束"),
        ResultAssertion(type="contains_title", value="已知风险"),
        # Other project tasks and admin noise should not appear
        ResultAssertion(type="contains_title", value="办公用品采购", negates=True),
        ResultAssertion(type="contains_title", value="Project Gamma", negates=True),
    ],

    expected_titles=[
        "api-integration 任务状态：接口设计完成，联调进行中",
        "api-integration 技术方案：REST + 异步回调模式",
        "api-integration 安全约束：对外 API 必须通过 Gateway + OAuth2",
        "api-integration 已知风险：YY 支付平台沙箱限流",
    ],
    forbidden_titles=[
        "Project Alpha 办公用品采购清单",
        "Project Gamma 前端重构任务进行中",
    ],
    expected_count_range=(3, 6),

    spec_ref="9.3.2.2 任务交接上下文",
    notes=(
        "验证系统在角色交接场景下能否提供完整的多维度上下文："
        "任务状态、技术决策、约束条件和已知风险。"
        "对应 spec 7.6 中'为交接场景提供阶段总结、关键待办和风险项'的要求，"
        "以及 spec 9.2 维度'协作交接完整性'。"
    ),
)


# ---------------------------------------------------------------------------
# Export all Track B cases
# ---------------------------------------------------------------------------
TRACK_B_CASES: list[BenchmarkCase] = [
    _B01_EXPERIENCE_REUSE,
    _B02_CROSS_TASK_TRANSFER,
    _B03_REDUCED_REPETITION,
    _B04_STRATEGY_CONTINUITY,
    _B05_RISK_IDENTIFICATION,
    _B06_TASK_HANDOVER,
]
