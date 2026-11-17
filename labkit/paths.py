"""Where the user's project lives. labkit is a library; configs, golden sets, and results belong to the project.

The project root is, in order: the LABKIT_ROOT environment variable, the nearest folder at or above the
current directory that contains `labkit.yaml`, or the current directory. Functions that read project files
take an optional `root` and fall back to `project_root()`.
"""

import os
import re
from pathlib import Path

MARKER = "labkit.yaml"
EXPERIMENT_NAME = re.compile(r"\w[\w.-]*")  # letters (any script), digits, _ - and . ; starts with a letter or digit
GRADES_SUFFIX = re.compile(r"(__regrade[\w.-]*)?")  # empty, or __regrade...: the cost totals and the noise check read
#                                                  only those files, and `_r2` would read like a run's own name
MAX_NAME = 80
WINDOWS_DEVICES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def check_experiment_name(name: str) -> str:
    """An experiment name becomes results/<name>/ and out/grades/<name>.jsonl, so it must be one plain path part:
    `dev/x` hid from the freeze and the report, and `dev_z/../../..` wrote results outside the project. Names that
    Windows cannot make into a folder (nul, con, ...) or that are too long would fail only after the LLM spend."""
    if (not isinstance(name, str) or not EXPERIMENT_NAME.fullmatch(name) or name.endswith(".") or ".." in name
            or len(name) > MAX_NAME or name.split(".")[0].casefold() in WINDOWS_DEVICES):
        raise ValueError(f"{name!r} is not a valid experiment name: use letters, digits, `_`, `-` and `.`, starting "
                         f"with a letter or digit, not ending in a dot, without `..`, at most {MAX_NAME} characters, "
                         "and not a Windows device name such as nul or con")
    return name


def check_no_case_clash(name: str, known) -> None:
    """On Windows and macOS results/Test_Final and results/test_final are one folder, so two experiments whose names
    differ only by case would write into each other's results and grades."""
    for other in sorted(known):
        if other != name and other.casefold() == name.casefold():
            raise ValueError(f"{name!r} differs only by case from the existing experiment {other!r}: they would share "
                             "one results folder and one grades file. Use a name that differs by more than case.")


def check_grades_suffix(suffix: str) -> str:
    if not isinstance(suffix, str) or not GRADES_SUFFIX.fullmatch(suffix):
        raise ValueError(f"{suffix!r} is not a valid grades suffix: it starts with __regrade, then letters, digits, "
                         "`_`, `-` or `.` (for example __regrade1); the cost totals and the noise check read only those")
    return suffix


def project_root(start: Path | str | None = None) -> Path:
    env = os.environ.get("LABKIT_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / MARKER).exists():
            return folder
    return here


def config_dir(root: Path | None = None) -> Path:
    return Path(root or project_root()) / "configs"


def baseline_path(root: Path | None = None) -> Path:
    return config_dir(root) / "baseline.yaml"


def pricing_path(root: Path | None = None) -> Path:
    return config_dir(root) / "pricing.yaml"


def state_path(root: Path | None = None) -> Path:
    return Path(root or project_root()) / "report" / "state.yaml"


def steps_dir(root: Path | None = None) -> Path:
    return Path(root or project_root()) / "docs" / "steps"


# Committed: golden/ and results/. Local only (git-ignored): out/ (run log, grades, report.html, indexes).

def golden_dir(root: Path | None = None) -> Path:
    return Path(root or project_root()) / "golden"


def results_dir(root: Path | None = None) -> Path:
    return Path(root or project_root()) / "results"


def runs_db(root: Path | None = None) -> Path:
    return Path(root or project_root()) / "out" / "runs.db"


def grades_dir(root: Path | None = None) -> Path:
    return Path(root or project_root()) / "out" / "grades"


def report_html(root: Path | None = None) -> Path:
    return Path(root or project_root()) / "out" / "report.html"
