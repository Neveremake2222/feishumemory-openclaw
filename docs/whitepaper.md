# 面向企业协作场景的结构化智能体记忆系统

**副标题**：从飞书消息到 OpenClaw 主动回忆的端到端闭环实现

> **版本**：1.1
> **日期**：2026-05-02
> **状态**：可运行 MVP，内部验证完成，云端飞书群聊验证通过

---

## 摘要

企业协作中，团队在飞书里做了大量决策、任务同步和个人偏好表达，但这些信息随着会话结束而消散。新智能体或新会话开始时，完全不知道历史上下文。普通聊天记录搜索只能找到"相关"文档，无法处理"旧决策被新决策替代"、"偏好只对某用户有效"、"任务状态已过期"等结构化记忆维护问题。

本项目实现了一个面向企业协作场景的结构化智能体记忆系统，将飞书/Lark 中的决策、任务状态和个人偏好抽取为可检索、可更新、可审计的结构化记忆，并通过 OpenClaw 适配层在智能体执行过程中主动召回和写入，形成完整闭环。项目重点不是“搜索聊天记录”，而是维护一套能被智能体持续使用的协作记忆。

**核心指标**：
- 单元/集成测试：345 passed, 3 skipped
- 提取准确率：100% (30/30, F1=1.00)
- 推送误报率：6.5% (2/31)
- 九轨 Benchmark：71/71 passed（Track A-I，覆盖对话记忆/任务决策/偏好学习/结构化优势/事件推理/工作流复用/记忆治理/长程改进/智能体评估）
- 召回性能：100 条记忆 P50=11.37ms，P95=12.34ms；1000 条记忆 P50=44.85ms，P95=53.11ms
- 代码规模：57+ 个 Python 文件，约 15,000+ 行代码

---

## 1. 背景与问题

### 1.1 企业协作中的记忆困境

在飞书协作环境中，团队每天产生大量需要被记住的信息：

- **决策**：技术选型、流程规范、负责人变更
- **任务状态**：当前进度、阻塞原因、下一步行动
- **个人偏好**：编码风格、工具选择、沟通方式

这些信息通常以消息形式散落在群聊中，缺乏结构化沉淀。当团队成员变更、智能体重启或新项目启动时，历史上下文完全丢失。

### 1.2 现有方案的局限性

| 方案 | 能做到 | 做不倒 |
|------|--------|--------|
| 聊天记录搜索 | 找到包含关键词的消息 | 判断"这条决策是否已被更新" |
| 普通 RAG | 检索相关文档片段 | 理解"偏好只对用户A有效" |
| 人工记录 | 结构化沉淀 | 及时性差，容易遗漏 |
| OpenClaw 内置记忆 | 跨会话持久化 | 无法与外部飞书事件联动，不支持结构化召回 |

### 1.3 本项目的切入点

本项目不替代 OpenClaw 的内置记忆系统，也不做通用 RAG。而是聚焦于**将飞书协作事件转化为结构化记忆，并让智能体在执行过程中主动使用这些记忆**。

---

## 2. 设计目标

| 目标 | 描述 |
|------|------|
| **结构化** | 将消息转化为 decision / task_status / preference 三类结构化记忆 |
| **可追溯** | 保留 source_ref、content_hash、evidence，支持来源审计 |
| **可维护** | 支持 update、archive、invalidate、promotion/demotion |
| **可隔离** | 支持 user / project / task / session 四级 scope |
| **可接入** | 通过 API 让 OpenClaw 主动 recall/write |
| **可评测** | Benchmark 四轨覆盖对话记忆、任务决策、偏好学习、结构化记忆优势验证 |

---

## 3. 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    飞书 / Lark 协作平台                       │
└──────────────┬──────────────────┬──────────────────────────┘
               │                  │
    ┌──────────▼──────┐   ┌──────▼──────────┐
    │  Fixture Replay  │   │  lark_ws WebSocket │  (实时)
    │  (测试/离线)     │   │  长连接适配器       │
    └──────────┬──────┘   └──────┬──────────┘
               │                  │
               └────────┬─────────┘
                        ▼
              ┌─────────────────────┐
              │   feishu_ingest    │  ← 候选抽取 + scope 推断
              │   Pipeline         │    + evidence 构造
              └─────────┬───────────┘    + 持久去重
                        ▼
              ┌─────────────────────┐     ┌──────────────────────────┐
              │   memory_engine     │  ←  │  Project Registry        │
              │   (SQLite)          │     │  (project_registry.json) │
              └─────────┬───────────┘     │  chat_id/doc_id/repo_path│
                        │                  │  → project_id 统一映射    │
               ┌────────▼─────────┐       └──────────┬───────────────┘
               │  openclaw_adapter │  ←               │
               │  /recall /write   │    project_id    │
               │  + project_resolver│ ←───────────────┘
               └────────┬─────────┘
                        ▼
              ┌─────────────────────┐
              │   OpenClaw 智能体    │  ← 上下文注入
              └─────────────────────┘    + 主动写入
