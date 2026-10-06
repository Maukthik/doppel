import os
import sys
sys.path.insert(0, '.')

os.environ.setdefault('DATABASE_URL', 'sqlite:///instance/db.sqlite')
os.environ.setdefault('SECRET_KEY', 'top-secret!')
os.environ.setdefault('DISABLE_AUTH', '')
os.environ.setdefault('MAIL_SERVER', '')
os.environ.setdefault('MAIL_PORT', '')
os.environ.setdefault('MAIL_USE_TLS', '')
os.environ.setdefault('MAIL_USERNAME', '')
os.environ.setdefault('MAIL_PASSWORD', '')
os.environ.setdefault('MAIL_DEFAULT_SENDER', '')
os.environ.setdefault('PASSWORD_RESET_URL', 'http://localhost:3000/reset')
os.environ.setdefault('ACCESS_TOKEN_MINUTES', '15')
os.environ.setdefault('REFRESH_TOKEN_DAYS', '7')
os.environ.setdefault('REFRESH_TOKEN_IN_COOKIE', 'true')
os.environ.setdefault('REFRESH_TOKEN_IN_BODY', 'false')
os.environ.setdefault('RESET_TOKEN_MINUTES', '15')
os.environ.setdefault('USE_CORS', 'true')
os.environ.setdefault('APIFAIRY_UI', 'elements')
os.environ.setdefault('DOCS_UI', 'elements')
os.environ.setdefault('SQL_ECHO', '')

from api.app import create_app
from api.models import db, User, Post, Token
from api.dates import aware_utcnow
from datetime import datetime, timedelta, timezone

app = create_app()
with app.app_context():
    db.create_all()
    
    users_data = [
        ('alice', 'alice@example.com', 'Hello from Alice!'),
        ('bob', 'bob@example.com', 'Hi from Bob!'),
        ('charlie', 'charlie@example.com', 'Charlie here!'),
    ]
    users = []
    for username, email, about in users_data:
        user = User(username=username, email=email, about_me=about)
        user.password = 'doppel-pass'
        user.first_seen = aware_utcnow()
        user.last_seen = aware_utcnow()
        db.session.add(user)
        users.append(user)
    db.session.commit()
    
    posts_data = [
        (users[0], 'Hello world from Alice!'),
        (users[0], "Alice's second post."),
        (users[1], 'Bob says hello.'),
        (users[1], 'Another post from Bob.'),
        (users[2], "Charlie's first post."),
        (users[2], 'Charlie again.'),
        (users[0], 'Alice again.'),
        (users[1], 'Bob again.'),
        (users[2], 'Charlie again.'),
        (users[0], 'Final post from Alice.'),
    ]
    base_time = aware_utcnow()
    for i, (author, text) in enumerate(posts_data):
        post = Post(author=author, text=text)
        post.timestamp = base_time - timedelta(minutes=len(posts_data) - i)
        db.session.add(post)
    db.session.commit()
    
    for user in users:
        token = Token(user=user)
        token.generate()
        token.access_expiration = aware_utcnow() + timedelta(days=36500)
        token.refresh_expiration = aware_utcnow() + timedelta(days=36500)
        db.session.add(token)
    db.session.commit()
    
    print(f'seeded {len(users)} users, {len(posts_data)} posts into sqlite:///instance/db.sqlite')
