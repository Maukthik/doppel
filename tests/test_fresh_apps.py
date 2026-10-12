"""The zero-setup experiment harness: two sets, pinned commits, honest names and reasons."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))
import fresh_apps  # noqa: E402


def test_the_heldout_set_is_pinned_and_disjoint_from_the_dev_set():
    dev = {a["name"] for a in fresh_apps.APPS}
    held = {a["name"] for a in fresh_apps.HELDOUT}
    assert len(held) == 10 and not dev & held
    assert all(len(a["sha"]) == 40 for a in fresh_apps.HELDOUT)     # a full commit: the run cannot drift
    assert not any("subdir" in a for a in fresh_apps.HELDOUT)        # nothing hand-given: Doppel must find it


def test_an_unknown_app_name_is_an_error_not_a_silent_skip(capsys):
    assert fresh_apps.main(["run", "full-stack-fastapi-template"]) == 2
    err = capsys.readouterr().err
    assert "unknown app name" in err and "fastapi-template" in err


def test_a_missing_module_is_a_dependency_gap_not_a_service():
    assert fresh_apps.category("E   ModuleNotFoundError: No module named 'redis'") == "missing dependency"
    assert fresh_apps.category("could not connect to server: Connection refused") == "needs a service"


def test_a_pinned_app_is_cloned_at_exactly_its_commit(tmp_path, monkeypatch):
    origin = tmp_path / "origin"
    origin.mkdir()
    git = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True, text=True)  # noqa: E731
    git("init", "-q")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "first")
    first = git("rev-parse", "HEAD").stdout.strip()
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "second")
    git("config", "uploadpack.allowReachableSHA1InWant", "true")
    monkeypatch.setattr(fresh_apps, "CACHE", tmp_path / "cache")
    code, sha = fresh_apps.clone({"name": "x", "repo": str(origin), "sha": first})
    assert first.startswith(sha) and code == tmp_path / "cache" / "x"
    _, again = fresh_apps.clone({"name": "x", "repo": str(origin), "sha": first})   # cached: no refetch needed
    assert again == sha


def test_the_heldout_page_has_its_own_folder_and_title(tmp_path, monkeypatch):
    monkeypatch.setattr(fresh_apps, "HELDOUT_RESULTS", tmp_path)
    (tmp_path / "babybuddy").mkdir()
    (tmp_path / "babybuddy" / "result.json").write_text(json.dumps(
        {"name": "babybuddy", "framework": "Django", "status": "stopped", "commit": "2f80de4", "seconds": 9,
         "category": "missing dependency", "reason": "No module named 'x'"}))
    page = fresh_apps.page("heldout").read_text(encoding="utf-8")
    assert "held-out" in page and "pinned" in page and "babybuddy" in page and "missing dependency" in page



def test_round_two_is_pinned_and_shares_no_app_with_the_earlier_sets():
    earlier = {a["name"] for a in fresh_apps.APPS + fresh_apps.HELDOUT}
    earlier_repos = {a["repo"] for a in fresh_apps.APPS + fresh_apps.HELDOUT}
    round2 = fresh_apps.ROUND2
    assert len(round2) == 10 and not earlier & {a["name"] for a in round2}
    assert not earlier_repos & {a["repo"] for a in round2}
    assert all(len(a["sha"]) == 40 and "subdir" not in a for a in round2)
    assert fresh_apps.results_for("round2").name == "round2"
