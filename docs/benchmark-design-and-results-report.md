# Benchmark 设计报告与项目通过情况

## 1. 报告结论

本项目设计了一套多轨道 benchmark 体系，用来验证飞书项目记忆助手是否真正具备“可写入、可治理、可召回、可拒答、可复用、可交付”的能力。

Benchmark 不是为了制造好看的通过率，而是为了暴露记忆系统在真实企业协作场景中的核心风险：记忆幻觉、旧决策误召回、偏好无法复用、上下文噪声过多、agent 任务闭环不稳定。

当前项目形成了三类关键评测结果：

| 报告类型 | Case 数 | 结果 | 作用 |
|---|---:|---:|---|
| 历史全量压力报告 | 5,348 | 4,296/5,348 passed，80.3% | 暴露真实缺陷 |
| 当前 P0 关键门禁 | 1,248 | 1,248/1,248 passed | 验证关键问题修复 |
| 当前提交版分片报告 | 338 | 338/338 passed | 支撑演示和提交 |

## 2. Benchmark 的设计目标

本项目 benchmark 的目标不是只验证“能不能搜到一条记忆”，而是验证完整记忆系统在项目管理和 agent 协作中的实际可用性。

主要目标包括：

1. 验证飞书消息能否被正确抽取为结构化记忆。
2. 验证项目、用户、任务之间的记忆是否隔离。
3. 验证当前决策是否能覆盖旧决策。
4. 验证用户偏好和隐式习惯是否能被沉淀并召回。
5. 验证不存在的信息是否能正确拒答。
6. 验证噪声数据增加后系统是否仍能稳定召回。
7. 验证 agent 是否能在完整任务中使用记忆，而不是只完成单次检索。
8. 验证项目管理场景下是否能带来实际业务价值。

## 3. Benchmark 的整体架构

Benchmark 的执行链路如下：

```text
BenchmarkCase
-> setup_events / setup_memories
-> MemoryEngine.write()
-> 可选 MemoryEngine.review()
-> MemoryEngine.recall()
-> assertion 检查
-> 指标统计
-> Markdown / JSONL 报告输出
```

核心代码模块包括：

| 模块 | 作用 |
|---|---|
| `benchmarks/structures.py` | 定义 benchmark case、recall spec、assertion、干扰数据 |
| `benchmarks/runner.py` | 执行写入、召回、断言和指标统计 |
| `benchmarks/report.py` | 生成 Markdown 报告 |
| `benchmarks/generator.py` | 批量生成大规模测试用例 |
| `benchmarks/regression_gate.py` | 回归门禁 |
| `benchmarks/evaluation/*` | 抽取评测、推送评测等专项评估 |

## 4. Benchmark 考察维度

### 4.1 记忆写入正确性

考察飞书消息、OpenClaw 操作和测试事件能否被正确写入为结构化记忆。

重点检查：

- `decision` 是否正确识别
- `task_status` 是否正确识别
- `preference` 是否正确识别
- `project_id` / `task_id` / `user_id` 是否正确绑定
- evidence 是否保留

### 4.2 召回准确性

考察用户查询时是否能找回正确记忆。

指标包括：

- expected title 是否出现
- forbidden title 是否被排除
- context precision
- context recall
- answer faithfulness
- answer relevancy

### 4.3 零结果拒绝

考察当用户询问不存在的话题时，系统是否能够返回空结果，而不是强行返回相似但无关的记忆。

这是防止 AI 幻觉的核心维度。

### 4.4 决策版本链

考察系统是否能区分当前决策和历史废弃版本。

重点检查：

- 当前版本是否优先返回
- 旧版本是否被 `superseded`
- `replaces_memory_id` 是否形成链路
- 查询当前方案时是否排除旧方案

### 4.5 偏好与隐式学习

考察用户偏好是否能被记录、聚合、确认和召回。

包括：

- 显式偏好写入
- 隐式偏好观察
- preference candidate 生成
- stable preference 沉淀
- preference lookup 召回

### 4.6 作用域隔离

考察不同项目、不同用户、不同任务之间是否会串记忆。

重点验证：

- project scope
- user scope
- task scope
- cross-project noise isolation

### 4.7 抗干扰能力

考察在大量噪声记忆存在时，系统是否仍能召回正确结果。

这个维度对应真实飞书群聊环境，因为项目群里会混入大量闲聊、重复消息、过期状态和弱相关内容。

### 4.8 规模与性能

考察数据量增加后的写入和召回表现。

指标包括：

