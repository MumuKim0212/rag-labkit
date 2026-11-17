"""labkit.config, cost, paths, repro, env: all against temp project folders (no LLM, no network)."""

import os
import subprocess

import pytest

from labkit.config import experiment_name, load_config, settings
from labkit.cost import call_usd, load_prices, record_call, totals
from labkit.env import TRACING_VARS, require_env, setup
from labkit.paths import MARKER, baseline_path, project_root
from labkit.repro import environment, git_info, requirement_names

BASELINE = "name: 000_baseline\nchunking: {chunk_size: 800, chunk_overlap: 100}\nretrieval: {method: vector, k: 5}\n"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("LABKIT_ROOT", raising=False)
    write(tmp_path / MARKER, "")
    write(tmp_path / "configs" / "baseline.yaml", BASELINE)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_project_root_marker_env_and_fallback(project, tmp_path, monkeypatch):
    sub = project / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert project_root() == project                        # nearest ancestor with labkit.yaml
    assert baseline_path() == project / "configs" / "baseline.yaml"
    other = tmp_path.parent
    monkeypatch.setenv("LABKIT_ROOT", str(other))
    assert project_root() == other.resolve()                # the environment variable wins
    monkeypatch.delenv("LABKIT_ROOT")
    assert project_root(other) == other.resolve()           # no marker anywhere above: the start folder


def test_baseline_has_no_changes(project):
    cfg = load_config(project / "configs" / "baseline.yaml")
    assert cfg["changed"] == {} and cfg["config_file"] == "configs/baseline.yaml"
    assert experiment_name(cfg, "dev") == "dev_000_baseline"
    assert experiment_name(cfg, "dev", retrieval_only=True) == "dev_000_baseline_retrieval"


def test_variant_records_only_its_change(project):
    cfgs = project / "configs"
    v1 = write(cfgs / "001-chunk400.yaml", "base: baseline.yaml\nname: 001_chunk400\nchunking: {chunk_size: 400}\n")
    v2 = write(cfgs / "004-hybrid.yaml", "base: 001-chunk400.yaml\nname: 004_hybrid\nretrieval: {method: hybrid}\n")
    cfg1, cfg2 = load_config(v1), load_config(v2)
    assert cfg1["changed"] == {"chunking.chunk_size": {"from": 800, "to": 400}}
    assert set(cfg2["changed"]) == {"chunking.chunk_size", "retrieval.method"}
    assert cfg2["chunking"]["chunk_overlap"] == 100          # untouched keys inherited
    assert cfg1["config_hash"] != cfg2["config_hash"]
    assert load_config(v1)["config_hash"] == cfg1["config_hash"]


def test_a_chained_variant_shows_its_own_change_next_to_the_whole_chain(project):
    cfgs = project / "configs"
    write(cfgs / "001-chunk400.yaml", "base: baseline.yaml\nname: 001_chunk400\nchunking: {chunk_size: 400}\n")
    v2 = write(cfgs / "004-hybrid.yaml", "base: 001-chunk400.yaml\nname: 004_hybrid\nretrieval: {method: hybrid}\n")
    cfg = load_config(v2)
    assert set(cfg["changed"]) == {"chunking.chunk_size", "retrieval.method"}    # everything since baseline.yaml
    assert list(cfg["changed_from_base"]) == ["retrieval.method"]                # what this card is about
    assert load_config(cfgs / "001-chunk400.yaml")["changed_from_base"] == load_config(cfgs / "001-chunk400.yaml")["changed"]
    assert load_config(cfgs / "baseline.yaml")["changed_from_base"] == {}


def test_what_identifies_a_file_is_not_inherited(project):
    cfgs = project / "configs"
    write(cfgs / "001-chunk400.yaml", "base: baseline.yaml\nname: 001_chunk400\ncard: experiments/001-chunk.md\n"
                                      "description: chunks of 400\ncompare_to: dev_000_baseline\nchunking: {chunk_size: 400}\n")
    child = write(cfgs / "002-k9.yaml", "base: 001-chunk400.yaml\nretrieval: {k: 9}\n")      # no name, card, description
    cfg = load_config(child)
    assert cfg["name"] == "002-k9"                                    # its own file name, not the parent's `001_chunk400`
    assert experiment_name(cfg, "dev") == "dev_002-k9"
    assert all(key not in cfg for key in ("card", "description", "compare_to"))
    assert cfg["chunking"]["chunk_size"] == 400                       # settings still are inherited
    # the baseline's own name is not inherited either: a variant without `name:` must not become dev_000_baseline
    bare = write(cfgs / "003-bare.yaml", "base: baseline.yaml\nretrieval: {k: 3}\n")
    assert load_config(bare)["name"] == "003-bare"


