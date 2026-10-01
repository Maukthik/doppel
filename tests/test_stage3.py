"""Stage 3 offline tests: the judge with a fake Nemotron, the report, and the generated regression
tests run for real against the shop app (they must pass on base and fail on head)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from doppel import cli
from doppel.backends import local
from doppel.diff import compare
from doppel.judge import Verdict, group, judge, regression_tests
from doppel.report import markdown
from doppel.spec import TwinSpec

SHOP = Path(__file__).resolve().parent.parent / "examples" / "shop"
PERSONAS = json.loads((SHOP / "personas.json").read_text(encoding="utf-8"))


def shop_findings():
    spec = TwinSpec.load(SHOP / "head" / "doppel.toml")
    worlds = local.run_twin(SHOP / "base", SHOP / "head", spec, PERSONAS)
    return compare(worlds["base"], worlds["head"], spec.ignore, spec.slow_ratio, spec.slow_min_ms)


def fake_ultra(model, prompt, temperature=0.7):
    """Labels like a good judge would: in_stock is in the PR text, the rest isn't."""
    verdicts = []
    for line in prompt.split("Behavior changes:\n", 1)[1].splitlines():
        c = json.loads(line)
        label = "intended" if "in_stock" in c["what"] else "regression"
        verdicts.append({"id": c["id"], "label": label, "why": f"because {c['what']}"})
    return json.dumps({"verdicts": verdicts, "summary": "Two unmentioned changes."}), 3000, 400


def test_group_merges_the_same_change_across_steps():
    findings = shop_findings()
    groups = group(findings)
    assert len(findings) == 6 and len(groups) == 3   # qty status, coupon total, in_stock field


def test_judge_labels_every_finding_and_report_puts_regressions_first():
    findings = shop_findings()
    judged = judge(findings, (SHOP / "PR.md").read_text(), "diff", model="ultra", chat=fake_ultra)
    labels = {(f.persona, f.request): v.label for f, v in zip(findings, judged["verdicts"], strict=True)}
    assert labels[("coupon buyer", "POST /orders")] == "regression"
    assert labels[("careless clicker", "POST /orders")] == "regression"
    assert labels[("window shopper", "GET /products")] == "intended"
    md = markdown(findings, len(PERSONAS), 11, judged)
    assert md.startswith("## Doppel: 2 regression(s)")
    assert md.index("Regressions") < md.index("<details>")      # intended changes fold away
    assert "Two unmentioned changes." in md


def test_missing_or_bad_verdicts_become_needs_human():
    findings = shop_findings()
    judged = judge(findings, "pr", "diff", chat=lambda m, p, temperature=0.7: ("nonsense", 1, 1))
    assert {v.label for v in judged["verdicts"]} == {"needs_human"}


def _serve(code_dir: Path, tmp: Path):
    world = tmp / code_dir.name
    shutil.copytree(code_dir, world)
    subprocess.run([sys.executable, "seed.py"], cwd=world, check=True, capture_output=True)
    port = local._free_port()
    app = subprocess.Popen([sys.executable, "app.py"], cwd=world, env={**os.environ, "PORT": str(port)})
    assert local.runner.wait_ready(f"http://127.0.0.1:{port}", "/health", 20)
    return app, f"http://127.0.0.1:{port}"


def test_generated_regression_tests_pass_on_base_and_fail_on_head(tmp_path):
    findings = shop_findings()
    verdicts = [Verdict("intended" if "in_stock" in f.detail else "regression", "w") for f in findings]
    test_file = tmp_path / "test_doppel_regressions.py"
    test_file.write_text(regression_tests(findings, verdicts, PERSONAS), encoding="utf-8")
    assert test_file.read_text().count("\ndef test_") == 3

    results = {}
    for name in ("base", "head"):
        app, url = _serve(SHOP / name, tmp_path)
        try:
            p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test_file)],
                               env={**os.environ, "DOPPEL_BASE_URL": url}, capture_output=True, text=True)
            results[name] = p.stdout.strip().splitlines()[-1]
        finally:
            app.terminate()
            app.wait(timeout=5)
    assert "3 passed" in results["base"], results
    assert "3 failed" in results["head"], results


def test_cli_with_pr_writes_judged_report_and_tests(tmp_path, monkeypatch):
    import doppel.judge as j
    monkeypatch.setattr(j, "nemotron_chat", fake_ultra)
    monkeypatch.setattr("doppel.judge.judge.__defaults__", (None, fake_ultra))
    code = cli.main(["run", "--base", str(SHOP / "base"), "--head", str(SHOP / "head"),
                     "--personas", str(SHOP / "personas.json"), "--pr", str(SHOP / "PR.md"),
                     "--out", str(tmp_path / "r.md"), "--tests-out", str(tmp_path / "test_reg.py"), "--strict"])
    assert code == 1                                            # regressions fail CI
    assert "regression(s)" in (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "def test_" in (tmp_path / "test_reg.py").read_text(encoding="utf-8")
