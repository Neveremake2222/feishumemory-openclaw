# 隐式学习证明材料

## 1. 测试结论

本次测试已经证明：项目中的隐式习惯学习链路可以从用户连续输入中自动识别重复行为模式，并将其沉淀为可治理、可追溯、可召回的稳定偏好记忆。

本次验证使用的本地数据库为：

```text
tests_runtime/implicit_step_demo.sqlite3
```

最终结果：

```text
candidate_id = 5
stable_preference_id = 6
decision = approve
```

这说明系统完成了以下闭环：

```text
三条用户输入
-> 三条隐式偏好观察
-> 自动聚合为候选偏好 candidate_id=5
-> 多评审治理通过
-> 生成稳定偏好 stable_preference_id=6
-> 通过 preference_lookup 成功召回
```

## 2. 测试输入

本次测试不是直接告诉系统“我喜欢 markdown 输出”，而是连续输入了三条不同场景下的项目消息。三条消息都体现出用户倾向于使用 markdown、table、checklist、bullet list 等结构化输出形式。

| 输入编号 | source_ref | timestamp | 输入内容 |
|---|---|---|---|
| 1 | `test://implicit-step/1` | `2026-05-07T10:00:00+08:00` | For Apollo weekly status, please use markdown table with columns owner progress risk deadline blocker metric decision note. |
| 2 | `test://implicit-step/2` | `2026-05-07T10:05:00+08:00` | For Beta customer handover, please use markdown checklist bullets covering requirement dependency interface acceptance rollback contact budget. |
| 3 | `test://implicit-step/3` | `2026-05-07T10:10:00+08:00` | For Gamma release review, please use markdown bullet list with deployment verification monitoring alert timeline responsibility owner. |

这三条输入的共同特征是：用户在不同项目场景中反复要求结构化输出。系统由此识别出潜在的输出格式偏好。

## 3. 隐式观察阶段

系统首先不会直接生成稳定偏好，而是将每一次行为记录为低置信度的隐式偏好观察。

本次测试中，三条输入被识别为同一种偏好模式：

```text
kind = implicit_preference_observation
preference_kind = output_format
pattern_key = pref.output.structured_format
signal = structured_output_requested
polarity = positive
risk_level = low
```

该阶段的设计意义：

- 单条输入只作为弱信号，不直接修改长期偏好。
- 每条观察都保留来源 `source_ref`、时间和原始片段。
- 使用 `pattern_key = pref.output.structured_format` 将不同说法归一到同一偏好模式。
- 后续 review 可以基于多条观察进行聚合，而不是依赖单次判断。

## 4. 候选偏好生成

当三条同类观察累计后，系统通过 `review()` 自动聚合出候选偏好。

本次测试生成的候选偏好为：

```text
candidate_id = 5
pattern_key = pref.output.structured_format
positive_evidence_count = 3
negative_evidence_count = 0
```

这一步证明：系统不是通过人工配置偏好，而是通过多次行为自动归纳出“用户偏好结构化输出”这一候选习惯。

## 5. 多评审治理确认

随后执行：

```powershell
@'
from memory_engine import MemoryEngine

e = MemoryEngine("tests_runtime/implicit_step_demo.sqlite3")
print(e.confirm_preference_candidate(5, user_id="user_demo"))
e.close()
'@ | python -
```

返回结果显示候选偏好通过治理：

```text
candidate_id = 5
stable_preference_id = 6
decision = approve
reason = implicit preference governance approved
```

多评审治理结果如下：

| Reviewer | Vote | Score | 判断理由 |
|---|---:|---:|---|
| EvidenceReviewer | approve | 1.0 | evidence and source event are traceable |
| PrivacyReviewer | approve | 1.0 | no sensitive marker detected |
| ScopeReviewer | approve | 1.0 | project scope has project_id |
| PreferenceEvidenceReviewer | approve | 0.8 | implicit habit has enough supporting observations: positive=3, negative=0 |
| PreferenceConflictReviewer | approve | 0.8 | no active stable preference conflict found |

治理投票统计：

```text
approve = 5
reject = 0
abstain = 0
quorum = 5
decision = approve
```

这说明稳定偏好不是直接写入，而是经过了证据可追溯性、隐私、项目作用域、证据数量和冲突检查。

## 6. 来源证据链

`EvidenceReviewer` 返回的证据来源为：

```text
test://implicit-step/1
test://implicit-step/2
test://implicit-step/3
```

