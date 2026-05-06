"""Extraction test data — 30 labeled Feishu messages for accuracy evaluation.

每个用例格式：
    message: str              — 飞书消息原文
    expected: str | None     — 期望的 memory_type，None = 不应提取
    confidence_min: float    — 期望的最低置信度
    notes: str               — 评分说明

分类覆盖：
    decision   — 决策类
    preference — 偏好类
    task_status — 进度/状态类
    none       — 不应提取任何记忆
"""

TEST_MESSAGES: list[dict] = [
    # ========== 决策类 (Decision) ==========
    {
        "id": "D-01",
        "message": "我们决定采用 React + TypeScript 做前端，因为团队更熟悉",
        "expected": "decision",
        "confidence_min": 0.6,
        "notes": "明确决策：决定+技术方案",
    },
    {
        "id": "D-02",
        "message": "最终决定用方案B，不采用方案A了",
        "expected": "decision",
        "confidence_min": 0.6,
        "notes": "明确决策：最终决定+否定旧方案",
    },
    {
        "id": "D-03",
        "message": "确认采用 PostgreSQL 替代 SQLite",
        "expected": "decision",
        "confidence_min": 0.6,
        "notes": "明确决策：确认+替代",
    },
    {
        "id": "D-04",
        "message": "各位，经过三方评审，最终定用方案B",
        "expected": "decision",
        "confidence_min": 0.6,
        "notes": "明确决策：经过评审+最终",
    },
    {
        "id": "D-05",
        "message": "我决定了，这个项目用 SQLite + BM25 做检索",
        "expected": "decision",
        "confidence_min": 0.6,
        "notes": "明确决策：第一人称决定",
    },
    {
        "id": "D-06",
        "message": "技术选型会议结论：前端框架选 React",
        "expected": "decision",
        "confidence_min": 0.6,
        "notes": "明确决策：结论+技术选型",
    },
    {
        "id": "D-07",
        "message": "同意采用微服务架构，不做单体了",
        "expected": "decision",
        "confidence_min": 0.6,
        "notes": "明确决策：同意+架构决策",
    },
    {
        "id": "D-08",
        "message": "方案A放弃，改用方案B",
        "expected": "decision",
        "confidence_min": 0.6,
        "notes": "明确决策：放弃+改用",
    },

    # ========== 偏好类 (Preference) ==========
    {
        "id": "P-01",
        "message": "以后默认先列计划再写代码",
        "expected": "preference",
        "confidence_min": 0.6,
        "notes": "明确偏好：以后+默认",
    },
    {
        "id": "P-02",
        "message": "我更喜欢用表格视图，不要列表视图",
        "expected": "preference",
        "confidence_min": 0.6,
        "notes": "明确偏好：更喜欢+否定",
    },
    {
        "id": "P-03",
        "message": "请用 Markdown 写文档，不要用 Word",
        "expected": "preference",
        "confidence_min": 0.6,
        "notes": "明确偏好：请用+不要用",
    },
    {
        "id": "P-04",
        "message": "周报默认发给 A 先生",
        "expected": "preference",
        "confidence_min": 0.6,
        "notes": "明确偏好：默认+工作流程",
    },
    {
        "id": "P-05",
        "message": "我倾向用异步沟通，减少实时会议",
        "expected": "preference",
        "confidence_min": 0.6,
        "notes": "明确偏好：倾向+习惯偏好",
    },
    {
        "id": "P-06",
        "message": "建议不要在周五下午开会",
        "expected": "preference",
        "confidence_min": 0.6,
        "notes": "明确偏好：建议不要",
    },
    {
        "id": "P-07",
        "message": "每周五下午4点自动帮我整理周报",
        "expected": "preference",
        "confidence_min": 0.6,
        "notes": "明确偏好：每周+自动习惯",
    },

    # ========== 进度/状态类 (Task Status) ==========
    {
        "id": "T-01",
        "message": "Phase 1-3 性能优化全部完成，237 测试通过",
        "expected": "task_status",
        "confidence_min": 0.6,
        "notes": "明确进度：完成+数字指标",
    },
    {
        "id": "T-02",
        "message": "API 文档更新任务已开始，预计本周完成",
        "expected": "task_status",
        "confidence_min": 0.6,
        "notes": "明确状态：任务+开始/预计",
    },
    {
        "id": "T-03",
        "message": "服务器迁移进度 70%，下周完成数据库迁移",
        "expected": "task_status",
        "confidence_min": 0.6,
        "notes": "明确进度：进度+百分比",
    },
    {
        "id": "T-04",
        "message": "测试覆盖率从 60% 提升到 85%",
        "expected": "task_status",
        "confidence_min": 0.6,
        "notes": "明确进度：覆盖率+提升",
    },
    {
        "id": "T-05",
        "message": "设计稿已提交评审，等待反馈中",
        "expected": "task_status",
        "confidence_min": 0.6,
        "notes": "明确状态：提交+等待",
    },

    # ========== 不应提取（问句/普通对话） ==========
    {
        "id": "N-01",
        "message": "之前我们前端技术选型决定了什么？",
        "expected": None,
        "confidence_min": 0,
        "notes": "问句：包含决定但本质是询问，不应提取",
    },
    {
        "id": "N-02",
        "message": "那个技术方案选型定的啥来着？",
        "expected": None,
        "confidence_min": 0,
        "notes": "问句：啥来着=询问，不应提取",
    },
    {
        "id": "N-03",
        "message": "开始写代码吧",
        "expected": None,
        "confidence_min": 0,
        "notes": "普通动作指令，不含决策/偏好/进度信号",
    },
    {
        "id": "N-04",
        "message": "帮我整理一下周报",
        "expected": None,
        "confidence_min": 0,
        "notes": "普通请求，不含偏好表达",
    },
    {
        "id": "N-05",
        "message": "张三今天请假了",
        "expected": None,
        "confidence_min": 0,
        "notes": "普通通知，无决策/偏好/进度",
    },
    {
        "id": "N-06",
        "message": "大家下午好，我们开始今天的站会",
        "expected": None,
        "confidence_min": 0,
        "notes": "普通开场白，无信息价值",
    },
    {
        "id": "N-07",
        "message": "谁有空的来帮忙 review 一下这个 PR",
        "expected": None,
        "confidence_min": 0,
        "notes": "普通请求，无决策/偏好/进度",
    },
    {
        "id": "N-08",
        "message": "好的，收到",
        "expected": None,
        "confidence_min": 0,
        "notes": "简单确认，无信息价值",
    },
    {
        "id": "N-09",
        "message": "之前决定用什么方案来着？",
        "expected": None,
        "confidence_min": 0,
        "notes": "问句：之前决定但后面是询问，不应提取",
    },
    {
        "id": "N-10",
        "message": "为什么选了方案A？",
        "expected": None,
        "confidence_min": 0,
        "notes": "问句：为什么+选择，不应提取",
    },
]
