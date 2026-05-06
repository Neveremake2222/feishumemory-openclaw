"""Tests for feishu_ingest.reply_triggers."""

from __future__ import annotations

import unittest

from feishu_ingest.reply_triggers import (
    is_operation_trigger,
    is_related_trigger,
    is_summary_trigger,
)


class TestReplyTriggers(unittest.TestCase):

    def test_related_trigger_chinese(self):
        self.assertTrue(is_related_trigger("那个技术方案选型定的啥来着？"))
        self.assertTrue(is_related_trigger("之前我们决定用方案B"))
        self.assertTrue(is_related_trigger("什么来着"))
        self.assertTrue(is_related_trigger("定了方案A"))
        self.assertTrue(is_related_trigger("以前决定用这个方案"))

    def test_related_trigger_english(self):
        self.assertTrue(is_related_trigger("what was decided before?"))
        self.assertTrue(is_related_trigger("which one did we decide on?"))

    def test_related_trigger_not_triggered(self):
        self.assertFalse(is_related_trigger("开始写代码吧"))
        self.assertFalse(is_related_trigger("帮我整理周报"))

    def test_summary_trigger(self):
        self.assertTrue(is_summary_trigger("帮我整理项目当前情况"))
        self.assertTrue(is_summary_trigger("项目状态汇总"))
        self.assertTrue(is_summary_trigger("记忆汇总"))
        self.assertTrue(is_summary_trigger("当前项目有哪些决策"))

    def test_summary_trigger_not_triggered(self):
        self.assertFalse(is_summary_trigger("方案B确定使用"))
        self.assertFalse(is_summary_trigger("开始写代码"))

    def test_operation_trigger(self):
        self.assertTrue(is_operation_trigger("开始写代码"))
        self.assertTrue(is_operation_trigger("开始处理这个任务"))
        self.assertTrue(is_operation_trigger("帮我整理周报"))
        self.assertTrue(is_operation_trigger("规划一下"))

    def test_operation_trigger_not_triggered(self):
        self.assertFalse(is_operation_trigger("方案B确定使用"))
        self.assertFalse(is_operation_trigger("什么来着"))


if __name__ == "__main__":
    unittest.main()