这证明 `stable_preference_id = 6` 不是凭空生成的，而是明确来自三条独立输入。

三条 evidence 在召回结果中保留了完整片段：

```text
For Apollo weekly status, please use markdown table with columns owner progress risk deadline blocker metric decision note.

For Beta customer handover, please use markdown checklist bullets covering requirement dependency interface acceptance rollback contact budget.

For Gamma release review, please use markdown bullet list with deployment verification monitoring alert timeline responsibility owner.
```

每条证据还包含独立的 `content_hash`，用于证明来源内容可追溯、可校验。

## 7. 稳定偏好写入结果

确认后，系统生成稳定偏好：

```text
id = 6
memory_type = preference
title = Confirmed preference: output_format
kind = stable_preference
preference_kind = output_format
pattern_key = pref.output.structured_format
positive_evidence_count = 3
negative_evidence_count = 0
distinct_project_count = 1
confirmed = true
confidence = 0.75
importance = 0.65
observation_memory_ids = 2,3,4
derived_from_candidate_id = 5
replaces_memory_id = 5
logical_layer = L2
confidence_tier_label = direct_injection
```

关键字段说明：

| 字段 | 含义 |
|---|---|
| `stable_preference` | 表示该偏好已经从观察升级为稳定记忆 |
| `positive_evidence_count = 3` | 表示有三条正向观察支持该习惯 |
| `negative_evidence_count = 0` | 表示没有相反证据 |
| `observation_memory_ids = 2,3,4` | 表示稳定偏好来自三条观察记忆 |
| `derived_from_candidate_id = 5` | 表示稳定偏好由候选偏好升级而来 |
| `confirmed = true` | 表示已经通过治理确认 |
| `confidence = 0.75` | 表示稳定偏好的置信度高于单条观察 |

## 8. 召回验证

确认稳定偏好后，执行召回：

```powershell
@'
from memory_engine import MemoryEngine, RecallRequest
import json

e = MemoryEngine("tests_runtime/implicit_step_demo.sqlite3")
rows = e.recall(
    RecallRequest(
        query="markdown structured output preference",
        user_id="user_demo",
        project_id="proj_implicit_step_demo",
        intent="preference_lookup",
    ),
    limit=5,
)

print(json.dumps(rows, ensure_ascii=False, indent=2))
e.close()
'@ | python -
```

召回结果第一条为：

```text
id = 6
memory_type = preference
title = Confirmed preference: output_format
summary = Aggregated 3 positive and 0 negative observations for pref.output.structured_format
score = 0.875
relevance_raw = 1.2321
status = active
```

这证明稳定偏好已经进入可检索记忆层，后续 AI 助手可以在项目总结、周报、交接、复盘等场景中主动使用该偏好。

## 9. 本次测试证明的能力

| 能力 | 本次证据 |
|---|---|
| 隐式行为识别 | 从三条普通输入中识别出结构化输出偏好 |
| 弱信号沉淀 | 每条输入先成为隐式偏好观察，而不是直接成为稳定记忆 |
| 跨输入聚合 | 三条观察聚合为 `candidate_id = 5` |
| 多评审治理 | 5 个 reviewer 全部 approve |
| 来源可追溯 | evidence_refs 保留三条 `test://implicit-step/*` 来源 |
| 冲突控制 | PreferenceConflictReviewer 确认没有已有稳定偏好冲突 |
| 长期记忆写入 | 生成 `stable_preference_id = 6` |
| 可召回复用 | `preference_lookup` 查询第一条返回稳定偏好，score 为 `0.875` |

## 10. 可提交结论

本次本地测试证明，项目中的隐式学习机制已经跑通完整闭环。系统能够从用户连续三次项目输入中自动发现“偏好 markdown / table / checklist / bullet list 等结构化输出”的隐式习惯，并将其记录为三条可追溯观察记忆。随后，系统通过 review 聚合生成 `candidate_id = 5`，再经过 EvidenceReviewer、PrivacyReviewer、ScopeReviewer、PreferenceEvidenceReviewer、PreferenceConflictReviewer 五个评审角色共同治理，最终确认生成 `stable_preference_id = 6`。

最终召回结果显示，该稳定偏好能够通过 `preference_lookup` 被检索出来，返回分数 `score = 0.875`。因此，本项目的隐式学习不是人工配置，也不是一次性关键词规则，而是一个从行为观察、证据聚合、治理确认到长期召回的完整记忆学习链路。
