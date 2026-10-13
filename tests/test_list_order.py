from doppel.traffic import _canon, _merge_noise, compare_worlds, noise


def _world(*bodies):
    requests = [{"sig": "POST /api/flow_runs/filter", "status": 200, "body": b} for b in bodies]
    return {"tests": {"t": {"outcome": "passed", "requests": requests}}}


RUNS = [{"name": "a", "tags": ["blue"]}, {"name": "b", "tags": ["red"]}]


def test_lists_in_another_order_are_noise_not_paths():
    nz = noise(_world(RUNS), _world(list(reversed(RUNS))))
    assert ("t", 0) in nz["order"] and ("t", 0) not in nz["paths"]


def test_order_only_differences_are_not_findings_but_real_changes_are():
    nz = _merge_noise(noise(_world(RUNS), _world(list(reversed(RUNS)))))
    same_items = _world(list(reversed(RUNS)))
    assert compare_worlds(_world(RUNS), same_items, nz, set())[0] == []
    changed = _world([{"name": "a", "tags": ["blue"]}, {"name": "b", "tags": ["green"]}])
    assert compare_worlds(_world(RUNS), changed, nz, set())[0]


def test_canon_is_order_free_and_nested():
    assert _canon({"x": [3, 1, 2], "y": [{"k": [2, 1]}]}) == _canon({"y": [{"k": [1, 2]}], "x": [2, 3, 1]})
