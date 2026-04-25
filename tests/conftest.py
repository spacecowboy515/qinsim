"""pytest fixtures shared across the qinsim suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def bundled_scenarios_dir() -> Path:
    """Absolute path to the bundled scenarios directory.

    Tests parametrise over this so adding a new scenario YAML
    automatically exercises the validation + smoke paths without
    touching the test code.
    """
    here = Path(__file__).resolve().parent
    return here.parent / "src" / "qinsim" / "scenarios"


@pytest.fixture(scope="session")
def bundled_scenario_paths(bundled_scenarios_dir: Path) -> list[Path]:
    return sorted(bundled_scenarios_dir.glob("*.yaml"))
