"""Rehearsal repairs a probe whose request could not have reached its code (isso's edit probes)."""
import json

from doppel.personas import PROBES
from doppel.rehearsal import probe_stuck, repair


def rec(name, *steps):
    return {"persona": name, "steps": [{"i": i, "request": r, "status": s, "body": {}} for i, (r, s) in
                                       enumerate(steps)]}


# isso 2e58889: the probe created a comment (201), then edited it on a route that only takes POST (405)
EDIT_PROBE = {"name": "probe-s4", "probe": "s4", "goal": "edits no longer escape author and website",
              "steps": [{"method": "POST", "path": "/new?uri=/blog/x", "json": {"text": "abc"}, "save": {"id": "id"}},
                        {"method": "PUT", "path": "/id/{id}/edit/{hash}", "json": {"author": "<b>M</b>"}}]}
EDIT_RUN = rec("probe-s4", ("POST /new?uri=/blog/x", 201), ("PUT /id/4/edit/e929563f93d9", 405))
FIXED = {**EDIT_PROBE, "steps": [EDIT_PROBE["steps"][0],
                                 {"method": "PUT", "path": "/id/{id}", "json": {"author": "<b>M</b>"}}]}


def fake_chat(reply, seen):
    def chat(model, prompt, temperature=0.2):
        seen.append(prompt)
        return json.dumps({"personas": reply}), 10, 10
    return chat


def test_a_probe_whose_target_answered_405_is_repaired_and_keeps_its_tag(tmp_path):
    seen = []
    out = repair([EDIT_PROBE], [EDIT_RUN], tmp_path, chat=fake_chat([FIXED], seen))
    assert out["repaired"] == ["probe-s4"]
    assert out["personas"][0]["steps"][1]["path"] == "/id/{id}" and out["personas"][0]["probe"] == "s4"
    assert "keep its trigger input" in seen[0] and "PUT /id/4/edit/e929563f93d9 -> 405" in seen[0]


def test_an_ordinary_persona_with_one_405_is_left_alone(tmp_path):
    user = {**EDIT_PROBE, "name": "grace_edit"}
    user.pop("probe")
    seen = []
    out = repair([user], [rec("grace_edit", *[s for s in [(r["request"], r["status"]) for r in EDIT_RUN["steps"]]])],
                 tmp_path, chat=fake_chat([], seen))
    assert out["repaired"] == [] and seen == []                       # rehearsal for personas is unchanged


def test_a_probe_that_meant_to_get_404_is_not_stuck():
    # r19-style: delete a post that doesn't exist; the 404 is the point of the probe
    assert not probe_stuck(rec("probe-s2", ("POST /api/tokens", 200), ("DELETE /api/posts/9999", 404)))


def test_an_unfilled_placeholder_is_stuck_but_a_query_with_braces_is_not():
    assert probe_stuck(rec("probe-s1", ("GET /api/users/{user_id}", 404)))
    assert not probe_stuck(rec("probe-s1", ("GET /api/search?q={x}", 200)))


def test_probes_are_told_to_send_every_value_the_trigger_names():
    assert "send every one of them" in PROBES and "routes and methods that exist" in PROBES
