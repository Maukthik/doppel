"""Stage 4 offline tests: the PR comment upsert and the action definition."""
import json
from pathlib import Path

import pytest

from doppel import cli
from doppel.github import MARKER, pr_number_from_event, upsert_comment

ROOT = Path(__file__).resolve().parent.parent


class FakeGitHub:
    def __init__(self, existing):
        self.comments, self.calls = existing, []

    def __call__(self, method, url, token, body=None):
        self.calls.append((method, url.split("/repos/")[1], body))
        if method == "GET":
            page = int(url.rsplit("page=", 1)[1])
            return self.comments[(page - 1) * 100: page * 100]
        return {}


def test_creates_a_comment_when_none_exists():
    gh = FakeGitHub([{"id": 1, "body": "LGTM"}])
    assert upsert_comment("## Doppel", "me/app", 7, "t", request=gh) == "created"
    method, path, body = gh.calls[-1]
    assert (method, path) == ("POST", "me/app/issues/7/comments") and body["body"].startswith(MARKER)


def test_updates_its_own_comment_even_on_a_later_page():
    existing = [{"id": i, "body": "chatter"} for i in range(100)] + [{"id": 555, "body": f"{MARKER}\nold"}]
    gh = FakeGitHub(existing)
    assert upsert_comment("## Doppel v2", "me/app", 7, "t", request=gh) == "updated"
    assert gh.calls[-1][:2] == ("PATCH", "me/app/issues/comments/555")


def test_pr_number_comes_from_the_event(tmp_path):
    ev = tmp_path / "event.json"
    ev.write_text(json.dumps({"pull_request": {"number": 42}}))
    assert pr_number_from_event(str(ev)) == 42
    ev.write_text(json.dumps({"push": {}}))
    with pytest.raises(RuntimeError):
        pr_number_from_event(str(ev))


def test_doppel_errors_exit_2_not_1(tmp_path):
    code = cli.main(["run", "--base", str(tmp_path), "--head", str(tmp_path), "--personas", str(tmp_path / "x.json")])
    assert code == 2   # 1 is reserved for "regressions found", so CI can tell them apart


def test_action_definition_wires_the_steps():
    yaml = pytest.importorskip("yaml")
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    names = [s.get("name") for s in action["runs"]["steps"]]
    assert names.index("Run the twin") < names.index("Comment on the PR") < names.index("Fail on regressions")
    assert {"nebius-api-key", "nebius-project-id"} <= {k for k, v in action["inputs"].items() if v.get("required")}
