"""template/ is what a new project starts from: it must load as a labkit project, its documents must agree
with each other, and nothing from the project it was extracted from may remain."""

import argparse
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from labkit.cli import build_parser, main
from labkit.config import load_config
from labkit.cost import load_prices
from labkit.project import load_project

TEMPLATE = Path(__file__).resolve().parents[1] / "labkit" / "template"
DOCS = sorted(TEMPLATE.rglob("*.md"))
STEPS = sorted((TEMPLATE / "docs" / "steps").glob("*.md"))


def test_settings_files_load_as_a_labkit_project():
    project = load_project(TEMPLATE)
    assert project["golden"]["files"] == ["golden.jsonl"] and project["max_score"] == 2
    assert project["measures"] == ["retrieval", "citation", "status"]
    cfg = load_config(TEMPLATE / "configs" / "baseline.yaml", root=TEMPLATE)
    assert cfg["changed"] == {} and cfg["system"] and cfg["judge"]["name"]
    prices = load_prices(TEMPLATE / "configs" / "pricing.yaml")
    assert cfg["answer"]["model"] in prices and cfg["judge"]["model"] in prices
    for path in [*TEMPLATE.rglob("*.yaml"), *TEMPLATE.rglob("*.json")]:
        if path.suffix == ".yaml":
            yaml.safe_load(path.read_text(encoding="utf-8"))


def test_template_is_a_valid_project_root(tmp_path):
    root = tmp_path / "proj"
    shutil.copytree(TEMPLATE, root)
    before = list(sys.path)
    try:
        assert main(["--root", str(root), "report"]) == 0
    finally:
        sys.path[:] = before
    assert (root / "results" / "SUMMARY.md").exists()


def test_every_step_has_exit_criteria():
    assert [p.name[:2] for p in STEPS] == [f"{n:02d}" for n in range(1, 9)]
    for path in STEPS:
        text = path.read_text(encoding="utf-8")
        criteria = text.split("## Exit criteria", 1)
        assert len(criteria) == 2, f"{path.name}: no '## Exit criteria' section"
        assert re.search(r"^- \[ \] ", criteria[1], re.MULTILINE), f"{path.name}: exit criteria are not checkboxes"


def test_workflow_index_matches_the_step_files():
    workflow = (TEMPLATE / "docs" / "WORKFLOW.md").read_text(encoding="utf-8")
    linked = re.findall(r"\]\(steps/([^)]+)\)", workflow)
    assert linked == [p.name for p in STEPS]


def split_frontmatter(text: str):
    """(parsed YAML, rest) for a file that starts with a `---` block, as skill files must; (None, text) otherwise."""
    if text.startswith("---\n"):
        end = text.index("\n---\n", 4)
        return yaml.safe_load(text[4:end]), text[end + 5:]
    return None, text


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.relative_to(TEMPLATE).as_posix())
def test_documents_keep_their_guide_comment(path):
    _, body = split_frontmatter(path.read_text(encoding="utf-8"))     # a skill's comment follows its front matter
    assert body.startswith("<!--\nGUIDE:") and "Keep guide comment in place" in body.split("-->", 1)[0]


# ---------------------------------------------------------------- the /labkit-next skill

SKILL = TEMPLATE / ".claude" / "skills" / "labkit-next" / "SKILL.md"


def test_skill_is_manual_only_thin_and_pre_approves_only_read_only_commands():
    meta, body = split_frontmatter(SKILL.read_text(encoding="utf-8"))
    assert meta["name"] == SKILL.parent.name == "labkit-next" and meta["description"]
    assert meta["disable-model-invocation"] is True                    # only the owner starts a step
    allowed = re.findall(r"\w+\([^)]*\)", meta["allowed-tools"])       # entries look like Bash(labkit status *)
    assert set(allowed) == {"Bash(labkit status *)", "Bash(labkit check *)"}   # tick writes: not pre-approved
    assert len(body.splitlines()) < 60                                 # a thin loop; the docs recommend well under 500 lines
    assert "Never run `labkit approve`" in body                        # the rule that matters most is stated first


def test_skill_names_only_commands_and_options_labkit_has():
    parser = build_parser()
    commands = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices
    _, body = split_frontmatter(SKILL.read_text(encoding="utf-8"))
    seen = set()
    for span in re.findall(r"`(labkit [^`]+)`", body):
        words = span.split()
        assert words[1] in commands, f"{span!r}: labkit has no command {words[1]!r}"
        seen.add(words[1])
        for flag in re.findall(r"--[\w-]+", span):
            assert flag in commands[words[1]]._option_string_actions, f"{span!r}: {words[1]} has no option {flag}"
    assert {"status", "check", "tick", "approve", "revoke", "new-card"} <= seen


def test_skill_does_not_copy_the_content_of_the_step_files():
    """Step content lives in docs/steps/; a copy here would drift from it."""
    body = SKILL.read_text(encoding="utf-8")
    for step in STEPS:
        content = step.read_text(encoding="utf-8").split("-->", 1)[1]    # skip the guide comment every document shares
        for line in content.splitlines():
            text = line.strip().removeprefix("- [ ] ")
            if len(text) > 40:
                assert text not in body, f"{step.name} line copied into the skill: {text[:60]}"


