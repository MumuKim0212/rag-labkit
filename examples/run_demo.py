"""Run the toy-rag example from an empty folder to two decided experiment cards, and print what labkit says.

    python examples/run_demo.py                          # a temporary project, deleted afterwards
    python examples/run_demo.py --keep my-demo           # keep the project in my-demo/ to look around
    python examples/run_demo.py --transcript out.txt     # also write the output to a file

It needs only labkit and git: the "models" of the example are simulated (examples/toy-rag/src/toyrag), so there is no
network and no API key. examples/transcript.txt is its output, and tests/test_examples.py checks that it is current.
What the output shows is explained in examples/README.md.
"""

import argparse
import ast
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXAMPLE = HERE / "toy-rag"
CARD_FILES = ("configs/001-bigger-chunks.yaml", "experiments/001-bigger-chunks.md",
              "configs/002-more-k.yaml", "experiments/002-more-k.md")
KEY_METRICS = ("evid@1", "evid", "status_ok", "citation_on_target")


class Demo:
    def __init__(self, project: Path):
        self.project = project
        self.out: list[str] = []
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep),
               "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        for name in ("LABKIT_ROOT", "TOYRAG_SEED"):
            env.pop(name, None)
        self.env = env

    # ---- output
    def say(self, text: str = "") -> None:
        self.out.append(text)

    def heading(self, text: str) -> None:
        self.say(f"\n== {text}\n")

    def clean(self, text: str) -> str:
        text = text.replace("\r\n", "\n")
        # labkit prints the resolved path, which can differ from the one given (a short 8.3 name on Windows, /private on macOS)
        for path in (self.project, self.project.resolve()):
            for form in (str(path), path.as_posix(), str(path).replace("\\", "\\\\")):
                text = text.replace(form, "toy-rag")
        text = re.sub(r"(?m)^(\s*p90 latency s:).*$", r"\1 (wall clock of a simulated system: not shown)", text)
        text = re.sub(r"run [0-9a-f]{12}", "run <id>", text)  # what a log row carries that differs on every run
        text = re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", "<time>", text)
        text = re.sub(r"latency [\d.]+s", "latency <s>", text)
        return text.rstrip("\n")

    # ---- running things
    def run(self, argv: list[str], *, shown: str, cwd: Path | None = None, seed: int | None = None, ok=(0,)) -> str:
        env = dict(self.env, **({"TOYRAG_SEED": str(seed)} if seed is not None else {}))
        done = subprocess.run(argv, cwd=cwd or self.project, env=env, capture_output=True, text=True, encoding="utf-8")
        if done.returncode not in ok:
            sys.exit(f"demo stopped: `{shown}` exited {done.returncode}\n{done.stdout}\n{done.stderr}")
        text = self.clean("\n".join(part for part in (abridged(done.stdout).rstrip("\n"), done.stderr.rstrip("\n")) if part))
        self.say(f"$ {'TOYRAG_SEED=%d ' % seed if seed is not None else ''}{shown}")
        if text:
            self.say(text)
        if done.returncode:
            self.say(f"(exit {done.returncode})")
        self.say()
        return text

    def labkit(self, *args: str, seed: int | None = None, ok=(0,), cwd: Path | None = None) -> str:
        return self.run([sys.executable, "-m", "labkit", *args], shown="labkit " + " ".join(quote(a) for a in args),
                        seed=seed, ok=ok, cwd=cwd)

    def module(self, module: str, *args: str) -> str:
        return self.run([sys.executable, "-m", module, *args], shown=f"python -m {module} " + " ".join(args))

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.project, env=self.env, capture_output=True, check=True)

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        self.say(f"$ git add -A && git commit -m {quote(message)}\n")

    # ---- the example's own files
    def overlay(self, names: list[str] | None = None) -> None:
        """Copy the example's files over the new project (all of them except the cards, or just the named ones)."""
        for source in sorted(p for p in EXAMPLE.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
            relative = source.relative_to(EXAMPLE).as_posix()
            wanted = relative not in CARD_FILES if names is None else relative in names
            if not wanted:
                continue
            target = self.project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

    def set_gates(self, card: str, answer: str) -> None:
        path = self.project / card
        text = path.read_text(encoding="utf-8")
        new, count = re.subn(r"(?m)^- Gates passed\?.*$", f"- Gates passed? {answer}", text)
        assert count == 1, f"{card} has no `Gates passed?` line"
        path.write_text(new, encoding="utf-8", newline="\n")
        self.say(f"# section 5 of {card} now reads: - Gates passed? {answer}\n")


def quote(word: str) -> str:
    return f'"{word}"' if re.search(r"[\s]", word) else word


def abridged(stdout: str) -> str:
    """`labkit evaluate` prints one long dict and `grade` and `report` print paths of this machine: keep the numbers the
    walkthrough talks about, and nothing that differs between machines."""
    first = stdout.strip().splitlines()[0] if stdout.strip() else ""
    if first.startswith("{'grades'"):
        g = ast.literal_eval(first)
        return f"graded {g['graded_now']} now, {g['already_graded']} already graded: out/grades/{Path(g['grades']).name}\n"
    if first.startswith("{'experiments'"):
        r = ast.literal_eval(first)
        return f"collected {r['experiments']} experiments into results/SUMMARY.md and out/report.html\n"
    if not first.startswith("{'experiment'"):
        return stdout
    s = ast.literal_eval(first)
    lines = [f"{s['experiment']}: complete={s['complete']}, graded {s['n_graded']} of {s['n_expected']} questions"]
    if s["n_graded"]:
        lines.append(f"  correctness_mean {s['scores']['correctness_mean']:.4f} (0 to 2), fully_correct {s['scores']['fully_correct']:.4f}")
    lines.append("  " + ", ".join(f"{k} {s['metrics'][k]:.4f}" for k in KEY_METRICS if k in s["metrics"]))
    if s["n_graded"]:
        lines.append(f"  cost: {s['cost']['tokens_per_question']} tokens per question, {s['cost']['usd_per_question'] * 1e6:.1f} USD per million questions")
    rest = stdout.strip().splitlines()[1:]
    return "\n".join(lines + rest) + "\n"


def summary_table(markdown: str, columns=("Experiment", "Changed", "Score", "evid@5", "Tokens/question")) -> str:
    """A few columns of the markdown table in results/SUMMARY.md, as it is written there."""
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in markdown.splitlines() if line.startswith("|")]
    header, body = rows[0], [r for r in rows[2:]]
    keep = [header.index(c) for c in columns]
    return "\n".join("| " + " | ".join(r[i] for i in keep) + " |" for r in [header, ["---"] * len(header), *body])


