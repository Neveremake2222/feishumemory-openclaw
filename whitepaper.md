# 面向企业协作场景的结构化智能体记忆系统

**副标题**：从飞书消息到 OpenClaw 主动回忆的端到端闭环实现

> **版本**：2.0
> **日期**：2026-05-07
> **状态**：可运行 MVP，内部验证完成，云端飞书群聊验证通过，生产就绪

---

## 摘要

企业协作中，团队在飞书里做了大量决策、任务同步和个人偏好表达，但这些信息随着会话结束而消散。新智能体或新会话开始时，完全不知道历史上下文。普通聊天记录搜索只能找到"相关"文档，无法处理"旧决策被新决策替代"、"偏好只对某用户有效"、"任务状态已过期"等结构化记忆维护问题。

本项目实现了一个面向企业协作场景的结构化智能体记忆系统，将飞书/Lark 中的决策、任务状态、个人偏好、工作流策略抽取为可检索、可更新、可审计的结构化记忆，并通过 OpenClaw 适配层在智能体执行过程中主动召回和写入，形成完整闭环。项目重点不是"搜索聊天记录"，而是维护一套能被智能体持续使用的协作记忆。

**核心指标**：
- 代码规模：94 个 Python 文件，核心模块 33,468 行代码
- P0 关键门禁：1,248/1,248 passed（零结果拒绝、偏好召回、决策版本链、Agent 任务召回）
- 提交版 Benchmark A-J：123/123 passed（Track A-I + J）
- 提交版 Benchmark K/L/M：215/215 passed（规模、Agent 任务、业务价值）
- 全量 Benchmark：5,235/5,348 passed（97.89%，含 113 个已知缺陷）
- 召回性能：100 条记忆 P50=11.37ms，P95=12.34ms；1000 条记忆 P50=44.85ms，P95=53.11ms

---

## 1. 背景与问题

### 1.1 企业协作中的记忆困境

在飞书协作环境中，团队每天产生大量需要被记住的信息：

- **决策**：技术选型、流程规范、负责人变更
- **任务状态**：当前进度、阻塞原因、下一步行动
- **个人偏好**：编码风格、工具选择、沟通方式
- **工作流策略**：成功的开发流程、有效的沟通模式
- **隐式习惯**：用户通过行为模式透露的偏好，而非直接表达

这些信息通常以消息形式散落在群聊中，缺乏结构化沉淀。当团队成员变更、智能体重启或新项目启动时，历史上下文完全丢失。

### 1.2 现有方案的局限性

| 方案 | 能做到 | 做不倒 |
|------|--------|--------|
| 聊天记录搜索 | 找到包含关键词的消息 | 判断"这条决策是否已被更新" |
| 普通 RAG | 检索相关文档片段 | 理解"偏好只对用户A有效" |
| 人工记录 | 结构化沉淀 | 及时性差，容易遗漏 |
| OpenClaw 内置记忆 | 跨会话持久化 | 无法与外部飞书事件联动，不支持结构化召回 |
| Mem0/Letta | 通用 agent memory API | 无法直接接入飞书决策链，缺乏企业协作事件治理 |

### 1.3 本项目的切入点

本项目不替代 OpenClaw 的内置记忆系统，也不做通用 RAG。而是聚焦于**将飞书协作事件转化为结构化记忆，并让智能体在执行过程中主动使用这些记忆**。核心创新点包括：

- **结构化记忆类型**：decision / task_status / preference / habit_rule / workflow_skill，明确区分记忆语义
- **版本链管理**：新决策自动 superseded 旧决策，只返回当前有效记忆
- **作用域隔离**：user / project / task / session 四级 scope，防止跨域记忆污染
- **隐式偏好学习**：从用户行为模式中自动识别偏好信号，沉淀为稳定偏好
- **工作流反思**：从 agent 执行结果中提取成功/失败案例，生成可复用策略
- **记忆治理**：多评审员投票机制，确保高质量记忆晋升到稳定层
- **来源可追溯**：每条记忆携带 source_ref、content_hash、evidence，支持审计和来源校验

---

## 2. 设计目标