```

**关键设计决策**：

    └── explicit local API/CLI/tool invocation
2. **SQLite 作为 MVP 存储**：轻量、无依赖、支持 SAVEPOINT 事务，适合本地优先验证
3. **确定性抽取**：不使用 LLM，基于正则规则抽取，保证可解释性和评测确定性
4. **BM25 词法召回**：Phase 1 阶段不引入向量库，通过 BM25 + 四维评分（相关性、新鲜度、重要度、置信度）+ MMR 多样性重排实现召回
5. **Project Registry 统一项目身份**：机器可读的 JSON 注册表连接飞书 ingest 和 OpenClaw adapter，保证写入侧和召回侧使用同一个 `project_id` 体系

---

## 4. 记忆数据模型

### 4.1 核心数据类型

**SourceEvent（来源事件）**：
```python
source_type: str       # message / doc
source_ref: str        # 飞书消息 ID 等全局唯一标识
actors: list[str]      # 参与者 open_id 列表
timestamp: str         # ISO-8601 UTC 时间
content: str           # 脱敏后正文
scope: str             # user / session / project / organization
payload: dict          # 元数据（source_url, content_hash, actors）
```

**MemoryCandidate（候选记忆）**：
```python
memory_type: str       # decision / task_status / preference / semantic
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
| `memories` | 结构化记忆 | memory_type, scope, importance, confidence, status, memory_layer, logical_layer |
| `recall_log` | 召回观测 | memory_id, query, was_returned(0-4), raw_score, rank_index |
| `audit_log` | 操作审计 | action, target_id, actor, sensitive_detections, audited_at |

**recall_log.was_returned 语义**：
- 0：进入候选池但最终未返回
- 1：实际返回给调用方
- 2：零结果占位
- 3：超过 MMR 阈值被过滤
- 4：低于评分阈值被过滤

### 4.3 记忆层级

| 层级 | 说明 |
|------|------|
| `working` (L0) | 短期会话记忆，可晋升为 factual |
| `factual` (L1) | 稳定事实记忆，可晋升为 L2/L3 |
| L2/L3 | 规则化晋升/降级层，当前已支持确定性 promotion/demotion；更复杂的用户反馈自进化仍是后续工作 |

---

## 5. 写入机制

### 5.1 完整流程

```
来源事件
    ↓
隐私扫描（scan_and_mask）
    ↓
候选抽取（extract_candidates）
    ↓ 每类约 20 条中英文确定性模式，覆盖决策/进度/偏好表达
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

1. **evidence_conflict**：同主题 + 数字矛盾 → 两者保留，降低置信度，标记 review
2. **role_change**：决策 + 角色关键词 → supersede + 通知
3. **goal_drift**：决策 + 目标关键词 → 两者保留，构成决策链
4. **constraint_supplement**：语义 + 增量关键词 → 两者保留
5. **fact_override**：事实变更 → supersede + 版本链
6. **potential_overlap**：近重复 → 两者保留

### 5.3 持久去重

`feishu_ingest.pipeline` 在每次处理前查询 `events` 表：

| 情况 | 行为 |
|------|------|
| `source_ref` + `content_hash` 完全匹配 | 跳过（跨进程重复） |
| `source_ref` 匹配但 `content_hash` 不同 | 正常写入（内容变更） |
| `source_ref` 不存在 | 正常写入（新事件） |

---

## 6. 召回机制

### 6.1 评分公式

```
score = 0.4 × norm(BM25) + 0.2 × freshness + 0.25 × importance + 0.15 × confidence
```

- **BM25**：词法相关性，k1=1.5, b=0.75，基于全量 active 记忆 IDF；中文文本额外生成 bigram token 提升短语匹配稳定性
- **freshness**：指数衰减，`0.5^(age_hours / half_life)`，half_life 按类型：decision=60天、task_status=14天、preference=90天
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

### 6.3 BM25 统计量缓存

`_compute_lexical_stats()` 的结果缓存在内存中（`_lexical_stats_cache`），以下操作会失效缓存：

- write / update / archive / invalidate / compact / promote / demote

---

## 7. 记忆维护机制

| 操作 | 说明 |
|------|------|
| **update** | 新版记忆 supersede 旧版，保留版本链（superseded_by UUID） |
| **archive** | 归档低价值记忆，保留数据但不出现在召回结果中 |
| **invalidate** | 使记忆失效（删除权限变更等场景） |
| **compact** | 合并近重复记忆 + 归档长期低价值记忆 + 过期 working 记忆 |
| **promote** | working → factual 晋升，创建审计链 |
| **flush** | 预压缩 flush，标记晋升候选 |
| **review** | 按项目过滤，扫描待晋升/待降级记忆 |
| **heartbeat** | 定期执行 compact + review + validate_sources，维护记忆健康状态 |

### 7.1 后台 Heartbeat 维护

`memory_engine.heartbeat` 模块提供轻量后台维护：

```python
from memory_engine.heartbeat import run_once, run_periodic

# 单次维护
result = run_once(engine)
# {'compact': {...}, 'review': {...}, 'validate_sources': [...]}

# 定期维护（默认30分钟间隔）
run_periodic(engine, interval_seconds=1800)
```

### 7.2 来源校验

系统已实现只读版 `validate_sources(resolver)`，用于比较库内来源指纹与外部来源当前状态。

返回状态：

| 状态 | 含义 |
|---|---|
| `ok` | 来源仍存在，且 `content_hash/source_version` 与库内一致 |
| `changed` | 来源仍存在，但 hash 或 version 已变化 |
| `missing` | 来源已不可访问或被删除 |
| `unknown` | 未提供 resolver，或外部来源暂不支持校验 |

第一版 source validation 只报告状态，不自动 archive 或 invalidate 记忆，避免因外部 API 抖动误伤已有记忆。它的价值在于：系统已经具备来源变化感知接口，后续可以接入真实飞书 resolver 做定期校验。

---

## 8. 飞书接入

### 8.1 三层适配器

| 适配器 | 用途 | 数据源 |
|--------|------|--------|
| `FixtureAdapter` | 测试/离线回放 | JSONL 文件 |
| `LarkCLIAdapter` | 读取历史消息/文档 | lark-cli CLI |
| `LarkWsAdapter` | 实时 WebSocket 事件 | lark-oapi SDK |

**lark_ws WebSocket 长连接**：
- 使用 `lark-oapi` SDK 原生 `lark.ws.Client`
- 事件通过线程安全队列从 SDK 回调传递到主线程
- 支持 `allowed_chat_ids` 过滤和自动重连

### 8.2 实时 ingest 链路

```
LarkWsAdapter.stream_events()
    → feishu_ingest.pipeline.run_ingest()
    → MemoryEngine.write()
    → 持久去重（跨进程）
    → OpenClaw 可召回
