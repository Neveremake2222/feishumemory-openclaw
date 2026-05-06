# Benchmark 在项目中的作用总结

## 1. 核心结论

本项目的 benchmark 不是普通跑分工具，而是项目质量控制、缺陷暴露、优化验证和成果展示的核心证据。

它回答三个问题：

1. 项目到底设计了多少评测用例？
2. 当前系统通过率是多少？
3. benchmark 暴露出了哪些真实问题？

目前项目形成了两类 benchmark 证据：

| 类型 | 用途 | 数量与结果 |
|---|---|---|
| 历史全量压力快照 | 暴露真实缺陷，指导优化 | 5,348 cases，4,296 passed，整体通过率 80.3% |
| 当前提交版分片报告 | 展示优化后的可交付状态 | A-J 共 123/123 passed，K/L/M 共 215/215 passed |
| 当前关键门禁报告 | 快速验证 P0 风险是否修复 | 1,248/1,248 passed |

因此，benchmark 在本项目中同时承担“问题放大镜”和“交付证明”两个角色。

## 2. Benchmark 的设计数量

### 2.1 全量设计规模

项目中设计并导出的 benchmark 数据集共有：

```text
5,348 test cases
```

这些 case 覆盖多个 track 和能力维度，包括：

- 对话记忆
- 任务决策
- 偏好学习
- 结构化记忆优势
- 事件时间推理
- 工作流反思与复用
- 记忆治理
- 长程自我改进
- 检索质量
- 规模测试
- agent 任务测试
- 项目管理业务价值

其中大规模生成用例主要集中在 Track J/J-gen，用于检索质量、零结果拒绝、版本链、偏好召回、噪声干扰等核心问题。

### 2.2 当前提交版分片报告数量

当前可提交版本拆成两组报告：

| 报告 | 覆盖 Track | Case 数 | 结果 |
|---|---|---:|---|
| `benchmark_report_submit_ready_A_to_J.md` | A-J | 123 | 123/123 passed |
| `benchmark_report_submit_ready_KLM.md` | K/L/M | 215 | 215/215 passed |
| 合计 | A-M 主要提交轨道 | 338 | 338/338 passed |

其中 K/L/M 的分布为：

| Track | Case 数 | 通过率 |
|---|---:|---:|
| K Scale Benchmark | 45 | 100% |
| L Agent Task Benchmark | 150 | 100% |
| M Project Management Business Value | 20 | 100% |

### 2.3 当前关键门禁数量

当前 P0 gate 报告覆盖了最容易出严重问题的能力：

```text
1,248 cases
1,248/1,248 passed
```

关键门禁包含：

| Gate | Case 数 | 结果 |
|---|---:|---:|
| JGEN decision version | 400 | 400/400 passed |
| JGEN preference recall | 150 | 150/150 passed |
| JGEN zero result | 500 | 500/500 passed |
| Track A/B/J/L hand-written | 198 | 198/198 passed |

这说明优化后，曾经最严重的三个问题已经被纳入快速回归门禁。

## 3. 历史全量压力报告通过率

历史全量报告 `BENCHMARK_REPORT.md` 中记录了严格断言下的真实系统表现：

```text
Total Cases: 5,348
Passed: 4,296
Failed: 1,052
Overall Pass Rate: 80.3%
```

Track 级结果如下：

| Track | Name | Cases | Passed | Pass Rate |
|---|---|---:|---:|---:|
| A | Dialogue Memory | 12 | 12 | 100% |
| B | Task Decision | 6 | 6 | 100% |
| C | Preference Learning | 11 | 11 | 100% |
| D | Structured Memory Advantage | 7 | 7 | 100% |
| E | Event-Centric Temporal Reasoning | 4 | 4 | 100% |
| F | Workflow Reflection And Reuse | 11 | 11 | 100% |
| G | Memory Governance | 4 | 4 | 100% |
| H | Long-Horizon Self Improvement | 8 | 8 | 100% |
| I | Agent Memory Eval Dataset MVP | 30 | 30 | 100% |
| J | Retrieval Quality Hand-crafted | 30 | 30 | 100% |
| J-gen | Retrieval Quality Generated | 5,030 | 4,080 | 81.1% |
| K | Scale Benchmark | 45 | 45 | 100% |
| L | Agent Task Benchmark | 150 | 48 | 32.0% |

这个结果说明：手写核心能力用例基本通过，但大规模生成评测和 agent 任务评测暴露出明显短板。

## 4. Benchmark 暴露出的主要不足

历史全量压力报告暴露了 1,052 个失败 case，失败不是为了降低分数，而是为了定位真实缺陷。

失败类型分布如下：

| Failure Type | Count | 暴露的问题 |
|---|---:|---|
| `hallucinated_memory` | 400 | 零结果场景下仍返回记忆，说明系统存在幻觉式召回风险 |
| `over_retrieval_noise` | 321 | 返回过多无关结果，说明上下文边界和结果过滤还不够严格 |
| `missed_recall` | 231 | 应该召回的记忆没有召回，说明排序、标签或作用域信号仍需加强 |
| `event_trace_missing` | 100 | 事件链追踪不完整，说明 evidence/event_entries 记录链路需要补强 |

### 4.1 零结果拒绝不足

历史报告中，零结果能力曾经是最严重问题之一：

```text
zero_result: 0/500 passed
```

这说明当用户询问不存在的话题时，系统仍然可能返回看似相关但实际上不支持回答的记忆。

这个问题的风险是：