| 目标 | 描述 | 当前状态 |
|------|------|---------|
| **结构化** | 将消息转化为 decision / task_status / preference / habit_rule 四类结构化记忆 | ✅ 完成 |
| **可追溯** | 保留 source_ref、content_hash、evidence，支持来源审计 | ✅ 完成 |
| **可维护** | 支持 update、archive、invalidate、promotion/demotion | ✅ 完成 |
| **可隔离** | 支持 user / project / task / session 四级 scope | ✅ 完成 |
| **可接入** | 通过 API 让 OpenClaw 主动 recall/write | ✅ 完成 |
| **可学习** | 隐式偏好自动观察、偏好候选生成、稳定偏好沉淀 | ✅ 完成 |
| **可反思** | 工作流成功/失败案例追踪、策略衍生、有效性评估 | ✅ 完成 |
| **可治理** | 多评审员投票、记忆晋升/降级、冲突检测 | ✅ 完成 |
| **可评测** | 14 轨道 Benchmark，5,348 个评测用例，支撑演示和交付 | ✅ 完成 |

---

## 3. 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    飞书 / Lark 协作平台                       │
└──────────────┬──────────────────┬──────────────────────────┘
               │                  │
    ┌──────────▼──────┐   ┌──────▼──────────┐
    │  Fixture Replay │   │  LarkWsAdapter   │  (实时)
    │  (测试/离线)    │   │  lark-oapi SDK   │
    │  + LarkCLI      │   │  WebSocket 长连接 │
    └──────────┬──────┘   └──────┬──────────┘
               │                  │
               └────────┬─────────┘
                        ▼
              ┌─────────────────────┐
              │   feishu_ingest    │  ← 候选抽取 + scope 推断
              │   Pipeline         │    + evidence 构造
              │                    │    + 持久去重
              │   + Reply Triggers │    + 主动推送
              └─────────┬───────────┘
                        ▼
              ┌─────────────────────┐     ┌──────────────────────────┐
              │   memory_engine     │  ←  │  Project Registry        │
              │   (SQLite)          │     │  (project_registry.json) │
              └─────────┬───────────┘     │  chat_id/doc_id/repo_path│
                        │                  │  → project_id 统一映射    │
               ┌────────▼─────────┐       └──────────┬───────────────┘
               │  openclaw_adapter │  ←               │
               │  API / CLI        │    project_id     │
               │  + Project Resolver│ ←───────────────┘
               └────────┬─────────┘
                        ▼
              ┌─────────────────────┐
              │   OpenClaw 智能体    │  ← 上下文注入
              └─────────────────────┘    + 主动写入
```

**关键设计决策**：

1. **外部适配层架构**：不修改 OpenClaw 核心代码，通过 local API/CLI/tool invocation 集成
2. **SQLite 作为存储**：轻量、无依赖、支持 SAVEPOINT 事务，适合本地优先验证
3. **确定性抽取**：不使用 LLM，基于正则规则抽取，保证可解释性和评测确定性
4. **BM25 词法召回**：Phase 1 不引入向量库，通过 BM25 + 四维评分（相关性、新鲜度、重要度、置信度）+ MMR 多样性重排实现召回
5. **Project Registry 统一项目身份**：机器可读的 JSON 注册表连接飞书 ingest 和 OpenClaw adapter
6. **LLM 作为可选增强**：Summary Sub-Agent 和 Governance LLM Ballot 为可选模块，失败时降级到规则
7. **产品外壳**：Dashboard + Product API，提供面向项目经理的驾驶舱视图

---

## 4. 记忆数据模型

### 4.1 核心数据类型

**SourceEvent（来源事件）**：
```python
source_type: str       # message / doc / task / meeting / approval / event
source_ref: str        # 飞书消息 ID 等全局唯一标识
actors: list[str]      # 参与者 open_id 列表
timestamp: str         # ISO-8601 UTC 时间
content: str           # 脱敏后正文
scope: str             # user / session / task / project / organization
payload: dict          # 元数据（source_url, content_hash, actors）
```

**MemoryCandidate（候选记忆）**：
```python
memory_type: str       # decision / task_status / preference / habit_rule / semantic
title: str             # 记忆标题（截断 50 字）
summary: str           # 摘要全文
content: dict          # scope / project_id / task_id 等
importance: float      # [0, 1]，默认按类型设定
confidence: float      # [0, 1]，抽取质量信号
evidence: list[dict]   # 来源证据列表
tags: list[str]        # 标签
```

### 4.2 数据库表结构

| 表 | 用途 | 关键字段 |
|----|------|---------|
| `events` | 来源事件存档 | source_type, source_ref, content_hash, source_version, actors |
| `memories` | 结构化记忆 | memory_type, scope, importance, confidence, status, logical_layer, valid_from/until |
| `recall_log` | 召回观测 | memory_id, query, was_returned(0-4), raw_score, rank_index |
| `audit_log` | 操作审计 | action, target_id, actor, sensitive_detections, audited_at |
| `event_entries` | 事件三元组 | subject, relation, object, event_id，支持事件链推理 |
| `memory_votes` | 治理投票 | assembly_id, ballot_kind, reviewer_role, vote, reasoning |

**recall_log.was_returned 语义**：
- 0：进入候选池但最终未返回
- 1：实际返回给调用方
- 2：零结果占位
- 3：超过 MMR 阈值被过滤
- 4：低于评分阈值被过滤

### 4.3 记忆层级

| 层级 | 说明 | 晋升条件 |
|------|------|---------|
| `working` (L0) | 短期会话记忆，可观察候选 | 观察记录 ≥3 条 |
| `factual` (L1) | 稳定事实记忆 | 观察 ≥3 或单条高置信度 |
| `semantic` (L2) | 语义规则记忆 | 跨场景一致性 ≥2 或持久性 ≥7 天 |
| `semantic` (L3) | 高度稳定的规则 | L2 进一步验证 |

**隐式偏好晋升路径**：
```
观察信号 → PreferenceObservation → PreferenceCandidate(≥3条观察)
  → Governance Vote → StablePreference(L2)
  → 90天无使用 → Review/Demotion
