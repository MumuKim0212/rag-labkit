"""Process setup shared by every entry point: load .env and force tracing off before any LLM library import.

Batch runs would send thousands of traces to a tracing service, and a prompt or question set may not be
meant to leave the machine. `setup()` therefore sets the tracing switches to "false" after loading .env,
so a .env value cannot turn them back on. Every new entry point calls `setup()` first.

Loading .env needs python-dotenv (`pip install rag-labkit[env]`). If a .env file exists and python-dotenv
is missing, setup() raises instead of silently skipping it, so missing API keys are not a mystery later.
"""

import os
from pathlib import Path

from .paths import project_root

TRACING_VARS = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")


def setup(root: Path | None = None) -> None:
    env_file = Path(root or project_root()) / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
        except ImportError as e:
            raise RuntimeError(f"{env_file} exists but python-dotenv is not installed: pip install python-dotenv "
                               "(or install labkit with its env extra, as the project's requirements.txt does)") from e
        load_dotenv(env_file)
    for var in TRACING_VARS:
        os.environ[var] = "false"


def require_env(name: str) -> str:
    """The value of a required variable, e.g. an API key; placeholder values like "your-key-here" count as unset."""
    value = os.environ.get(name, "")
    if not value or value.startswith("your-"):
        raise RuntimeError(f"{name} is not set: copy .env.example to .env and fill in your own value")
    return value