def test_execution_settings_are_not_part_of_the_config(project):
    cfgs = project / "configs"
    base = load_config(cfgs / "baseline.yaml")
    more_workers = write(cfgs / "faster.yaml", "base: baseline.yaml\nname: faster\nrun: {concurrency: 8}\n")
    cfg = load_config(more_workers)
    assert cfg["run"]["concurrency"] == 8                             # still read by the harness
    assert cfg["changed"] == {} and cfg["config_hash"] == base["config_hash"]   # but not a variant, and the same setup


@pytest.mark.parametrize("line, key", [
    ("corpus_date: 2026-01-01", "corpus_date"),                                  # an unquoted YAML date
    ("snapshot: 2026-01-01 10:30:00", "snapshot"),
    ("steps: [{when: 2026-01-01}]", r"steps\[0\].when"),
    ("limits: {nested: {at: 2026-01-01}}", r"limits.nested.at"),
    ("flags: !!set {a, b}", "flags"),
])
def test_a_config_value_the_run_log_cannot_store_is_refused_when_the_config_loads(project, line, key):
    """An unquoted date made `labkit run` die with a TypeError inside RunLog.save, after the first question had
    already been answered and paid for, and the answer was lost."""
    write(project / "configs" / "baseline.yaml", BASELINE + line + "\n")
    with pytest.raises(ValueError, match=rf"baseline.yaml: `{key}` is .*write.*in quotes"):
        load_config(project / "configs" / "baseline.yaml")


@pytest.mark.parametrize("value", [".inf", "-.inf", ".nan"])
def test_a_non_finite_number_in_a_config_is_refused_where_it_is_written(project, value):
    """`timeout_s: .inf` ran and graded, then evaluate refused with a message that blamed a system or a judge."""
    write(project / "configs" / "baseline.yaml", BASELINE + f"run: {{timeout_s: {value}}}\n")
    with pytest.raises(ValueError, match=r"baseline.yaml: `run.timeout_s` is .*not a finite number"):
        load_config(project / "configs" / "baseline.yaml")


def test_a_bad_value_is_refused_in_a_variant_and_names_that_file(project):
    write(project / "configs" / "001-x.yaml", "base: baseline.yaml\nname: 001_x\nretrieval: {since: 2026-01-01}\n")
    with pytest.raises(ValueError, match=r"001-x.yaml: `retrieval.since`"):
        load_config(project / "configs" / "001-x.yaml")


def test_quoted_dates_and_ordinary_values_still_load(project):
    write(project / "configs" / "baseline.yaml", BASELINE + "corpus_date: '2026-01-01'\nratio: 0.5\nok: true\nnone: null\n"
          "items: [1, two, {k: 3.5}]\n")
    cfg = load_config(project / "configs" / "baseline.yaml")
    assert cfg["corpus_date"] == "2026-01-01" and cfg["items"][2] == {"k": 3.5}


def test_a_misspelled_output_price_key_fails_loudly_instead_of_pricing_at_zero():
    """`{input: 0.75, outpt: 3.75}` priced every answer's output at zero: cost.usd came out half of what it was."""
    typo = {"m": {"input": 0.75, "outpt": 3.75}}
    with pytest.raises(KeyError, match=r"no `output` price for 'm'.*5 output tokens"):
        call_usd({"model": "m", "input_tokens": 10, "output_tokens": 5}, typo)
    assert call_usd({"model": "m", "input_tokens": 1_000_000, "output_tokens": 0}, typo) == pytest.approx(0.75)
    embedding_only = {"e": {"input": 0.02}}                                  # an embedding model has no output price
    assert call_usd({"model": "e", "input_tokens": 1_000_000, "output_tokens": 0}, embedding_only) == pytest.approx(0.02)