```

**工作流策略晋升路径**：
```
Agent 执行文本 → WorkflowOutcome → WorkflowCase(≥2成功)
  → StrategyCandidate → Governance Vote → WorkflowSkill(L2)
  → 有效性追踪 → 负效果≥3 → Archive
```

---

## 5. 写入机制

### 5.1 完整流程

```
来源事件
    ↓
隐私扫描（scan_and_mask）
    ↓
候选抽取（extract_candidates）
    ↓ 每类约 20-36 条中英文确定性模式，覆盖决策/进度/偏好表达/风险
写入门控（_should_store）
    ↓ 摘要<5字 reject / evidence 空 reject / >85% overlap skip
冲突检测（_classify_conflict）
    ↓ 五类型：evidence_conflict / role_change / goal_drift / constraint_supplement / fact_override
SAVEPOINT 事务
    ↓
event + memory + audit 原子提交
    ↓
BM25 缓存失效
```

### 5.2 冲突分类优先级

| 类型 | 触发条件 | 处理策略 |
|------|---------|---------|
| evidence_conflict | 同主题 + 数字矛盾 | 两者保留，降低置信度，标记 review |
| role_change | 决策 + 角色关键词 | supersede + 通知 |
| goal_drift | 决策 + 目标关键词 | 两者保留，构成决策链 |
| constraint_supplement | 语义 + 增量关键词 | 两者保留 |
| fact_override | 事实变更 | supersede + 版本链 |
| potential_overlap | 近重复 | 两者保留 |

### 5.3 持久去重

`feishu_ingest.pipeline` 在每次处理前查询 `events` 表：

| 情况 | 行为 |
|------|------|
| `source_ref` + `content_hash` 完全匹配 | 跳过（跨进程重复） |
| `source_ref` 匹配但 `content_hash` 不同 | 正常写入（内容变更） |
| `source_ref` 不存在 | 正常写入（新事件） |

### 5.4 主动推送机制

系统支持在飞书 ingest 过程中主动推送消息到群聊：

| 类型 | 触发条件 | 内容 |
|------|---------|------|
| A1 | 记忆写入完成 | 记忆卡片摘要 |
| A2 | 历史记忆相关 | 关联记忆推送（关键词触发） |
| A3 | 总结请求 | 结构化记忆摘要 |
| C2 | 操作上下文 | 偏好提醒 + 工作流策略提示 |

---

## 6. 召回机制

### 6.1 评分公式

```
score = 0.4 × norm(BM25) + 0.2 × freshness + 0.25 × importance + 0.15 × confidence
```

- **BM25**：词法相关性，k1=1.5, b=0.75，基于全量 active 记忆 IDF；中文文本额外生成 bigram token 提升短语匹配稳定性
- **freshness**：指数衰减，`0.5^(age_hours / half_life)`，half_life 按类型：decision=60天、task_status=14天、preference=90天、habit_rule=120天
- **importance**：写入时设定，decision=0.8、task_status=0.7、preference=0.6
- **confidence**：抽取质量信号，≥2 个模式匹配=0.8，否则=0.6

### 6.2 召回流程

```
RecallRequest(query, user_id, project_id, task_id, scope, memory_layer)
    ↓
