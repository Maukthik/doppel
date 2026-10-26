"""doppel view: an init trace becomes one static page with the trace embedded as JSON."""
import json
import re

from doppel import bootstrap, cli, viewer
from tests.test_init import SHOP_SPEC, ScriptedModel, reply, shop_without_spec


def traced_shop_run(tmp_path):
    app = shop_without_spec(tmp_path)
    broken = SHOP_SPEC.replace('start = "python app.py"', 'start = "python missing.py"')
    model = ScriptedModel([reply(SHOP_SPEC, seed="from x import (\n"), reply(broken), reply(SHOP_SPEC)])
    bootstrap.init(app, backend="local", tries=3, chat=model, log=lambda m: None, trace=app / ".doppel-trace")
    return app


def test_each_attempt_shows_how_far_it_got_and_what_init_decided(tmp_path):
    app = traced_shop_run(tmp_path)
    data = viewer.load_trace(app / ".doppel-trace")
    one, two, three = data["attempts"]
    assert (one["status"], one["stage"]) == ("caught", None) and not one["boot"]["ran"]
    assert not one["checks"][-1]["ok"] and "syntax error" in one["checks"][-1]["problems"][0]
    assert (two["status"], two["stage"]) == ("kept", 2) and "missing.py" in two["boot"]["error"]
    assert (three["status"], three["stage"]) == ("works", 5) and data["outcome"] == 3
    assert three["boot"]["smoke"] == [{"request": "GET /health", "status": 200}]
    assert any("products=10" in line for line in three["database"])
    assert "doppel.toml" in three["files"] and three["prompt"].startswith("You set up a web API")


def test_the_page_is_self_contained_and_keeps_script_tags_out_of_the_data(tmp_path):
    app = traced_shop_run(tmp_path)
    (app / ".doppel-trace" / "attempt-3" / "2-drafted-doppel_seed.py").write_text("x = '</script><b>'\n")
    out = viewer.write_view(app / ".doppel-trace")
    page = out.read_text(encoding="utf-8")
    assert out == app / "doppel-view.html"
    blob = re.search(r'<script id="trace" type="application/json">(.*?)</script>', page, re.S).group(1)
    assert "</script>" not in blob and json.loads(blob)["attempts"][2]["files"]["doppel_seed.py"].startswith("x = '</")
    assert "fetch(" not in page                                       # nothing is loaded at runtime


def test_the_command_refuses_a_folder_that_is_not_a_trace(tmp_path, capsys):
    assert cli.main(["view", str(tmp_path), "--no-open"]) == 2
    assert "is not a check" in capsys.readouterr().err
    app = traced_shop_run(tmp_path)
    assert cli.main(["view", str(app / ".doppel-trace"), "--no-open"]) == 0
    assert (app / "doppel-view.html").exists()


# --- View 1: a pull request run ----------------------------------------------------------------

def judged_run(tmp_path):
    from tests.test_doppel import SHOP
    out = tmp_path / "run.json"
    assert cli.main(["run", "--base", str(SHOP / "base"), "--head", str(SHOP / "head"),
                     "--personas", str(SHOP / "personas.json"), "--json", str(out)]) == 0
    data = json.loads(out.read_text())
    data["verdicts"] = [{"label": "intended" if "in_stock" in f["detail"] else "regression", "why": "because"}
                        for f in data["findings"]]
    data["pr"] = {"title": "Show stock status on products", "body": "..."}
    data["suspicions"] = [{"id": "s1", "claim": "Tax before coupon", "status": "confirmed", "evidence": "measured"}]
    out.write_text(json.dumps(data))
    return out


def test_the_run_view_lines_up_both_worlds_per_user_with_verdicts_and_reach(tmp_path):
    run = viewer.load_run(judged_run(tmp_path))
    assert run["pr"]["title"] == "Show stock status on products" and run["judged"]
    assert [c["where"] for c in run["changes"]] == ["product_json", "order_total", "Handler.do_POST"]
    differing = [s for u in run["users"] for s in u["steps"] if s["findings"]]
    assert differing and all(s["base"] is not None and s["head"] is not None for s in differing)
    labels = {run["findings"][i]["label"] for s in differing for i in s["findings"]}
    assert labels == {"intended", "regression"}
    coupon = next(u for u in run["users"] if u["name"] == "coupon buyer")
    assert any(s["base_body"] != s["head_body"] for s in coupon["steps"])   # (the shop's personas have no goal)


def test_doppel_view_picks_the_run_view_for_run_json_and_its_folder(tmp_path):
    out = judged_run(tmp_path)
    assert cli.main(["view", str(out), "--no-open"]) == 0
    page = (tmp_path / "doppel-run.html").read_text(encoding="utf-8")
    assert "<title>doppel: Show stock status on products</title>" in page
    blob = re.search(r'<script id="run" type="application/json">(.*?)</script>', page, re.S).group(1)
    assert json.loads(blob)["suspicions"][0]["claim"] == "Tax before coupon"
    (tmp_path / "doppel-run.html").unlink()
    assert cli.main(["view", str(tmp_path), "--no-open"]) == 0 and (tmp_path / "doppel-run.html").exists()
