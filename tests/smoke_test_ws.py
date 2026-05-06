"""Live smoke test for LarkWsAdapter.

Usage:
    F:/Anaconda3/python.exe tests/smoke_test_ws.py

Then send a message in your Feishu test group.
"""
import sys
import os

sys.stdout.reconfigure(line_buffering=True)

import lark_oapi as lark
from feishu_ingest.adapters.lark_ws import LarkWsAdapter

app_secret = os.environ.get("LARK_APP_SECRET")
if not app_secret:
    raise SystemExit("LARK_APP_SECRET is required for live smoke test")

adapter = LarkWsAdapter(
    app_id=os.environ.get("LARK_APP_ID", "cli_example_app_id"),
    app_secret=app_secret,
    log_level=lark.LogLevel.WARNING,
    auto_reconnect=False,
)

print("Listening for Feishu messages (90s timeout)...", flush=True)
print("Send a message in your test group now!", flush=True)
print(flush=True)

try:
    events = list(adapter.stream_events())
    if events:
        print(f"RECEIVED {len(events)} EVENT(S):", flush=True)
        for e in events:
            print(f"  source_ref : {e.source_ref}", flush=True)
            print(f"  chat_id    : {e.payload.get('chat_id')}", flush=True)
            print(f"  sender     : {e.user_id}", flush=True)
            print(f"  content    : {e.content[:80]}", flush=True)
            print(f"  timestamp  : {e.timestamp}", flush=True)
            print(f"  scope      : {e.scope}", flush=True)
            print(flush=True)
        print("SMOKE TEST PASSED", flush=True)
    else:
        print("No events received in 90s", flush=True)
finally:
    adapter.close()
    print("Adapter closed.", flush=True)