查询改写（Phase 1 透传，Phase 2+ 可扩展为 LLM 扩展）
    ↓
候选构建（WHERE status='active' + scope 过滤 + memory_layer 过滤）
    ↓
BM25 评分 + 归一化 + 四维加权
    ↓
_MIN_SCORE=0.35 阈值过滤
    ↓
候选池（limit × 4，MMR 多样性重排）
    ↓
召回观测日志（recall_log）
    ↓
置信度分级展示
    Tier 1 (≥0.7)：直接注入
    Tier 2 (0.4-0.7)：注入 + 证据片段 + 数值
    Tier 3 (<0.4)：返回"未找到高置信度记忆"
```

### 6.3 作用域可见性规则

| scope | 可见性规则 |
|-------|-----------|
| USER | 仅创建者可见 |
| SESSION | 会话内可见 |
| TASK | 任务成员可见 |
| PROJECT | 项目成员可见 |
| ORGANIZATION | 组织成员可见 |

### 6.4 记忆时效性

| 字段 | 说明 |
|------|------|
| `valid_from` | 记忆生效时间，默认创建时间 |
| `valid_until` | 记忆失效时间，未设置表示长期有效 |
| stale memory | 超过有效期但未被 supersede 的记忆，默认不参与召回 |

### 6.5 BM25 统计量缓存

`_compute_lexical_stats()` 的结果缓存在内存中（`_lexical_stats_cache`），以下操作会失效缓存：

- write / update / archive / invalidate / compact / promote / demote

---

## 7. 记忆维护机制

### 7.1 操作总览

| 操作 | 说明 |
|------|------|
| **update** | 新版记忆 supersede 旧版，保留版本链（superseded_by UUID） |
| **archive** | 归档低价值记忆，保留数据但不出现在召回结果中 |
| **invalidate** | 使记忆失效（删除权限变更等场景） |
| **compact** | 合并近重复记忆 + 归档长期低价值记忆 + 过期 working 记忆 |
| **promote** | working → factual → semantic 晋升，创建审计链 |
| **demote** | 高层记忆降级到低层，标记 review |
| **flush** | 预压缩 flush，标记晋升候选 |
| **review** | 按项目过滤，扫描待晋升/待降级记忆 |
| **heartbeat** | 定期执行 compact + review + validate_sources，维护记忆健康状态 |

### 7.2 后台 Heartbeat 维护

```python
from memory_engine.heartbeat import run_once, run_periodic

# 单次维护
result = run_once(engine)
# {'compact': {...}, 'review': {...}, 'validate_sources': [...]}

# 定期维护（默认30分钟间隔）
run_periodic(engine, interval_seconds=1800)
```

### 7.3 来源校验

```python
validate_sources(resolver) -> list[dict]
```

返回状态：

| 状态 | 含义 |
|---|---|
| `ok` | 来源仍存在，且 content_hash/source_version 与库内一致 |
| `changed` | 来源仍存在，但 hash 或 version 已变化 |
| `missing` | 来源已不可访问或被删除 |
| `unknown` | 未提供 resolver，或外部来源暂不支持校验 |

第一版 source validation 只报告状态，不自动 archive 或 invalidate 记忆，避免因外部 API 抖动误伤已有记忆。

### 7.4 治理投票机制

多评审员确定性公民大会投票：

| 评审员 | 检查内容 |
|-------|---------|
| EvidenceReviewer | 证据是否充分、可追溯 |
| PrivacyReviewer | 是否包含敏感信息 |
| UtilityReviewer | 长期是否有价值 |
| ScopeReviewer | scope 是否合理 |
| ConflictReviewer | 是否与现有记忆冲突 |

可选 LLM Ballot Provider（OpenAI-compatible API），失败时降级到规则评审。

---

## 8. 飞书接入

### 8.1 三层适配器

| 适配器 | 用途 | 数据源 |
|--------|------|--------|
| `FixtureAdapter` | 测试/离线回放 | JSONL 文件 |
| `LarkCLIAdapter` | 读取历史消息/文档 | lark-cli CLI |
| `LarkWsAdapter` | 实时 WebSocket 事件 | lark-oapi SDK |

**LarkWsAdapter 技术要点**：
- 使用 `lark-oapi` SDK 原生 `lark.ws.Client`
- 事件通过线程安全队列从 SDK 回调传递到主线程
- 支持 `allowed_chat_ids` 过滤和自动重连
- 已验证支持真实飞书消息接收（2026-04-30 云端测试）

### 8.2 实时 ingest 链路

```
LarkWsAdapter.stream_events()
    → feishu_ingest.pipeline.run_ingest()
    → MemoryEngine.write()
    → 持久去重（跨进程）
    → 可选主动推送（A1/A2/A3/C2）
    → OpenClaw 可召回