def test_two_model_names_that_differ_only_by_case_or_blanks_are_one_model():
    from labkit.config import same_model
    assert same_model("Model-A", " model-a ") and not same_model("model-a", "model-b")
    assert not same_model(None, None) and not same_model("", "")                # nothing is not "the same model"


def test_only_the_worker_count_is_outside_the_hash_not_everything_under_run(project):
    """The template puts `timeout_s` and `max_retries` under `run:` next to `concurrency`. A timeout changes which
    questions fail and a retry count changes what a flaky system returns, so they are part of what a result means."""
    cfgs = project / "configs"
    write(cfgs / "baseline.yaml", BASELINE + "run: {concurrency: 4, timeout_s: 120, max_retries: 2}\n")
    base = load_config(cfgs / "baseline.yaml")
    workers = load_config(write(cfgs / "w.yaml", "base: baseline.yaml\nname: w\nrun: {concurrency: 1}\n"))
    assert workers["changed"] == {} and workers["config_hash"] == base["config_hash"]
    short = load_config(write(cfgs / "t.yaml", "base: baseline.yaml\nname: t\nrun: {timeout_s: 30}\n"))
    assert short["changed"] == {"run.timeout_s": {"from": 120, "to": 30}}      # a variant: it needs a card
    assert short["config_hash"] != base["config_hash"]
    assert short["run"] == {"concurrency": 4, "timeout_s": 30, "max_retries": 2}   # and the harness still reads it all
    retries = load_config(write(cfgs / "r.yaml", "base: baseline.yaml\nname: r\nrun: {max_retries: 5, concurrency: 9}\n"))
    assert set(retries["changed"]) == {"run.max_retries"}
    only = load_config(write(cfgs / "o.yaml", "name: o\nrun: {concurrency: 2}\n"))          # nothing else under run
    assert "run" not in settings(only) and settings(only) == {}


def test_hash_ignores_meta_keys_and_loops_are_rejected(project):
    cfgs = project / "configs"
    a = write(cfgs / "a.yaml", "base: baseline.yaml\nname: a\ndescription: x\nchunking: {chunk_size: 400}\n")
    b = write(cfgs / "b.yaml", "base: baseline.yaml\nname: b\ndescription: y\nchunking: {chunk_size: 400}\n")
    assert load_config(a)["config_hash"] == load_config(b)["config_hash"]
    write(cfgs / "x.yaml", "base: y.yaml\n")
    write(cfgs / "y.yaml", "base: x.yaml\n")
    with pytest.raises(ValueError, match="loop"):
        load_config(cfgs / "x.yaml")


def test_cost_totals_and_prices(project):
    prices = {"m": {"input": 1.0, "output": 4.0}, "e": {"input": 0.2}}
    steps = {}
    record_call(steps, node="generate", model="m", input_tokens=1_000_000, output_tokens=500_000, thinking_tokens=200_000)
    record_call(steps, node="embed", model="e", kind="embedding", input_tokens=1_000_000)
    t = totals(steps["calls"], prices)
    assert t["usd"] == pytest.approx(1.0 + 2.0 + 0.2)
    assert (t["llm_calls"], t["embedding_calls"], t["thinking_tokens"]) == (1, 1, 200_000)
    with pytest.raises(KeyError):
        call_usd({"model": "unknown"}, prices)
    write(project / "configs" / "pricing.yaml", "models:\n  m: {input: 1.0, output: 4.0}\n")
    assert load_prices() == {"m": {"input": 1.0, "output": 4.0}}


def test_requirements_and_environment_outside_git(project):
    assert requirement_names(project) == []                  # no requirements.txt
    write(project / "requirements.txt", "# pins\nPyYAML==6.0.3  # config\npytest>=8\n\n")
    assert requirement_names(project) == ["PyYAML", "pytest"]
    env = environment(project)
    assert env["packages"]["PyYAML"] is not None
    assert set(env["git"]) == {"commit", "branch", "dirty", "changes"}


