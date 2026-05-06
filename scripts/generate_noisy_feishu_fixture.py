"""Generate a noisy Feishu JSONL fixture with real messages mixed into chatter."""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


NOISE_POOL = [
    "今天下午茶到了。",
    "会议室投影仪好像坏了。",
    "周五午餐订餐名单还没收齐。",
    "仓库门口快递到了，麻烦去取一下。",
    "大家先别急，等我看一下。",
    "刚才那个话题先放一下，后面再看。",
    "楼下咖啡机没水了。",
    "有人知道明天谁值班吗？",
    "这个文件先放到共享盘里。",
    "先别动，等产品同学看完。",
    "晚上八点再拉一次同步会。",
    "这个话题先留个 TODO。",
    "今天还是按原节奏推进。",
    "先处理手头的事情。",
    "我发一下截图。",
    "这个点大家都留意一下。",
    "有空的人看一下这个消息。",
    "先收个口径。",
    "下午再对一下。",
    "如果没问题就这样。",
    "有人带充电器吗？",
    "这个表情包太好笑了。",
    "明天可能会下雨，带伞。",
    "群公告我稍后整理。",
]

REAL_PROJECT_BLOCKS = [
    {
        "source_ref": "ticket_day01_0930_pm_intro",
        "timestamp": "2026-05-05T09:30:00+08:00",
        "content": "本群为【飞书智能工单管理系统 V1.0】专属项目群，30 天完成开发、测试、上线全流程，核心目标是完成飞书生态内的智能工单系统开发，支持飞书消息事件回调、工单自动流转、卡片交互、数据统计四大模块。",
    },
    {
        "source_ref": "ticket_day10_ws_decision",
        "timestamp": "2026-05-14T16:10:00+08:00",
        "content": "问题解决了！官方提供长连接模式（WebSocket），决定用它替代公网 HTTP 回调方案，本地电脑也能接收飞书消息事件和卡片回调。",
    },
    {
        "source_ref": "ticket_day11_ws_demo_done",
        "timestamp": "2026-05-15T09:00:00+08:00",
        "content": "飞书长连接模式 demo 已经跑通了，本地电脑直接建立 WebSocket 连接，成功接收到飞书的消息事件和卡片回调。",
    },
    {
        "source_ref": "ticket_day17_smoke_bug_report",
        "timestamp": "2026-05-21T11:50:00+08:00",
        "content": "第一轮冒烟测试结果已同步到飞书文档，一共提了 32 个 bug，其中 P0 级 2 个，P1 级 8 个，P2 级 22 个，请优先修复高优先级 bug。",
    },
    {
        "source_ref": "ticket_day18_card_callback_fix",
        "timestamp": "2026-05-22T19:30:00+08:00",
        "content": "飞书卡片按钮回调接口问题已解决，原因是长连接的事件参数格式和 HTTP 回调有细微差异。",
    },
    {
        "source_ref": "ticket_day29_launch_success",
        "timestamp": "2026-06-02T11:30:00+08:00",
        "content": "飞书智能工单管理系统 V1.0 正式全量上线成功，正式环境冒烟测试完成，核心流程全部跑通，没有发现任何问题。",
    },
]


@dataclass
class GeneratedEvent:
    source_type: str
    source_ref: str
    source_url: str
    actors: list[str]
    timestamp: str
    content: str
    scope: str
    project_id: str
    task_id: str | None
    user_id: str
    payload: dict
    source_version: str = "noisy-fixture-v1"

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)


def build_fixture(noise_per_real: int, seed: int) -> list[GeneratedEvent]:
    rng = random.Random(seed)
    events: list[GeneratedEvent] = []
    noise_index = 0

    for block_index, block in enumerate(REAL_PROJECT_BLOCKS):
        for _ in range(noise_per_real):
            noise = f"{rng.choice(NOISE_POOL)}（闲聊 {noise_index}）"
            events.append(
                GeneratedEvent(
                    source_type="message",
                    source_ref=f"noise_{block_index:02d}_{noise_index:04d}",
                    source_url=f"https://feishu.cn/message/noise_{block_index:02d}_{noise_index:04d}",
                    actors=["ou_noise"],
                    timestamp=block["timestamp"],
                    content=noise,
                    scope="project",
                    project_id="proj_feishu_ticket_v1",
                    task_id=None,
                    user_id="ou_noise",
                    payload={"chat_id": "oc_ticket_project_demo", "chat_title": "飞书智能工单管理系统 V1.0 项目攻坚群", "sender_name": "噪声消息", "sender_role": "群成员", "msg_type": "text"},
                )
            )
            noise_index += 1

        events.append(
            GeneratedEvent(
                source_type="message",
                source_ref=block["source_ref"],
                source_url=f"https://feishu.cn/message/{block['source_ref']}",
                actors=["李想"],
                timestamp=block["timestamp"],
                content=block["content"],
                scope="project",
                project_id="proj_feishu_ticket_v1",
                task_id=None,
                user_id="pm_lixiang",
                payload={"chat_id": "oc_ticket_project_demo", "chat_title": "飞书智能工单管理系统 V1.0 项目攻坚群", "sender_name": "李想", "sender_role": "项目经理", "msg_type": "text"},
            )
        )

    return events


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a noisy Feishu JSONL fixture.")
    parser.add_argument("--output", default="tests/fixtures/feishu_ticket_project_noisy.jsonl")
    parser.add_argument("--noise-per-real", type=int, default=12)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    events = build_fixture(args.noise_per_real, args.seed)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as fh:
        for event in events:
            fh.write(event.to_json())
            fh.write("\n")

    print(f"wrote {len(events)} events to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