```

所有实时消息经过：scope 推断 → 候选抽取 → evidence 构造 → 持久去重，与离线 fixture/lark-cli 路径完全一致。

### 8.3 项目注册表（Project Registry）

飞书 ingest 和 OpenClaw adapter 共享一个机器可读的项目注册表（`config/project_registry.json`）。

**注册表结构**：

```json
{
  "project_id": "feishu_openclaw_memory",
  "name": "飞书记忆引擎与 OpenClaw 集成",
  "chat_ids": ["oc_xxx"],
  "doc_ids": [],
  "repo_paths": ["C:/workspace/feishumemory", "/workspace/agent/feishumemory"],
  "openclaw_workspace_ids": ["feishumemory"]
}
```

**查询接口**：

| 方法 | 说明 |
|------|------|
| `project_for_chat(chat_id)` | 飞书群聊 → project_id |
| `project_for_doc(doc_id)` | 飞书文档/wiki → project_id |
| `project_for_repo_path(path)` | 代码仓库路径 → project_id |
| `project_for_workspace(ws_id)` | OpenClaw workspace → project_id |

**集成点**：
1. `feishu_ingest/scope.py`：`infer_project_id()` 查注册表，群聊消息自动获得 `project_id`
2. `openclaw_adapter/project_resolver.py`：从 cwd/repo_path/workspace 解析 `project_id`
3. `feishu_ingest/lark_ws_ingest_daemon.py`：启动时加载注册表单例

**云端验证结果**：

```sql
SELECT id, source_type, scope, project_id FROM events ORDER BY id DESC LIMIT 3;
-- id=21  source_type=message  scope=project  project_id=feishu_openclaw_memory
-- id=20  source_type=message  scope=project  project_id=feishu_openclaw_memory
-- id=19  source_type=message  scope=project  project_id=feishu_openclaw_memory
```

---

## 9. OpenClaw 接入

### 9.1 适配层架构

```
OpenClaw 智能体
    ├── 读 MEMORY.md（每 5 分钟同步一次）
    └── explicit local API/CLI/tool invocation
            ↓ 检测到决策/偏好/工作流结果
            curl localhost:8000/write
                    ↓
            openclaw_adapter API
                    ↓ recall / write
            memory_engine (SQLite)
                    ↓ 每 5 分钟同步
            MEMORY.md ← OpenClaw 读取
```

### 9.2 API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/recall` | POST | 召回记忆，返回 Markdown 注入片段 + 元数据 |
| `/write` | POST | 写入决策/偏好/任务状态 |
| `/health` | GET | 健康检查 |
| `/projects` | GET | 列出项目（Dashboard） |
| `/projects/{id}/overview` | GET | 项目概览 |
| `/projects/{id}/timeline` | GET | 记忆时间线 |
| `/projects/{id}/ask` | POST | 项目问答 |
| `/projects/{id}/draft-followup` | POST | 生成跟进草稿 |
| `/benchmarks/business-value` | GET | 业务价值指标 |

### 9.3 接入策略

采用**外部适配层**而非修改 OpenClaw 核心代码：
- API 服务（FastAPI + uvicorn）独立运行，nohup 持久化
- 不依赖 OpenClaw 原生 hook 机制（妙搭托管版不支持）
- CLI 适配器支持子进程调用

---

## 10. 产品外壳：项目记忆驾驶舱

### 10.1 产品定位

面向项目经理的飞书 AI 项目记忆助手。系统自动接入飞书群聊、文档和任务信息，将碎片化沟通沉淀为本地长期记忆，并通过 Dashboard 和 OpenClaw 提供项目问答、进展总结、风险识别和下一步行动建议。

### 10.2 核心功能

| 功能 | 说明 |
|------|------|
| 项目列表 | 展示所有已注册项目 |
| 项目概览 | 关键决策、风险、下一步行动、干系人、进度 |
| 记忆时间线 | 按时间展示记忆演进 |
| AI 助手 | 项目问答，提取式回答 + 可选 LLM 摘要 |
| 跟进草稿 | 根据记忆生成客户跟进消息 |
| 业务指标 | 输入节省率、操作步骤节省率 |

