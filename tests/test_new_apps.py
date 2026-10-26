"""Gaps found by running Doppel on apps it had never seen (TESTING-APPS.md): each pinned here."""
from doppel import traffic_recorder
from doppel.setup_plan import names_tests, plan, python_files
from doppel.traffic import DERIVED_KEYS, is_test_file
from doppel.traffic import test_files as files_pytest_runs


def test_pytest_collects_the_files_its_configuration_names(tmp_path):
    # django-allauth: python_files = tests.py test_*.py *_tests.py, so most of its tests are in tests.py files
    (tmp_path / "pytest.ini").write_text("[pytest]\npython_files = tests.py test_*.py *_tests.py\n", encoding="utf-8")
    for rel in ("pkg/account/tests.py", "pkg/mfa/totp_tests.py", "tests/test_views.py", "pkg/models.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("", encoding="utf-8")
    assert python_files(tmp_path) == ["tests.py", "test_*.py", "*_tests.py"]
    assert files_pytest_runs(tmp_path, "python -m pytest") == [
        "pkg/account/tests.py", "pkg/mfa/totp_tests.py", "tests/test_views.py"]
    # and the PR's own copies of them are replaced by the base's, like any other test file
    assert is_test_file("pkg/account/tests.py") and is_test_file("pkg/mfa/totp_tests.py")
    assert not is_test_file("pkg/models.py")


def test_a_makefile_target_that_names_no_tests_is_passed_over(tmp_path):
    # django-allauth's Makefile still says `pytest allauth/`; its tests moved to tests/ long ago
    (tmp_path / "Makefile").write_text("test:\n\tpytest allauth/\n", encoding="utf-8")
    (tmp_path / "allauth").mkdir()
    (tmp_path / "allauth" / "views.py").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_login.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    assert not names_tests(tmp_path, "pytest allauth/") and names_tests(tmp_path, "pytest tests/")
    p = plan(tmp_path)
    assert "allauth" not in p.test and p.source["test"] != "Makefile"


def test_a_content_length_inside_a_body_is_not_a_change_of_its_own():
    # Kinto's batch answers carry each sub-response's headers: the length follows the body, which is compared
    assert "Content-Length" in DERIVED_KEYS and "content-length" in DERIVED_KEYS


def test_old_werkzeug_clients_are_recorded_with_their_path():
    class Builder:   # what Flask before 2.0 hands Client.open: no response.request to read the path from
        method, path, query_string = "POST", "/api/v1/posts/", b"page=2"
    assert traffic_recorder._werkzeug_target((Builder(),), {}) == ("POST", "/api/v1/posts/", "page=2")
    assert traffic_recorder._werkzeug_target(({"REQUEST_METHOD": "GET", "PATH_INFO": "/x", "QUERY_STRING": "a=1"},),
                                             {}) == ("GET", "/x", "a=1")
    assert traffic_recorder._werkzeug_target(("/users/1?full=1",), {"method": "PUT"}) == ("PUT", "/users/1", "full=1")


def test_httpx2_clients_are_recorded_like_httpx():
    # Datasette's test client moved to httpx2, a fork with the same transports: nothing was recorded
    assert traffic_recorder.HOOKS["httpx2"] is traffic_recorder.HOOKS["httpx"]


def test_build_leftovers_in_the_base_checkout_stay_out_of_every_world(tmp_path):
    # an editable install leaves app.egg-info in base only; copied into base's worlds, it changed how pytest
    # loaded the app there (Datasette: an error page differed on every run, a false alarm)
    from doppel.traffic import LocalSession
    base, head = tmp_path / "base", tmp_path / "head"
    for root in (base, head):
        (root / "app").mkdir(parents=True)
        (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (base / "app.egg-info").mkdir()
    (base / "app.egg-info" / "entry_points.txt").write_text("[pytest11]\napp = app\n", encoding="utf-8")
    session = LocalSession(base, head)
    try:
        assert not (session.code["base"] / "app.egg-info").exists()
        assert sorted(p.name for p in session.code["base"].iterdir()) == \
            sorted(p.name for p in session.code["head"].iterdir())
    finally:
        session.close()


def test_a_real_change_that_came_with_a_timestamp_survives_its_confirmation(monkeypatch):
    # flasky under load: both base runs fell in the same second, so `timestamp` didn't look like noise and the
    # first comparison said "author_email (added), timestamp". The reruns, a second later, showed the timestamp
    # varying; the finding no longer matched them word for word and was dropped whole, author_email with it.
    from doppel import traffic

    def world(email, stamp):
        body = {"id": 1, "timestamp": stamp} | ({"author_email": email} if email else {})
        return {"tests": {"t::posts": {"outcome": "passed", "requests": [
            {"sig": "GET /posts/1", "status": 200, "body": body}]}}, "exit": "0", "output": "", "recorder_errors": ""}
    a, a2, b = world(None, "10:00:01"), world(None, "10:00:01"), world("j@x.com", "10:00:02")
    nz = traffic.noise(a, a2)
    findings, _ = traffic.compare_worlds(a, b, nz, set())
    assert [f.detail for f in findings] == ["changed: author_email (added), timestamp"]
    reruns = iter([world(None, "10:00:05"), world("j@x.com", "10:00:06"),
                   world(None, "10:00:07"), world("j@x.com", "10:00:08")])

    class Session:
        def run(self, jobs):
            return [next(reruns) for _ in jobs]
    monkeypatch.setattr(traffic, "read_world", lambda w: w)
    monkeypatch.setattr(traffic, "confirm_command", lambda *a, **k: "python -m pytest t")
    kept, info = traffic.confirm(Session(), None, "python -m pytest", findings, a, b, nz, set())
    assert [f.detail for f in kept] == ["changed: author_email (added)"] and info["dropped"] == 0


def test_a_leaked_hash_is_shown_as_recorded_not_as_noise(monkeypatch):
    # doppel-demo#1 on the Try bot: each head run salts the hash differently, so the reruns learned
    # password_hash as noise and the comment's example read `password_hash: "(noise)"`
    from doppel import traffic

    def world(salt):
        body = {"id": 1} | ({"password_hash": f"scrypt:32768:8:1${salt}$abc"} if salt else {})
        return {"tests": {"t::users": {"outcome": "passed", "requests": [
            {"sig": "GET /api/users/1", "status": 200, "body": body}]}}, "exit": "0", "output": "",
            "recorder_errors": ""}
    a, a2, b = world(None), world(None), world("s1")
    nz = traffic.noise(a, a2)
    findings, _ = traffic.compare_worlds(a, b, nz, set())
    reruns = iter([world(None), world("s2"), world(None), world("s3")])

    class Session:
        def run(self, jobs):
            return [next(reruns) for _ in jobs]
    monkeypatch.setattr(traffic, "read_world", lambda w: w)
    monkeypatch.setattr(traffic, "confirm_command", lambda *a, **k: "python -m pytest t")
    kept, info = traffic.confirm(Session(), None, "python -m pytest", findings, a, b, nz, set())
    assert [f.detail for f in kept] == ["changed: password_hash (added)"]
    assert kept[0].head == {"password_hash": "scrypt:32768:8:1$s1$abc"}
    assert kept[0].base == {"password_hash": "(absent)"}
