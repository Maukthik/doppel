"""Timeline recording, the run page's timeline, and the static replay site. Offline."""
import asyncio
import json
from pathlib import Path

import pytest

from doppel import site
from doppel.backends import local, nebius
from doppel.spec import TwinSpec
from doppel.viewer import render_run, run_data
from tests.test_stage2 import FakeClient

ROOT = Path(__file__).resolve().parent.parent
SHOP = ROOT / "examples" / "shop"


def test_nebius_twin_records_build_steps_and_every_persona_run(tmp_path):
    base, head = tmp_path / "base", tmp_path / "head"
    for d, body in ((base, "BASE"), (head, "HEAD")):
        d.mkdir()
        (d / "app.py").write_text(body)
    spec = TwinSpec(start="python app.py", ready="/health", install="pip install x", seed="python seed.py")
    personas = [{"name": f"p{i}", "steps": [{"method": "GET", "path": "/x"}]} for i in range(3)]
    worlds = asyncio.run(nebius.NebiusTwin(FakeClient(), spec, log=lambda *_: None).run(base, head, personas))
    tl = worlds["timeline"]
    assert tl["backend"] == "nebius" and tl["sandbox_runs"] >= 10
    labels = [e["label"] for e in tl["events"] if e["lane"] == "build"]
    assert {"upload base code", "install (base)", "seed", "fork head world (PR files on top)",
            "install (head)"} <= set(labels)
    runs = [e for e in tl["events"] if e["kind"] == "persona"]
    expected = sorted((w, f"p{i}") for w in ("base", "head") for i in range(3))
    assert sorted((e["lane"], e["label"]) for e in runs) == expected
    assert all(e["end"] >= e["start"] and e["ok"] and e["requests"] == 1 for e in runs)
    assert max(e["end"] for e in tl["events"]) == tl["total"]


def test_local_twin_timeline_reaches_the_run_page():
    spec = TwinSpec.load(SHOP / "base" / "doppel.toml")
    personas = json.loads((SHOP / "personas.json").read_text())
    worlds = local.run_twin(SHOP / "base", SHOP / "head", spec, personas)
    tl = worlds["timeline"]
    assert tl["backend"] == "local"
    assert [e["kind"] for e in tl["events"] if e["lane"] == "build"] == ["step", "checkpoint", "fork", "fork"]
    assert sum(e["kind"] == "persona" for e in tl["events"]) == 2 * len(personas)
    page = render_run(run_data({"findings": [], "worlds": worlds}, "shop"))
    assert '"timeline"' in page and "Inside the twin" in page


def test_sanitize_masks_credentials_but_keeps_the_evidence():
    raw = {"access_token": "abc.def.ghijklmnop", "password": "pw", "user": {"username": "alice",
           "password_hash": "scrypt:32768:8:1$salt$deadbeef"},
           "body": json.dumps({"token": "secret-value-123", "id": 1}),
           "log": "sent Authorization: Bearer abcdefghijklmnop and eyJhbGciOiJI.eyJzdWIiOiIx.c2lnbmF0dXJl",
           "template": {"Authorization": "Bearer {token}"}}
    out = site.sanitize(raw)
    text = json.dumps(out)
    for secret in ("abc.def.ghijklmnop", "salt$deadbeef", "secret-value-123", "abcdefghijklmnop", "eyJhbGciOiJI"):
        assert secret not in text
    assert out["user"]["password_hash"].startswith("scrypt:32768:8:1$")   # the leak stays visible
    assert out["user"]["username"] == "alice" and json.loads(out["body"])["id"] == 1
    assert out["template"]["Authorization"] == "Bearer {token}"            # a placeholder, not a secret


def test_check_secrets_fails_closed():
    env = {"NEBIUS_API_KEY": "nb-live-0123456789", "HOME": "/home/me", "SHORT_TOKEN": "abc"}
    site.check_secrets("nothing here /home/me abc", "page", env)
    with pytest.raises(site.SecretLeak):
        site.check_secrets("oops nb-live-0123456789", "page", env)


def test_bench_scores_match_the_published_results():
    s = site.bench_scores(ROOT / "bench" / "results")
    assert {k: (v["caught"], v.get("alarms")) for k, v in s.items()} == {
        "tests": (12, 0), "review": (17, 1), "doppel": (11, 1), "doppel_guided": (16, 0),
        "tests_plus_guided": (20, None)}


def test_from_bench_points_each_finding_at_the_step_that_differs():
    result = json.loads((ROOT / "bench" / "results" / "r17-hash-leak.json").read_text())
    raw = site.from_bench(result)
    data = run_data(raw, "microblog-api")
    users = {u["name"]: u for u in data["users"]}
    for f in data["findings"]:
        step = users[f["persona"]]["steps"][f["step"]]
        assert step["request"] == f["request"]
    assert data["timeline"]["events"] and all(e["kind"] == "phase" for e in data["timeline"]["events"])
    assert all(f["label"] == "regression" for f in data["findings"])


def test_build_writes_the_gallery_and_hides_hashes(tmp_path):
    out = tmp_path / "_site"
    files = site.build(ROOT / "site" / "gallery.json", out, root=ROOT, env={})
    assert (out / "index.html") in files and (out / ".nojekyll").exists()
    index = (out / "index.html").read_text()
    assert "password_hash" in index and "12/20" in index and "20/20" in index
    for slug in ("hash-leak", "revoke-crash", "safe-posts-count", "shop-coupon"):
        page = out / "runs" / f"{slug}.html"
        assert page.exists() and 'href="../index.html"' in page.read_text()
        assert f'runs/{slug}.html' in index
    for page in out.rglob("*.html"):   # no recorded hash survives anywhere on the site
        text = page.read_text()
        for line in text.split("scrypt:")[1:]:
            assert line.split("$", 1)[1].startswith("[hidden on the site]")