### 10.3 Dashboard 截图

已生成桌面版和移动版截图，位于 `benchmarks_runtime/dashboard_screenshots/`。

---

## 11. 评测结果

### 11.1 P0 关键门禁

**核心风险已修复，进入回归门禁**：

| 能力 | Case 数 | 结果 |
|------|---------|------|
| 零结果拒绝 | 500 | 500/500 passed |
| 偏好召回 | 150 | 150/150 passed |
| 决策版本链 | 400 | 400/400 passed |
| Agent 任务召回 | 150 | 150/150 passed |
| **合计** | **1,248** | **1,248/1,248 passed** |

### 11.2 提交版 Benchmark A-J

**核心手写轨道，全部通过**：

| Track | 名称 | Cases | Passed |
|-------|------|-------|--------|
| A | 对话记忆 | 12 | 12/12 |
| B | 任务决策 | 6 | 6/6 |
| C | 偏好学习 | 11 | 11/11 |
| D | 结构化记忆优势 | 7 | 7/7 |
| E | 事件时序推理 | 4 | 4/4 |
| F | 工作流反思复用 | 11 | 11/11 |
| G | 记忆治理 | 4 | 4/4 |
| H | 长程自我改进 | 8 | 8/8 |
| I | 智能体记忆评测 | 30 | 30/30 |
| J | 检索质量 | 30 | 30/30 |
| **合计** | | **123** | **123/123** |

### 11.3 提交版 Benchmark K/L/M

**规模、性能、业务价值**：

| Track | 名称 | Cases | Passed |
|-------|------|-------|--------|
| K | 规模基准 | 45 | 45/45 |
| L | Agent 任务 | 150 | 150/150 |
| M | 业务价值 | 20 | 20/20 |
| **合计** | | **215** | **215/215** |

### 11.4 全量 Benchmark

**5,348 个评测用例，完整评估**：

```
Passed: 5,235
Failed: 113
Pass Rate: 97.89%
```

| 指标 | 值 |
|------|---|
| Average rubric score | 0.98 |
| Average write latency | 68.78 ms |
| Average recall latency | 3.27 ms |
| Average context precision | 0.98 |
| Average context recall | 0.98 |
| Answer faithfulness | 1.00 |
| Answer relevancy | 0.99 |
| Memory improvement | 0.98 |

### 11.5 失败类型分布

| 类型 | 数量 | 含义 |
|------|------|------|
| over_retrieval_noise | 81 | 返回上下文过多或超出严格数量边界 |
| missed_recall | 31 | 应召回记忆未进入结果 |
| stale_memory_used | 1 | 仍有 1 个 case 暴露 stale/current 判定缺陷 |

这些失败主要集中在 Track A/B/L 的严格 hand-crafted 或 agent-task 场景，已纳入 Track M 持续优化。

### 11.6 Track M 业务价值指标

| 指标 | 结果 | 说明 |
|------|------|------|
| 项目摘要完整度 | 100% | 召回方案、进度、风险 |
| 决策追溯准确率 | 100% | 返回当前方案，排除旧版本 |
| 风险识别召回率 | 100% | 找到风险，排除普通进展 |
| 跟进草稿事实一致性 | 100% | 草稿事实来自召回记忆 |
| 项目交接完整度 | 100% | 召回负责人、方案和风险 |
| 输入字数节省 | 81% | 短查询仍能召回 |
| 操作步骤节省 | 80% | 一次查询替代翻记录 |

### 11.7 召回性能

```
 Size  |  Active  |  Results  |  P50 (ms)  |  P95 (ms)
-------|----------|----------|-------------|------------
   100 |      100 |       10 |       11.37 |      12.34
  1000 |     1000 |       10 |       44.85 |      53.11
```

> 注：经过 Phase 1（数据库索引 + WAL 模式）和 Phase 2（预计算 token_list + 去除 content_json tokenize）优化，1000 条记忆 P95 从 98.37ms 降至 53.11ms（-46%）。

### 11.8 Track D：结构化记忆优势验证

