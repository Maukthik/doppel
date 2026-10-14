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


def _world_outcome(outcome, *bodies):
    w = _world(*bodies)
    w["tests"]["t"]["outcome"] = outcome
    return w


def test_a_reordering_no_test_notices_is_counted_not_flagged():
    nz = _merge_noise(noise(_world(RUNS), _world(RUNS)))       # base agreed with itself on the order
    findings, stats = compare_worlds(_world(RUNS), _world(list(reversed(RUNS))), nz, set())
    assert findings == [] and stats["order_only"] == 1


def test_a_reordering_that_breaks_a_test_is_still_found():
    nz = _merge_noise(noise(_world(RUNS), _world(RUNS)))       # h02: the feed flipped, its test fails on head
    findings, stats = compare_worlds(_world(RUNS), _world_outcome("failed", list(reversed(RUNS))), nz, set())
    assert findings and stats["order_only"] == 0
    assert "test fails on this" in findings[0].detail


def test_a_changed_item_is_never_order_only():
    nz = _merge_noise(noise(_world(RUNS), _world(RUNS)))
    changed = [{"name": "b", "tags": ["red"]}, {"name": "a", "tags": ["green"]}]
    findings, stats = compare_worlds(_world(RUNS), _world(changed), nz, set())
    assert findings and stats["order_only"] == 0