def walk(demo: Demo) -> None:
    p = demo.project
    demo.say("# labkit's own words are shown as they came out; `evaluate` is abridged to the numbers used below.")
    demo.say("# TOYRAG_SEED fixes the simulated model's sampling so this output reproduces; a real model has no such knob.")

    demo.heading("0. Start a project")
    demo.run([sys.executable, "-m", "labkit", "init", str(p), "--name", "Toy RAG", "--git"], cwd=p.parent,
             shown='labkit init toy-rag --name "Toy RAG" --git')
    demo.git("config", "core.autocrlf", "false")
    demo.git("config", "user.name", "demo")
    demo.git("config", "user.email", "demo@example.com")
    demo.overlay()
    demo.say("# copy examples/toy-rag/ over the new project: the corpus, the golden set, the system and the baseline config\n")
    demo.commit("Start the toy project")
    demo.labkit("status")

    demo.heading("1. The golden set")
    demo.labkit("golden-check")

    demo.heading("2. The baseline: a smoke test first, then every dev question")
    demo.labkit("run", "--config", "configs/baseline.yaml", "--split", "dev", "--limit", "1", seed=1)
    demo.labkit("show", "dev_000_baseline", "--qid", "Q001", "--chars", "90")
    demo.labkit("run", "--config", "configs/baseline.yaml", "--split", "dev", seed=1)
    demo.labkit("grade", "--experiment", "dev_000_baseline")
    demo.labkit("evaluate", "--experiment", "dev_000_baseline")

    demo.heading("3. Noise: how much a rerun changes by itself")
    demo.labkit("run", "--config", "configs/baseline.yaml", "--split", "dev", "--name", "dev_000_baseline_r2", seed=2)
    demo.labkit("grade", "--experiment", "dev_000_baseline_r2")
    demo.labkit("evaluate", "--experiment", "dev_000_baseline_r2")
    demo.module("labkit.eval.paired", "out/grades/dev_000_baseline.jsonl", "out/grades/dev_000_baseline_r2.jsonl", "--max-score", "2")
    demo.labkit("grade", "--experiment", "dev_000_baseline", "--suffix", "__regrade1")
    demo.module("labkit.eval.paired", "out/grades/dev_000_baseline.jsonl", "out/grades/dev_000_baseline__regrade1.jsonl", "--max-score", "2")
    demo.labkit("check", "3", ok=(0, 1))

    demo.heading("4. What labkit refuses")
    demo.say("# the test split is locked until the last step")
    demo.labkit("run", "--config", "configs/baseline.yaml", "--split", "test", ok=(1,))
    demo.say("# a variant needs a card whose sections 1-4 are written before it runs")
    demo.labkit("new-card", "bigger-chunks", "--title", "Bigger chunks")
    demo.overlay(["configs/001-bigger-chunks.yaml"])
    demo.say("# the config now changes chunking.sentences (copied from examples/toy-rag/); the card is still the empty template\n")
    demo.labkit("run", "--config", "configs/001-bigger-chunks.yaml", "--split", "dev", "--retrieval-only", ok=(1,))

    demo.heading("5. Card 001: bigger chunks")
    demo.overlay(["experiments/001-bigger-chunks.md"])
    demo.say("# the card prepared in examples/toy-rag/ replaces the empty one; sections 1-4 are written, 5-7 are not\n")
    demo.commit("Write card 001 sections 1-4")
    demo.say("# LLM-free gate: the same questions, search only, no model call")
    demo.labkit("run", "--config", "configs/baseline.yaml", "--split", "dev", "--retrieval-only")
    demo.labkit("evaluate", "--experiment", "dev_000_baseline_retrieval")
    demo.labkit("run", "--config", "configs/001-bigger-chunks.yaml", "--split", "dev", "--retrieval-only")
    demo.labkit("evaluate", "--experiment", "dev_001_bigger_chunks_retrieval")
    demo.labkit("compare", "dev_000_baseline_retrieval", "dev_001_bigger_chunks_retrieval", "--metric", "evid@1", "--noise", "1")
    demo.module("labkit.eval.headroom", "--scores", "results/dev_000_baseline/grades.jsonl",
                "--before", "results/dev_000_baseline_retrieval/metrics.jsonl",
                "--after", "results/dev_001_bigger_chunks_retrieval/metrics.jsonl", "--metric", "evid@1", "--noise", "1")
    demo.say("# an answer run is refused until section 5 says the gates passed")
    demo.labkit("run", "--config", "configs/001-bigger-chunks.yaml", "--split", "dev", ok=(1,))
    demo.set_gates("experiments/001-bigger-chunks.md", "yes: evid@1 +6 questions (noise 1), headroom upside 6, risk 0")
    demo.commit("Card 001: the LLM-free gates passed")
    demo.labkit("run", "--config", "configs/001-bigger-chunks.yaml", "--split", "dev", seed=3)
    demo.labkit("grade", "--experiment", "dev_001_bigger_chunks")
    demo.labkit("evaluate", "--experiment", "dev_001_bigger_chunks")
    demo.labkit("compare", "dev_000_baseline", "dev_001_bigger_chunks", "--noise", "1")
    demo.say("# the card also accepts the same direction in a second independent run")
    demo.labkit("run", "--config", "configs/001-bigger-chunks.yaml", "--split", "dev", "--name", "dev_001_bigger_chunks_r2", seed=4)
    demo.labkit("grade", "--experiment", "dev_001_bigger_chunks_r2")
    demo.labkit("evaluate", "--experiment", "dev_001_bigger_chunks_r2")
    demo.labkit("compare", "dev_000_baseline", "dev_001_bigger_chunks_r2", "--noise", "1")

    demo.heading("6. Card 002: more chunks, stopped at the gate")
    demo.labkit("new-card", "more-k", "--title", "More chunks")
    demo.overlay(["configs/002-more-k.yaml", "experiments/002-more-k.md"])
    demo.commit("Write card 002 sections 1-4")
    demo.labkit("run", "--config", "configs/002-more-k.yaml", "--split", "dev", "--retrieval-only")
    demo.labkit("evaluate", "--experiment", "dev_002_more_k_retrieval")
    demo.labkit("compare", "dev_000_baseline_retrieval", "dev_002_more_k_retrieval", "--metric", "evid@1", "--noise", "1")
    demo.module("labkit.eval.headroom", "--scores", "results/dev_000_baseline/grades.jsonl",
                "--before", "results/dev_000_baseline_retrieval/metrics.jsonl",
                "--after", "results/dev_002_more_k_retrieval/metrics.jsonl", "--metric", "evid@1", "--noise", "1")
    demo.set_gates("experiments/002-more-k.md", "no: evid@1 +0 questions, below the noise of 1")
    demo.labkit("run", "--config", "configs/002-more-k.yaml", "--split", "dev", ok=(1,))

    demo.heading("7. The report")
    demo.labkit("report")
    demo.say("# results/SUMMARY.md has more columns; these are the ones the walkthrough uses")
    demo.say(summary_table((p / "results" / "SUMMARY.md").read_text(encoding="utf-8")))