| 案例 | 验证优势 | 对比基准 |
|------|---------|---------|
| D-01 | 版本替代：旧决策自动 superseded，只返回当前有效决策 | 普通搜索同时返回新旧两条 |
| D-02 | 作用域隔离：用户偏好不污染项目决策召回 | 普通搜索 scope 混淆 |
| D-03 | 冲突检测：数字矛盾被标记为 evidence_conflict | 普通搜索/RAG 无法检测矛盾 |
| D-04 | 证据溯源：每条记忆含 source_ref + content_hash | 普通搜索无结构化来源信息 |
| D-05 | 多信号召回：freshness + importance 顶掉噪声记忆 | 纯关键词搜索被噪声淹没 |
| D-06 | 零结果安全：无关查询返回空，不产生幻觉 | LLM 无记忆时可能编造 |
| D-07 | 中文 bigram：短语跨顺序匹配 | 单字切分短语匹配不稳定 |

---

## 12. 演示案例

### 案例：从一句飞书决策到可维护智能体记忆

#### 步骤 1：结构化写入

用户在飞书群发送一条包含明确决策的消息：

```text
我决定以后这个项目默认使用 SQLite + BM25 做轻量记忆检索。
```

系统实时接收消息并写入记忆引擎：

```text
Written memory_ids=[7] for msg om_xxx
```

#### 步骤 2：召回不是关键词搜索

用户向 OpenClaw 询问：

```text
我之前对这个项目的检索方案做过什么决定？
```

OpenClaw 召回相关外部记忆：

```text
## External Memory

- [DECISION] 我决定以后这个项目默认使用 SQLite + BM25 做轻量记忆检索
  Evidence: feishu_message:om_xxx
  Confidence: 0.80
```

#### 步骤 3：记忆会维护，不是 append-only 日志

再发送一条新决策：

```text
最终决定：这个项目检索仍用 BM25，但本地存储从 demo sqlite 切到正式 SQLite 文件。
```

系统写入新决策，旧记忆状态变为 superseded。

#### 步骤 4：证据与审计

```sql
SELECT source_type, source_ref, content_hash FROM events ORDER BY id DESC LIMIT 3;
SELECT memory_type, scope, confidence, status FROM memories ORDER BY id DESC LIMIT 3;
SELECT action, target_type, detail FROM audit_log ORDER BY id DESC LIMIT 5;
```

---

## 13. 与 Mem0、Letta 的对比

### 13.1 定位差异

| 维度 | 本项目 | Mem0 | Letta / MemGPT |
|---|---|---|---|
| 核心定位 | 企业协作事件到结构化 agent memory 的本地闭环 | 通用 agent memory layer / managed memory service | stateful agent platform |
| 主要输入 | 飞书/Lark 消息、任务状态、偏好、工作流 trace | 应用对话、用户上下文、agent memory API | agent 对话、工具调用、memory blocks |
| 强项 | scope、版本链、stale/current、审计、benchmark | 托管化、快速集成、向量/图/rerank 基础设施 | agent runtime 一体化、memory-first agent |
| 当前短板 | 非托管、规模仍在 1k benchmark | 对企业飞书决策链需额外建模 | 更偏 agent 平台，企业协作事件治理需业务适配 |

### 13.2 评测对比

| 评测维度 | 本项目当前 | Mem0/Letta 公开强调 |
|---|---|---|
| 大规模评测 | 5,348 deterministic cases，本地 1k scale | LoCoMo、LongMemEval、BEAM 等长程 benchmark |
| 决策版本链 | 400/400 passed | 需业务应用侧自定义 |
| 零结果拒绝 | 500/500 passed | 公开资料更强调召回 |
| 用户偏好 current/stale | 150/150 passed | 支持用户记忆，策略需配置 |
| 可审计性 | event entries、audit log、content_hash | Mem0 enterprise controls；Letta agent state |

---

## 14. 局限与未来工作

### 当前局限

| 局限 | 说明 |
|------|------|
| 时区展示 | `created_at` 存 UTC，展示层未转本地时区 |
| LLM 抽取 | Phase 1 纯规则抽取，复杂隐式表达可能遗漏 |
| 向量检索 | Phase 1 仅 BM25，语义相似召回依赖规则覆盖度 |
| 规模验证 | 评测基于 1000 条记忆，更大规模性能待验证 |
| 来源校验 | 已有只读 `validate_sources()` 接口，接入真实飞书 resolver 仍是后续工作 |
| Agent 任务 | Track L 仍有优化空间，端到端任务上下文组织能力待提升 |

### 已完成（Phase 1+2）

