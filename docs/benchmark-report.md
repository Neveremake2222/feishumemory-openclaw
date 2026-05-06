# 自证评测报告 (Benchmark Report)

> 项目：面向企业协作场景的结构化智能体记忆系统
> 日期：2026-05-05
> 测试环境：Python 3.14, Windows 11, SQLite, 本地磁盘

## 0. 最新核心指标

### 0.1 业务收益指标（新增 Track M）

| 业务指标 | 结果 | 说明 |
|---|---:|---|
| Track M 单独验证 | 7/7 passed | 覆盖项目总结、决策追溯、风险识别、跟进消息、项目交接 |
| 项目摘要完整度 | 100% | 方案、进度、风险均进入召回结果 |
| 历史决策追溯准确率 | 100% | 当前方案命中，旧方案被排除 |
| 风险识别召回率 | 100% | 标注风险命中，普通进展噪声排除 |
| 跟进消息事实一致性 | 100% | 生成草稿所需事实来自记忆 |
| 输入字数节省 | 81% | 从完整背景描述降为短查询 |
| 操作步骤节省 | 80% | 从人工翻记录降为一次记忆查询 |

### 0.2 技术指标

| 指标 | 结果 |
|------|------|
| 单元/集成测试 | 345 passed, 3 skipped |
| Benchmark 总用例 | 70/70 passed (Track A-I) |
| 提取准确率 | 100% (30/30, F1=1.00) |
| 推送误报率 | 6.5% (2/31) |
| 抗干扰 Top-1/Top-3 | 100% / 100% (500 条噪声) |
| 召回性能 100 条 | P50=11.37ms, P95=12.34ms |
| 召回性能 1000 条 | P50=44.85ms, P95=53.11ms |

---

## 1. 抗干扰测试（测试1）

**目标**：在大量无关对话/操作后，系统依然能精准召回关键记忆。

**方法**：注入 1 条关键决策 + N 条噪声记忆，使用 3 条中文查询测试 Top-1/Top-3 命中率，每级别运行 5 次取 P50/P95。

**关键记忆**：`项目决定使用 SQLite + BM25 做轻量记忆检索` (importance=0.9, confidence=0.9, 1h ago)

**结果**：

| 干扰量 | Top-1 命中率 | Top-3 命中率 | P50 (ms) | P95 (ms) |
|--------|------------|------------|----------|----------|
| 50     | 100%       | 100%       | 9.62     | 19.72    |
| 100    | 100%       | 100%       | 9.82     | 10.79    |
| 500    | 100%       | 100%       | 10.35    | 11.89    |

**结论**：关键记忆在 500 条噪声干扰下仍保持 100% Top-1 命中，延迟稳定在 10ms 量级。多信号加权（BM25 + freshness + importance + confidence）抗干扰能力显著优于纯关键词搜索。

---

## 2. 矛盾更新测试（测试2）

**目标**：证明系统能处理矛盾决策，自动 supersede 旧记忆。

**方法**：写入 "周报发给 A" → 写入 "周报发给 B" → 查询当前状态。

**结果**：

| 项目 | 期望 | 实际 |
|------|------|------|
| recall 返回记忆 | 只包含 B | PASS — 仅返回 B |
| 旧记忆(A)状态 | superseded | PASS — status=superseded |
| 新记忆(B)状态 | active | PASS — status=active |
| 冲突类型 | fact_override | PASS — fact_override |
| audit_log | 有更新/冲突记录 | PASS — 4 条审计记录 |

**结论**：系统自动检测 fact_override 冲突，将旧记忆标记为 superseded，新记忆保持 active。查询只返回当前有效记忆。

---

## 3. 效能指标验证（测试3）

**目标**：量化使用记忆系统前后的操作成本差异。

| 场景 | 使用前 | 使用后 | 提效方式 |
|------|--------|--------|---------|
| 找项目检索方案 | 搜飞书 + 翻记录 + 复制上下文 (4步) | 直接问 OpenClaw (1步) | 操作步数 ↓75% |
| 让 AI 遵循个人偏好 | 每次重复描述偏好 (约100字/次) | 自动召回 preference (0字) | 输入字符 ↓100% |
| 查当前周报接收人 | 搜旧消息并人工判断新旧 (3步) | recall 返回当前有效记忆 (1步) | 判断成本 ↓67% |

**示例**：

> 以"查项目检索方案"为例：
> 使用前需要 4 步：打开飞书、搜索关键词、筛选消息、复制给 AI。
> 使用后需要 1 步：向 OpenClaw 提问。
> 操作步数从 4 降到 1，下降 75%。

