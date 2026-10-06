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
    assert "is not an init trace" in capsys.readouterr().err
    app = traced_shop_run(tmp_path)
    assert cli.main(["view", str(app / ".doppel-trace"), "--no-open"]) == 0
    assert (app / "doppel-view.html").exists()
