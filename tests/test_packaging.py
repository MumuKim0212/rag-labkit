"""What gets installed: the wheel must carry the whole template, and a new project must be able to install labkit.

The rest of the suite imports labkit from the source tree (`pythonpath = ["."]`), so nothing else would notice a
template file that `pyproject.toml` fails to ship: an installed `labkit init` would give a project with no
.gitignore (so .env and out/ are not ignored) and no /labkit-next skill, while every other test still passed.
"""

import re
import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest

from labkit import __version__
from labkit.repro import requirement_names

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "labkit" / "template"
REPO = "https://github.com/MumuKim0212/rag-labkit"


def pyproject_text() -> str:  # a regex, not tomllib: the package supports Python 3.10, which has no TOML reader
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_the_package_version_and_pyproject_agree():
    assert re.search(r'^version = "([^"]+)"', pyproject_text(), re.MULTILINE).group(1) == __version__


def test_a_new_project_requirements_file_names_a_place_pip_can_install_labkit_from():
    """`rag-labkit` alone is not on PyPI: `pip install -r requirements.txt` failed in every fresh project. The line
    pins a tag of the team's repository, and that tag is this package's version, so a release bumps both."""
    text = (TEMPLATE / "requirements.txt").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert lines == [f"rag-labkit[env] @ git+{REPO}.git@v{__version__}"]
    assert requirement_names(TEMPLATE) == ["rag-labkit"]          # labkit records this package's version in each run


@pytest.mark.parametrize("readme", ["README.md", "docs/README.ko.md"])
def test_the_readme_says_how_to_install_from_the_repository(readme):
    """The Korean copy has its own install line, so a release bumps it too."""
    assert f"git+{REPO}.git@v{__version__}" in (ROOT / readme).read_text(encoding="utf-8")


@pytest.mark.parametrize("readme", ["README.md", "docs/README.ko.md"])
def test_the_readmes_serve_a_visitor_not_a_maintainer(readme):
    """The repository is public: the README says how to get the source (a clone, not "this folder") and links to the
    workflow the eight steps come from. How a release is made is not for the README, and the public repository is a
    copy that is not built from commits, so the old release paragraph would misdescribe it."""
    path = ROOT / readme
    text = path.read_text(encoding="utf-8")
    assert f"git clone {REPO}.git" in text
    assert "labkit/template/docs/WORKFLOW.md" in text
    assert "To release" not in text and "릴리스할 때는" not in text
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
        if not target.startswith(("http", "mailto")):
            assert (path.parent / target).exists(), f"{readme}: {target} does not exist"


def test_pyproject_describes_the_package():
    assert re.search(r'^readme = "README.md"', pyproject_text(), re.MULTILINE) and (ROOT / "README.md").is_file()


def test_the_package_declares_the_mit_license_and_where_it_lives():
    """The repository is public: without a license nobody may use it. PEP 639 fields need setuptools 77."""
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License") and "Permission is hereby granted, free of charge" in text
    assert re.search(r'^license = "MIT"', pyproject_text(), re.MULTILINE)
    assert re.search(r'^license-files = \["LICENSE"\]', pyproject_text(), re.MULTILINE)
    assert re.search(r'^requires = \["setuptools>=77"\]', pyproject_text(), re.MULTILINE)
    assert f"{REPO}" in pyproject_text()                       # [project.urls]


@pytest.mark.parametrize("readme, link", [("README.md", "(LICENSE)"), ("docs/README.ko.md", "(../LICENSE)")])
def test_the_readmes_name_the_license_and_link_to_the_file(readme, link):
    """The license is named where a visitor reads (the link target is relative to the README's own folder)."""
    text = (ROOT / readme).read_text(encoding="utf-8")
    assert f"[MIT License]{link}" in text
    assert ((ROOT / readme).parent / link.strip("()")).resolve() == (ROOT / "LICENSE").resolve()


