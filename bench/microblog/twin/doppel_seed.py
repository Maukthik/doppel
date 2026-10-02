"""Seed data for the Doppel twin of microblog-api: a small social network to replay users against.

Runs once on the base code (both worlds start from this database). With --migrate-only it just
applies migrations, which is what each world runs after its own code is in place.

Accounts that synthetic users can log in with (password for all: "doppel-pass"):
    alice, bob, carol, dave, erin, frank
plus 24 imported users without a password. Alice follows bob and carol; bob follows alice;
carol follows nobody; dave follows everyone named above. 150 posts with fixed timestamps.
"""
import random
import sys
from datetime import datetime, timedelta

from alembic.config import main as alembic

alembic(argv=["--raiseerr", "upgrade", "head"])
if "--migrate-only" in sys.argv:
    sys.exit(0)

from api.app import create_app, db  # noqa: E402
from api.models import Post, User  # noqa: E402
from faker import Faker  # noqa: E402

PASSWORD = "doppel-pass"
NAMED = ["alice", "bob", "carol", "dave", "erin", "frank"]
FOLLOWS = {"alice": ["bob", "carol"], "bob": ["alice"], "dave": ["alice", "bob", "carol", "erin", "frank"]}

random.seed(7)
Faker.seed(7)
fake = Faker()
start = datetime(2026, 1, 1, 9, 0, 0)

app = create_app()
with app.app_context():
    users = {}
    for name in NAMED:
        users[name] = User(username=name, email=f"{name}@example.com", password=PASSWORD,
                           about_me=f"I am {name}.", first_seen=start, last_seen=start)
    imported = [User(username=f"{fake.user_name()}{i}", email=f"user{i}@example.org",
                     about_me=fake.sentence(), first_seen=start, last_seen=start) for i in range(24)]
    db.session.add_all([*users.values(), *imported])
    db.session.flush()
    for who, targets in FOLLOWS.items():
        for t in targets:
            users[who].follow(users[t])
    for u in imported:
        for t in random.sample([*users.values(), *imported], 3):
            if t is not u:
                u.follow(t)
    everyone = [*users.values(), *imported]
    for i in range(150):
        author = users[NAMED[i % len(NAMED)]] if i % 3 == 0 else random.choice(everyone)
        db.session.add(Post(text=fake.paragraph()[:280], author=author,
                            timestamp=start + timedelta(minutes=17 * i)))
    db.session.commit()
    print(f"seeded {len(everyone)} users, 150 posts")
