"""A pass has to mean something: the PR can't blind the twin, and a broken head is never a clean run."""
import json
import shutil
from pathlib import Path

import pytest

from doppel import cli
from doppel.backends.nebius import pair

SHOP = Path(__file__).resolve().parent.parent / "examples" / "shop"


def run(tmp_path, head, *extra):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    code = cli.main(["run", "--base", str(SHOP / "base"), "--head", str(head),
                     "--personas", str(SHOP / "personas.json"),
                     "--out", str(out / "report.md"), "--json", str(out / "run.json"), *extra])
    data = json.loads((out / "run.json").read_text("utf-8")) if (out / "run.json").exists() else {}
    md = (out / "report.md").read_text("utf-8") if (out / "report.md").exists() else ""
    return code, md, data


def head_copy(tmp_path) -> Path:
    head = tmp_path / "head"
    shutil.copytree(SHOP / "head", head)
    return head


# --- a broken head is a regression, not a skipped persona ------------------------------

def test_pair_says_which_world_a_persona_failed_in():
    out = pair([{"name": "a"}, {"name": "b"}, {"name": "c"}],
               [{"persona": "a"}, RuntimeError("x"), RuntimeError("y")],
               [RuntimeError("boom"), {"persona": "b"}, RuntimeError("z")])
    assert [(f["persona"], f["world"]) for f in out["failed"]] == [("a", "head"), ("b", "base"), ("c", "both")]
    assert out["base"] == [] and out["head"] == []


def test_a_head_that_no_longer_starts_fails_the_check(tmp_path):
    head = head_copy(tmp_path)
    app = head / "app.py"
    app.write_text(app.read_text("utf-8").replace("TAX_PCT = 18", "TAX_PCT = 18 +"), encoding="utf-8")
    code, md, data = run(tmp_path, head, "--strict")
    assert code == 1                                   # before the fix: 0, "no behavior changes"
    errors = [f for f in data["findings"] if f["kind"] == "error"]
    assert len(errors) == 5 and all(f["detail"] == "ran on base, failed on head" for f in errors)
    assert "no behavior changes" not in md


def test_a_run_where_nothing_replayed_is_doppel_failing_not_a_pass(tmp_path):
    base = tmp_path / "base"
    shutil.copytree(SHOP / "base", base)
    head = head_copy(tmp_path)
    # both worlds start an app that exits at once: no persona runs anywhere
    for world in (base, head):
        (world / "app.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    code = cli.main(["run", "--base", str(base), "--head", str(head),
                     "--personas", str(SHOP / "personas.json"), "--strict"])
    assert code == 2


# --- the PR can't blind the twin -------------------------------------------------------

def test_a_pr_cannot_hide_its_change_by_ignoring_the_field(tmp_path):
    head = head_copy(tmp_path)
    toml = head / "doppel.toml"
    toml.write_text(toml.read_text("utf-8").replace("ignore = []", 'ignore = ["total"]'), encoding="utf-8")
    code, md, data = run(tmp_path, head)
    assert ("coupon buyer", "POST /orders", "body") in {(f["persona"], f["request"], f["kind"])
                                                        for f in data["findings"]}
    assert "edits Doppel's own setup (doppel.toml)" in md


def test_the_pr_still_supplies_how_the_app_installs_and_starts(tmp_path):
    head = head_copy(tmp_path)
    toml = head / "doppel.toml"
    toml.write_text(toml.read_text("utf-8").replace('ready = "/health"', 'ready = "/products"'), encoding="utf-8")
    spec = cli.load_spec(SHOP / "base", head)
    assert spec.ready == "/products"                   # [app] comes from the PR
    assert spec.ignore == []                           # [diff] comes from base


def test_an_honest_pr_gets_no_setup_warning(tmp_path):
    _, md, _ = run(tmp_path, SHOP / "head")
    assert "own setup" not in md


def test_the_action_reads_personas_from_the_base_branch():
    yaml = pytest.importorskip("yaml")
    action = yaml.safe_load((SHOP.parent.parent / "action.yml").read_text(encoding="utf-8"))
    script = next(s["run"] for s in action["runs"]["steps"] if s.get("name") == "Run the twin")
    assert 'PERSONAS="$BASE/' in script and 'PERSONAS="$HEAD/' not in script
