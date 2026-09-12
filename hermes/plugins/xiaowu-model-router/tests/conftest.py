from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "yinyue_router_testpkg"


def pytest_configure():
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)


@pytest.fixture(autouse=True)
def stub_hermes_worker_override(monkeypatch):
    """Keep unit tests independent from a full Hermes installation."""
    gateway_hook = importlib.import_module(f"{PACKAGE}.gateway_hook")

    def resolve(route):
        return {
            "model": route.model,
            "provider": route.runtime_provider,
            "base_url": route.base_url,
            "api_mode": route.api_mode,
        }

    monkeypatch.setattr(gateway_hook, "_resolve_worker_override", resolve)
