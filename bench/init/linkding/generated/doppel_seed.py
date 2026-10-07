import os
import sys
import datetime

sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bookmarks.settings.dev')
import django
django.setup()

from django.contrib.auth import get_user_model
from bookmarks.models import Bookmark, Tag, UserProfile, ApiToken

User = get_user_model()
users = []
for username in ['doppel', 'alice', 'bob']:
    user, _ = User.objects.get_or_create(username=username, defaults={'email': f'{username}@example.com'})
    user.set_password('doppel-pass')
    user.save()
    users.append(user)

# Create API tokens with unique fixed 40-char keys
for i, user in enumerate(users):
    token = ApiToken(user=user, name=f'seed-token-{i}', key='a' * 39 + chr(ord('a') + i))
    token.save()

# Create tags with fixed date_added
fixed_date = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
tech_tag, _ = Tag.objects.get_or_create(name='tech', owner=users[0], defaults={'date_added': fixed_date})
dev_tag, _ = Tag.objects.get_or_create(name='dev', owner=users[0], defaults={'date_added': fixed_date})
news_tag, _ = Tag.objects.get_or_create(name='news', owner=users[1], defaults={'date_added': fixed_date})

# Create bookmarks
bookmarks_data = [
    {'url': 'https://github.com', 'title': 'GitHub', 'description': 'Code hosting platform', 'tags': [tech_tag, dev_tag], 'owner': users[0]},
    {'url': 'https://www.django-rest-framework.org', 'title': 'DRF', 'description': 'Web APIs for Django', 'tags': [tech_tag, dev_tag], 'owner': users[0]},
    {'url': 'https://docs.djangoproject.com', 'title': 'Django Docs', 'description': 'Official Django documentation', 'tags': [tech_tag], 'owner': users[0]},
    {'url': 'https://news.ycombinator.com', 'title': 'Hacker News', 'description': 'Tech news aggregator', 'tags': [news_tag, tech_tag], 'owner': users[1]},
    {'url': 'https://lobste.rs', 'title': 'Lobsters', 'description': 'Tech news for developers', 'tags': [news_tag, dev_tag], 'owner': users[1]},
    {'url': 'https://python.org', 'title': 'Python.org', 'description': 'Python programming language', 'tags': [tech_tag, dev_tag], 'owner': users[2]},
    {'url': 'https://sqlite.org', 'title': 'SQLite', 'description': 'Embedded database engine', 'tags': [tech_tag], 'owner': users[2]},
    {'url': 'https://redis.io', 'title': 'Redis', 'description': 'In-memory data store', 'tags': [tech_tag, dev_tag], 'owner': users[2]},
    {'url': 'https://nginx.org', 'title': 'NGINX', 'description': 'High-performance web server', 'tags': [tech_tag], 'owner': users[2]},
    {'url': 'https://postgresql.org', 'title': 'PostgreSQL', 'description': 'Advanced open source database', 'tags': [tech_tag, dev_tag], 'owner': users[2]},
]

for bm in bookmarks_data:
    bookmark = Bookmark.objects.create(
        url=bm['url'],
        title=bm['title'],
        description=bm['description'],
        notes='',
        date_added=fixed_date,
        date_modified=fixed_date,
        owner=bm['owner'],
    )
    bookmark.tags.set(bm['tags'])

print(f'seeded {len(users)} users, {len(bookmarks_data)} bookmarks into sqlite:///data/db.sqlite3')
