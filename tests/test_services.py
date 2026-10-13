from doppel.services import missing_names, postgres_create, postgres_needed

WEBLATE = "    from psycopg2 import errors, extensions, sql  # NOQA\nModuleNotFoundError: No module named 'psycopg2'"
DEFECTDOJO = ("django.db.utils.OperationalError: failed to resolve host 'postgres': "
              "[Errno -3] Temporary failure in name resolution")
REFUSED = ('connection to server at "localhost" (127.0.0.1), port 5432 failed: Connection refused\n'
           "\tIs the server running on that host and accepting TCP/IP connections?")


def test_postgres_needed():
    for out in (WEBLATE, DEFECTDOJO, REFUSED, "could not translate host name \"db\" to address"):
        assert postgres_needed(out), out


def test_other_failures_do_not_start_postgres():
    for out in ("No module named 'requests'", "redis.exceptions.ConnectionError: port 6379", "AssertionError"):
        assert not postgres_needed(out), out


def test_missing_roles_and_databases():
    out = 'FATAL:  role "weblate" does not exist\nFATAL:  database "dojo" does not exist\nrole "weblate" does not exist'
    assert missing_names(out) == ["weblate", "dojo"]


def test_create_skips_unsafe_names():
    sh = postgres_create(["weblate", "x'; DROP TABLE y; --"])
    assert 'CREATE ROLE "weblate"' in sh and "DROP" not in sh
