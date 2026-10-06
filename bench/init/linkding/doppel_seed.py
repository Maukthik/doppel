"""Starting data for the twin: two users with fixed API tokens and a handful of tagged bookmarks."""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bookmarks.settings.dev")
import django  # noqa: E402

django.setup()
from datetime import datetime, timedelta, timezone  # noqa: E402

from django.contrib.auth.models import User  # noqa: E402

from bookmarks.models import ApiToken, Bookmark, Tag  # noqa: E402

BOOKMARKS = {
    "alice": [("https://docs.python.org/3/", "Python docs", ["python", "docs"]),
              ("https://www.djangoproject.com/", "Django", ["python", "web"]),
              ("https://sqlite.org/lang.html", "SQLite SQL", ["databases"]),
              ("https://developer.mozilla.org/", "MDN", ["web", "docs"]),
              ("https://news.ycombinator.com/", "Hacker News", ["news"])],
    "bob": [("https://go.dev/doc/", "Go docs", ["go", "docs"]),
            ("https://www.postgresql.org/docs/", "Postgres docs", ["databases", "docs"])],
}

START = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)  # fixed dates keep both worlds identical
when = iter(START + timedelta(hours=i) for i in range(100))

for name, char in (("alice", "a"), ("bob", "b")):
    user = User.objects.create_user(name, f"{name}@example.com", "doppel-pass")
    ApiToken.objects.create(user=user, key=char * 40, name="doppel")
    for url, title, tags in BOOKMARKS[name]:
        stamp = next(when)
        bookmark = Bookmark.objects.create(url=url, title=title, owner=user, date_added=stamp, date_modified=stamp)
        for tag_name in tags:
            tag, _ = Tag.objects.get_or_create(name=tag_name, owner=user, defaults={"date_added": stamp})
            bookmark.tags.add(tag)
print("seeded alice and bob")
