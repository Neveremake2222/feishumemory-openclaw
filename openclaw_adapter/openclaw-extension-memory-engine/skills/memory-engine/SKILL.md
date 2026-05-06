---
name: memory-engine
description: Structured memory recall and write for decisions, task status, and preferences. 记忆引擎召回和写入。When the user asks about past decisions, completed tasks, preferences, or relevant context — or when the agent makes a decision that should be remembered.
user-invocable: false
---

# memory-engine — Structured Memory Adapter

本工具将 OpenClaw 与结构化记忆引擎（memory-engine）对接，实现决策/任务状态/偏好的召回和持久化。

## 何时使用（自动触发）

满足以下任一条件时，使用本工具：

1. **Recall 触发**：用户询问关于过去的决策、已完成的任务、个人偏好、相关上下文
   - "我上次决定是什么"
   - "之前关于 XX 的结论"
   - "我偏好用 XX"
   - "上次做 XX 是什么时候"
2. **Write 触发**：用户在对话中做出了明确的决定、偏好表达，或任务完成
   - 用户说"决定/采用/选择 XX"
   - 用户说"以后/默认/偏好 XX"
   - 工具执行后显示完成（"passed", "success", "通过", "完成"）

## API 地址

```
http://localhost:8000
```

> 注意：API 服务必须先启动：
> `cd ~/workspace/agent/feishumemory && python3 -m uvicorn openclaw_adapter.api:app --host 0.0.0.0 --port 8000 &`

## Recall — 召回记忆

当需要召回相关记忆时，执行以下命令：

```bash
curl -s -X POST http://localhost:8000/recall \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"<用户的问题或关键词>\",\"limit\":5,\"project_id\":\"feishu_openclaw_memory\"}"
```

> **注意**：`project_id` 是可选的。如果不传，系统会自动从工作目录解析项目。
> 但为了确保召回准确性，建议显式传入 `project_id`。

返回格式为 Markdown 片段，示例：

````
## External Memory
- [DECISION] 使用 SQLite 作为存储方案
  决定用 SQLite 作为 MVP 存储方案
  Evidence: openclaw_outcome:session:demo1
- [PREFERENCE] 偏好 Markdown 文件记忆
  用户表示更喜欢文件型记忆方式
  Evidence: openclaw_outcome:session:demo2
````

**将返回的 Markdown 片段插入上下文中，帮助回答用户问题。**

## Write — 写入记忆

当用户做出决策或表达偏好时，写入记忆：

### 决策写入

```bash
curl -s -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d "{\"user_message\":\"<用户的原始决策表达>\",\"project_id\":\"feishu_openclaw_memory\",\"timestamp\":\"<ISO时间>\",\"session_id\":\"<当前会话ID>\"}"
```

### 偏好写入

```bash
curl -s -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d "{\"user_message\":\"<用户的偏好表达>\",\"project_id\":\"feishu_openclaw_memory\",\"timestamp\":\"<ISO时间>\",\"session_id\":\"<当前会话ID>\"}"
```

返回示例：
```json
{"action":"write","written":true,"memory_ids":[3],"skip_reason":null,"conflict_detected":false}
```

## 注意事项

1. **确认 API 运行**：首次使用时，如果 curl 返回连接错误，先启动 API 服务
2. **不要重复写入**：相同 session 内短时间内的相同内容会被自动去重
3. **证据溯源**：所有写入的记忆都带有 source_ref，便于后续召回
4. **Fail-open**：如果 API 不可用，继续正常工作，不阻塞主流程

## 演示测试

```bash
# 测试 API 连通性
curl http://localhost:8000/health

# 召回测试（带项目过滤）
curl -s -X POST http://localhost:8000/recall \
  -H "Content-Type: application/json" \
  -d '{"query":"测试记忆","limit":3,"project_id":"feishu_openclaw_memory"}'

# 写入测试
curl -s -X POST http://localhost:8000/write \
  -H "Content-Type: application/json" \
  -d '{"user_message":"决定用 memory-engine 管理结构化记忆","project_id":"feishu_openclaw_memory","timestamp":"2026-05-01T20:00:00+08:00","session_id":"test"}'
```
