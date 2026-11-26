"""The continuous-integration workflow (.github/workflows/tests.yml) runs what the release procedure runs by hand, and
the READMEs show its status.

The suite was run on Windows, on Linux and on the oldest supported Python by hand before each release; a visitor to the
public repository cannot see that, but can see a badge. These tests keep the workflow from drifting away from what the
package supports (the oldest Python in pyproject.toml, both platforms the suite has found real defects on).
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
REPO = "https://github.com/MumuKim0212/rag-labkit"


def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def commands(job: dict) -> list[str]:
    return [step["run"] for step in job["steps"] if "run" in step]


def test_the_workflow_runs_on_push_to_main_and_on_pull_requests():
    triggers = workflow()[True]  # PyYAML reads the key `on` as the boolean True
    assert triggers["push"]["branches"] == ["main"] and "pull_request" in triggers


def test_the_workflow_covers_both_platforms_and_the_oldest_and_a_recent_python():
    matrix = workflow()["jobs"]["test"]["strategy"]["matrix"]
    assert {"ubuntu-latest", "windows-latest"} <= set(matrix["os"])
    oldest = re.search(r'^requires-python = ">=(\d+\.\d+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.MULTILINE)
    assert oldest.group(1) in matrix["python"]
    assert max(matrix["python"], key=lambda v: tuple(map(int, v.split(".")))) != oldest.group(1)
    assert workflow()["jobs"]["test"]["strategy"]["fail-fast"] is False  # one platform failing must not hide the other


def test_the_workflow_installs_the_dev_extra_and_runs_the_whole_suite():
    run = commands(workflow()["jobs"]["test"])
    assert any('pip install -e ".[dev]"' in c for c in run), "without setuptools the wheel and sdist tests are skipped"
    assert any(c.strip() == "python -m pytest -q" for c in run)


def test_the_workflow_uses_actions_that_run_on_node_24():
    """checkout@v4 and setup-python@v5 run on Node 20, which the runners deprecate: every run printed a warning on the
    public repository's Actions page. Version 7 of both is the first checked to run on node24 (their action.yml)."""
    used = [step["uses"] for step in workflow()["jobs"]["test"]["steps"] if "uses" in step]
    assert {u.split("@")[0] for u in used} == {"actions/checkout", "actions/setup-python"}
    for action in used:
        assert int(action.split("@v")[1]) >= 7, action


@pytest.mark.parametrize("readme", ["README.md", "docs/README.ko.md"])
def test_the_readmes_show_the_status_of_the_workflow(readme):
    text = (ROOT / readme).read_text(encoding="utf-8")
    assert f"{REPO}/actions/workflows/{WORKFLOW.name}/badge.svg" in text
    assert f"{REPO}/actions/workflows/{WORKFLOW.name})" in text  # the badge links to the runs
    assert WORKFLOW.is_file()