def commit_all(root):
    for args in (["add", "."], ["commit", "-m", "snapshot"]):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root, check=True,
                       capture_output=True)


@pytest.fixture
def repo_root(project, monkeypatch):
    """The `project` folder as a git repository whose temp parent is not part of another repository."""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(project.parent))
    try:
        subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    return project


def test_code_folders_follow_the_projects_pythonpath_and_modules(project):
    """The fingerprint watched only src, scripts, configs, prompts and golden: an edit to a plugin kept anywhere else
    (`pythonpath: [lib]`, or a package at the project root) left `dirty: false`."""
    from labkit.repro import TRACKED, code_folders
    assert code_folders(project) == list(TRACKED)                              # no pythonpath given: [.], no modules
    write(project / "labkit.yaml", "pythonpath: [lib, ../elsewhere, .]\nmodules: [myplug.sys, single, absent.mod]\n")
    (project / "lib").mkdir()
    (project / "myplug").mkdir()
    write(project / "single.py", "X = 1\n")
    folders = code_folders(project)
    assert folders == [*TRACKED, "lib", "myplug", "single.py"]               # outside paths and missing modules dropped
    write(project / "labkit.yaml", "pythonpath: [{unclosed\n")                  # unreadable: the defaults, no crash
    assert code_folders(project) == list(TRACKED)


@pytest.mark.needs_git
def test_an_edit_to_a_plugin_in_a_pythonpath_folder_is_seen(repo_root):
    write(repo_root / "labkit.yaml", "pythonpath: [lib]\nmodules: [myplug.sys]\n")
    write(repo_root / "lib" / "myplug" / "sys.py", "V = 1\n")
    commit_all(repo_root)
    assert git_info(repo_root)["dirty"] is False and git_info(repo_root)["changes"] is None
    write(repo_root / "lib" / "myplug" / "sys.py", "V = 2\n")
    first = git_info(repo_root)
    write(repo_root / "lib" / "myplug" / "sys.py", "V = 3\n")
    second = git_info(repo_root)
    assert first["dirty"] is True and first["changes"] and second["changes"] not in (None, first["changes"])


@pytest.mark.needs_git
def test_a_plugin_package_at_the_project_root_is_seen_but_other_root_files_are_not(repo_root):
    write(repo_root / "labkit.yaml", "modules: [myplug.sys, single]\n")                       # pythonpath defaults to [.]
    write(repo_root / "myplug" / "sys.py", "V = 1\n")
    write(repo_root / "single.py", "S = 1\n")
    write(repo_root / "notes.md", "a\n")
    commit_all(repo_root)
    write(repo_root / "notes.md", "b\n")                                                       # not code
    write(repo_root / "results" / "x" / "summary.json", "{}")                                  # an export
    assert git_info(repo_root)["dirty"] is False
    write(repo_root / "myplug" / "sys.py", "V = 2\n")
    assert git_info(repo_root)["dirty"] is True
    commit_all(repo_root)
    write(repo_root / "single.py", "S = 2\n")                                                  # a module that is one file
    assert git_info(repo_root)["dirty"] is True


@pytest.mark.needs_git
def test_the_fingerprint_does_not_read_a_huge_untracked_file_whole(repo_root, monkeypatch):
    """A 2 GB file in a watched folder would have been read into memory; above the limit only its size counts."""
    monkeypatch.setattr("labkit.repro.MAX_HASHED_BYTES", 8)
    write(repo_root / "src" / "plug.py", "V = 1\n")
    commit_all(repo_root)
    (repo_root / "src" / "blob.bin").write_bytes(b"a" * 100)
    first = git_info(repo_root)["changes"]
    (repo_root / "src" / "blob.bin").write_bytes(b"b" * 100)                                   # same size: not seen
    assert first and git_info(repo_root)["changes"] == first
    (repo_root / "src" / "blob.bin").write_bytes(b"b" * 101)                                   # another size: seen
    assert git_info(repo_root)["changes"] != first
    (repo_root / "src" / "small.txt").write_text("tiny", encoding="utf-8")                      # under the limit: by content
    one = git_info(repo_root)["changes"]
    (repo_root / "src" / "small.txt").write_text("tinY", encoding="utf-8")
    assert git_info(repo_root)["changes"] != one


