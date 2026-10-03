"""Review-guided twin: the reviewer suspects, probe personas are written for each suspicion, and
each suspicion is checked against what the twin saw. Offline, with fake Nemotron calls."""
import json
from pathlib import Path

from doppel import personas as gen
from doppel.backends import local
from doppel.diff import compare
from doppel.report import markdown
from doppel.spec import TwinSpec
from doppel.suspects import check, suspect

SHOP = Path(__file__).resolve().parent.parent / "examples" / "shop"


def test_suspect_numbers_and_trims_suspicions():
    reply = {"suspicions": [{"claim": "coupon applied after tax", "route": "POST /orders", "trigger": "SAVE10"},
                            {"route": "no claim, dropped"},
                            {"claim": "qty 0 accepted", "route": "POST /orders", "trigger": "qty 0"}]}
    out = suspect("adds in_stock", "diff", "src", chat=lambda m, p, temperature=0.7: (json.dumps(reply), 10, 5))
    assert [s["id"] for s in out["suspicions"]] == ["s1", "s2"]
    assert out["suspicions"][1]["claim"] == "qty 0 accepted"


def test_probe_personas_are_tagged_and_asked_for():
    seen = {}

    def fake(model, prompt, temperature=0.7):
        seen["prompt"] = prompt
        return json.dumps({"personas": [{"name": "probe qty", "probe": "s1",
                                         "steps": [{"method": "GET", "path": "/products"}]}]}), 10, 5

    sus = [{"id": "s1", "claim": "qty 0 accepted", "route": "POST /orders", "trigger": "qty 0"}]
    out = gen.generate(SHOP / "base", head_dir=SHOP / "head", chat=fake, suspicions=sus, probes_only=True)
    assert "write only exactly one probe persona" in seen["prompt"] and "exactly 1 personas" in seen["prompt"]
    assert out["personas"][0]["probe"] == "s1"


def test_check_confirms_refutes_and_defaults_to_untested():
    spec = TwinSpec.load(SHOP / "head" / "doppel.toml")
    probes = [{"name": "probe zero", "probe": "s1", "vars": {"user": 3}, "steps": [
        {"method": "POST", "path": "/orders", "json": {"user_id": "{user}", "items": [{"product_id": 7, "qty": 0}]}}]},
              {"name": "probe lamp", "probe": "s2", "steps": [{"method": "GET", "path": "/products?q=lamp"}]}]
    w = local.run_twin(SHOP / "base", SHOP / "head", spec, probes)
    findings = [f for f in compare(w["base"], w["head"], spec.ignore) if f.kind != "slow"]
    sus = [{"id": "s1", "claim": "qty 0 accepted"}, {"id": "s2", "claim": "search broken"},
           {"id": "s3", "claim": "never probed"}]

    def fake(model, prompt, temperature=0.7):
        assert "probe 'probe zero' for s1" in prompt and "base 400" in prompt and "head 201" in prompt
        return json.dumps({"checks": [{"id": "s1", "status": "confirmed", "changes": ["f1"], "evidence": "400 -> 201"},
                                      {"id": "s2", "status": "refuted", "evidence": "same in both"}]}), 10, 5

    out = check(sus, findings, None, probes, w["base"], w["head"], chat=fake)["checks"]
    assert [out[k]["status"] for k in ("s1", "s2", "s3")] == ["confirmed", "refuted", "untested"]

    def no_proof(model, prompt, temperature=0.7):  # "confirmed" without a cited change is not proof
        return json.dumps({"checks": [{"id": "s2", "status": "confirmed", "evidence": "same in both"}]}), 1, 1
    assert check(sus, findings, None, probes, w["base"], w["head"], chat=no_proof)["checks"]["s2"]["status"] \
        == "untested"
    md = markdown(findings, 2, 2, None, None, [{**s, **out[s["id"]]} for s in sus])
    section = md.split("Reviewer's suspicions")[1]
    assert section.index("confirmed") < section.index("untested") < section.index("refuted")