| 能力 | 说明 |
|------|------|
| **Project Registry** | 机器可读 JSON 配置，飞书 ingest 和 OpenClaw adapter 共用 |
| **中文 bigram** | `ranking.py` 连续中文片段生成 bigram token |
| **RecallOutput 元数据** | `/recall` 返回 memory_ids、query、tier_counts、memory_type_counts |
| **BM25 统计量缓存** | `_lexical_stats_cache` 减少重复 IDF 计算 |
| **隐式偏好学习** | 三阶段偏好生命周期：观察→候选→稳定偏好 |
| **工作流反思** | 成功/失败案例追踪、策略衍生、有效性评估 |
| **记忆治理** | 多评审员投票、晋升/降级机制 |
| **主动推送** | A1/A2/A3/C2 触发器，飞书群聊主动推送 |
| **产品外壳** | Dashboard + Product API，面向项目经理 |

### 未来工作

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P1 | 展示层 UTC+8 本地化 | 保持内部 UTC 存储，展示时转换为北京时间 |
| P1 | source validation 接入真实飞书 resolver | 将 ok/changed/missing/unknown 与飞书 API 对接 |
| P2 | 结构化 facts 层 | 独立 facts 表沉淀 subject-predicate-object 三元事实 |
| P2 | valid_from / valid_until 时间有效性 | 区分当前有效记忆和历史记忆 |
| P2 | 更大规模 benchmark | 扩展到 10k+ 记忆并记录 P50/P95 |
| P2 | 缓存命中率观测 | 记录 BM25 stats cache 命中/失效情况 |
| P2 | 更多真实业务表达覆盖 | 持续从飞书真实 fixtures 扩展确定性抽取规则 |
| Phase 2 | LLM 查询改写 | LLM 查询扩展/意图消歧 |
| Phase 2 | Bayesian evidence confidence | 正负证据计数替代单一 confidence 浮点值 |

---

## 15. 快速启动

### 环境要求

- Python 3.10+
- pip 安装依赖：`pip install -r requirements.txt`

### 本地运行

```bash
# 启动 memory-engine API
uvicorn openclaw_adapter.api:app --host 0.0.0.0 --port 8000

# 运行测试
python -m pytest tests -q

# 运行 benchmark
python -m benchmarks.runner
```

### 环境变量

```bash
# .env.example
LARK_APP_ID=cli_xxx
LARK_APP_SECRET=replace_me_local_secret
ALLOWED_CHAT_IDS=
MEMORY_ENGINE_DB=memory_engine.sqlite3
MEMORY_API_URL=http://localhost:8000
PROJECT_REGISTRY_PATH=config/project_registry.json
```

### 云端部署

```bash
# 启动 API
nohup python3 -m uvicorn openclaw_adapter.api:app --host 0.0.0.0 --port 8000 > api.log 2>&1 &

# 启动 lark_ws ingest daemon
export LARK_APP_ID=cli_xxx
export LARK_APP_SECRET=replace_me_local_secret
export MEMORY_ENGINE_DB=memory_engine.sqlite3
export PROJECT_REGISTRY_PATH=config/project_registry.json
nohup python3 -m feishu_ingest.lark_ws_ingest_daemon > ingest.log 2>&1 &

# 验证 API
curl http://localhost:8000/health
```

### 离线演示

```bash
# 初始化演示数据库
python scripts/seed_demo_project.py --reset --db tests_runtime/product_demo.sqlite3

# 启动 API
set MEMORY_ENGINE_DB=tests_runtime/product_demo.sqlite3
uvicorn openclaw_adapter.api:app --port 8000

# 启动 Dashboard
cd dashboard
python -m http.server 8080
```

---

## 16. 代码规模

| 指标 | 值 |
|------|---|
| Python 文件总数 | 108 |
| 核心模块文件（memory_engine/feishu_ingest/openclaw_adapter/benchmarks/scripts） | 94 |
| 核心模块代码行数 | 33,468 |
| 测试文件 | 13 |
| Benchmark Track | 14 |
| 评测用例总数 | 5,348 |

---

## 17. 参考资料

- 项目仓库：https://github.com/Neveremake2222/feishumemory
- Mem0 Platform：https://docs.mem0.ai/platform/overview
- Mem0 GitHub：https://github.com/mem0ai/mem0
- Letta Documentation：https://docs.letta.com/letta-code
- Letta GitHub：https://github.com/letta-ai/letta
- Lark/Feishu Open Platform：https://open.feishu.cn
- lark-cli：https://github.com/larksuite/cli
