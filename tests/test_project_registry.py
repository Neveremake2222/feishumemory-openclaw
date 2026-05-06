"""Tests for feishu_ingest.project_registry and scope integration."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory_engine.models import Scope

from feishu_ingest.models import FeishuEvent
from feishu_ingest.project_registry import ProjectRegistry, ProjectRegistryProject
from feishu_ingest.scope import infer_project_id, infer_scope


def _make_event(
    content: str = "test",
    source_type: str = "message",
    chat_id: str | None = None,
    source_ref: str = "msg://test",
    project_id: str | None = None,
    scope: Scope = Scope.USER,
) -> FeishuEvent:
    return FeishuEvent(
        source_type=source_type,
        source_ref=source_ref,
        source_url=None,
        actors=["user1"],
        timestamp="2026-05-02T00:00:00+00:00",
        content=content,
        scope=scope,
        project_id=project_id,
        task_id=None,
        user_id="user1",
        payload={"chat_id": chat_id} if chat_id else {},
        content_hash=None,
        source_version=None,
    )


class TestProjectRegistryLoad(unittest.TestCase):
    """Test loading and validating project_registry.json."""

    def setUp(self):
        self._tmp = Path("tests_runtime") / "registry_test"
        self._tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        ProjectRegistry.reset()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_json(self, data: dict, name: str = "test.json") -> Path:
        p = self._tmp / name
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return p

    def test_load_valid_registry(self):
        p = self._write_json({
            "version": 1,
            "projects": [
                {
                    "project_id": "proj_a",
                    "name": "Project A",
                    "chat_ids": ["oc_123"],
                    "repo_paths": ["/home/user/proj_a"],
                }
            ]
        })
        registry = ProjectRegistry.load(p)
        self.assertEqual(registry.project_for_chat("oc_123"), "proj_a")
        self.assertEqual(registry.get_project("proj_a").name, "Project A")

    def test_load_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            ProjectRegistry.load(self._tmp / "nonexistent.json")

    def test_load_empty_projects_raises(self):
        p = self._write_json({"version": 1, "projects": []})
        with self.assertRaises(ValueError):
            ProjectRegistry.load(p)

    def test_duplicate_chat_id_raises(self):
        p = self._write_json({
            "version": 1,
            "projects": [
                {"project_id": "a", "chat_ids": ["oc_dup"]},
                {"project_id": "b", "chat_ids": ["oc_dup"]},
            ]
        })
        with self.assertRaises(ValueError) as ctx:
            ProjectRegistry.load(p)
        self.assertIn("oc_dup", str(ctx.exception))

    def test_duplicate_workspace_raises(self):
        p = self._write_json({
            "version": 1,
            "projects": [
                {"project_id": "a", "openclaw_workspace_ids": ["ws1"]},
                {"project_id": "b", "openclaw_workspace_ids": ["ws1"]},
            ]
        })
        with self.assertRaises(ValueError) as ctx:
            ProjectRegistry.load(p)
        self.assertIn("ws1", str(ctx.exception))

    def test_duplicate_repo_path_within_same_project_is_allowed(self):
        p = self._write_json({
            "version": 1,
            "projects": [
                {
                    "project_id": "a",
                    "repo_paths": ["C:/workspace/feishumemory", "E:\\feishumemory"],
                },
            ],
        })
        registry = ProjectRegistry.load(p)
        self.assertEqual(registry.project_for_repo_path("C:/workspace/feishumemory"), "a")


class TestProjectRegistryLookup(unittest.TestCase):
    """Test registry lookup methods."""

    def setUp(self):
        self.registry = ProjectRegistry([
            ProjectRegistryProject(
                project_id="proj_alpha",
                name="Project Alpha",
                chat_ids=("oc_chat1", "oc_chat2"),
                doc_ids=("doc_abc",),
                wiki_ids=("wiki_xyz",),
                repo_paths=("C:/workspace/feishumemory", "/workspace/agent/feishumemory"),
                openclaw_workspace_ids=("feishumemory",),
            ),
        ])

    def tearDown(self):
        ProjectRegistry.reset()

    def test_project_for_chat(self):
        self.assertEqual(self.registry.project_for_chat("oc_chat1"), "proj_alpha")
        self.assertIsNone(self.registry.project_for_chat("oc_unknown"))

    def test_project_for_doc(self):
        self.assertEqual(self.registry.project_for_doc("doc_abc"), "proj_alpha")
        self.assertIsNone(self.registry.project_for_doc("doc_unknown"))

    def test_project_for_wiki(self):
        self.assertEqual(self.registry.project_for_wiki("wiki_xyz"), "proj_alpha")

    def test_project_for_workspace(self):
        self.assertEqual(self.registry.project_for_workspace("feishumemory"), "proj_alpha")

    def test_project_for_repo_path_exact(self):
        pid = self.registry.project_for_repo_path("C:/workspace/feishumemory")
        self.assertEqual(pid, "proj_alpha")

    def test_project_for_repo_path_subdirectory(self):
        pid = self.registry.project_for_repo_path("C:/workspace/feishumemory/memory_engine/engine.py")
        self.assertEqual(pid, "proj_alpha")

    def test_project_for_repo_path_no_match(self):
        self.assertIsNone(self.registry.project_for_repo_path("C:/other/project"))

    def test_singleton_configure_and_get(self):
        self.assertIsNone(ProjectRegistry.get_instance())
        ProjectRegistry.configure(self.registry)
        self.assertIs(ProjectRegistry.get_instance(), self.registry)
        ProjectRegistry.reset()
        self.assertIsNone(ProjectRegistry.get_instance())


class TestScopeRegistryIntegration(unittest.TestCase):
    """Test that scope.py uses ProjectRegistry when configured."""

    def setUp(self):
        self.registry = ProjectRegistry([
            ProjectRegistryProject(
                project_id="proj_registered",
                name="Registered Project",
                chat_ids=("oc_reg_chat",),
                doc_ids=("doc_reg",),
            ),
        ])
        ProjectRegistry.configure(self.registry)

    def tearDown(self):
        ProjectRegistry.reset()

    def test_registered_chat_infers_project_scope(self):
        event = _make_event(chat_id="oc_reg_chat")
        scope = infer_scope(event)
        self.assertEqual(scope, Scope.PROJECT)

    def test_registered_chat_infers_project_id(self):
        event = _make_event(chat_id="oc_reg_chat")
        pid = infer_project_id(event)
        self.assertEqual(pid, "proj_registered")

    def test_unregistered_chat_stays_user_scope(self):
        event = _make_event(chat_id="oc_unknown")
        scope = infer_scope(event)
        self.assertEqual(scope, Scope.USER)

    def test_unregistered_chat_no_project_id(self):
        event = _make_event(chat_id="oc_unknown")
        pid = infer_project_id(event)
        self.assertIsNone(pid)

    def test_registered_doc_infers_project_scope(self):
        event = _make_event(
            source_type="doc",
            source_ref="doc_reg",
            scope=Scope.USER,
        )
        scope = infer_scope(event)
        self.assertEqual(scope, Scope.PROJECT)

    def test_registered_doc_infers_project_id(self):
        event = _make_event(
            source_type="doc",
            source_ref="doc_reg",
        )
        pid = infer_project_id(event)
        self.assertEqual(pid, "proj_registered")

    def test_explicit_project_tag_overrides_registry(self):
        event = _make_event(
            content="#project:explicit_proj test",
            chat_id="oc_reg_chat",
        )
        pid = infer_project_id(event)
        self.assertEqual(pid, "explicit_proj")

    def test_no_registry_returns_none(self):
        ProjectRegistry.reset()
        event = _make_event(chat_id="oc_reg_chat")
        pid = infer_project_id(event)
        self.assertIsNone(pid)


class TestAutoRegister(unittest.TestCase):
    """Test auto-registration of new Feishu chats."""

    def setUp(self):
        self._tmp = Path("tests_runtime") / "autoregister_test"
        self._tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        ProjectRegistry.reset()
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_json(self, data: dict, name: str = "test.json") -> Path:
        p = self._tmp / name
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return p

    def test_register_chat_creates_new_project(self):
        p = self._write_json({
            "version": 1,
            "projects": [{"project_id": "existing", "name": "Existing"}],
        })
        registry = ProjectRegistry.load(p)
        pid = registry.register_chat("oc_new_chat")
        self.assertEqual(pid, "auto_oc_new_chat")
        self.assertEqual(registry.project_for_chat("oc_new_chat"), "auto_oc_new_chat")
        self.assertEqual(registry.get_project("auto_oc_new_chat").name, "椋炰功缇よ亰 oc_new_chat")

    def test_register_chat_idempotent(self):
        p = self._write_json({
            "version": 1,
            "projects": [{"project_id": "existing", "name": "Existing"}],
        })
        registry = ProjectRegistry.load(p)
        pid1 = registry.register_chat("oc_known_chat")
        pid2 = registry.register_chat("oc_known_chat")
        self.assertEqual(pid1, pid2)
        self.assertEqual(pid1, "auto_oc_known_chat")

    def test_register_chat_appends_to_existing_project(self):
        p = self._write_json({
            "version": 1,
            "projects": [
                {"project_id": "my_project", "name": "My Project", "chat_ids": ["oc_existing"]}
            ],
        })
        registry = ProjectRegistry.load(p)
        pid = registry.register_chat("oc_new_member", project_id="my_project")
        self.assertEqual(pid, "my_project")
        proj = registry.get_project("my_project")
        self.assertIn("oc_new_member", proj.chat_ids)
        self.assertIn("oc_existing", proj.chat_ids)

    def test_save_and_load_roundtrip(self):
        p = self._write_json({
            "version": 1,
            "projects": [{"project_id": "existing", "name": "Existing"}],
        })
        registry = ProjectRegistry.load(p)
        registry.register_chat("oc_roundtrip")
        registry.save()

        # Reload and verify
        registry2 = ProjectRegistry.load(p)
        self.assertEqual(registry2.project_for_chat("oc_roundtrip"), "auto_oc_roundtrip")
        proj = registry2.get_project("auto_oc_roundtrip")
        self.assertEqual(proj.name, "椋炰功缇よ亰 oc_roundtrip")

    def test_auto_register_in_daemon_flow(self):
        """Simulate: daemon receives unregistered chat, auto-registers, then event
        flows through infer_scope/infer_project_id and gets correct values."""
        p = self._write_json({
            "version": 1,
            "projects": [{"project_id": "existing", "name": "Existing"}],
        })
        registry = ProjectRegistry.load(p)
        ProjectRegistry.configure(registry)

        try:
            chat_id = "oc_daemon_test_chat"
            # Before registration
            self.assertIsNone(registry.project_for_chat(chat_id))
            event_before = _make_event(chat_id=chat_id)
            self.assertIsNone(infer_project_id(event_before))
            self.assertEqual(infer_scope(event_before), Scope.USER)

            # Simulate daemon auto-register
            pid = registry.register_chat(chat_id)
            registry.save()
            self.assertEqual(pid, "auto_oc_daemon_test_chat")

            # After registration 鈥?scope.py should now resolve correctly
            event_after = _make_event(chat_id=chat_id)
            self.assertEqual(infer_project_id(event_after), "auto_oc_daemon_test_chat")
            self.assertEqual(infer_scope(event_after), Scope.PROJECT)
        finally:
            ProjectRegistry.reset()


class TestProjectResolver(unittest.TestCase):
    """Test openclaw_adapter.project_resolver."""

    def setUp(self):
        self.registry = ProjectRegistry([
            ProjectRegistryProject(
                project_id="proj_resolver",
                name="Resolver Test",
                repo_paths=("C:/workspace/feishumemory",),
                openclaw_workspace_ids=("feishumemory",),
            ),
        ])
        ProjectRegistry.configure(self.registry)
        # Reset the resolver cache
        from openclaw_adapter.project_resolver import reset as resolver_reset
        resolver_reset()

    def tearDown(self):
        ProjectRegistry.reset()
        from openclaw_adapter.project_resolver import reset as resolver_reset
        resolver_reset()

    def test_explicit_wins(self):
        from openclaw_adapter.project_resolver import resolve_project_id
        result = resolve_project_id(explicit="explicit_id", cwd="C:/workspace/feishumemory")
        self.assertEqual(result, "explicit_id")

    def test_workspace_id_resolves(self):
        from openclaw_adapter.project_resolver import resolve_project_id
        result = resolve_project_id(workspace_id="feishumemory")
        self.assertEqual(result, "proj_resolver")

    def test_cwd_resolves(self):
        from openclaw_adapter.project_resolver import resolve_project_id
        result = resolve_project_id(cwd="C:/workspace/feishumemory")
        self.assertEqual(result, "proj_resolver")

    def test_open_files_resolves(self):
        from openclaw_adapter.project_resolver import resolve_project_id
        result = resolve_project_id(open_files=["C:/workspace/feishumemory/src/main.py"])
        self.assertEqual(result, "proj_resolver")

    def test_no_match_returns_none(self):
        from openclaw_adapter.project_resolver import resolve_project_id
        result = resolve_project_id(cwd="/tmp/unknown")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
