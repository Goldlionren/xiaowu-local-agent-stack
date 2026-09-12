from __future__ import annotations

import copy
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
avatarctl = importlib.import_module("avatarctl")
executor = importlib.import_module("mcp_executor")


class PollRecoveryTests(unittest.TestCase):
    def run_poll(self, responses):
        record = {
            "status": "submitted", "target": "comfy_5090",
            "prompt_id": "existing-prompt", "deadline_seconds": 240,
            "remote_output_dir": "F:\\AI\\XiaowuAvatar\\output\\existing",
        }
        calls = []
        def caller(name, args):
            calls.append((name, args))
            if name.endswith("__job"):
                return responses.pop(0)
            if name.endswith("__fetch_outputs"):
                return {"result": "MEDIA:/tmp/existing.png"}
            raise AssertionError("Recovery must not submit: " + name)
        def completed(*args):
            record["status"] = "generation_completed"
        with mock.patch.object(avatarctl, "cmd_transaction_status", side_effect=lambda *a: dict(record)), mock.patch.object(
            avatarctl, "workflow_registry", return_value={}
        ), mock.patch.object(avatarctl, "cmd_mark_completed", side_effect=completed), mock.patch.object(
            avatarctl, "cmd_mark_pending_completion", return_value={"pending": True}
        ), mock.patch.object(avatarctl, "cmd_commit", return_value={"delivered": True}) as commit, mock.patch.object(
            executor.time, "sleep"
        ):
            result = executor.execute_transaction({}, "existing-transaction", caller)
        return result, calls, commit

    def test_transient_disconnect_recovers_same_job_and_delivers(self):
        result, calls, commit = self.run_poll([
            {"error": "comfy jobs status failed [server_not_running]"},
            {"status": "completed"},
        ])
        self.assertEqual(result, {"delivered": True})
        self.assertEqual([name.rsplit("__", 1)[-1] for name, _ in calls], ["job", "job", "fetch_outputs"])
        self.assertTrue(all(args["prompt_id"] == "existing-prompt" for _, args in calls))
        commit.assert_called_once()

    def test_persistent_disconnect_keeps_bound_job_pending(self):
        result, calls, commit = self.run_poll([{"error": "connection refused"} for _ in range(3)])
        self.assertEqual(result, {"pending": True})
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(name.endswith("__job") for name, _ in calls))
        commit.assert_not_called()

    def test_real_failed_job_is_not_retried(self):
        with self.assertRaisesRegex(executor.MCPExecutionError, "terminal status"):
            self.run_poll([{"status": "failed"}])

    def test_non_connection_error_is_not_swallowed(self):
        with self.assertRaisesRegex(executor.MCPExecutionError, "permission denied"):
            self.run_poll([{"error": "permission denied"}])


class WorkflowArgumentTests(unittest.TestCase):
    def test_auto_aliases_prepare_saved_target_without_gpu_submission(self):
        for value in (None, "", "avatar", "auto", "yinyue_cosplay01"):
            with self.subTest(workflow=value), tempfile.TemporaryDirectory() as tmp:
                config = avatarctl.load_config()
                config["runtime"]["state_dir"] = tmp
                config["execution"]["default_target"] = "comfy_5090"
                config["telegram"]["enabled"] = False
                args = {"intent": "穿日常服装拍照", "workflow": value}
                before = copy.deepcopy(args)
                caller = mock.Mock(side_effect=AssertionError("Must not submit"))
                with mock.patch.object(avatarctl, "load_config", return_value=config), mock.patch.object(
                    executor, "execute_transaction", return_value={"mocked": True}
                ) as execute:
                    executor.generate(args, caller)
                    transaction_id = execute.call_args.args[1]
                    record = avatarctl.cmd_transaction_status(config, transaction_id)
                self.assertEqual(record["target"], "comfy_5090")
                self.assertEqual(record["workflow_id"], "yinyue_cosplay01")
                self.assertFalse(record.get("prompt_id"))
                self.assertEqual(args, before)
                caller.assert_not_called()

    def test_unknown_workflow_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = avatarctl.load_config()
            config["runtime"]["state_dir"] = tmp
            with mock.patch.object(avatarctl, "load_config", return_value=config), mock.patch.object(
                executor, "execute_transaction"
            ) as execute:
                with self.assertRaisesRegex(avatarctl.AvatarError, "未知 workflow"):
                    executor.generate({"intent": "日常照片", "workflow": "invented-workflow"}, mock.Mock())
                execute.assert_not_called()

    def test_schema_lists_registry_workflows(self):
        spec = importlib.util.spec_from_file_location("workflow_schema_test", ROOT / "plugins/xiaowu-visual/__init__.py")
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        actual = plugin._generate_schema()["parameters"]["properties"]["workflow"]
        registered = avatarctl.workflow_registry(avatarctl.load_config())["workflows"]
        self.assertEqual(set(actual["enum"]), {"", *registered})
        self.assertNotIn("avatar", actual["enum"])