```

所有实时消息经过：scope 推断 → 候选抽取 → evidence 构造 → 持久去重，与离线 fixture/lark-cli 路径完全一致。

### 8.3 项目注册表（Project Registry）

飞书 ingest 和 OpenClaw adapter 共享一个机器可读的项目注册表（`config/project_registry.json`），保证写入侧和召回侧使用同一个 `project_id` 体系。

**问题背景**：四条写入/召回路径对 `project_id` 的处理不一致——群聊消息可能写入 `project_id=NULL`，而 OpenClaw 按 `project_id` 过滤召回时查不到。根本原因是缺少统一的项目身份映射。

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
2. `openclaw_adapter/project_resolver.py`：从 cwd/repo_path/workspace 解析 `project_id`，当请求未显式传 `project_id` 时自动解析
3. `feishu_ingest/lark_ws_ingest_daemon.py`：启动时加载注册表单例

**云端验证结果**：

```sql
SELECT id, source_type, scope, project_id FROM events ORDER BY id DESC LIMIT 3;
-- id=21  source_type=message  scope=project  project_id=feishu_openclaw_memory
-- id=20  source_type=message  scope=project  project_id=feishu_openclaw_memory
-- id=19  source_type=message  scope=project  project_id=feishu_openclaw_memory
```

注册表上线前（id=1-18）的记录 `project_id=NULL`；上线后新消息稳定归属到正确项目。历史数据修复脚本 `scripts/backfill_project_ids.py` 已就绪，支持 dry-run 模式。

---

## 9. OpenClaw 接入

### 9.1 适配层架构

```
OpenClaw 智能体
    ├── 读 MEMORY.md（每 5 分钟同步一次）
    └── explicit local API/CLI/tool invocation
            ↓ 检测到决策/偏好
            curl localhost:8000/write  # local explicit operation
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
| `/recall` | POST | 召回记忆，返回 Markdown 注入片段 + 元数据（memory_ids、query、tier_counts、memory_type_counts） |
| `/write` | POST | 写入决策/偏好/任务状态 |
| `/health` | GET | 健康检查 |

### 9.3 接入策略

采用**外部适配层**而非修改 OpenClaw 核心代码：
- API 服务（FastAPI + uvicorn）独立运行，nohup 持久化
    └── explicit local API/CLI/tool invocation
- 不依赖 OpenClaw 原生 hook 机制（妙搭托管版不支持）

---

## 10. 评测结果

### 10.1 单元/集成测试

```
345 passed, 3 skipped
```

覆盖范围：memory_engine、feishu_ingest、security/guard、openclaw_adapter、project_registry

### 10.2 九轨 Benchmark（Track A-I）

```
Track A (对话记忆): 12/12 passed
Track B (任务决策): 6/6 passed
Track C (偏好学习): 11/11 passed
Track D (结构化记忆优势): 7/7 passed
Track E (事件时序推理): 4/4 passed
Track F (工作流反思复用): 11/11 passed
Track G (记忆治理): 4/4 passed
Track H (长程自我改进): 5/5 passed
Track I (智能体记忆评估): 10/10 passed
OVERALL: 71/71 passed
```

**Track D 验证的结构化记忆优势**：

| 案例 | 验证优势 | 对比基准 |
|------|---------|---------|
| D-01 | 版本替代：旧决策自动 superseded，只返回当前有效决策 | 普通搜索同时返回新旧两条 |
| D-02 | 作用域隔离：用户偏好不污染项目决策召回 | 普通搜索 scope 混淆 |
| D-03 | 冲突检测：数字矛盾被标记为 evidence_conflict | 普通搜索/RAG 无法检测矛盾 |
| D-04 | 证据溯源：每条记忆含 source_ref + content_hash | 普通搜索无结构化来源信息 |
| D-05 | 多信号召回：freshness + importance 顶掉噪声记忆 | 纯关键词搜索被噪声淹没 |
| D-06 | 零结果安全：无关查询返回空，不产生幻觉 | LLM 无记忆时可能编造 |
| D-07 | 中文 bigram：短语跨顺序匹配（BM25检索方案 → 检索方案采用BM25） | 单字切分短语匹配不稳定 |

### 10.3 召回性能

```
 Size  |  Active  |  Results  |  P50 (ms)  |  P95 (ms)
-------|----------|----------|-------------|------------
   100 |      100 |       10 |       11.37 |      12.34
  1000 |     1000 |       10 |       44.85 |      53.11
```

> 注：经过 Phase 1（数据库索引 + WAL 模式）和 Phase 2（预计算 token_list + 去除 content_json tokenize）优化，1000 条记忆 P95 从 98.37ms 降至 53.11ms（-46%）。详见 `docs/performance-optimization-plan.md`。

### 10.4 代码规模

- Python 文件：57 个
- 代码行数：约 15,142 行
- 核心模块：memory_engine、feishu_ingest、openclaw_adapter、project_registry

---

## 11. 演示案例

### 案例：从一句飞书决策到可维护智能体记忆

