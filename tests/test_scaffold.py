"""labkit init and labkit new-card."""

import filecmp
import subprocess
from pathlib import Path

import pytest

from labkit import cards, scaffold
from labkit.cli import main
from labkit.config import load_config
from labkit.env import TRACING_VARS
from labkit.project import load_project

TEMPLATE = Path(__file__).resolve().parents[1] / "labkit" / "template"


@pytest.fixture(autouse=True)
def quiet_env(monkeypatch):
    monkeypatch.delenv("LABKIT_ROOT", raising=False)
    for var in TRACING_VARS:                      # the CLI forces these off; restore the original state afterwards
        monkeypatch.setenv(var, "x")
        monkeypatch.delenv(var)


def files_of(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts}


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    scaffold.init_project(root)
    return root


# ---------------------------------------------------------------- init

def test_init_copies_the_whole_template_unchanged(tmp_path):
    root = tmp_path / "new-project"
    created = scaffold.init_project(root)
    assert set(created) == files_of(TEMPLATE) and len(created) == len(files_of(TEMPLATE))
    assert ".claude/settings.json" in created and ".gitignore" in created       # hidden files come along
    for name in created:
        assert filecmp.cmp(TEMPLATE / name, root / name, shallow=False), name
    assert not [p for p in root.rglob("*") if "__pycache__" in p.parts]


def test_init_never_overwrites(tmp_path):
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(FileExistsError, match="not empty"):
        scaffold.init_project(busy)
    assert (busy / "notes.txt").read_text(encoding="utf-8") == "keep me" and files_of(busy) == {"notes.txt"}
    a_file = tmp_path / "a-file"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError, match="is a file"):
        scaffold.init_project(a_file)
    fresh = tmp_path / "fresh"
    scaffold.init_project(fresh)
    with pytest.raises(FileExistsError):                                       # a second init into the same folder
        scaffold.init_project(fresh)