@pytest.mark.needs_git
def test_changes_fingerprint_tells_two_uncommitted_versions_of_the_code_apart(project):
    """`dirty: true` before and after an edit looks the same, so a batch resumed after a mid-run code fix was not
    flagged as mixed. `changes` hashes the uncommitted edits and new files in the run-defining folders."""
    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=project, check=True,
                       capture_output=True)
    try:
        git("init")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    write(project / "src" / "plug.py", "VALUE = 1\n")
    git("add", ".")
    git("commit", "-m", "init")
    assert git_info(project)["changes"] is None                           # clean: nothing to fingerprint
    write(project / "results" / "x" / "summary.json", "{}")               # not a run-defining folder
    assert git_info(project)["changes"] is None
    write(project / "src" / "plug.py", "VALUE = 2\n")
    first = git_info(project)["changes"]
    assert first and git_info(project)["changes"] == first               # stable while nothing changes
    write(project / "src" / "plug.py", "VALUE = 3\n")
    second = git_info(project)["changes"]
    assert second and second != first and git_info(project)["dirty"] is True
    write(project / "prompts" / "new.md", "a new, untracked prompt\n")   # new files count as code too
    third = git_info(project)["changes"]
    assert third not in (None, first, second)
    write(project / "src" / "plug.py", "VALUE = 1\n")                     # back to the committed code + the new file
    write(project / "prompts" / "new.md", "an edited untracked prompt\n")
    assert git_info(project)["changes"] not in (None, first, second, third)


@pytest.mark.needs_git
def test_git_dirty_looks_only_at_run_defining_folders(project):
    def git(*args):
        subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)
    try:
        git("init")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    git("-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
    assert git_info(project)["dirty"] is False
    write(project / "results" / "x" / "summary.json", "{}")  # results are not part of what defines a run
    assert git_info(project)["dirty"] is False
    write(project / "configs" / "baseline.yaml", BASELINE + "# edited\n")
    assert git_info(project)["dirty"] is True


def test_setup_forces_tracing_off_and_reads_env_file(project, monkeypatch):
    pytest.importorskip("dotenv")
    write(project / ".env", "LANGSMITH_TRACING=true\nMY_KEY=abc\n")
    for var in ("MY_KEY", *TRACING_VARS):        # set then unset, so teardown restores "unset" and nothing leaks
        monkeypatch.setenv(var, "x")
        monkeypatch.delenv(var)
    setup(project)
    assert all(os.environ[v] == "false" for v in TRACING_VARS)     # .env cannot turn tracing back on
    assert require_env("MY_KEY") == "abc"
    monkeypatch.setenv("MY_KEY", "your-key-here")
    with pytest.raises(RuntimeError):
        require_env("MY_KEY")


# ---------------------------------------------------------------- reserved keys

@pytest.mark.parametrize("key", ["smoke", "selection", "environment", "retrieval_only", "file_hashes", "card_check",
                                 "index_key", "config_hash", "changed"])
def test_a_config_may_not_use_a_key_labkit_writes_into_the_stored_run_config(project, key):
    """`smoke: true` in a baseline once turned every full run into smoke rows: never resumed, never evaluated."""
    write(project / "configs" / "baseline.yaml", BASELINE + f"{key}: true\n")
    with pytest.raises(ValueError, match=rf"baseline.yaml: `{key}` is a key labkit writes itself"):
        load_config(project / "configs" / "baseline.yaml")


def test_a_reserved_key_is_refused_in_any_file_of_the_chain(project):
    write(project / "configs" / "001-x.yaml", "base: baseline.yaml\nname: 001_x\nsmoke: true\n")
    with pytest.raises(ValueError, match=r"001-x.yaml.*`smoke`"):
        load_config(project / "configs" / "001-x.yaml")
    write(project / "configs" / "baseline.yaml", BASELINE + "selection: {split: test}\n")
    write(project / "configs" / "002-y.yaml", "base: baseline.yaml\nname: 002_y\n")
    with pytest.raises(ValueError, match=r"baseline.yaml.*`selection`"):
        load_config(project / "configs" / "002-y.yaml")