这段演示不只证明“飞书消息能被搜索到”，而是证明系统具备完整记忆维护能力：写入、召回、更新、溯源、校验。

#### 步骤 1：结构化写入

用户在飞书群发送一条包含明确决策的消息：

```text
我决定以后这个项目默认使用 SQLite + BM25 做轻量记忆检索。
```

`lark_ws_ingest_daemon` 通过 WebSocket 实时接收消息，调用 `run_ingest()` 写入 memory-engine：

```text
Written memory_ids=[7] for msg om_xxx
```

这条消息会被抽取成结构化记忆，而不是只保存原始文本：

| 字段 | 示例 |
|---|---|
| memory_type | `decision` |
| scope | `project` |
| summary | `SQLite + BM25 做轻量记忆检索` |
| confidence | `0.8` |
| source_ref | `om_xxx` |
| content_hash | `sha256(...)` |
| evidence | 飞书消息来源和脱敏片段 |

#### 步骤 2：召回不是关键词搜索

用户向 OpenClaw 询问：

```text
我之前对这个项目的检索方案做过什么决定？
```

OpenClaw 召回相关外部记忆：

```text
## External Memory

- [DECISION] 我决定以后这个项目默认使用 SQLite + BM25 做轻量记忆检索
  我决定以后这个项目默认使用 SQLite + BM25 做轻量记忆检索。
  Evidence: feishu_message:om_xxx
```

这一步展示的是结构化 recall：

- scope filter 限定项目上下文。
- BM25 负责词法相关性。
- freshness / importance / confidence 共同影响排序。
- MMR 避免重复记忆堆叠。
- recall_log 记录命中和未命中原因。

#### 步骤 3：记忆会维护，不是 append-only 日志

再发送一条新决策：

```text
最终决定：这个项目检索仍用 BM25，但本地存储从 demo sqlite 切到正式 SQLite 文件。
```

系统会写入新决策，并通过冲突分类和版本/状态机制处理旧记忆。演示时可以展示：

- 新 memory_id。
- 旧记忆是否 superseded 或进入冲突处理。
- `audit_log` 中的更新/冲突记录。

这个环节用于说明：系统维护的是“当前可用记忆”，不是简单堆叠聊天历史。

#### 步骤 4：证据与审计

展示 SQLite 或日志中的来源字段：

```sql
SELECT source_type, source_ref, content_hash, source_version
FROM events
ORDER BY id DESC
LIMIT 3;
```

```sql
SELECT memory_type, scope, confidence, evidence_json
FROM memories
ORDER BY id DESC
LIMIT 3;
```

```sql
SELECT action, target_type, target_id, detail, audited_at
FROM audit_log
ORDER BY id DESC
LIMIT 5;
```

讲解重点：

> 这不是模型凭空生成的“记忆”，而是可以追溯到飞书 source_ref、content_hash 和 evidence 的结构化记忆。

#### 步骤 5：来源校验

使用 mock resolver 演示 `validate_sources()`：

```text
ok       -> 来源仍一致
changed  -> 来源内容或版本变化
missing  -> 来源不存在
unknown  -> 暂无 resolver 或不支持该来源
```

讲解重点：

> 第一版只读校验，不自动修改记忆，避免误伤。后续可接入飞书 API 做真实来源巡检。

---

## 12. 局限与未来工作

### 当前局限

| 局限 | 说明 |
|------|------|
| 时区展示 | `created_at` 存 UTC，展示层未转本地时区 |
| LLM 抽取 | Phase 1 纯规则抽取，复杂隐式表达可能遗漏 |
| 向量检索 | Phase 1 仅 BM25，语义相似召回依赖规则覆盖度 |
| 规模验证 | 评测基于 1000 条记忆，更大规模性能待验证 |
| 来源校验 | 已有只读 `validate_sources()` 接口，接入真实飞书 resolver 仍是后续工作 |

### 已完成（Phase 1+2）

| 能力 | 说明 |
|------|------|
| **Project Registry** | 机器可读 JSON 配置，飞书 ingest 和 OpenClaw adapter 共用，解决 project_id 不一致问题 |
| **中文 bigram** | `ranking.py` 连续中文片段生成 bigram token，提升短语匹配稳定性 |
| **RecallOutput 元数据** | `/recall` 返回 memory_ids、query、tier_counts、memory_type_counts |
| **BM25 统计量缓存** | `_lexical_stats_cache` 减少重复 IDF 计算 |

### 未来工作

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P1 | 展示层 UTC+8 本地化 | 保持内部 UTC 存储，展示时转换为北京时间 |
| P1 | source validation 接入真实飞书 resolver | 将 `ok/changed/missing/unknown` 与飞书 API 对接 |
| P2 | 结构化 facts 层 | 独立 facts 表沉淀 subject-predicate-object 三元事实 |
| P2 | valid_from / valid_until 时间有效性 | 区分当前有效记忆和历史记忆 |
| P2 | 更大规模 benchmark | 扩展到 10k+ 记忆并记录 P50/P95 |
| P2 | 后台 heartbeat 维护 | 定期触发 compact / review / validate_sources |
| P2 | visibility 用户隔离 | 显式 private / project / org 权限边界 |
| P2 | 缓存命中率观测 | 记录 BM25 stats cache 命中/失效情况 |
| P2 | 更多真实业务表达覆盖 | 持续从飞书真实 fixtures 扩展确定性抽取规则 |
| Phase 2 | LLM 查询改写 | Phase 2 扩展为 LLM 查询扩展/意图消歧 |
| Phase 2 | 反馈驱动晋升 | 在现有规则化 L1/L2/L3 基础上加入用户反馈信号 |
| Phase 2 | Bayesian evidence confidence | 正负证据计数替代单一 confidence 浮点值 |

