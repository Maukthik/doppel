"""Offline tests: no network, no Nebius, no model calls. The shop example runs as local processes."""
import json
from pathlib import Path

import pytest

from doppel import cli, runner
from doppel.diff import changed_paths, compare
from doppel.report import markdown
from doppel.spec import TwinSpec

SHOP = Path(__file__).resolve().parent.parent / "examples" / "shop"


# --- runner helpers ---------------------------------------------------------

def test_render_keeps_types_and_fills_strings():
    v = {"id": 7, "name": "lamp"}
    assert runner.render({"product_id": "{id}", "path": "/p/{id}/{name}", "x": ["{missing}"]}, v) == \
        {"product_id": 7, "path": "/p/7/lamp", "x": ["{missing}"]}


def test_extract_dotted_paths():
    body = {"items": [{"id": 3}], "total": 10}
    assert runner.extract(body, "items.0.id") == 3
    assert runner.extract(body, "items.5.id") is None
    assert runner.extract(None, "id") is None


# --- diff ---------------------------------------------------------------------

def rec(name, *steps):
    return [{"persona": name, "steps": [dict(i=i, request=r, status=s, body=b, ms=ms)
                                        for i, (r, s, b, ms) in enumerate(steps)]}]


def test_compare_finds_each_kind_and_ignores_listed_keys():
    base = rec("p", ("GET /a", 200, {"v": 1, "t": "x"}, 10), ("GET /b", 200, {}, 10),
               ("GET /c", 400, {}, 10), ("GET /d", 200, {}, 10))
    head = rec("p", ("GET /a", 200, {"v": 2, "t": "y"}, 10), ("GET /b", 500, {}, 10),
               ("GET /c", 201, {}, 10), ("GET /d", 200, {}, 300))
    kinds = {(f.kind, f.request) for f in compare(base, head, ignore=["t"])}
    assert kinds == {("body", "GET /a"), ("error", "GET /b"), ("status", "GET /c"), ("slow", "GET /d")}


def test_ignored_keys_and_small_latency_noise_are_not_findings():
    base = rec("p", ("GET /a", 200, {"t": 1}, 10))
    head = rec("p", ("GET /a", 200, {"t": 2}, 40))  # 4x slower but only 30 ms: noise
    assert compare(base, head, ignore=["t"]) == []


def test_changed_paths_names_added_and_changed_fields():
    assert changed_paths({"a": 1, "b": [1, 2]}, {"a": 1, "b": [1, 3], "c": 0}) == ["b.1", "c (added)"]


def test_list_paths_are_collapsed():
    from doppel.diff import summarize_paths
    assert summarize_paths(["0.in_stock (added)", "1.in_stock (added)", "total"]) == "*.in_stock (added) x2, total"


def test_markdown_escapes_pipes():
    base = rec("p", ("GET /a", 200, {"v": "a|b"}, 1))
    head = rec("p", ("GET /a", 200, {"v": "c"}, 1))
    md = markdown(compare(base, head), personas=1, steps=1)
    assert "a\\|b" in md and md.count("\n|") >= 3


# --- end to end on the shop example --------------------------------------------

@pytest.fixture(scope="module")
def shop_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("run")
    code = cli.main(["run", "--base", str(SHOP / "base"), "--head", str(SHOP / "head"),
                     "--personas", str(SHOP / "personas.json"),
                     "--out", str(out / "report.md"), "--json", str(out / "run.json")])
    return code, (out / "report.md").read_text(encoding="utf-8"), json.loads((out / "run.json").read_text("utf-8"))


def test_shop_twin_catches_the_hidden_regressions(shop_run):
    code, md, data = shop_run
    assert code == 0
    found = {(f["persona"], f["request"], f["kind"]) for f in data["findings"]}
    # the coupon regression changes the total, both when ordering and when reading the order back
    assert ("coupon buyer", "POST /orders", "body") in found
    assert ("coupon buyer", "GET /orders/1", "body") in found
    # the lost qty check turns a 400 into a 201
    assert ("careless clicker", "POST /orders", "status") in found
    # the intended change shows up too (stage 2 will label it "intended" from PR.md)
    assert ("window shopper", "GET /products", "body") in found
    # untouched flows stay quiet
    assert not any(f["persona"] in ("plain buyer", "last-copy hunter") for f in data["findings"])
    assert "Doppel:" in md


def test_both_worlds_start_from_the_same_seed(shop_run):
    _, _, data = shop_run
    base, head = data["worlds"]["base"], data["worlds"]["head"]
    hunter = [r for r in base if r["persona"] == "last-copy hunter"][0]
    assert [s["status"] for s in hunter["steps"]] == [201, 409]  # only one Ink bottle in stock
    assert [r["persona"] for r in base] == [r["persona"] for r in head]


def test_strict_mode_fails_ci_when_behavior_changes(tmp_path):
    code = cli.main(["run", "--base", str(SHOP / "base"), "--head", str(SHOP / "head"),
                     "--personas", str(SHOP / "personas.json"), "--strict"])
    assert code == 1


def test_identical_code_reports_no_changes():
    spec = TwinSpec.load(SHOP / "base" / "doppel.toml")
    from doppel.backends.local import run_twin
    personas = json.loads((SHOP / "personas.json").read_text(encoding="utf-8"))
    worlds = run_twin(SHOP / "base", SHOP / "base", spec, personas)
    assert compare(worlds["base"], worlds["head"], spec.ignore, spec.slow_ratio, spec.slow_min_ms) == []


def test_spec_requires_a_start_command(tmp_path):
    p = tmp_path / "doppel.toml"
    p.write_text("[app]\nready = '/'\n")
    with pytest.raises(ValueError):
        TwinSpec.load(p)
