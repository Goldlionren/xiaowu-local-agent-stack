from __future__ import annotations

import importlib
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
avatarctl = importlib.import_module("avatarctl")
visual = importlib.import_module("visual_v030")


class TargetSelectionTests(unittest.TestCase):
    def test_saved_default_precedes_workflow_preference(self) -> None:
        registry = avatarctl.load_json(ROOT / "registry/workflows.json")
        workflow = registry["workflows"]["yinyue_cosplay01"]

        self.assertEqual(workflow["preferred_target"], "comfy_3060")
        self.assertEqual(
            visual.select_target(
                registry, workflow, requested_target="", default_target="comfy_5090"
            ),
            "comfy_5090",
        )

    def test_explicit_target_precedes_saved_default(self) -> None:
        registry = avatarctl.load_json(ROOT / "registry/workflows.json")
        workflow = registry["workflows"]["yinyue_cosplay01"]
        actual = visual.select_target(
            registry, workflow, requested_target="comfy_4080s", default_target="comfy_5090"
        )
        self.assertEqual(actual, "comfy_4080s")
