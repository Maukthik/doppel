"""Did the users run the code the PR changed? Changed regions, the line tracer, and the summary."""
import json
import subprocess
import sys
import textwrap

from doppel import reach
from doppel.backends import nebius
from doppel.spec import TwinSpec

OLD = textwrap.dedent('''
    PRICE = 10                     # module level: runs on import, not per request

    class Shop:
        tax = 5                    # class level: same

        def total(self, qty):
            """Price for qty items."""
            if qty <= 0:
                raise ValueError("qty")
            return qty * PRICE + self.tax

    def label(item):
        return {"name": item,
                "kind": "thing"}
''')
NEW = OLD.replace('''        if qty <= 0:
            raise ValueError("qty")
''', '').replace('PRICE = 10 ', 'PRICE = 12 ').replace('"kind": "thing"}', '"kind": "thing", "new": True}')


def two_trees(tmp_path, old=OLD, new=NEW):
    (tmp_path / "base").mkdir()
    (tmp_path / "head").mkdir()
    (tmp_path / "base" / "app.py").write_text(old)
    (tmp_path / "head" / "app.py").write_text(new)
    return tmp_path / "base", tmp_path / "head"


def test_changes_keep_only_code_that_runs_per_request_and_name_its_function(tmp_path):
    base, head = two_trees(tmp_path)
    found = reach.changes(base, head, ["app.py"])
    assert [(c["where"], c["base_lines"], c["head_lines"]) for c in found] == [
        ("Shop.total", [9, 10], []),        # the removed check: only base can run it
        ("label", [14], [12]),              # a continuation line maps to its statement's first line
    ]                                       # PRICE = 12 (module level) is not a change users can reach
    assert reach.targets(found) == {"base": {"app.py": [9, 10, 14]}, "head": {"app.py": [12]}}


def test_the_tracer_records_watched_lines_across_threads_and_child_processes(tmp_path):
    (tmp_path / "app.py").write_text(textwrap.dedent('''
        import subprocess, sys, threading

        def watched(n):
            return n * 2

        def unwatched():
            return 1

        t = threading.Thread(target=watched, args=(1,))
        t.start(); t.join()
        unwatched()
        if len(sys.argv) == 1:
            subprocess.run([sys.executable, __file__, "child"], check=True)
    '''))
    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(reach.TRACER)
    (hook / "targets.json").write_text(json.dumps({"app.py": [5, 8]}))
    out = tmp_path / "hits.txt"
    env = {"PYTHONPATH": str(hook), reach.TARGETS_ENV: str(hook / "targets.json"),
           reach.OUT_ENV: str(out), reach.ROOT_ENV: str(tmp_path), "PATH": ""}
    subprocess.run([sys.executable, "app.py"], cwd=tmp_path, env=env, check=True)
    lines = out.read_text().splitlines()
    assert sorted(set(lines)) == ["app.py:5", "app.py:8"]
    assert lines.count("app.py:5") == 2        # once in the parent's thread, once in the child process
    assert reach.parse_hits("noise\n" + reach.MARKER + "\napp.py:5\napp.py:5\nbad line\n") == ["app.py:5"]


def test_the_tracer_never_breaks_an_app_when_it_has_nothing_to_watch(tmp_path):
    (tmp_path / "hook").mkdir()
    (tmp_path / "hook" / "sitecustomize.py").write_text(reach.TRACER)
    done = subprocess.run([sys.executable, "-c", "print('fine')"], capture_output=True, text=True,
                          env={"PYTHONPATH": str(tmp_path / "hook"), reach.TARGETS_ENV: "/nope.json", "PATH": ""})
    assert done.stdout.strip() == "fine" and done.returncode == 0


def test_the_summary_says_which_changes_users_reached_and_which_none_did():
    found = [{"id": "c1", "file": "app.py", "where": "Shop.total", "base_lines": [9], "head_lines": []},
             {"id": "c2", "file": "app.py", "where": "label", "base_lines": [14], "head_lines": [14]},
             {"id": "c3", "file": "app.py", "where": "refund", "base_lines": [], "head_lines": [30]}]
    worlds = {"base": [{"persona": "careless", "reached": ["app.py:9"]}, {"persona": "shopper", "reached": []}],
              "head": [{"persona": "careless", "reached": []}, {"persona": "shopper", "reached": ["app.py:14"]}]}
    summary = reach.summarize(found, worlds)
    assert [(c["reached_by"], c["reached_in"]) for c in summary] == [
        (["careless"], ["base"]), (["shopper"], ["head"]), ([], [])]
    assert reach.reach_line(summary) == ("Changed code reached by the users: 2 of 3 change(s). "
                                         "Not reached by any user: `refund` (app.py).")


def test_the_sandbox_command_loads_the_tracer_only_when_asked():
    spec = TwinSpec(start="python app.py", ready="/")
    plain, traced = nebius.persona_command(spec), nebius.persona_command(spec, trace=True)
    assert "PYTHONPATH" not in plain and reach.MARKER not in plain
    assert f"PYTHONPATH=/doppel/{reach.TRACE_DIR}${{PYTHONPATH:+:$PYTHONPATH}}" in traced
    assert traced.index("PYTHONPATH") < traced.index("python app.py") < traced.index(reach.MARKER)
    assert traced.rstrip().endswith("exit $rc")                       # the runner's exit code still wins


def test_a_shop_run_reports_reach_in_the_comment_and_the_json(tmp_path):
    from doppel import cli
    from tests.test_doppel import SHOP
    out = tmp_path / "run.json"
    assert cli.main(["run", "--base", str(SHOP / "base"), "--head", str(SHOP / "head"),
                     "--personas", str(SHOP / "personas.json"), "--json", str(out), "--out",
                     str(tmp_path / "report.md")]) == 0
    data = json.loads(out.read_text())
    assert [c["where"] for c in data["changes"]] == ["product_json", "order_total", "Handler.do_POST"]
    assert all(c["reached_by"] for c in data["changes"])
    assert "Changed code reached by the users: 3 of 3 change(s)." in (tmp_path / "report.md").read_text()
