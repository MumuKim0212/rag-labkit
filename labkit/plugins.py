"""Load the project's own code so its @register_system / @register_judge / @register_measure run.

labkit.yaml:
    pythonpath: [src]            # folders (relative to the project root) added to sys.path; default [.]
    modules: [myproj.systems, myproj.judge]

The CLI calls load_modules() before every command. Library users call it once before run/grade/evaluate.
"""

import importlib
import sys
from pathlib import Path

from .paths import project_root
from .project import load_project


def load_modules(root: Path | None = None, project: dict | None = None) -> list[str]:
    root = Path(root or project_root())
    project = project or load_project(root)
    for entry in project["pythonpath"]:
        folder = str((root / entry).resolve())
        if folder not in sys.path:
            sys.path.insert(0, folder)
    for name in project["modules"]:
        try:
            importlib.import_module(name)
        except ImportError as e:
            raise ImportError(f"labkit.yaml `modules` lists {name!r}, which cannot be imported: {e}") from e
    return list(project["modules"])
