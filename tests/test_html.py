"""HTML pages compare by what a person sees and can do, not by their bytes."""
from doppel.diff import compare, line_changes
from doppel.runner import is_html, page_view

PAGE = """<!doctype html><html><head><title>Sign in</title>
<meta name="csrf-token" content="{token}"><script nonce="{token}">var t = "{token}";</script>
<style>p {{ color: red }}</style></head><body>
<nav><a href="/">Home</a> <a href="/help/">Help</a></nav>
<h1>Sign in</h1><p>{notice}</p>
<form method="POST" action="/login/"><input type="hidden" name="csrfmiddlewaretoken" value="{token}">
<input name="username" value=""><input type="checkbox" name="remember" value="1" checked>
<button>Go</button></form>{extra}</body></html>"""


def page(token="abc", notice="Orders ship in 2 days.", extra=""):
    return page_view(PAGE.format(token=token, notice=notice, extra=extra))


def rec(body):
    return [{"persona": "p", "steps": [{"i": 0, "request": "GET /login/", "status": 200, "body": body, "ms": 1}]}]


def test_a_page_becomes_title_text_forms_and_links():
    view = page()["html"]
    assert view["title"] == "Sign in"
    assert view["text"] == ["Home Help", "Sign in", "Orders ship in 2 days.", "Go"]
    assert view["forms"] == [{"action": "/login/", "method": "post",
                              "fields": {"csrfmiddlewaretoken": "(token)", "username": "", "remember": "1 checked"}}]
    assert view["links"] == ["/", "/help/"]


def test_tokens_and_nonces_never_show_up_as_differences():
    assert compare(rec(page(token="first")), rec(page(token="second"))) == []


def test_a_changed_line_is_named_with_its_old_and_new_text():
    [f] = compare(rec(page()), rec(page(notice="Orders ship in 5 days.")))
    assert f.detail == "changed: html.text.*"   # list positions summarize as *; the values name the line
    assert (f.base, f.head) == ({"html.text.2": "Orders ship in 2 days."}, {"html.text.2": "Orders ship in 5 days."})


def test_an_added_line_shows_only_that_line():
    [f] = compare(rec(page()), rec(page(extra="<p>Sale ends today</p>")))
    assert f.detail == "changed: html.text (-0 +1 lines)"
    assert f.head == {"html.text": ["Sale ends today"]} and f.base == {"html.text": []}


def test_ignore_works_on_form_fields_too():
    a, b = page(), page()
    b["html"]["forms"][0]["fields"]["username"] = "prefilled"
    assert compare(rec(a), rec(b), ignore={"username"}) == []


def test_line_changes_aligns_like_a_text_diff():
    assert line_changes(["a", "b", "c"], ["a", "x", "c", "d"]) == (["b"], ["x", "d"])


def test_html_is_recognized_by_content_type_or_doctype():
    assert is_html("text/html; charset=utf-8", "hello")
    assert is_html("", "  <!DOCTYPE html><html>")
    assert not is_html("text/plain", "OK")


def test_broken_markup_still_gives_a_view():
    assert page_view("<html><body><p>unclosed <div>still here")["html"]["text"] == ["unclosed", "still here"]
