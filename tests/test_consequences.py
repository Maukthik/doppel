"""The judge and side effects: a direct consequence of an announced change is intended (with a real quote); a
change that contradicts or reaches beyond the announcement stays a regression."""
import json

from doppel.diff import Finding, compare, context
from doppel.judge import ANNOUNCE_PROMPT, judge

PR = "# Add an optional location\n\nUsers can set a `location` with `PUT /api/me`; it is `null` until they do."


def fake_chat(first: dict, second: dict, seen: list):
    answers = iter([first, second])

    def chat(model, prompt, temperature=0.1):
        seen.append(prompt)
        return json.dumps(next(answers)), 10, 10
    return chat


def finding(ctx: str) -> Finding:
    return Finding("status", "probe", 1, "PUT /api/me", "status 400 -> 200", 400, 200, ctx)


def test_context_reaches_the_judge_and_a_consequence_with_a_real_quote_is_intended():
    seen = []
    chat = fake_chat({"verdicts": [{"id": "c1", "label": "regression", "why": "status changed"}]},
                     {"checks": [{"id": "c1", "expected": True,
                                  "quote": "Users can set a `location` with `PUT /api/me`",
                                  "because": "the request sends location, which head now accepts"}]}, seen)
    f = finding('sent {"location": "Lisbon"} | base response fields: id, username')
    [v] = judge([f], PR, "diff", chat=chat)["verdicts"]
    assert 'sent {\\"location\\": \\"Lisbon\\"}' in seen[1] or '"location": "Lisbon"' in seen[1]
    assert v.label == "intended" and "sends location" in v.why


def test_a_made_up_quote_is_still_not_an_announcement():
    chat = fake_chat({"verdicts": [{"id": "c1", "label": "intended", "quote": "x", "why": "w"}]},
                     {"checks": [{"id": "c1", "expected": True, "quote": "Status codes for PUT /api/me change.",
                                  "because": "it says so"}]}, [])
    [v] = judge([finding("sent {}")], PR, "diff", chat=chat)["verdicts"]
    assert v.label == "regression"


def test_a_contradiction_stays_a_regression_with_the_judges_reason():
    chat = fake_chat({"verdicts": [{"id": "c1", "label": "regression", "why": "w"}]},
                     {"checks": [{"id": "c1", "expected": False,
                                  "because": "a 20-character post is rejected under a 140-character rule"}]}, [])
    [v] = judge([finding("sent {}")], PR, "diff", chat=chat)["verdicts"]
    assert v.label == "regression" and "140-character rule" in v.why


def test_the_rules_name_contradiction_and_overreach():
    assert "contradicts the announcement" in ANNOUNCE_PROMPT and "reaches beyond it" in ANNOUNCE_PROMPT
    assert "silently" in ANNOUNCE_PROMPT and "word for word" in ANNOUNCE_PROMPT


def test_context_shows_the_step_before_what_was_sent_and_the_fields():
    base = [{"persona": "p", "steps": [
        {"i": 0, "request": "POST /api/me/following/3", "status": 204, "body": None, "ms": 5},
        {"i": 1, "request": "GET /api/me/following", "status": 200, "body": {"data": [{"id": 3}]}, "ms": 5},
        {"i": 2, "request": "PUT /api/me", "status": 200, "sent": {"bio": "hi"},
         "body": {"id": 1, "about_me": "hi"}, "ms": 5}]}]
    head = [{"persona": "p", "steps": [
        {"i": 0, "request": "POST /api/me/following/3", "status": 400, "body": {"message": "no"}, "ms": 5},
        {"i": 1, "request": "GET /api/me/following", "status": 200, "body": {"data": []}, "ms": 5},
        {"i": 2, "request": "PUT /api/me", "status": 200, "sent": {"bio": "hi"}, "body": {"id": 1}, "ms": 5}]}]
    by_req = {f.request: f.context for f in compare(base, head)}
    assert "before that: POST /api/me/following/3 -> base 204, head 400" in by_req["GET /api/me/following"]
    assert 'sent {"bio": "hi"}' in by_req["PUT /api/me"]
    assert "base response fields: id, about_me; head response fields: id" in by_req["PUT /api/me"]  # data lost


def test_context_is_empty_safe():
    assert context([{"request": "GET /", "status": 200}], 0, [], 0) == ""