@pytest.mark.needs_git
def test_init_into_an_empty_folder_or_one_that_only_has_git(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    scaffold.init_project(empty)
    assert (empty / "labkit.yaml").exists()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    scaffold.init_project(repo)
    assert (repo / "labkit.yaml").exists() and (repo / ".git").is_dir()


@pytest.mark.needs_git
def test_init_options(tmp_path):
    root = tmp_path / "p"
    scaffold.init_project(root, name="Policy QA", git=True, main_branch="trunk")
    assert (root / "CLAUDE.md").read_text(encoding="utf-8").startswith("<!--")
    assert "# Policy QA: RAG experiment repo" in (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert load_project(root)["main_branch"] == "trunk"
    head = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"], cwd=root, capture_output=True, text=True)
    assert head.stdout.strip() == "trunk"
    with pytest.raises(ValueError, match="branch name"):
        scaffold.init_project(tmp_path / "q", main_branch=" ")


def test_init_with_git_but_no_git_creates_nothing(tmp_path, monkeypatch, capsys):
    """It copied all 30 files, then died on `[WinError 2]`; the folder was no longer empty, so init refused to retry."""
    monkeypatch.setattr("labkit.scaffold.shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match=r"--git needs git on PATH"):
        scaffold.init_project(tmp_path / "p", git=True)
    assert not (tmp_path / "p").exists()
    monkeypatch.chdir(tmp_path)
    assert main(["init", "q", "--git"]) == 1
    assert "--git needs git on PATH" in capsys.readouterr().err and not (tmp_path / "q").exists()
    assert scaffold.init_project(tmp_path / "r")                                  # without --git it never needed git


@pytest.mark.needs_git
def test_init_git_falls_back_when_git_is_older_than_2_28(tmp_path, monkeypatch):
    """`git init -b` arrived in git 2.28; on an older git the project gets the same branch another way."""
    real = subprocess.run

    def old_git(args, *a, **kw):
        if list(args[:3]) == ["git", "init", "-b"]:
            return subprocess.CompletedProcess(args, 129, "", "error: unknown switch `b'")
        return real(args, *a, **kw)
    monkeypatch.setattr("labkit.scaffold.subprocess.run", old_git)
    root = tmp_path / "p"
    scaffold.init_project(root, git=True, main_branch="trunk")
    head = real(["git", "symbolic-ref", "--short", "HEAD"], cwd=root, capture_output=True, text=True)
    assert head.stdout.strip() == "trunk"


@pytest.mark.parametrize("branch", ["bad name", "v1: x", "a..b", "-x", "x.lock", "a~b", "a@{b}", "a b/c"])
def test_init_refuses_an_invalid_branch_name_before_creating_anything(tmp_path, branch):
    """`--main-branch "bad name"` copied 48 files and only then failed in git, leaving a folder init would not retry;
    `v1: x` wrote a labkit.yaml that was not valid YAML."""
    with pytest.raises(ValueError, match="not a valid branch name"):
        scaffold.init_project(tmp_path / "p", main_branch=branch)
    assert not (tmp_path / "p").exists()


def test_the_branch_name_check_works_without_git_too(tmp_path, monkeypatch):
    monkeypatch.setattr("labkit.scaffold.shutil.which", lambda name: None)
    for bad in ("bad name", "v1: x", "a..b", "-x", "x.lock", "a/", "@{-1}"):
        with pytest.raises(ValueError, match="not a valid branch name"):
            scaffold.init_project(tmp_path / "p", main_branch=bad)
    assert not (tmp_path / "p").exists()
    scaffold.init_project(tmp_path / "ok", main_branch="release/1.0")
    assert load_project(tmp_path / "ok")["main_branch"] == "release/1.0"


@pytest.mark.parametrize("branch", ["trunk", "feature/x", "a#b", "v1.0", "release_2"])
def test_a_valid_branch_name_always_lands_in_labkit_yaml_as_the_same_text(tmp_path, branch):
    scaffold.init_project(tmp_path / "p", main_branch=branch)
    assert load_project(tmp_path / "p")["main_branch"] == branch


@pytest.mark.parametrize("title", ["Hybrid search: alpha 0.5", "[WIP] bm25", "- leading dash", "# not a comment",
                                   'say "hi"', "it's here", "a: b: c", "{x}", "100%", "  padded  ", "한글: 제목"])
def test_new_card_writes_a_config_that_loads_whatever_the_title_says(proj, title):
    """`--title "Hybrid search: alpha 0.5"` wrote `description: Hybrid search: alpha 0.5`, which is not YAML."""
    made = scaffold.new_card(proj, "x", title=title)
    cfg = load_config(proj / made["config"], root=proj)
    assert cfg["description"] == title.strip() and cfg["card"] == made["card"]
    heading = next(l for l in (proj / made["card"]).read_text(encoding="utf-8").splitlines() if l.startswith("# 0"))
    assert heading == f"# 001: {title.strip()}"                          # the card keeps the title as typed


def test_a_config_that_is_not_valid_yaml_is_one_error_line_naming_the_file(proj, capsys):
    (proj / "configs" / "bad.yaml").write_text("name: [unclosed\nretrieval: {k: 9}\n", encoding="utf-8")
    assert main(["--root", str(proj), "run", "--config", "configs/bad.yaml", "--split", "dev"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("labkit: error:") and "bad.yaml" in err and "Traceback" not in err and err.count("\n") == 1
    (proj / "labkit.yaml").write_text("golden: {files: [g.jsonl]\n", encoding="utf-8")
    assert main(["--root", str(proj), "status"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("labkit: error:") and "labkit.yaml" in err and "Traceback" not in err


def test_a_title_with_a_line_break_is_one_line_in_the_card_and_the_config(proj):
    made = scaffold.new_card(proj, "multi", title="line one\nline two")
    heading = next(l for l in (proj / made["card"]).read_text(encoding="utf-8").splitlines() if l.startswith("# 0"))
    assert heading == "# 001: line one line two"
    assert load_config(proj / made["config"], root=proj)["description"] == "line one line two"


def test_files_labkit_writes_have_unix_line_endings_on_every_platform(tmp_path):
    """On Windows `write_text` turned every newline into CRLF: init, new-card and the step record were CRLF while the
    other 28 template files were LF, so each one showed as a whole-file change in git."""
    from labkit import steps
    root = tmp_path / "p"
    scaffold.init_project(root, name="Policy QA", main_branch="trunk")
    made = scaffold.new_card(root, "x", title="A title")
    steps.save_state(root, steps.load_state(root))
    for relative in ("CLAUDE.md", "labkit.yaml", made["card"], made["config"], "report/state.yaml"):
        assert b"\r\n" not in (root / relative).read_bytes(), relative


def test_a_fresh_project_runs_labkit_commands(proj, capsys):
    assert main(["--root", str(proj), "status"]) == 0
    assert "CURRENT" in capsys.readouterr().out
    assert main(["--root", str(proj), "check"]) == 1                          # nothing done yet, but it runs


def test_a_command_outside_a_project_says_so_instead_of_naming_a_step_file(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "status"]) == 1
    err = capsys.readouterr().err
    assert "not a labkit project" in err and "labkit init" in err and "--root" in err


def test_a_missing_file_is_named_without_the_raw_os_error(proj, capsys):
    assert main(["--root", str(proj), "run", "--config", "configs/missing.yaml", "--split", "dev"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("labkit: error: file not found:") and "missing.yaml" in err and "[Errno" not in err


def test_init_through_the_cli_does_not_need_a_project(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init", "made", "--name", "Demo"]) == 0
    out = capsys.readouterr().out
    assert f"created {len(files_of(TEMPLATE))} files" in out and "labkit status" in out
    assert (tmp_path / "made" / "labkit.yaml").exists()
    assert main(["init", "made"]) == 1                                         # one-line error, no traceback
    err = capsys.readouterr().err
    assert err.startswith("labkit: error:") and "not empty" in err and "Traceback" not in err


# ---------------------------------------------------------------- new-card

def test_next_number_never_reuses_a_number(tmp_path):
    assert scaffold.next_number(tmp_path) == 1
    (tmp_path / "experiments").mkdir()
    (tmp_path / "configs").mkdir()
    for name in ("experiments/_TEMPLATE.md", "experiments/001-chunk-size.md", "experiments/002b-answers.md",
                 "configs/baseline.yaml", "configs/pricing.yaml", "configs/007-model.yaml", "experiments/notes.md"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert scaffold.next_number(tmp_path) == 8                                 # 007 is the highest; letters and non-numbered names ignored


def test_new_card_writes_a_card_and_a_config_and_the_gate_still_blocks(proj):
    made = scaffold.new_card(proj, "hybrid-search", title="Hybrid search")
    assert made["card"] == "experiments/001-hybrid-search.md" and made["config"] == "configs/001-hybrid-search.yaml"
    assert made["experiment"] == "dev_001_hybrid_search" and made["branch"].endswith("/001-hybrid-search")
    card = (proj / made["card"]).read_text(encoding="utf-8")
    assert card.startswith("<!--") and "# 001: Hybrid search" in card and "Status: planned" in card
    assert f"Branch: `{made['branch']}` · Config: `configs/001-hybrid-search.yaml`" in card
    assert "Date: 20" in card and "_YYYY-MM-DD_" not in card
    assert "## 1. Hypothesis" in card and "## 7. Decision and notes" in card   # the rest of the template is intact

    cfg = load_config(proj / made["config"], root=proj)
    assert cfg["name"] == "001_hybrid_search" and cfg["card"] == made["card"] and cfg["changed"] == {}
    # the card starts with placeholders, so a run of a config that changes something is refused until they are written
    assert len(cards.problems(proj / made["card"])) == 4
    with (proj / made["config"]).open("a", encoding="utf-8") as f:
        f.write("retrieval:\n  k: 8\n")
    changed = load_config(proj / made["config"], root=proj)
    assert list(changed["changed"]) == ["retrieval.k"]
    with pytest.raises(ValueError, match="write sections 1–4 before running"):
        cards.gate(changed, proj)


def test_new_card_numbers_follow_each_other_and_validate_input(proj):
    first = scaffold.new_card(proj, "chunk-size")
    second = scaffold.new_card(proj, "rerank", base="baseline.yaml")
    assert (first["number"], second["number"]) == (1, 2)
    assert "# 002: rerank" in (proj / second["card"]).read_text(encoding="utf-8")     # default title: the slug as words
    for bad in ("Chunk Size", "chunk_size", "-x", "x-", "a--b", "", "abc\n", "abc\r\n"):   # `$` alone lets "abc\n" through
        with pytest.raises(ValueError, match="slug"):
            scaffold.new_card(proj, bad)
    with pytest.raises(FileNotFoundError, match="configs/missing.yaml"):
        scaffold.new_card(proj, "other", base="missing.yaml")
    (proj / "experiments" / "_TEMPLATE.md").unlink()
    with pytest.raises(FileNotFoundError, match="labkit init"):
        scaffold.new_card(proj, "other")


def test_new_card_uses_the_projects_own_template(proj):
    template = proj / "experiments" / "_TEMPLATE.md"
    template.write_text(template.read_text(encoding="utf-8").replace("## 4. LLM budget", "## 4. LLM budget (team edit)"),
                        encoding="utf-8")
    made = scaffold.new_card(proj, "edited")
    assert "## 4. LLM budget (team edit)" in (proj / made["card"]).read_text(encoding="utf-8")


def test_new_card_through_the_cli(proj, capsys, monkeypatch):
    monkeypatch.chdir(proj)
    assert main(["new-card", "hybrid-search", "--title", "Hybrid search"]) == 0
    out = capsys.readouterr().out
    assert "experiments/001-hybrid-search.md" in out and "git switch -c" in out and "report/PROCESS.md on main" in out
    assert main(["new-card", "Bad Slug"]) == 1
    assert "slug" in capsys.readouterr().err
