"""labkit command line: the fixed entry points every teammate uses (workflow: docs/WORKFLOW.md).

    labkit run      --config configs/baseline.yaml --split dev [--sets a,b] [--limit 3] [--retrieval-only] [--name NAME]
    labkit grade    --experiment dev_000_baseline [--suffix __regrade1]
    labkit evaluate --experiment dev_000_baseline [--with-runs] [--overwrite]
    labkit compare  dev_000_baseline dev_004_hybrid [--metric gold@5] [--max-score N] [--noise N]
    labkit report   [--include-test]
    labkit show     dev_000_baseline [--qid Q1 ...] [--all] [--json]   read the logged answers, context, citations
    labkit golden-check [--pages corpus/pages.jsonl] [--split dev]      do the evidence quotes exist on their pages?

    labkit init PATH [--name N] [--git] [--main-branch B]
                                            start a project from the template in an empty folder
    labkit new-card SLUG [--title T] [--base CONFIG]
                                            next experiment card (experiments/NNN-slug.md) and variant config

    labkit status                           which step is current, who approved the earlier ones
    labkit check [STEP]                     do the exit criteria hold? (checked by code, or claimed with evidence)
    labkit tick STEP N --evidence "…"       claim a manual criterion
    labkit approve STEP --by NAME [--skip --note WHY]   the owner approves; the next step opens
    labkit revoke STEP --reason WHY         withdraw the latest approval

The test split needs --final on run / grade / evaluate / compare / show / report (the last workflow step only).
Every command first loads .env, forces tracing off (labkit.env), and imports the project's `modules:`
(labkit.plugins) so its systems, judges, and measures are registered. `--root` picks the project folder
(default: the nearest folder at or above the current one that contains labkit.yaml).

Where a relative path starts: `run --config` starts in the folder given with `--root` when `--root` is given, and in the
current folder when it is not (even if the project was found from labkit.yaml or LABKIT_ROOT); `golden-check --pages`
and the golden files named in labkit.yaml start at the project folder either way.
"""

import argparse
import sys
import warnings
from pathlib import Path

import yaml

from . import __version__, scaffold, steps
from .compare import compare_experiments
from .config import experiment_name, load_config
from .env import setup
from .golden_check import run_check
from .harness import SplitLocked, build_report, evaluate_experiment, grade_experiment, run_experiment
from .paths import project_root
from .plugins import load_modules
from .project import LabkitWarning, load_project
from .show import show

USER_ERRORS = (SplitLocked, FileExistsError, FileNotFoundError, KeyError, ValueError, ImportError, RuntimeError,
               yaml.YAMLError)