- write latency
- retrieval latency
- 100 / 500 / 1,000 规模下的召回稳定性
- context precision

### 4.9 Agent 任务闭环

考察 AI 是否能在完整任务中真正利用记忆，而不是只完成一次检索。

例如：

- 给定历史上下文后完成项目总结
- 根据历史决策生成下一步行动
- 根据偏好调整输出格式
- 根据风险记忆生成提醒

### 4.10 项目管理业务价值

考察项目是否真正服务于项目经理和企业协作。

Track M 重点验证：

- 项目总结完整性
- 关键决策追踪
- 风险识别
- 后续行动建议
- 输入减少率
- 操作步骤减少率

## 5. Track 设计

| Track | 名称 | 主要考察内容 |
|---|---|---|
| A | Dialogue Memory | 对话记忆和上下文恢复 |
| B | Task Decision | 任务决策和项目结论 |
| C | Preference Learning | 偏好学习和稳定偏好 |
| D | Structured Memory Advantage | 结构化记忆相对普通检索的优势 |
| E | Event-Centric Temporal Reasoning | 事件时间推理 |
| F | Workflow Reflection And Reuse | 工作流反思与复用 |
| G | Memory Governance | 记忆治理、晋升、冲突和审计 |
| H | Long-Horizon Self Improvement | 长程自我改进 |
| I | Agent Memory Eval Dataset MVP | 评测数据集基础能力 |
| J | Retrieval Quality | 检索质量 |
| K | Scale Benchmark | 规模和性能 |
| L | Agent Task Benchmark | agent 端到端任务 |
| M | Project Management Business Value | 项目管理业务价值 |

## 6. 历史全量压力测试结果

历史全量压力测试共设计：

```text
5,348 cases
```

整体结果：

```text
Passed: 4,296
Failed: 1,052
Pass Rate: 80.3%
```

Track 级结果：

| Track | Cases | Passed | Pass Rate |
|---|---:|---:|---:|
| A | 12 | 12 | 100% |
| B | 6 | 6 | 100% |
| C | 11 | 11 | 100% |
| D | 7 | 7 | 100% |
| E | 4 | 4 | 100% |
| F | 11 | 11 | 100% |
| G | 4 | 4 | 100% |
| H | 8 | 8 | 100% |
| I | 30 | 30 | 100% |
| J hand-crafted | 30 | 30 | 100% |
| J-gen | 5,030 | 4,080 | 81.1% |
| K | 45 | 45 | 100% |
| L | 150 | 48 | 32.0% |

这个结果说明：基础手写轨道整体稳定，但大规模生成评测和 agent 任务评测暴露了真实短板。

## 7. 历史报告暴露出的不足

### 7.1 零结果拒绝失败

历史压力报告中：

```text
zero_result: 0/500 passed
```

问题含义：

当用户询问不存在的话题时，系统仍然返回结果。这说明召回层缺少足够严格的零结果判断，存在记忆幻觉风险。

影响：

- AI 可能基于无关记忆编造答案。
- 项目经理可能误以为系统找到了依据。
- 企业场景中可能造成错误判断。

### 7.2 偏好召回失败

历史压力报告中：

```text
preference_recall: 0/150 passed
```

问题含义：

系统无法稳定召回用户当前偏好。即使偏好存在，也可能因为排序、标签、作用域或 stale preference 干扰而无法命中。

影响：

- AI 无法稳定遵守用户习惯。
- 隐式学习成果无法复用。
- 多用户、多项目场景下偏好容易混乱。

### 7.3 决策版本链不足

历史压力报告中：

```text
decision_version: 100/400 passed
Pass Rate: 25%
```

问题含义：

系统无法可靠区分当前决策和已废弃历史版本。

影响：

- 查询“当前方案”时可能返回旧方案。
- 历史决策没有被正确降权或替代。
- 项目管理场景中的版本链可靠性不足。

### 7.4 Agent 任务闭环不足

历史压力报告中：

```text
Track L: 48/150 passed
Pass Rate: 32.0%
```

问题含义：

单次召回正确不代表 agent 能完成完整任务。系统在端到端任务中仍存在上下文组织、任务推理和结果生成稳定性不足。

### 7.5 上下文噪声过多

历史报告中：

```text
over_retrieval_noise = 321
```

问题含义：

系统会返回过多弱相关记忆，导致上下文污染。

影响：

- 前端记忆卡片过长。
- AI 回答不够聚焦。
- OpenClaw 注入上下文成本增加。
- 用户难以判断哪些记忆是真正依据。

