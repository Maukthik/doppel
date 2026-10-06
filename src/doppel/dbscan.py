"""What is inside every SQLite database after the seed: tables and row counts.

Run right after the seed, in the seeded world, so a refused login can be explained with evidence
("users=6 in /data/app.db, users=0 in /app/db.sqlite: the server reads the empty one") instead of
guesses. Standard library only; it is also sent to sandboxes as a one-line `python3 -c` script.
"""
import os
import sqlite3

SKIP = {".git", "node_modules", ".venv", "venv", "site-packages", "__pycache__", "proc", "sys", "usr", "lib"}
SUFFIXES = (".sqlite", ".sqlite3", ".db")


def scan(roots=(".", "/data", "/tmp", "/var/lib"), depth=4, limit=10):
    found = []  # in root order: the app's own folder first, so its database is never cut by the limit
    for root in roots:
        if not os.path.isdir(root):
            continue
        base = root.rstrip("/").count("/")
        for here, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP] if here.count("/") - base < depth else []
            found += sorted(os.path.abspath(os.path.join(here, f)) for f in files if f.endswith(SUFFIXES))
    lines = []
    for path in list(dict.fromkeys(found))[:limit]:
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                      if not r[0].startswith("sqlite_")]
            counts = []
            for t in tables[:20]:
                n = con.execute('SELECT COUNT(*) FROM "' + t.replace('"', '""') + '"').fetchone()[0]
                counts.append(t + "=" + str(n))
            lines.append(f"[dbscan] {path}: " + (", ".join(counts) or "no tables"))
        except Exception as e:  # noqa: BLE001 - a scan must never break the seed
            lines.append(f"[dbscan] {path}: unreadable ({type(e).__name__})")
    return lines or ["[dbscan] no SQLite database files found"]


def one_liner() -> str:
    """This module as a shell-safe `python3 -c '...'` command."""
    src = open(__file__, encoding="utf-8").read().split('"""', 2)[2].split("\ndef one_liner")[0]
    return "python3 -c " + _quote(src + "\nprint('\\n'.join(scan()))")


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    print("\n".join(scan()))