def test_markdown_links_and_path_references_resolve():
    missing = []
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
            if "://" not in target and not (doc.parent / target).resolve().exists():
                missing.append(f"{doc.relative_to(TEMPLATE)} -> {target}")
        for ref in re.findall(r"`((?:docs|report|golden|results|configs|experiments|prompts)/[\w./-]+\.(?:md|yaml))`", text):
            if re.search(r"NNN|\d{3}|review_log|SUMMARY|state\.yaml|_v\d|<", ref):  # examples, files the project or labkit creates
                continue
            if not (TEMPLATE / ref).exists():
                missing.append(f"{doc.relative_to(TEMPLATE)} mentions {ref}")
    assert not missing, missing


def test_step_6_and_7_agree_on_where_rerankers_and_rewriting_belong():
    step5 = (TEMPLATE / "docs" / "steps" / "05-triage.md").read_text(encoding="utf-8")
    step6 = (TEMPLATE / "docs" / "steps" / "06-experiments.md").read_text(encoding="utf-8")
    step7 = (TEMPLATE / "docs" / "steps" / "07-structure.md").read_text(encoding="utf-8")
    workflow = (TEMPLATE / "docs" / "WORKFLOW.md").read_text(encoding="utf-8")
    assert "A reranker is a step-6 card" in step6 and "Rerankers are step 6" in step7
    assert "rerank" not in next(line for line in workflow.splitlines() if "steps/07-structure.md" in line).lower()
    assert "query rewriting" in step7.lower() and "decomposition" in step7.lower()
    assert "pool_page_hit" in step5 and "pool_page_hit" in step6                # the same metric names the bound
    process = (TEMPLATE / "report" / "PROCESS.md").read_text(encoding="utf-8")
    assert "gold in the pool" in process and "gold not in the pool" in process  # the report's triage table matches


def test_template_declares_the_new_project_settings():
    settings = (TEMPLATE / "labkit.yaml").read_text(encoding="utf-8")
    assert "index_keys" in settings and "main_branch: main" in settings
    assert load_project(TEMPLATE)["main_branch"] == "main" and load_project(TEMPLATE)["index_keys"] == []


def test_process_section_references_exist():
    process = (TEMPLATE / "report" / "PROCESS.md").read_text(encoding="utf-8")
    sections = set(re.findall(r"^## (\d+)\.", process, re.MULTILINE)) | {"0"}
    for doc in DOCS:
        for number in re.findall(r"PROCESS\.md[`)]*,? ?§(\d+)", doc.read_text(encoding="utf-8")):
            assert number in sections, f"{doc.relative_to(TEMPLATE)} refers to PROCESS.md §{number}"
    for number in range(1, 9):       # step N is recorded in section N
        step = (TEMPLATE / "docs" / "steps" / next(p.name for p in STEPS if p.name.startswith(f"{number:02d}")))
        assert f"§{number}" in step.read_text(encoding="utf-8"), f"{step.name} does not name PROCESS.md §{number}"


def test_claude_md_points_to_every_document_of_the_template():
    """CLAUDE.md is the index an agent reads first (it is loaded into every session): a document it does not name
    is found by searching, or read whole when only one part was needed. A step file is covered by `docs/steps/`, a
    report file by its name in the report row, the skill by `/labkit-next`; any other file by its path."""
    index = (TEMPLATE / "CLAUDE.md").read_text(encoding="utf-8")
    unnamed = []
    for doc in DOCS:
        path = doc.relative_to(TEMPLATE).as_posix()
        if path == "CLAUDE.md" or path in index:
            continue
        if (path.startswith("docs/steps/") and "docs/steps/" in index) or (path.startswith(".claude/skills/labkit-next/")
                                                                          and "/labkit-next" in index):
            continue
        if path.startswith("report/") and re.search(rf"\b{re.escape(doc.stem)}\b", index):
            continue
        if path.startswith("golden/") and doc.name in index:
            continue
        unnamed.append(path)
    assert not unnamed, f"CLAUDE.md does not name: {unnamed}"


def test_the_sections_claude_md_names_exist():
    """The reading table names sections (`results/README.md` ("summary.json", ...)) so an agent reads one part of a
    long reference; a renamed heading would send it looking for a section that is gone."""
    index = (TEMPLATE / "CLAUDE.md").read_text(encoding="utf-8")
    named = 0
    for path, inside in re.findall(r"`(\w+/[\w/.]+\.md)` \(([^)]*)\)", index):   # a path; `WORKFLOW.md` (facts) is a note
        headings = [h.strip() for h in re.findall(r"^## (.+)$", (TEMPLATE / path).read_text(encoding="utf-8"), re.M)]
        for section in re.findall(r'"([^"]+)"', inside):
            named += 1
            assert any(h.startswith(section) for h in headings), f"{path} has no section {section!r}: {headings}"
    assert named >= 6                                                     # the table above names sections at all