def test_personas_that_never_log_in_get_the_owner_login_step(tmp_path):
    from doppel.rehearsal import repair
    login = {"method": "POST", "path": "/api/tokens", "auth": ["alice", "pw"], "save": {"token": "access_token"},
             "header": {"Authorization": "Bearer {token}"}}
    lost = {"name": "lost", "steps": [{"method": "GET", "path": "/api/posts"}, {"method": "GET", "path": "/api/me"}]}
    recs = [{"persona": "lost", "steps": [{"i": 0, "request": "GET /api/posts", "status": 401, "body": {}},
                                          {"i": 1, "request": "GET /api/me", "status": 401, "body": {}}]}]
    out = repair([lost], recs, tmp_path, login=login, chat=lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    fixed = out["personas"][0]
    assert out["repaired"] == ["lost"] and out["usd"] == 0.0                 # no model call needed
    assert fixed["steps"][0]["auth"] == ["alice", "pw"] and fixed["headers"]["Authorization"] == "Bearer {token}"


def test_a_single_slow_sample_is_never_an_automatic_regression():
    from doppel.diff import Finding
    from doppel.judge import judge
    slow = Finding("slow", "p", 0, "POST /api/tokens", "104 ms -> 1440 ms", 104, 1440)

    def says_regression(model, prompt, temperature=0.7):
        return json.dumps({"verdicts": [{"id": "c1", "label": "regression", "why": "slower"}]}), 1, 1
    v = judge([slow], "refactor", "diff", chat=says_regression)["verdicts"][0]
    assert v.label == "needs_human" and "one sample" in v.why


def test_probes_keep_their_tag_even_when_the_model_drops_it():
    sus = [{"id": "s1", "claim": "a"}, {"id": "s2", "claim": "b"}]
    reply = {"personas": [{"name": "x", "steps": [{"method": "GET", "path": "/products"}]},
                          {"name": "y", "steps": [{"method": "GET", "path": "/products"}]}]}
    out = gen.generate(SHOP / "base", chat=lambda m, p, temperature=0.7: (json.dumps(reply), 1, 1),
                       suspicions=sus, probes_only=True)["personas"]
    assert [(p["probe"], p["name"]) for p in out] == [("s1", "probe s1: x"), ("s2", "probe s2: y")]


def test_suspect_asks_again_when_the_reply_is_unreadable():
    replies = iter(["sorry, something went wrong", json.dumps({"suspicions": [{"claim": "x", "route": "GET /"}]})])
    out = suspect("pr", "diff", "src", chat=lambda m, p, temperature=0.7: (next(replies), 1, 1))
    assert [s["claim"] for s in out["suspicions"]] == ["x"]
    # a readable "nothing suspicious" is an answer, not a failure: no second call
    calls = []
    suspect("pr", "diff", "src", chat=lambda m, p, temperature=0.7: (calls.append(1) or '{"suspicions": []}', 1, 1))
    assert len(calls) == 1


def test_intended_needs_a_real_quote_and_confirmed_suspicions_escalate():
    from doppel.diff import Finding
    from doppel.judge import Verdict, escalate, judge
    f = [Finding("status", "p", 1, "POST /api/me/following/2", "status 409 -> 204", 409, 204),
         Finding("body", "q", 0, "GET /products", "changed: in_stock (added)", {}, {})]
    pr = ("# Simplify follow\n\n`User.follow` already ignores duplicates, so the endpoint doesn't need its "
          "own check.\nAdds an `in_stock` flag.")

    def judge_says_intended(model, prompt, temperature=0.7):
        if "Observed changes:" in prompt:  # without the diff: only in_stock is announced
            return json.dumps({"checks": [{"id": "c1", "announced": False},
                                          {"id": "c2", "announced": True, "quote": "Adds an in_stock flag"}]}), 1, 1
        return json.dumps({"verdicts": [
            {"id": "c1", "label": "intended", "quote": "follow now answers 204 for duplicates", "why": "matches diff"},
            {"id": "c2", "label": "intended", "quote": "Adds an in_stock flag", "why": "announced"}]}), 1, 1
    v = judge(f, pr, "diff", chat=judge_says_intended)["verdicts"]
    # the judge saw the diff and excused the 409 -> 204; asked without the diff, nothing announces it
    assert [x.label for x in v] == ["regression", "intended"]
    checks = {"s1": {"status": "confirmed", "changes": ["f1"]}, "s2": {"status": "refuted", "changes": ["f2"]}}
    assert [x.label for x in escalate([Verdict("needs_human", "?"), v[1]], checks)] == ["regression", "intended"]