def remove_tree(path: Path) -> None:
    """Delete a folder with a git repository in it: git marks its objects read-only, which Windows will not delete."""
    def writable_then(function, name, _error):
        os.chmod(name, stat.S_IWRITE)
        function(name)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable_then)
    else:
        shutil.rmtree(path, onerror=writable_then)


def run_demo(keep: Path | None = None) -> str:
    """The demo's output as text. The project is made in `keep` (an empty or new folder) or in a temporary one."""
    if not shutil.which("git"):
        sys.exit("the demo needs git on PATH")
    base = Path(tempfile.mkdtemp(prefix="labkit-demo-"))
    project = keep.resolve() if keep else base / "toy-rag"
    try:
        demo = Demo(project)
        walk(demo)
    finally:
        remove_tree(base)  # with --keep, `base` is the empty folder the temporary name came from
    return "\n".join(demo.out).rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", type=Path, help="make the project in this empty or new folder and leave it there")
    parser.add_argument("--transcript", type=Path, help="also write the output to this file")
    args = parser.parse_args(argv)
    if args.keep and args.keep.exists() and any(args.keep.iterdir()):
        sys.exit(f"{args.keep} is not empty")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    text = run_demo(args.keep)
    print(text, end="")
    if args.transcript:
        args.transcript.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
