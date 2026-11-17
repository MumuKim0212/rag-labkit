"""Shared test setup: tests that run git are skipped, not failed, where git is not installed."""

import shutil

import pytest


def without_small_n(text: str) -> str:
    """A comparison report without the small-sample warning (labkit.eval.paired.SMALL_N), which every comparison of
    the few-question test projects carries: tests that check for no other warning read the rest."""
    return "\n".join(line for line in text.splitlines() if not line.startswith("WARNING: only "))


def pytest_collection_modifyitems(config, items):
    if shutil.which("git"):
        return
    skip = pytest.mark.skip(reason="git is not installed")
    for item in items:
        if "needs_git" in item.keywords:
            item.add_marker(skip)
