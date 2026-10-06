"""What is inside every SQLite database after the seed: tables and row counts.

Run right after the seed, in the seeded world, so a refused login can be explained with evidence
("users=6 in /data/app.db, users=0 in /app/db.sqlite: the server reads the empty one") instead of
guesses. Standard library only; it is also sent to sandboxes as a one-line `python3 -c` script.
"""
import os
import sqlite3

SKIP = {".git", "node_modules", ".venv", "venv", "site-packages", "__pycache__", "proc", "sys", "usr", "lib"}
SUFFIXES = (".sqlite", ".sqlite3", ".db")
USERISH = ("user", "users", "account", "accounts", "member", "members", "auth_user", "profile", "profiles")
IDENTITY = ("username", "user_name", "login", "email", "name")
HASH_PREFIXES = ("scrypt:", "pbkdf2:", "pbkdf2_", "argon2", "bcrypt", "$2a$", "$2b$", "$2y$", "sha256$", "sha1$",
                 "md5$", "!")


def describe_secret(value):
    """How a stored password looks, never the value itself: a known hash scheme, or plain text."""
    if value is None or value == "":
        return "EMPTY"
    text = str(value)
    if text.lower().startswith(HASH_PREFIXES) or (len(text) >= 40 and ("$" in text or ":" in text)):
        return "hashed(" + text.split("$")[0].split(":")[0][:12] + ")"
    return "PLAIN TEXT, not hashed"


def sample_users(con, table, rows=5):
    """Who can log in: identity columns as they are, password columns described, for the first rows."""
    cols = [r[1] for r in con.execute('PRAGMA table_info("' + table.replace('"', '""') + '")')]
    ident = [c for c in cols if c.lower() in IDENTITY]
    secret = [c for c in cols if "password" in c.lower() or c.lower() in ("hash", "pw_hash", "passwd")]
    if not ident and not secret:
        return []
    picked = ident + secret
    sql = "SELECT " + ", ".join('"' + c.replace('"', '""') + '"' for c in picked) + \
          ' FROM "' + table.replace('"', '""') + '" LIMIT ' + str(rows)
    out = []
    for row in con.execute(sql):
        parts = [ident[i] + "=" + str(row[i]) for i in range(len(ident))]  # no zip(strict=): runs on old Pythons
        parts += [secret[j] + "=" + describe_secret(row[len(ident) + j]) for j in range(len(secret))]
        out.append("[dbscan]   " + table + ": " + " ".join(parts))
    return out


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
            for t in tables:
                if t.lower() in USERISH:
                    lines += sample_users(con, t)
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
