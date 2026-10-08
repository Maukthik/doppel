import os
import sqlite3
from werkzeug.security import generate_password_hash

os.makedirs("instance", exist_ok=True)
db_path = os.path.join("instance", "flaskr.sqlite")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

password_hash = generate_password_hash("doppel-pass")
users = [
    ("alice", password_hash),
    ("bob", password_hash),
    ("charlie", password_hash),
]
for username, pwd in users:
    conn.execute(
        "INSERT OR IGNORE INTO user (username, password) VALUES (?, ?)",
        (username, pwd),
    )
conn.commit()

user_rows = conn.execute("SELECT id, username FROM user").fetchall()
user_map = {row["username"]: row["id"] for row in user_rows}

posts = [
    ("First Post", "This is the first post.", user_map["alice"]),
    ("Second Post", "Content of the second post.", user_map["bob"]),
    ("Third Post", "Another post here.", user_map["charlie"]),
    ("Fourth Post", "More content.", user_map["alice"]),
    ("Fifth Post", "Yet another post.", user_map["bob"]),
    ("Sixth Post", "Post number six.", user_map["charlie"]),
    ("Seventh Post", "Lucky seven.", user_map["alice"]),
    ("Eighth Post", "Eight is great.", user_map["bob"]),
    ("Ninth Post", "Almost ten.", user_map["charlie"]),
    ("Tenth Post", "The last one.", user_map["alice"]),
]
for title, body, author_id in posts:
    conn.execute(
        "INSERT INTO post (title, body, author_id, created) VALUES (?, ?, ?, datetime('now'))",
        (title, body, author_id),
    )
conn.commit()
conn.close()
print(f"seeded 3 users, 10 posts into {db_path}")