def test_the_wheel_ships_every_template_file(tmp_path, monkeypatch):
    """Built from a copy of the repository, so nothing is left in the working tree. A package-data glob list that
    forgets dotfiles still installs, passes every other test, and ships a template without its .gitignore."""
    build_meta = pytest.importorskip("setuptools.build_meta")
    src = tmp_path / "src"
    shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".git", "__pycache__", "tests", "build", "dist", "*.egg-info",
                                                             ".pytest_cache", ".venv", "venv"))
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.chdir(src)
    try:
        wheel = build_meta.build_wheel(str(out))
    except ModuleNotFoundError as e:  # setuptools older than 70.1 builds wheels through the `wheel` package
        pytest.skip(f"cannot build a wheel here: {e}")
    names = set(zipfile.ZipFile(out / wheel).namelist())
    expected = {f"labkit/template/{p.relative_to(TEMPLATE).as_posix()}"
                for p in TEMPLATE.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    assert len(expected) >= 30 and not expected - names, f"missing from the wheel: {sorted(expected - names)}"
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
    assert {"labkit/template/.gitignore", "labkit/template/.env.example",
            "labkit/template/.claude/skills/labkit-next/SKILL.md"} <= names
    assert f"rag_labkit-{__version__}.dist-info/licenses/LICENSE" in names


def test_a_template_file_added_later_is_shipped_without_touching_pyproject(tmp_path, monkeypatch):
    """The old globs listed .claude/ by name: a new dot-folder such as .github/ was silently dropped."""
    build_meta = pytest.importorskip("setuptools.build_meta")
    src = tmp_path / "src"
    shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".git", "__pycache__", "tests", "build", "dist", "*.egg-info",
                                                             ".pytest_cache", ".venv", "venv"))
    for relative in (".github/workflows/ci.yml", "docs/.hidden/note.txt", ".newdotfile"):
        path = src / "labkit" / "template" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.chdir(src)
    try:
        wheel = build_meta.build_wheel(str(out))
    except ModuleNotFoundError as e:
        pytest.skip(f"cannot build a wheel here: {e}")
    names = set(zipfile.ZipFile(out / wheel).namelist())
    assert {f"labkit/template/{p}" for p in (".github/workflows/ci.yml", "docs/.hidden/note.txt", ".newdotfile")} <= names


def test_local_files_in_the_template_folder_never_ship(tmp_path, monkeypatch):
    """`recursive-include labkit/template *` takes whatever is on disk, not what git tracks: a developer's own `.env`
    (with real keys) or an editor's backup file would ship in the wheel and be copied into every new project. The
    template's own `.env.example` must still ship."""
    build_meta = pytest.importorskip("setuptools.build_meta")
    src = tmp_path / "src"
    shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".git", "__pycache__", "tests", "build", "dist", "*.egg-info",
                                                             ".pytest_cache", ".venv", "venv"))
    for stray in (".env", ".DS_Store", "notes.txt~", "WORKFLOW.md.swp", "configs/.env", "docs/Thumbs.db"):
        path = src / "labkit" / "template" / stray
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("SECRET=1\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.chdir(src)
    try:
        wheel = build_meta.build_wheel(str(out))
    except ModuleNotFoundError as e:
        pytest.skip(f"cannot build a wheel here: {e}")
    names = set(zipfile.ZipFile(out / wheel).namelist())
    shipped = [n for n in names if n.rsplit("/", 1)[-1] in {".env", ".DS_Store", "Thumbs.db"}
               or n.endswith(("~", ".swp"))]
    assert not shipped, shipped
    assert "labkit/template/.env.example" in names


def test_the_sdist_ships_every_test_file_with_the_conftest(tmp_path, monkeypatch):
    """setuptools' default takes `tests/test_*.py` into an sdist but not `tests/conftest.py`. The sdist's tests then
    ran without the conftest that skips the git tests where git is missing, and one of them failed instead."""
    build_meta = pytest.importorskip("setuptools.build_meta")
    src = tmp_path / "src"
    shutil.copytree(ROOT, src, ignore=shutil.ignore_patterns(".git", "__pycache__", "build", "dist", "*.egg-info",
                                                             ".pytest_cache", ".venv", "venv"))
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.chdir(src)
    sdist = build_meta.build_sdist(str(out))
    with tarfile.open(out / sdist) as tar:
        names = {name.split("/", 1)[1] for name in tar.getnames() if "/" in name}   # drop the rag_labkit-X.Y.Z/ folder
    expected = {f"tests/{p.name}" for p in (ROOT / "tests").iterdir() if p.is_file() and p.suffix == ".py"}
    assert "tests/conftest.py" in expected and len(expected) >= 15
    # tests/test_doc_commands.py reads the Korean README; without it an unpacked sdist fails that test.
    expected |= {"LICENSE", "docs/README.ko.md"}
    assert not expected - names, f"missing from the sdist: {sorted(expected - names)}"
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]


def test_the_dev_extra_installs_what_the_wheel_tests_need():
    """Without setuptools the wheel and sdist tests are skipped, and a default dev environment never ran them."""
    dev = re.search(r"^dev = \[([^\]]*)\]", pyproject_text(), re.MULTILINE).group(1)
    assert "setuptools" in dev


def test_template_has_no_python_cache_files():
    assert not [p for p in TEMPLATE.rglob("*") if "__pycache__" in p.parts or p.suffix == ".pyc"]
    assert re.search(r"__pycache__", (TEMPLATE / ".gitignore").read_text(encoding="utf-8"))