---

## 13. 快速启动

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
python benchmarks/runner.py

# 运行召回性能基准
python benchmarks/recall_baseline.py
```

### 环境变量

```bash
# .env.example
LARK_APP_ID=cli_xxx
LARK_APP_SECRET=replace_me_local_secret
ALLOWED_CHAT_IDS=
MEMORY_ENGINE_DB=memory_engine.sqlite3
MEMORY_API_URL=http://localhost:8000
```

### 云端部署

```bash
# 启动 API
cd ~/workspace/agent/feishumemory
nohup python3 -m uvicorn openclaw_adapter.api:app --host 0.0.0.0 --port 8000 > api.log 2>&1 &

# 启动 lark_ws ingest daemon（加载 Project Registry）
export LARK_APP_ID=cli_xxx
export LARK_APP_SECRET=replace_me_local_secret
export MEMORY_ENGINE_DB=memory_engine.sqlite3
export PROJECT_REGISTRY_PATH=config/project_registry.json
nohup python3 -m feishu_ingest.lark_ws_ingest_daemon > ingest.log 2>&1 &

# 验证 API
curl http://localhost:8000/health

# 验证项目归属：检查新消息是否获得正确的 project_id
sqlite3 memory_engine.sqlite3 "SELECT id, source_type, scope, project_id FROM events ORDER BY id DESC LIMIT 5;"
```

---

## 14. 演示视频脚本与复现实验

### 14.1 视频结构

建议标题：

> 从一句飞书决策到可维护智能体记忆：写入、召回、更新、溯源全流程

建议时长：8 分钟。

| 时间 | 内容 | 画面 |
|---|---|---|
| 0:00-0:40 | 问题引入：飞书决策会丢，普通搜索不够 | 飞书消息/白皮书标题 |
| 0:40-1:30 | 架构总览 | 系统架构图 |
| 1:30-2:40 | 飞书决策写入结构化记忆 | 飞书 + daemon 日志 |
| 2:40-3:50 | 记忆详情：type/scope/evidence/hash | SQLite 查询结果 |
| 3:50-5:00 | OpenClaw 召回历史决策 | OpenClaw 回答 |
| 5:00-6:10 | 新决策替代旧决策 | 第二条飞书消息 + 记忆变化 |
| 6:10-7:10 | source validation / audit | 查询结果 |
| 7:10-8:00 | 测试和 benchmark | 终端输出 |

### 14.2 固定演示输入

飞书消息 1：

```text
我决定以后这个项目默认使用 SQLite + BM25 做轻量记忆检索。
```

飞书消息 2：

```text
最终决定：这个项目检索仍用 BM25，但本地存储从 demo sqlite 切到正式 SQLite 文件。
```

飞书消息 3：

```text
以后我默认希望先列计划，再修改代码，最后写验证结果。
```

OpenClaw 查询 1：

```text
我之前对这个项目的检索方案做过什么决定？
```

OpenClaw 查询 2：

```text
我对编码流程有什么偏好？
```

OpenClaw 查询 3：

```text
这个项目当前有哪些可追溯的记忆来源？
```

### 14.3 复现命令

启动 API：

```bash
python -m uvicorn openclaw_adapter.api:app --host 0.0.0.0 --port 8000
```

启动飞书 WebSocket ingest：

```bash
export LARK_APP_ID=cli_xxx
export LARK_APP_SECRET=replace_me_local_secret
export MEMORY_ENGINE_DB=memory_engine.sqlite3
python -m feishu_ingest.lark_ws_ingest_daemon
```

验证 API：

```bash
curl http://localhost:8000/health
```

查看最近来源事件：

```sql
SELECT id, source_type, source_ref, content_hash, source_version
FROM events
ORDER BY id DESC
LIMIT 5;
```

查看最近记忆：

```sql
SELECT id, memory_type, title, scope, project_id, confidence, status
FROM memories
ORDER BY id DESC
LIMIT 5;
```

查看审计日志：

```sql
SELECT action, target_type, target_id, detail, audited_at
FROM audit_log
ORDER BY id DESC
LIMIT 5;
```

验证测试：

```bash
python -m pytest tests -q
python benchmarks/runner.py
python benchmarks/recall_baseline.py
```

### 14.4 演示讲解重点

讲解时优先强调：

1. 飞书消息进入系统后会被结构化为记忆，而不是只保存聊天文本。
2. 记忆带有 scope，个人偏好不会污染项目决策。
3. 新决策可以影响旧决策状态，系统不是 append-only 日志。
4. 召回结果带 evidence，可追溯到原始飞书 source_ref。
5. `validate_sources()` 让系统具备来源变化感知能力。

避免强调：

- 不要说“已经生产级安全”。
- 不要说“完全自动自进化”。
- 不要说“支持所有飞书对象”。
- 不要说“已经替代 OpenClaw 内置记忆”。

---

## 15. Benchmark 设计思路与当前评测报告

> 本章基于 2026-05-06 当前代码重新生成的 `benchmarks_runtime/benchmark_report_current.md`。评测原则是：不削弱断言，不为了通过率隐藏失败；失败 case 必须继续暴露真实缺陷，例如过召回、遗漏召回、stale memory 使用和任务级上下文不足。

### 15.1 Benchmark 设计目标

本项目的 benchmark 不是单纯验证“能不能搜到一条相关文本”，而是验证企业协作 memory engine 是否真正具备以下能力：

1. **可召回**：能在项目、用户、任务 scope 内找到正确记忆。
2. **可拒绝**：不存在证据时返回空结果，不用 freshness / importance / confidence 拼出伪相关结果。
3. **可维护**：新旧决策、偏好变更、stale memory、superseded memory 必须被识别。
4. **可隔离**：跨项目、跨用户、跨任务不能串线。
5. **可审计**：memory 必须带 source_ref、evidence、event entry 和 audit trail。
6. **可规模化**：在 100 / 500 / 1000 条记忆规模下记录写入延迟、召回延迟和上下文精度。
7. **可用于 agent 任务**：不只测 retrieval，还测记忆对任务完成率、rubric score、answer relevancy 的帮助。

四层评测结构：

| 层级 | 覆盖内容 | 作用 |
|---|---|---|
| Layer 1 Retrieval Quality | Track J / J-gen | 验证 fact、decision、preference、zero-result、scope、stale、interference、multi-hop |
| Layer 2 Scale Benchmark | Track K | 验证 100 / 500 / 1000 记忆规模下的写入和召回成本 |
| Layer 3 Agent Task Benchmark | Track L | 验证 memory 是否改善端到端任务表现 |
| Layer 4 Memory Tax & Governance | Track E/F/G/H/I + aggregate metrics | 验证事件链、工作流复用、治理、自我改进和数据集可审查性 |

### 15.2 严格断言原则

每个 case 同时包含正向要求和负向要求：

- `expected_titles`：必须召回的记忆。
- `forbidden_titles`：不允许出现的旧版本、其他项目、其他用户或噪声记忆。
- `expected_count_range`：限制上下文大小，防止过召回。
- `expect_zero_results`：未知主题必须返回 0 条。
- `contains_tag` / `memory_content_*` / event assertions：验证结构化字段和事件链，而不只看 title。

偏好召回 case 不只要求返回 current preference，还要求 title 命中、dimension tag 命中、`current` tag 命中，并且 title/tag 均不能出现 `stale`。零结果 case 会在同一 scope 中放入合法但无关的 active memory；如果引擎返回任何结果，视为 hallucinated memory。

### 15.3 当前运行参数

当前检索主链路为 SQLite + BM25 + typed filter + evidence gate + currentness gate + weighted fusion + MMR：

| 参数 | 当前值 | 说明 |
|---|---:|---|
| BM25 `k1` | 1.5 | 词频饱和参数 |
| BM25 `b` | 0.75 | 文档长度归一化参数 |
| relevance weight | 0.40 | BM25 相关性权重 |
| freshness weight | 0.20 | 时间新鲜度权重 |
| importance weight | 0.25 | 记忆重要性权重 |
| confidence weight | 0.15 | 置信度权重 |
| fused score threshold | 0.35 | 融合分低于该值不返回 |
| default top-k | 10 | 默认召回上限 |
| decision half-life | 60 days | 决策相对稳定 |
| preference half-life | 90 days | 偏好更长期 |
| task_status half-life | 14 days | 任务状态更易过期 |
| evidence gate | non-empty query requires lexical evidence | 防止零结果幻觉 |
| currentness gate | stale preference / superseded decision excluded by default | 历史查询 intent 可显式召回历史 |

### 15.4 当前评测总览

当前完整 benchmark 数据集包含 **5,348** 个 case，当前结果为：

| 指标 | 当前结果 |
|---|---:|
| 总 case 数 | 5,348 |
| 通过 | 5,235 |
| 失败 | 113 |
| 总通过率 | 97.89% |
| 平均 rubric score | 0.98 |
| 平均写入延迟 | 68.78 ms |
| 平均召回延迟 | 3.27 ms |
| 平均 context precision | 0.98 |
| 平均 context recall | 0.98 |
| answer faithfulness | 1.00 |
| answer relevancy | 0.99 |
| memory improvement | 0.98 |
| memory event rate | 0.76 |
| relevant selected | 4,752 |
| irrelevant selected | 0 |

失败类型仍保留，不做隐藏：

| 失败类型 | 数量 | 含义 |
|---|---:|---|
| `over_retrieval_noise` | 81 | 返回上下文过多或超出严格数量边界 |
| `missed_recall` | 31 | 应召回记忆未进入结果 |
| `stale_memory_used` | 1 | 仍有 1 个 case 暴露 stale/current 判定缺陷 |

这些失败主要集中在 Track A/B/J/L 的严格 hand-crafted 或 agent-task 场景，说明下一步优化重点不是继续调高 J-gen 数字，而是改进复杂任务上下文组装和少量手工 case 的边界行为。

### 15.5 各 Track 结果

| Track | 能力 | Passed / Total | Context Precision | Context Recall | Avg Recall |
|---|---|---:|---:|---:|---:|
| A | Dialogue Memory | 7 / 12 | 0.82 | 0.64 | 4.04 ms |
| B | Task Decision | 4 / 6 | 0.67 | 0.67 | 3.59 ms |
| C | Preference Learning | 11 / 11 | 1.00 | 1.00 | 1.90 ms |
| D | Structured Memory Advantage | 7 / 7 | 1.00 | 1.00 | 3.51 ms |
| E | Event-Centric Temporal Reasoning | 4 / 4 | n/a | n/a | 1.60 ms |
| F | Workflow Reflection And Reuse | 11 / 11 | n/a | n/a | 2.13 ms |
| G | Memory Governance | 4 / 4 | n/a | n/a | 0.00 ms |
| H | Long-Horizon Self Improvement | 8 / 8 | n/a | n/a | 0.00 ms |
| I | Agent Memory Eval Dataset MVP | 30 / 30 | 1.00 | 1.00 | 3.54 ms |
| J | Retrieval Quality, hand-crafted | 29 / 30 | 1.00 | 0.99 | 3.40 ms |
| J-gen | Retrieval Quality, generated | 5,030 / 5,030 | 1.00 | 1.00 | 3.28 ms |
| K | Scale Benchmark | 45 / 45 | 1.00 | 1.00 | 3.49 ms |
| L | Agent Task Benchmark | 45 / 150 | 0.50 | 0.36 | 3.15 ms |

### 15.6 J-gen 关键能力结果

| Capability | Passed / Total | 说明 |
|---|---:|---|
| `fact_recall` | 1,200 / 1,200 | 跨 20 类事实、10 项目、6 噪声级别 |
| `decision_version` | 400 / 400 | 2-5 版本链，当前决策必须排除旧版本 |
| `preference_recall` | 150 / 150 | 当前偏好必须排除 stale preference |
| `scope_isolation` | 270 / 270 | 跨项目、跨用户隔离 |
| `stale_exclusion` | 2,000 / 2,000 | stale memory 默认不进入当前上下文 |
| `zero_result` | 500 / 500 | 未知主题必须返回空结果 |
| `interference_resistance` | 500 / 500 | 10-200 噪声项下仍召回目标 |
| `multi_hop` | 10 / 10 | 综合查询召回多条相关决策 |

这一组结果证明当前引擎已经修复三类关键缺陷：零结果幻觉、偏好 stale/current 混淆、决策版本链混淆。

### 15.7 规模与运行成本

Track K 规模测试覆盖 100 / 500 / 1000 条记忆：

| Scale | Cases | Avg Write Latency | Avg Recall Latency | Context Precision |
|---|---:|---:|---:|---:|
| K-100 | 15 | 约 248 ms | 约 3.10 ms | 1.00 |
| K-500 | 15 | 约 2450 ms | 约 4.20 ms | 1.00 |
| K-1k | 15 | 约 4757 ms | 约 6.95 ms | 1.00 |

写入延迟随批量 memory 数线性增长，主要受 SQLite 写入、事件构造和 token 预计算影响；召回延迟在 1000 条级别仍保持毫秒级，说明当前 SQLite + BM25 + token_list 方案足够支撑 MVP 和演示规模。当前尚未证明 10k/100k 规模下的性能，白皮书不能声称生产级大规模向量检索能力。

### 15.8 数据集构成

| 维度 | 数量 |
|---|---:|
| decision | 3,741 |
| semantic | 1,270 |
| preference | 428 |
| procedural | 276 |
| task_status | 25 |
| workflow_trace | 100 |
| dialogue / social / episodic | 13 |

按难度：

| Difficulty | Count |
|---|---:|
| easy | 523 |
| medium | 2,307 |
| hard | 1,912 |
| adversarial | 606 |

数据集字段完整度为 100%：`memory_target`、`evaluation_task`、`expected_behavior`、`ground_truth`、`scoring_rubric`、`difficulty`、`source_anchor` 均已填充。

### 15.9 评测结论

当前系统已经可靠覆盖结构化写入、当前记忆召回、版本与 stale 控制、零结果拒绝、scope 隔离、事件与治理。当前仍需优化 Track L agent-task 端到端任务通过率、少量 hand-crafted case 的边界行为、prompt token 成本统计，以及真实 Feishu 网络波动和开放式 LLM 生成质量评测。

---

## 16. 与 Mem0、Letta / MemGPT 的对比

> 外部资料来源：Mem0 官方文档称其为面向 AI agents 的 managed memory layer，提供托管 vector store、graph services、rerankers、enterprise controls；Letta 官方文档和 GitHub 将 Letta/MemGPT 定位为构建 stateful agents 的平台，强调 advanced memory、continual learning、skills/subagents 和 agent API。

### 16.1 定位差异

| 维度 | 本项目 | Mem0 | Letta / MemGPT |
|---|---|---|---|
| 核心定位 | 企业协作事件到结构化 agent memory 的本地闭环 | 通用 agent memory layer / managed memory service | stateful agent platform，agent 本身带长期状态和工具 |
| 主要输入 | 飞书/Lark 消息、任务状态、偏好、工作流 trace | 应用对话、用户上下文、agent memory API | agent 对话、工具调用、memory blocks、skills |
| 主要输出 | 可审计 recall 证据、OpenClaw 注入片段、SQLite memory | 个性化 memory search/update API | stateful agent response、memory blocks、工具/技能执行 |
| 强项 | scope、版本链、stale/current、审计、benchmark 可复现 | 托管化、快速集成、向量/图/rerank 基础设施 | agent runtime 一体化、memory-first agent、skills/subagents |
| 当前短板 | 非托管、规模仍在 1k benchmark、LLM 生成评测有限 | 对企业内部 Feishu 决策链需额外建模 | 更偏 agent 平台，企业协作事件治理需业务适配 |

### 16.2 架构对比

Mem0 更像“可嵌入任意应用的记忆服务”：应用调用 add/search/update/delete，平台侧负责向量存储、图、rerank、治理和扩展能力。它适合希望快速获得 managed memory layer 的产品团队。

Letta / MemGPT 更像“带内存的 agent 操作系统”：agent 有 memory blocks、tools、skills/subagents，重点是让 agent 自身变成有状态、可持续学习的执行体。它适合从 agent runtime 层构建长期个性化助手或数字员工。

本项目的切入点不同：它不试图替代通用 memory service 或 agent runtime，而是把企业协作中的飞书消息转化为可治理的结构化记忆，再通过 OpenClaw/local API 给 coding agent 使用。核心竞争点是：

- 企业协作事件 source_ref / content_hash 可追溯。
- decision / preference / task_status / workflow trace 类型明确。
- stale/current/superseded 进入检索前过滤，而不是只靠向量相似度。
- benchmark 明确包含 forbidden memory、zero-result、scope isolation 和 version chain。

### 16.3 评测对比

| 评测维度 | 本项目当前覆盖 | Mem0 / Letta 常见公开强调 |
|---|---|---|
| 大规模通用记忆评测 | 当前 5,348 deterministic cases，本地 1k scale | Mem0 公开强调 LoCoMo、LongMemEval、BEAM 等长程记忆 benchmark |
| 企业协作决策链 | decision_version 400/400，stale_exclusion 2000/2000 | 通常需要业务应用侧自定义 schema / metadata |
| 零结果拒绝 | zero_result 500/500 | 公开资料更强调记忆召回/个性化，拒绝误召回需看具体实现 |
| 用户偏好 current/stale | preference_recall 150/150 | Mem0/Letta 支持用户记忆，但业务偏好冲突策略需配置或应用层处理 |
| agent task | Track L 45/150，仍是主要短板 | Letta 更强在 agent runtime 和工具链，Mem0 更强在 memory API 托管 |
| 可审计性 | event entries、audit log、source_hash、本地 SQLite 可查 | Mem0 平台有 enterprise controls；Letta 有 agent state/API，具体审计能力依部署而定 |

### 16.4 项目价值边界

本项目适合强调以下问题的场景：

- 团队决策散落在飞书，需要沉淀为当前有效记忆。
- agent 需要知道“当前决策是什么”，而不是把所有历史都塞进上下文。
- 用户偏好、项目偏好、任务状态需要 scope 隔离。
- 需要本地可审计、可复现 benchmark，而不是只依赖托管黑盒指标。

如果目标是快速给任意 AI 应用加一层通用 memory API，Mem0 更成熟；如果目标是构建完整 memory-first agent runtime，Letta / MemGPT 更接近平台级方案。本项目的价值在于企业协作事件治理和本地可验证性，而不是托管基础设施或通用 agent 平台。

外部参考：

- Mem0 Platform Overview: https://docs.mem0.ai/platform/overview
- Mem0 GitHub: https://github.com/mem0ai/mem0
- Letta Code Overview: https://docs.letta.com/letta-code
- Letta GitHub: https://github.com/letta-ai/letta

---

## 17. 产品外壳与业务收益闭环

为了让非技术评委快速理解项目价值，当前实现新增了一个面向项目经理的产品外壳：**项目记忆驾驶舱**。它不是替代底层 `memory_engine`，而是把结构化记忆组织成项目经理能直接阅读和操作的视图。

新增交付物：

- `dashboard/index.html`：静态 Dashboard，展示项目列表、项目概览、记忆时间线、AI 助手和业务收益指标。
- `memory_engine/product_api.py`：产品层读模型，把底层记忆组装为项目概览、风险、下一步行动和跟进草稿。
- `openclaw_adapter/api.py`：新增 `/projects`、`/projects/{project_id}/overview`、`/projects/{project_id}/timeline`、`/projects/{project_id}/ask`、`/projects/{project_id}/draft-followup`、`/benchmarks/business-value`。
- `scripts/seed_demo_project.py`：基于飞书 fixture 初始化离线演示数据库。
- `benchmarks/cases/track_m.py`：新增 Track M 业务收益评测。
- `feishu_ingest/extractors.py`：新增项目管理字段抽取，包括 `risk`、`risk_level`、`impact`、`next_action`、`stakeholders`、`customer`、`deadline`、`progress`。

### 17.1 产品定位

本项目最终展示定位为：

> 面向项目经理的飞书 AI 项目记忆助手。系统自动接入飞书群聊、文档和任务信息，将碎片化沟通沉淀为本地长期记忆，并通过 OpenClaw 提供项目问答、进展总结、风险识别和下一步行动建议。

目标用户包括项目经理、交付负责人、客户成功和研发负责人。核心场景包括项目交接、客户跟进、历史决策追溯和风险识别。

### 17.2 Track M 业务收益评测

Track M 关注“记忆是否转化为项目管理收益”，覆盖 7 个确定性 case：

| 能力 | 结果 | 核心断言 |
|---|---:|---|
| 项目摘要完整度 | 100% | 召回方案、70% 进度、验收材料风险 |
| 决策追溯准确率 | 100% | 返回当前方案 B，排除旧方案 A |
| 风险识别召回率 | 100% | 找到验收材料风险，排除普通进展 |
| 跟进草稿事实一致性 | 100% | 草稿事实来自召回记忆 |
| 项目交接完整度 | 100% | 召回负责人、当前方案和风险 |
| 输入字数节省 | 81% | 短查询仍能召回当前决策 |
| 操作步骤节省 | 80% | 一次查询替代人工翻记录 |

单独运行结果：`7/7 passed`。详见 `docs/track_m_report.md`。

### 17.3 演示闭环

离线演示不依赖真实飞书网络，可通过 fixture 稳定复现：

```bash
python scripts/seed_demo_project.py --reset --db tests_runtime/product_demo.sqlite3
set MEMORY_ENGINE_DB=tests_runtime/product_demo.sqlite3
uvicorn openclaw_adapter.api:app --port 8000
cd dashboard
python -m http.server 8080
```

演示主线：

```text
飞书项目群消息
  -> 结构化记忆写入
  -> Dashboard 展示项目状态
  -> AI 助手回答项目问题
  -> 生成客户跟进消息
  -> Track M 证明业务收益
```
