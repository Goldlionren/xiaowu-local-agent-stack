from __future__ import annotations

import asyncio
import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / "plugins" / "xiaowu-visual" / "__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "test_xiaowu_management_commands_plugin", PLUGIN_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ManagementCommandTests(unittest.TestCase):
    def test_parser_accepts_only_dedicated_management_commands(self) -> None:
        plugin = load_plugin()
        self.assertEqual(
            plugin._parse_management_command("/xiaowu-avatar mcp"),
            {"kind": "mcp", "operation": "show", "value": ""},
        )
        self.assertEqual(
            plugin._parse_management_command("/xiaowu_avatar mcp 3060"),
            {"kind": "mcp", "operation": "set", "value": "3060"},
        )
        self.assertEqual(
            plugin._parse_management_command("/xiaowu-avatar 穿着"),
            {"kind": "wardrobe", "operation": "show", "value": ""},
        )
        self.assertEqual(
            plugin._parse_management_command(
                '/xiaowu-avatar 穿着 设置 {"鞋子":"黑色高跟鞋"}'
            ),
            {
                "kind": "wardrobe",
                "operation": "set",
                "value": '{"鞋子":"黑色高跟鞋"}',
            },
        )
        self.assertIsNone(
            plugin._parse_management_command("/xiaowu-avatar 状态")
        )
        self.assertIsNone(
            plugin._parse_management_command("/yinyue-avatar mcp")
        )

    def test_wardrobe_set_passes_validated_json_to_core_without_generation(self) -> None:
        plugin = load_plugin()
        calls = []

        class Core:
            @staticmethod
            def load_config():
                return {"fixture": True}

            @staticmethod
            def cmd_wardrobe_update(config, changes):
                calls.append((config, changes))
                return {
                    "changed": True,
                    "revision": 8,
                    "clothing": {"footwear": "黑色高跟鞋"},
                }

        with mock.patch.object(plugin, "_avatarctl_core", return_value=Core):
            reply = plugin._run_management_command(
                {
                    "kind": "wardrobe",
                    "operation": "set",
                    "value": '{"鞋子":"黑色高跟鞋"}',
                }
            )

        self.assertEqual(
            calls, [({"fixture": True}, {"鞋子": "黑色高跟鞋"})]
        )
        self.assertIn("没有生成图片", reply)
        self.assertIn('"revision": 8', reply)


class ManagementDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_command_is_skipped_before_the_llm(self) -> None:
        plugin = load_plugin()
        source = SimpleNamespace(
            platform=SimpleNamespace(value="telegram"),
            chat_id="123",
        )
        event = SimpleNamespace(
            text="/xiaowu-avatar mcp",
            source=source,
            raw_message=SimpleNamespace(text="/xiaowu-avatar mcp"),
        )
        gateway = SimpleNamespace(
            _is_user_authorized=lambda _source: True,
            _normalize_source_for_session_key=lambda value: value,
            _session_key_for_source=lambda _source: "telegram:123",
        )
        with mock.patch.object(
            plugin,
            "_handle_management_command",
            new_callable=mock.AsyncMock,
        ) as handler:
            result = plugin._on_pre_gateway_dispatch(
                event=event, gateway=gateway
            )
            await asyncio.sleep(0)

        self.assertEqual(result["action"], "skip")
        self.assertEqual(result["reason"], "xiaowu-mcp-management-command")
        handler.assert_awaited_once()
        self.assertEqual(handler.await_args.args[2], "telegram:123")

    async def test_mutation_is_blocked_while_session_is_running(self) -> None:
        plugin = load_plugin()
        gateway = SimpleNamespace(_is_session_running=lambda _key: True)
        source = SimpleNamespace(chat_id="123")
        command = {"kind": "wardrobe", "operation": "set", "value": "{}"}
        with mock.patch.object(
            plugin, "_run_management_command"
        ) as runner, mock.patch.object(
            plugin,
            "_send_management_reply",
            new_callable=mock.AsyncMock,
        ) as sender:
            await plugin._handle_management_command(
                gateway, source, "telegram:123", command
            )

        runner.assert_not_called()
        self.assertIn("仍有任务在运行", sender.await_args.args[2])


if __name__ == "__main__":
    unittest.main()