FINAL_HELP = "allow the test split (last workflow step only)"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="labkit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", type=Path, help="project folder (default: nearest folder with labkit.yaml). Only when given "
                                             "here, a relative --config starts in this folder; otherwise it starts in the "
                                             "current folder. A relative --pages always starts at the project folder")
    p.add_argument("--version", action="version", version=f"labkit {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    run = sub.add_parser("run", help="run a system over golden questions (resumes an interrupted batch)")
    run.add_argument("--config", required=True, help="the config to run, e.g. configs/baseline.yaml; a relative path starts "
                                                     "in the folder given with --root, else in the current folder")
    run.add_argument("--split", required=True, choices=["dev", "test"])
    run.add_argument("--sets", help="comma-separated golden sets")
    run.add_argument("--limit", type=int, help="run only the first N (at least 1) unfinished questions; up to 3 is a "
                                               "smoke test: logged, but part of no result and needs no card")
    run.add_argument("--retrieval-only", action="store_true", help="LLM-free comparison: search only")
    run.add_argument("--name", help="experiment name override (e.g. a noise rerun)")
    run.add_argument("--final", action="store_true", help=FINAL_HELP)

    grade = sub.add_parser("grade", help="grade an experiment's latest run per question with its judge")
    grade.add_argument("--experiment", required=True)
    grade.add_argument("--suffix", default="", help="write a separate grades file, e.g. __regrade1 for judge noise")
    grade.add_argument("--final", action="store_true", help=FINAL_HELP)

    evaluate = sub.add_parser("evaluate", help="score an experiment without LLM calls and export to results/")
    evaluate.add_argument("--experiment", required=True)
    evaluate.add_argument("--with-runs", action="store_true", help="also export runs.jsonl")
    evaluate.add_argument("--overwrite", action="store_true", help="only for folders not yet used in a decision "
                                                                   "(a complete test result is never overwritten)")
    evaluate.add_argument("--final", action="store_true", help=FINAL_HELP)

    compare = sub.add_parser("compare", help="paired comparison of two evaluated experiments, with cost")
    compare.add_argument("a")
    compare.add_argument("b")
    compare.add_argument("--metric", help="a metrics.jsonl column instead of the graded score")
    compare.add_argument("--max-score", type=float)
    compare.add_argument("--noise", type=int, help="questions changed in the noise run")
    compare.add_argument("--final", action="store_true", help=FINAL_HELP)

    report = sub.add_parser("report", help="collect results/*/summary.json into SUMMARY.md and out/report.html")
    report.add_argument("--include-test", action="store_true")
    report.add_argument("--final", action="store_true", help=FINAL_HELP)

    show_cmd = sub.add_parser("show", help="read what an experiment logged: answers, context, citations, grades")
    show_cmd.add_argument("experiment")
    show_cmd.add_argument("--qid", action="append", help="only this question (repeatable)")
    show_cmd.add_argument("--all", action="store_true", help="every logged row, not only the latest per question")
    show_cmd.add_argument("--json", action="store_true", help="the rows as JSON lines, nothing cut")
    show_cmd.add_argument("--chars", type=int, default=300, help="cut long texts after N characters (0 = no cut)")
    show_cmd.add_argument("--final", action="store_true", help=FINAL_HELP)

    gcheck = sub.add_parser("golden-check", help="check that every evidence quote exists on its target page")
    gcheck.add_argument("--pages", help="extracted page text, JSONL; a relative path is relative to the project root "
                                        "(default: golden.pages in labkit.yaml)")
    gcheck.add_argument("--split", choices=["dev", "test"], help="check one split only")

    init = sub.add_parser("init", help="start a new project from the template in an empty folder")
    init.add_argument("path", type=Path, help="folder to create or fill; must be empty (never overwritten)")
    init.add_argument("--name", help="project name, written into CLAUDE.md")
    init.add_argument("--git", action="store_true", help="also run `git init`")
    init.add_argument("--main-branch", default="main", help="branch for step records (default: main); must be a name git accepts, "
                                                                 "checked before anything is created")

    card = sub.add_parser("new-card", help="create the next experiment card and its variant config")
    card.add_argument("slug", help="short name: lowercase letters, digits, hyphens (e.g. hybrid-search)")
    card.add_argument("--title", help="card title (default: the slug as words)")
    card.add_argument("--base", default="baseline.yaml", help="config the card builds on (default: baseline.yaml)")

    sub.add_parser("status", help="which workflow step is current, and who approved the earlier ones")

    check = sub.add_parser("check", help="do the exit criteria of a step hold? (default: the current step)")
    check.add_argument("step", nargs="?", type=int)

    tick = sub.add_parser("tick", help="claim a manual exit criterion, with the evidence that it holds")
    tick.add_argument("step", type=int)
    tick.add_argument("criterion", type=int, help="criterion number as listed by `labkit check`")
    tick.add_argument("--evidence", required=True, help="what shows it holds: a file, a section, a number")
    tick.add_argument("--by", help="who claims it (default: git user.name)")

    approve = sub.add_parser("approve", help="the owner approves a step whose criteria hold; the next step opens")
    approve.add_argument("step", type=int)
    approve.add_argument("--by", help="who approves (default: git user.name)")
    approve.add_argument("--note", default="")
    approve.add_argument("--skip", action="store_true", help="skip an optional step (needs --note: why)")

    revoke = sub.add_parser("revoke", help="withdraw an approval after finding a problem (latest approved step only)")
    revoke.add_argument("step", type=int)
    revoke.add_argument("--reason", required=True)
    revoke.add_argument("--by", help="who revokes (default: git user.name)")
    return p


def dispatch(args, root: Path) -> int:
    if args.command == "run":
        cfg = load_config(args.config, root=root)
        experiment = args.name or experiment_name(cfg, args.split, args.retrieval_only)
        print(f"{experiment}: changed vs baseline = {cfg['changed'] or 'nothing'}")
        if cfg["changed_from_base"] != cfg["changed"]:  # built on another variant: show what this config adds
            print(f"{experiment}: changed vs its base = {cfg['changed_from_base'] or 'nothing'}")
        sets = [s.strip() for s in args.sets.split(",") if s.strip()] if args.sets is not None else None  # "a, b" is a and b
        result = run_experiment(cfg, args.split, experiment=experiment, sets=sets, limit=args.limit,
                                retrieval_only=args.retrieval_only, final=args.final, root=root,
                                warn=lambda message: print(f"labkit: warning: {message}", file=sys.stderr))
        print(result)
    elif args.command == "grade":
        print(grade_experiment(args.experiment, suffix=args.suffix, final=args.final, root=root))
    elif args.command == "evaluate":
        s = evaluate_experiment(args.experiment, with_runs=args.with_runs, final=args.final,
                                overwrite=args.overwrite, root=root)
        print({k: s[k] for k in ("experiment", "complete", "n_expected", "n_questions", "n_errors", "n_graded",
                                 "scores", "metrics", "cost")})
        if not s["complete"]:
            print(f"labkit: warning: {s['experiment']} is incomplete ({'; '.join(s['incomplete'])}). The scores cover "
                  "only the questions that ran and were graded, and a failed question is not counted as 0.",
                  file=sys.stderr)
        if s["environment_check"]["mixed_commits"]:
            print(f"labkit: warning: the runs of {s['experiment']} were made on different versions of the code "
                  f"({', '.join(s['environment_check']['code_versions'])}; +hash marks uncommitted edits), so the "
                  "result may mix two versions. Rerun it under a new name if the code change could matter.",
                  file=sys.stderr)
        changed = (s.get("file_check") or {}).get("changed_since_run")
        if changed:
            print(f"labkit: warning: {', '.join(changed)} changed since {s['experiment']} ran, so the repository no "
                  "longer holds what produced these results. Restore the file or rerun under a new name.",
                  file=sys.stderr)
        if (s.get("card_check") or {}).get("edited_after_run"):
            print(f"labkit: warning: {s['card_check']['file']} sections 1–4 were edited after the first run of "
                  f"{s['card_check'].get('first_run_experiment') or s['experiment']}; the criteria may have been "
                  "fitted to the results. Say so in section 7.", file=sys.stderr)
    elif args.command == "compare":
        print(compare_experiments(args.a, args.b, metric=args.metric, max_score=args.max_score, noise=args.noise,
                                  final=args.final, root=root))
    elif args.command == "report":
        print(build_report(include_test=args.include_test, final=args.final, root=root))
    elif args.command == "show":
        print(show(args.experiment, qids=args.qid, all_rows=args.all, as_json=args.json, chars=args.chars,
                   final=args.final, root=root))
    elif args.command == "golden-check":
        text, ok = run_check(args.pages, args.split, root)
        print(text)
        return 0 if ok else 1
    elif args.command == "init":
        files = scaffold.init_project(args.path, args.name, args.git, args.main_branch)
        print(f"created {len(files)} files in {Path(args.path).resolve()}")
        print(f"next: cd {args.path}; edit labkit.yaml and docs/WORKFLOW.md (Project facts); labkit status")
    elif args.command == "new-card":
        made = scaffold.new_card(root, args.slug, args.title, args.base)
        print(f"created {made['card']} and {made['config']}")
        print("next:\n"
              f"  1. claim the card in report/PROCESS.md on {load_project(root)['main_branch']} (commit that alone)\n"
              f"  2. git switch -c {made['branch']}\n"
              f"  3. write sections 1-4 of {made['card']} before running anything\n"
              f"  4. put only the changed keys in {made['config']}, then: labkit run --config {made['config']} "
              "--split dev --limit 1")
    elif args.command == "status":
        print(steps.format_status(root))
    elif args.command == "check":
        text, ok = steps.format_check(root, args.step)
        print(text)
        return 0 if ok else 1
    elif args.command == "tick":
        print(steps.tick(root, args.step, args.criterion, args.evidence, args.by))
    elif args.command == "approve":
        print(steps.approve(root, args.step, args.by, args.note, args.skip))
    elif args.command == "revoke":
        print(steps.revoke(root, args.step, args.reason, args.by))
    return 0


_SHOW_WARNING = warnings.showwarning


def _show_warning(message, category, filename, lineno, file=None, line=None):
    if issubclass(category, LabkitWarning):
        print(f"labkit: warning: {message}", file=sys.stderr)
    else:
        _SHOW_WARNING(message, category, filename, lineno, file, line)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # consoles that default to a legacy code page cannot print non-ASCII text
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else project_root()
    if args.root and args.command == "run":  # an explicit --root is where a relative --config starts (as --pages always does)
        args.config = str(root / args.config)  # an absolute --config stays as it is
    try:
        with warnings.catch_warnings():  # labkit's own warnings as one line each, like its errors
            warnings.showwarning = _show_warning
            if args.command != "init":  # init runs where no project exists yet
                setup(root)
                load_modules(root)
            return dispatch(args, root)
    except USER_ERRORS as e:
        message = e.args[0] if isinstance(e, KeyError) and e.args else e
        if isinstance(e, FileNotFoundError) and e.filename:  # not the raw "[Errno 2] No such file or directory: ..."
            message = f"file not found: {e.filename}"
        if isinstance(e, yaml.YAMLError):  # names the file and line; a YAML parser's message spans several lines
            message = "not valid YAML: " + " ".join(str(e).split())
        print(f"labkit: error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
