"""Hand-written seed for the harness self-check: two users, snippets with and without titles/line numbers."""
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tutorial.settings")
django.setup()

from django.contrib.auth.models import User  # noqa: E402
from snippets.models import Snippet  # noqa: E402

alice = User.objects.create_user("alice", "alice@example.com", "doppel-pass")
bob = User.objects.create_user("bob", "bob@example.com", "doppel-pass")
Snippet.objects.create(owner=alice, title="hello", code="print('hello')\nprint('world')", linenos=False)
Snippet.objects.create(owner=alice, title="", code="x = 1\ny = 2", linenos=True)
Snippet.objects.create(owner=bob, title="bob's", code="def f():\n    return 1", linenos=True)
