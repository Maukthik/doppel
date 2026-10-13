"""Services the tests need, started inside the test sandboxes (test mode). PostgreSQL for now.

Every test job runs in a throwaway copy of an image: what is installed into the image stays, a running server
does not. So the server is installed once into the image (doppel.traffic.NebiusSession.add_postgres) and every
job starts it before the tests (a step in the session's exports).
"""
from __future__ import annotations

import re

# test output that means "a PostgreSQL server (or its driver) is missing"
NEEDS_POSTGRES = re.compile("|".join([
    r"No module named '?psycopg2?'?",                       # weblate: django.contrib.postgres without a driver
    r"Error loading psycopg",                               # Django's ImproperlyConfigured wording
    r"failed to resolve host '?(?:postgres|postgresql|db|database)\b",   # psycopg 3 (defectdojo)
    r"could not translate host name \"?(?:postgres|postgresql|db|database)\b",   # psycopg2
    r"connection to server .{0,120}?port 5432 failed",
    r"Is the server running .{0,120}?5432",
    r"Connection refused[\s\S]{0,200}?5432|5432[\s\S]{0,200}?Connection refused",
]), re.I)
MISSING_NAME = re.compile(r'(?:role|database) "([A-Za-z0-9_.-]{1,63})" does not exist')
HOST_NAMES = "postgres postgresql db database"


def postgres_needed(output: str) -> bool:
    return bool(NEEDS_POSTGRES.search(output or ""))


def missing_names(output: str) -> list[str]:
    """Roles or databases the tests' connection asked for that don't exist yet, in order, once each."""
    return list(dict.fromkeys(MISSING_NAME.findall(output or "")))


def postgres_setup(apt: str, activate: str = "") -> str:
    """Install once into an image: the server, a cluster that lets every local login in, and the drivers."""
    hba = r"local all all trust\nhost all all 127.0.0.1/32 trust\nhost all all ::1/128 trust\n"
    return (f"{apt} update -qq >/dev/null 2>&1; "
            f"{apt} install -y -qq --no-install-recommends postgresql >/dev/null 2>&1 && "
            "V=$(ls /usr/lib/postgresql | sort -V | tail -1) && "
            '(pg_lsclusters -h | grep -q "^$V main" || pg_createcluster $V main >/dev/null) && '
            f"printf '{hba}' > /etc/postgresql/$V/main/pg_hba.conf && "
            f"{activate}pip install --quiet 'psycopg[binary]' psycopg2-binary")


def postgres_start() -> str:
    """Before every job: CI's host names point here, and the server runs (pg_ctlcluster waits until it's up)."""
    return (f"(grep -qw postgres /etc/hosts || echo '127.0.0.1 {HOST_NAMES}' >> /etc/hosts; "
            "pg_ctlcluster $(ls /usr/lib/postgresql | sort -V | tail -1) main start) >/dev/null 2>&1; ")


def postgres_create(names: list[str]) -> str:
    """Before every job, after the start: these names as superuser roles, each with a database of its own."""
    steps = []
    for n in names:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,63}", n):
            continue
        steps.append(f"(runuser -u postgres -- psql -qc 'CREATE ROLE \"{n}\" SUPERUSER LOGIN'; "
                     f"runuser -u postgres -- createdb -O \"{n}\" \"{n}\") >/dev/null 2>&1; ")
    return "".join(steps)
