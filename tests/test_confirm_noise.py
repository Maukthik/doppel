from doppel.traffic import _merge_noise


def test_merge_noise_is_the_union_of_every_pair():
    one = {"flaky": {"t1"}, "unstable": set(), "status": set(), "keys": {"id"}, "paths": {"t1": {"0.id"}}}
    two = {"flaky": set(), "unstable": {"t2"}, "status": {"t3"}, "keys": {"ts"}, "paths": {"t1": {"1.id"}, "t2": {"x"}}}
    m = _merge_noise(one, two)
    assert m["flaky"] == {"t1"} and m["unstable"] == {"t2"} and m["status"] == {"t3"}
    assert m["keys"] == {"id", "ts"} and m["paths"] == {"t1": {"0.id", "1.id"}, "t2": {"x"}}