- AI 助手会把无关记忆包装成答案。
- 项目经理会误以为系统找到了依据。
- 企业场景中会造成错误决策。

因此，后续优化必须引入更强的最小相关性阈值、零结果判定和拒答机制。

### 4.2 偏好召回不足

历史报告中，偏好召回曾经完全失败：

```text
preference_recall: 0/150 passed
```

这说明系统当时虽然可能写入了 preference，但召回阶段无法稳定把当前偏好排在正确位置。

暴露的问题包括：

- preference tag 信号不够强。
- stale preference 与 current preference 区分不足。
- preference_lookup intent 的排序策略需要单独优化。

这个问题后来也推动了隐式偏好链路的补强：从 `implicit_preference_observation` 到 `preference_candidate`，再到 `stable_preference`，并用多 reviewer 治理确认。

### 4.3 决策版本链不足

历史报告中，决策版本链表现为：

```text
decision_version: 100/400 passed
Pass Rate: 25%
```

这说明系统曾经无法可靠地区分“当前决策”和“已废弃历史版本”。

暴露的问题包括：

- superseded memory 降权不足。
- 当前版本和旧版本在召回排序中混在一起。
- 查询“当前方案”时可能召回旧方案。
- 决策链需要更明确的 `replaces_memory_id`、`status=superseded` 和事件链标记。

这个问题非常关键，因为项目管理场景里“旧决策被新决策替代”是高频情况。

### 4.4 Agent 任务闭环不足

历史报告中，Track L 的通过率为：

```text
48/150 passed
Pass Rate: 32.0%
```

说明早期系统在多步骤 agent 任务中，虽然能召回部分记忆，但不一定能稳定完成完整任务。

暴露的问题包括：

- 多步任务对上下文召回的稳定性要求更高。
- 单次 recall 正确不等于端到端任务完成。
- 需要评估 answer relevancy、memory improvement、context recall 等组合指标。

### 4.5 上下文噪声问题

历史报告中出现：

```text
over_retrieval_noise = 321
```

这说明系统在一些场景中返回了太多“有一点相关但不该进入上下文”的记忆。

这个问题直接影响：

- 前端记忆卡片展示长度
- AI 回答摘要质量
- OpenClaw 上下文注入成本
- 用户对系统可信度的感知

## 5. 优化后的通过率证据

针对历史报告暴露出的关键缺陷，当前提交版报告显示了优化后的结果。

### 5.1 P0 关键门禁

当前关键门禁报告：

```text
critical_benchmark_report_current.md
Case count: 1,248
Result: 1,248/1,248 passed
Failures: 0
```

这个门禁覆盖的正是之前最关键的问题：

- 零结果拒绝
- 偏好召回
- 决策版本链
- agent task recall

### 5.2 提交版分片报告

当前提交版分片报告：

```text
A-J: 123/123 passed
K/L/M: 215/215 passed
Total: 338/338 passed
```

这说明当前可提交版本已经能在核心展示范围内稳定通过。

## 6. Benchmark 对项目的实际作用

### 6.1 证明项目完整性

Benchmark 证明系统不是只有 demo 页面，而是覆盖了：

- 写入
- 抽取
- 治理
- 召回
- 版本处理
- 偏好学习
- 零结果拒绝
- 业务价值

### 6.2 暴露真实缺陷

历史 80.3% 通过率比 100% 更有价值，因为它明确暴露了项目最需要修复的地方：

- 零结果拒绝
- 偏好召回
- 决策版本链
- agent 任务闭环
- 上下文噪声控制

这些失败 case 没有被削弱断言，也没有通过降低标准让数字变好。

### 6.3 验证优化是否有效

后续关键门禁 1,248/1,248 passed 说明，之前暴露的 P0 问题已经被纳入回归测试，并能在当前版本中稳定通过。

### 6.4 支撑答辩和白皮书

Benchmark 给项目展示提供了可量化证据：

- 总评测规模：5,348 cases
- 历史严格通过率：80.3%
- 暴露失败数：1,052
- 当前关键门禁：1,248/1,248
- 当前提交版核心轨道：338/338
- 项目管理业务价值 Track M：20/20

这些数字可以直接用于项目结果展示。

## 7. 可以在答辩中这样表述

我们为项目设计了一个多轨道 benchmark 体系，不是只跑几个 demo case，而是覆盖了 5,348 个评测用例。历史全量压力测试中，系统通过 4,296 个，整体通过率 80.3%，同时暴露出 1,052 个失败 case。最严重的问题包括零结果拒绝 0%、偏好召回 0%、决策版本链 25%、Track L agent 任务 32%。这些失败没有被隐藏，也没有降低断言，而是作为真实缺陷进入优化计划。

经过针对性优化后，我们把最关键的风险重新纳入 P0 benchmark gate，包括 500 个零结果拒绝 case、150 个偏好召回 case、400 个决策版本链 case 和 agent task recall。当前关键门禁报告显示 1,248/1,248 passed，提交版 A-J 与 K/L/M 分片报告合计 338/338 passed。也就是说，benchmark 不只是展示成果，还真实驱动了项目从“能跑”变成“可验证、可迭代、可交付”。

## 8. 最终结论

Benchmark 在本项目中的作用，是用严格测试把记忆系统的真实能力和真实缺陷都暴露出来。它既证明了当前版本可以稳定演示和提交，也保留了历史压力测试中发现的不足，形成了后续优化的方向。

本项目的 benchmark 价值不在于制造一个好看的通过率，而在于建立一套可以持续追踪系统质量的评测标准。
