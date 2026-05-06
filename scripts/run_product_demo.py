"""Start the product-shell demo API and dashboard.

Usage:
    python scripts/run_product_demo.py
    python scripts/run_product_demo.py --db tests_runtime/product_demo.sqlite3 --api-port 8000 --dashboard-port 8080
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "tests_runtime" / "product_demo.sqlite3"
DEFAULT_LOG_DIR = ROOT / "tests_runtime" / "product_demo_logs"


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed and start the product demo API/dashboard.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite demo DB path.")
    parser.add_argument("--api-port", type=int, default=8000, help="FastAPI port.")
    parser.add_argument("--dashboard-port", type=int, default=8080, help="Static dashboard port.")
    parser.add_argument("--no-seed", action="store_true", help="Skip DB seeding before startup.")
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR), help="Directory for server logs.")
    args = parser.parse_args()

    db_path = Path(args.db)
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    if not args.no_seed:
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "seed_demo_project.py"),
                "--db",
                str(db_path),
            ],
            cwd=ROOT,
            check=True,
        )

    env = os.environ.copy()
    env["MEMORY_ENGINE_DB"] = str(db_path)
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    api_log = (log_dir / "api.log").open("a", encoding="utf-8")
    dashboard_log = (log_dir / "dashboard.log").open("a", encoding="utf-8")

    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "openclaw_adapter.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.api_port),
        ],
        cwd=ROOT,
        env=env,
        stdout=api_log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    dashboard = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "http.server",
            str(args.dashboard_port),
            "--bind",
            "127.0.0.1",
        ],
        cwd=ROOT / "dashboard",
        stdout=dashboard_log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )

    print(f"API: http://127.0.0.1:{args.api_port} pid={api.pid}")
    print(f"Dashboard: http://127.0.0.1:{args.dashboard_port} pid={dashboard.pid}")
    print(f"DB: {db_path}")
    print(f"Logs: {log_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
