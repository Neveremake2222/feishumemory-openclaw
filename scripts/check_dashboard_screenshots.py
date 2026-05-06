from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "tests_runtime" / "dashboard_visual_check.sqlite3"
DEFAULT_OUTPUT_DIR = ROOT / "benchmarks_runtime" / "dashboard_screenshots"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run browser-level screenshot checks for dashboard/index.html.")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path for seeded demo data.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for screenshots and report.")
    parser.add_argument("--keep-running", action="store_true", help="Keep local servers running after checks.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = Path(args.db)

    api_port = _free_port()
    dashboard_port = _free_port()
    processes: list[subprocess.Popen] = []
    try:
        _seed_demo_db(db_path)
        processes.append(_start_api(db_path, api_port, output_dir))
        processes.append(_start_dashboard(dashboard_port, output_dir))
        _wait_http(f"http://127.0.0.1:{api_port}/health")
        _wait_http(f"http://127.0.0.1:{dashboard_port}/")

        report = _run_browser_checks(
            api_port=api_port,
            dashboard_port=dashboard_port,
            output_dir=output_dir,
        )
        report_path = output_dir / "dashboard_visual_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "status": "passed" if report["passed"] else "failed",
            "report": str(report_path),
            "screenshots": [item["screenshot"] for item in report["viewports"]],
        }, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1
    finally:
        if not args.keep_running:
            for process in processes:
                _stop_process(process)


def _seed_demo_db(db_path: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "seed_demo_project.py"),
            "--db",
            str(db_path),
            "--reset",
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


def _start_api(db_path: Path, port: int, output_dir: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["MEMORY_ENGINE_DB"] = str(db_path)
    log = (output_dir / "api.log").open("w", encoding="utf-8")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "openclaw_adapter.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def _start_dashboard(port: int, output_dir: Path) -> subprocess.Popen:
    log = (output_dir / "dashboard.log").open("w", encoding="utf-8")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "http.server",
            str(port),
            "--bind",
            "127.0.0.1",
        ],
        cwd=ROOT / "dashboard",
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def _run_browser_checks(api_port: int, dashboard_port: int, output_dir: Path) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    chrome_path = _browser_executable()
    url = f"http://127.0.0.1:{dashboard_port}/index.html"
    viewports = [
        {"name": "desktop", "width": 1440, "height": 1000},
        {"name": "mobile", "width": 390, "height": 844},
    ]
    viewport_reports: list[dict[str, Any]] = []
    all_errors: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=str(chrome_path))
        try:
            for viewport in viewports:
                page = browser.new_page(viewport={"width": viewport["width"], "height": viewport["height"]})
                console_errors: list[str] = []
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                page.on("pageerror", lambda exc: console_errors.append(str(exc)))
                page.add_init_script(f"window.API_BASE = 'http://127.0.0.1:{api_port}';")
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.wait_for_selector("#metrics .metric", timeout=30000)
                action_card_counts: list[int] = []
                question_actions = page.locator("[data-question]")
                for action_index in range(question_actions.count()):
                    page.evaluate("document.querySelector('#answer').innerHTML = ''")
                    question_actions.nth(action_index).click()
                    page.wait_for_selector(".answer-card", timeout=30000)
                    action_card_counts.append(page.locator(".answer-card").count())
                page.evaluate("document.querySelector('#answer').innerHTML = ''")
                page.locator("[data-draft]").click()
                page.wait_for_selector(".answer-card", timeout=30000)
                action_card_counts.append(page.locator(".answer-card").count())
                detail_hidden_before_click = page.locator(".answer-card-detail").first.is_hidden()
                page.locator(".answer-card").first.click()
                page.wait_for_selector(".answer-card.expanded .answer-card-detail", timeout=10000)
                page.wait_for_timeout(500)

                screenshot_path = output_dir / f"{viewport['name']}.png"
                page.screenshot(path=str(screenshot_path), full_page=True)
                checks = page.evaluate(
                    """
                    () => {
                      const rect = (selector) => {
                        const el = document.querySelector(selector);
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        return {x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom};
                      };
                      const boxes = [...document.querySelectorAll('.topbar, .shell > .panel')]
                        .map((el, index) => {
                          const r = el.getBoundingClientRect();
                          return {index, cls: el.className, x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom};
                        })
                        .filter(r => r.width > 1 && r.height > 1);
                      const overlaps = [];
                      for (let i = 0; i < boxes.length; i++) {
                        for (let j = i + 1; j < boxes.length; j++) {
                          const a = boxes[i], b = boxes[j];
                          const x = Math.max(0, Math.min(a.right, b.right) - Math.max(a.x, b.x));
                          const y = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.y, b.y));
                          if (x * y > 4) overlaps.push({a: a.cls, b: b.cls, area: x * y});
                        }
                      }
                      const metricTexts = [...document.querySelectorAll('#metrics .metric')].map(el => el.innerText);
                      return {
                        title: document.querySelector('#projectTitle')?.innerText || '',
                        projectCount: document.querySelectorAll('.project').length,
                        metricCount: document.querySelectorAll('#metrics .metric').length,
                        baselineMetricCount: metricTexts.filter(text => text.includes('memory')).length,
                        memoryCount: document.querySelectorAll('.memory').length,
                        activeActionCount: document.querySelectorAll('.actions button.active').length,
                        answerCardCount: document.querySelectorAll('.answer-card').length,
                        expandedAnswerCardCount: document.querySelectorAll('.answer-card.expanded').length,
                        answerDetailText: document.querySelector('.answer-card.expanded .answer-card-detail')?.innerText || '',
                        hasAnswerPanel: Boolean(document.querySelector('#answer')),
                        scrollWidth: document.documentElement.scrollWidth,
                        clientWidth: document.documentElement.clientWidth,
                        overlaps,
                        rects: {
                          topbar: rect('.topbar'),
                          shell: rect('.shell'),
                          metrics: rect('#metrics'),
                          answer: rect('#answer')
                        }
                      };
                    }
                    """
                )
                checks["detailHiddenBeforeClick"] = detail_hidden_before_click
                checks["quickActionCardCounts"] = action_card_counts
                errors = _validate_viewport(viewport["name"], checks, console_errors)
                all_errors.extend(errors)
                viewport_reports.append({
                    **viewport,
                    "screenshot": str(screenshot_path),
                    "checks": checks,
                    "console_errors": console_errors,
                    "errors": errors,
                    "passed": not errors,
                })
                page.close()
        finally:
            browser.close()

    return {
        "passed": not all_errors,
        "errors": all_errors,
        "viewports": viewport_reports,
    }