---

## 4. 九轨 Benchmark 结果（Track A-I）

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
─────────────────────────────────
OVERALL: 70/70 passed
```

**各轨覆盖的能力维度**：

| Track | 核心能力 | 用例数 |
|--------|---------|-------|
| A — 对话记忆 | 跨部门协作、历史决策召回、版本链 | 12 |
| B — 任务决策 | 经验复用、跨任务约束传递、风险识别 | 6 |
| C — 偏好学习 | 显式偏好、隐式归纳、跨场景隔离、遗忘 | 11 |
| D — 结构化优势 | 版本替代、作用域隔离、多信号召回 | 7 |
| E — 事件推理 | 事件链重建、跨事件合成 | 4 |
| F — 工作流复用 | 策略合成、失败召回、治理审批 | 11 |
| G — 记忆治理 | L1→L2/L2→L3 晋升与治理投票 | 4 |
| H — 长程改进 | 技能替代、效果评估、稳定性分析 | 5 |
| I — 智能体评估 | 追踪完整性、故障恢复、记忆税守卫 | 10 |

---

## 5. 记忆演化链路验证（Track F/G/H）

### 工作流反思（Track F）
- 多次成功案例 → 策略候选生成
- 多次失败案例 → 技能归档
- 治理投票通过 → 策略确认为技能
- 可通过 recall 精准召回根因

### 记忆晋升与治理（Track G）
- L1 → L2：EvidenceReviewer + PrivacyReviewer + ScopeReviewer 三票通过
- L2 → L3：增加 UtilityReviewer + ConflictReviewer 五票通过
- 隐私风险：任意一票拒绝则晋升被阻止

### 长程自我改进（Track H）
- 旧技能多次失败 → 归档
- 新技能多次成功 → 提升为 improved 状态
- 效果不足 → 保持 not_improved

---

## 6. 召回性能基准

| 规模 | 活跃记忆 | 返回条数 | P50 (ms) | P95 (ms) |
|------|---------|---------|----------|----------|
| 小规模 | 100     | 10      | 11.37    | 12.34    |
| 中规模 | 1000    | 10      | 44.85    | 53.11    |
| 大规模 | 10000   | 10      | —        | —        |

优化路径：数据库索引 → WAL 模式 → 预计算 token_list

---

## 7. 提取准确率

**方法**：30 条人工标注消息，评估决策/偏好/任务状态分类

| 类别 | 精确率 | 召回率 | F1 |
|------|--------|--------|-----|
| decision | 1.00 | 1.00 | 1.00 |
| preference | 1.00 | 1.00 | 1.00 |
| task_status | 1.00 | 1.00 | 1.00 |
| **总体** | **1.00** | **1.00** | **1.00** |

---

## 8. 单元/集成测试覆盖

| 模块 | 测试数 |
|------|--------|
| memory_engine | 60+ |
| feishu_ingest | 56+ |
| security / guard | 22+ |
| openclaw_adapter | 22+ |
| project_registry | 26+ |
| benchmark_runner | 7 |
| 其他 | 5+ |
| **合计** | **345 passed, 3 skipped** |

---

## 9. 与竞品对比的优势

| 维度 | mem0 OSS | 本项目 |
|------|---------|--------|
| 冲突处理 | ADD-only，靠排序自然衰减 | 显式冲突检测 + superseded 版本链 |
| 作用域隔离 | 无 | project/user/task/session/organization 五级 |
| 记忆演化 | 无显式晋升 | L1→L2→L3 门控 + 治理投票 |
| 隐私脱敏 | 无 | 写入前自动检测 10 类敏感信息 |
| 全操作审计 | 无 | 每步操作均记录 audit_log |
| 工作流反思 | 无 | 策略合成 + 效果评估 + 自我改进 |
| 事件推理 | 无 | event_entries 表 + cross-event synthesis |

---

## 10. 复现命令

```bash
# 抗干扰测试
python benchmarks/interference_benchmark.py

# 矛盾更新测试
python benchmarks/contradiction_demo.py

# 三段式 Demo
python benchmarks/fixed_demo.py

# 九轨 Benchmark（推荐）
python benchmarks/runner.py

# 提取准确率评测
python benchmarks/evaluation/extraction_eval.py

# 推送触发评测
python benchmarks/evaluation/push_eval.py

# 召回性能基准
python benchmarks/recall_baseline.py

# 作用域隔离基准
python benchmarks/scope_filter_benchmark.py

# 全量评测（生成报告）
python benchmarks/evaluation/run_all.py

# 单元测试
python -m pytest tests -q
```