### 7.6 事件链追踪不完整

历史报告中：

```text
event_trace_missing = 100
```

问题含义：

部分记忆缺少完整事件链或 evidence trace。

影响：

- 难以审计记忆来源。
- 难以判断记忆是否可信。
- 不利于企业场景中的可追溯要求。

## 8. 当前优化后的通过情况

针对历史压力测试暴露的问题，当前版本增加了 P0 关键门禁。

当前关键门禁报告：

```text
Case count: 1,248
Result: 1,248/1,248 passed
Failures: 0
```

其中包括：

| 能力 | Case 数 | 当前结果 |
|---|---:|---:|
| 决策版本链 | 400 | 400/400 passed |
| 偏好召回 | 150 | 150/150 passed |
| 零结果拒绝 | 500 | 500/500 passed |
| 手写 A/B/J/L 关键轨道 | 198 | 198/198 passed |

这说明历史报告中最严重的 P0 问题已经进入回归门禁，并在当前版本中通过。

## 9. 当前提交版分片报告

当前提交版为了提高稳定性，采用分片 benchmark 报告。

| 报告 | 覆盖范围 | Case 数 | 结果 |
|---|---|---:|---:|
| `benchmark_report_submit_ready_A_to_J.md` | A-J | 123 | 123/123 passed |
| `benchmark_report_submit_ready_KLM.md` | K/L/M | 215 | 215/215 passed |
| 合计 | A-M 核心提交范围 | 338 | 338/338 passed |

这组结果适合用于演示、答辩和最终提交，证明当前版本的核心功能处于可交付状态。

## 10. Benchmark 如何驱动项目优化

Benchmark 暴露问题后，项目优化方向变得非常明确。

| 暴露问题 | 对应优化方向 |
|---|---|
| 零结果拒绝 0% | 增加最小相关性阈值、低分拒答、空结果占位 |
| 偏好召回 0% | 强化 preference tag、preference_lookup intent、稳定偏好治理 |
| 决策版本链 25% | 强化 superseded、replaces_memory_id、当前版本优先 |
| Track L 32% | 加强 agent task 上下文组装和端到端任务评估 |
| over_retrieval_noise | 增加过滤、MMR、多样性控制、摘要化记忆卡片 |
| event_trace_missing | 补强 events、event_entries、evidence 和 content_hash |

这些优化不是凭经验猜测，而是由失败 case 直接驱动。

## 11. 项目通过情况总结

| 阶段 | 结果 | 说明 |
|---|---|---|
| 历史全量压力测试 | 4,296/5,348，80.3% | 暴露真实缺陷 |
| 当前 P0 关键门禁 | 1,248/1,248，100% | 验证关键风险修复 |
| 当前提交版核心轨道 | 338/338，100% | 支撑演示和提交 |
| Track M 业务价值 | 20/20，100% | 证明项目管理场景可用 |

## 12. 可用于答辩的表述

我们为项目设计了一个多轨道 benchmark 体系，覆盖 5,348 个评测用例，不只是验证 demo 能不能跑，而是从记忆写入、召回准确性、零结果拒绝、决策版本链、偏好学习、作用域隔离、抗干扰能力、agent 任务闭环和项目管理业务价值等多个维度考察系统。

历史全量压力测试中，系统通过 4,296 个 case，整体通过率 80.3%，同时暴露出 1,052 个失败 case。最严重的问题包括零结果拒绝 0%、偏好召回 0%、决策版本链 25%、Track L agent 任务 32%。这些失败没有被隐藏，也没有降低断言，而是作为真实缺陷进入优化计划。

经过针对性优化后，我们把最关键风险重新纳入 P0 benchmark gate，包括 500 个零结果拒绝 case、150 个偏好召回 case、400 个决策版本链 case 和 agent task recall。当前关键门禁报告显示 1,248/1,248 passed，提交版 A-J 与 K/L/M 分片报告合计 338/338 passed。也就是说，benchmark 不只是展示成果，还真实驱动了项目从“能跑”变成“可验证、可迭代、可交付”。

## 13. 最终结论

Benchmark 是本项目的工程质量中枢。它既证明了当前系统已经具备可演示、可提交的核心能力，也保留了历史压力测试中暴露出的真实不足。

本项目 benchmark 的价值不在于单次通过率，而在于建立了一套持续评估记忆系统质量的标准：每一次新增功能、优化排序、修改治理逻辑，都可以通过 benchmark 验证是否真的提升了系统能力。
