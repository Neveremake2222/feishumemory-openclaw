# feishumemory-openclaw

Structured memory engine for Feishu/Lark collaboration and coding-agent workflows.

The project captures decisions, task status, preferences, workflow traces, and governance signals from conversations or local agent runs, stores them in SQLite, and recalls ranked evidence for later work.

## What It Does

- **Memory engine**: SQLite-backed write, recall, update, promotion, demotion, compaction, review, governance voting, workflow reflection, and event-centric memory.
- **Feishu ingest**: Fixture replay, lark-cli adapters, live event adapters, and a daemon-first Lark/Feishu WebSocket runtime.
- **OpenClaw adapter**: Passive local API/CLI adapter for explicit recall/write calls. Hosted OpenClaw/Miaoda auto-execution is not assumed.
- **Benchmarks**: Tracks A-I, baseline modes, dataset export, and markdown report generation.
- **Tests**: Local pytest suite plus deterministic benchmark runner.

## Current Runtime Boundary

- The active Feishu-side runtime is the local `feishu_ingest.lark_ws_ingest_daemon` process.
- Running in Feishu requires the local daemon to stay online unless you deploy an equivalent always-on service.
- The Feishu desktop/mobile client is only needed for live user interaction and rehearsal; it is not the process that runs the memory engine.
- The WebSocket mode uses the Lark/Feishu SDK long-connection adapter (`LarkWsAdapter`).
- `openclaw_adapter/`, `TOOLS.md`, and `curl` examples are explicit local operator/API paths, not proof that a hosted agent automatically calls local tools.

## Module Structure

```text
memory_engine/          Core structured memory engine and governance logic
feishu_ingest/          Feishu/Lark source adapters and WebSocket daemon
openclaw_adapter/       Passive local API/CLI integration surface
benchmarks/             Benchmark tracks, baseline comparison, export, report
scripts/                Local operator/demo helpers
tests/                  Pytest suite
docs/                   Implementation plans, design notes, governance docs
```

## Quick Start

```bash
pip install -r requirements.txt

python -m pytest tests -q
python -m benchmarks.runner

python -m benchmarks.export_dataset benchmarks_runtime/benchmark_cases.jsonl
python -m benchmarks.report benchmarks_runtime/benchmark_report.md --no-baselines
```

## Live Feishu WebSocket Setup

Create a local `.env` from `.env.example` or set the variables in your shell. Do not hardcode Feishu app secrets in source files.

```bash
export LARK_APP_ID=cli_xxx
export LARK_APP_SECRET=replace_me_local_secret
export MEMORY_ENGINE_DB=memory_engine.sqlite3

python -m feishu_ingest.lark_ws_ingest_daemon
```

For Windows PowerShell:

```powershell
$env:LARK_APP_ID="cli_xxx"
$env:LARK_APP_SECRET="replace_me_local_secret"
$env:MEMORY_ENGINE_DB="memory_engine.sqlite3"

python -m feishu_ingest.lark_ws_ingest_daemon
```

## Optional lark-cli Setup

The lark-cli path is optional and separate from the native WebSocket daemon.

```bash
npm install -g @larksuite/cli
lark-cli config init --new
lark-cli auth status
```

## Useful Docs

- `docs/whitepaper.md`
- `docs/椤圭洰灞曠ず鑴氭湰.md`
- `docs/鏍稿績浠ｇ爜灞曠ず娓呭崟.md`
- `docs/椤圭洰浜偣涓庤瘎鍒嗙淮搴﹀鐓?md`
- `docs/鏈€缁堟敼杩?md`
- `docs/鏄庢棩鎻愪氦娓呭崟.md`

## Requirements

- Python 3.10+
- SQLite, via the Python standard library
- `lark-oapi` for native WebSocket ingest
- Node.js 18+ only if you use the optional lark-cli adapter