def _validate_viewport(name: str, checks: dict[str, Any], console_errors: list[str]) -> list[str]:
    errors: list[str] = []
    if console_errors:
        errors.append(f"{name}: browser console errors: {console_errors}")
    if checks["projectCount"] < 1:
        errors.append(f"{name}: project list did not render")
    if checks["metricCount"] < 9:
        errors.append(f"{name}: expected at least 9 metric cards, got {checks['metricCount']}")
    if checks["baselineMetricCount"] < 3:
        errors.append(f"{name}: baseline metric cards did not render")
    if checks["memoryCount"] < 1:
        errors.append(f"{name}: timeline memories did not render")
    if checks["activeActionCount"] != 1:
        errors.append(f"{name}: expected exactly one active quick-action button, got {checks['activeActionCount']}")
    if checks["answerCardCount"] < 1:
        errors.append(f"{name}: AI answer memory cards did not render")
    quick_action_counts = checks.get("quickActionCardCounts") or []
    if len(quick_action_counts) != 4 or any(count < 1 for count in quick_action_counts):
        errors.append(f"{name}: expected all 4 quick actions to render memory cards, got {quick_action_counts}")
    if checks["expandedAnswerCardCount"] != 1:
        errors.append(f"{name}: expected one expanded answer card after click, got {checks['expandedAnswerCardCount']}")
    if not checks.get("detailHiddenBeforeClick"):
        errors.append(f"{name}: answer card detail should be hidden before user click")
    detail_text = checks.get("answerDetailText") or ""
    if "出处" not in detail_text or "时间" not in detail_text or "记录" not in detail_text:
        errors.append(f"{name}: expanded answer card detail missing source/time/record fields")
    if not checks["hasAnswerPanel"]:
        errors.append(f"{name}: answer panel missing")
    if checks["scrollWidth"] > checks["clientWidth"] + 2:
        errors.append(f"{name}: horizontal overflow {checks['scrollWidth']} > {checks['clientWidth']}")
    if checks["overlaps"]:
        errors.append(f"{name}: top-level layout overlaps detected: {checks['overlaps']}")
    return errors


def _browser_executable() -> Path:
    candidates = [
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No Chrome or Edge executable found.")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status < 500:
                    return
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for {url}: {last_error}")


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
