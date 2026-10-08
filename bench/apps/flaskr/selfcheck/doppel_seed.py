"""Hand-written seed for the harness self-check: two users and three posts."""
import sqlite3
from pathlib import Path

from flaskr import create_app
from flaskr.db import init_db
from werkzeug.security import generate_password_hash

app = create_app()
with app.app_context():
    init_db()
db = sqlite3.connect(Path(app.instance_path) / "flaskr.sqlite")
for name in ("alice", "bob"):
    db.execute("INSERT INTO user (username, password) VALUES (?, ?)", (name, generate_password_hash("doppel-pass")))
for author, title, body, created in [(1, "Hello", "First post", "2026-01-01 10:00:00"),
                                     (1, "Second", "More words", "2026-01-02 10:00:00"),
                                     (2, "Bob here", "Hi all", "2026-01-03 10:00:00")]:
    db.execute("INSERT INTO post (author_id, title, body, created) VALUES (?, ?, ?, ?)", (author, title, body, created))
db.commit()
