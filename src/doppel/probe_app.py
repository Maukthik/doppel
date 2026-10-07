"""Doppel's introspection probe: runs INSIDE the app's environment (after its dependencies are installed),
imports the app the way its framework would, and prints what is actually true as JSON on its last line:
the routes, where the database lives, how login works, the models and their required fields, the app's
own commands. `doppel init` hands these facts to the model so it stops guessing them from source.

Standard library only (plus whatever framework the app itself uses). Every section is best-effort: a
part that fails is reported under "errors" with its traceback, which is itself a fact (a missing env
var, a module that does not import).

    python probe_app.py            # from the app's root folder
"""
from __future__ import annotations

import inspect
import json
import os
import re
import sys
import traceback
from pathlib import Path

ROOT = Path.cwd()
SKIP = {"tests", "test", "venv", ".venv", "node_modules", "site-packages", "migrations", ".git", "docs", "frontend"}
MAX_ROUTES, MAX_MODELS, MAX_FIELDS = 250, 60, 40
LOGIN_DECORATOR = re.compile(r"login_required|auth_required|token_auth|jwt_required|permission_required|"
                             r"requires_auth|authenticated|IsAuthenticated|Depends\(\s*get_current", re.I)
LOGIN_CHECK = re.compile(r"check_password|verify_password|authenticate\(|login_user\(|auth_login\(|"
                         r"session\[\s*['\"]user|\blogin\(request|create_access_token|issue_token|OAuth2PasswordRequestForm")
errors: list[dict] = []
CONTEXT: list = []
NEEDS_TOKEN = re.compile(r"current_user|get_current|Bearer|APIKey|verify_token|require_|authenticated|auth_required",
                         re.I)  # an app context to enter for the model scan (Flask-SQLAlchemy needs one)


def note_error(where: str, exc: BaseException) -> None:
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    errors.append({"where": where, "error": f"{type(exc).__name__}: {exc}"[:500], "traceback": "".join(tb)[-2500:]})


def rel(path: str | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return ""  # a library file, not the app's


def where(fn) -> str:
    fn = inspect.unwrap(fn)
    try:
        f = rel(inspect.getsourcefile(fn))
        return f"{f}:{inspect.getsourcelines(fn)[1]}" if f else ""
    except (TypeError, OSError):
        return ""


def source_of(fn) -> str:
    try:
        return inspect.getsource(inspect.unwrap(fn))
    except (TypeError, OSError):
        return ""


def reads(src: str) -> list[str]:
    """What a view reads from the request, by its source."""
    found = []
    if re.search(r"request\.form\b|request\.POST\b|Form\(|validate_on_submit|OAuth2PasswordRequestForm", src):
        found.append("form")
    if re.search(r"get_json\(|request\.json\b|request\.data\b|Body\(|BaseModel|Serializer\(|\.body\b", src):
        found.append("json")
    if re.search(r"request\.args\b|request\.GET\b|Query\(", src):
        found.append("query")
    if re.search(r"request\.files\b|request\.FILES\b|UploadFile", src):
        found.append("files")
    return found


LOGIN_MIXINS = {"LoginRequiredMixin", "PermissionRequiredMixin", "UserPassesTestMixin"}


def decorated_methods(head: str, methods):
    """require_POST / require_GET / require_http_methods([...]) narrow what a Django view accepts."""
    if "require_POST" in head:
        return ["POST"]
    if "require_GET" in head or "require_safe" in head:
        return ["GET"]
    m = re.search(r"require_http_methods\(\s*\[([^\]]*)\]", head)
    return re.findall(r"[A-Z]+", m.group(1)) if m else methods


def route(methods, path, fn, extra=None) -> dict:
    src = source_of(fn)
    head = src.split("def ", 1)[0] if "def " in src and not inspect.isclass(fn) else ""
    own = bool(rel(getattr(inspect.getmodule(fn), "__file__", None)))  # the app's code, not a library's
    needs = bool(LOGIN_DECORATOR.search(head))
    if inspect.isclass(fn):
        perms = str(getattr(fn, "permission_classes", ""))
        needs = "writes" if "OrReadOnly" in perms else \
            any(b.__name__ in LOGIN_MIXINS for b in fn.__mro__) or "IsAuthenticated" in perms or "IsAdmin" in perms
    methods = decorated_methods(head, methods)
    out = {"methods": sorted(m for m in methods if m not in ("HEAD", "OPTIONS")), "path": path, "view": where(fn),
           "reads": reads(src) if own or inspect.isclass(fn) else [], "needs_login": needs}
    if LOGIN_CHECK.search(src):  # checks a password or starts a session: register views only hash one
        out["looks_like_login"] = True
    return {**out, **(extra or {})}


def app_modules() -> list:
    """Modules imported from the app's own folder."""
    out = []
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", None)
        if f and rel(f) and not SKIP & set(Path(rel(f)).parts):
            out.append(m)
    return out


# ---------------------------------------------------------------- generic: SQLAlchemy, SQLite files

def sqlalchemy_facts() -> dict:
    if "sqlalchemy" not in sys.modules:
        return {}
    out: dict = {"models": [], "engines": []}
    try:
        from sqlalchemy.engine import Engine
    except Exception:  # noqa: BLE001
        Engine = ()  # noqa: N806
    seen = set()
    for m in app_modules():
        for obj in list(vars(m).values()):
            try:  # proxies (flask.request, current_user) raise on any attribute access outside a request
                if Engine and type(obj) is not type and isinstance(obj, Engine):
                    out["engines"].append(obj.url.render_as_string(hide_password=True))
                    continue
                if not inspect.isclass(obj):
                    continue
                table = getattr(obj, "__table__", None)
            except Exception:  # noqa: BLE001
                continue
            if table is not None and hasattr(table, "columns") and obj not in seen:
                seen.add(obj)
                cols = []
                for c in list(table.columns)[:MAX_FIELDS]:
                    required = not c.nullable and c.default is None and c.server_default is None \
                        and not (c.primary_key and str(c.type).upper().startswith("INT"))
                    cols.append({"name": c.name, "type": str(c.type), "required": required,
                                 **({"unique": True} if c.unique else {}),
                                 **({"fk": [str(fk.column) for fk in c.foreign_keys]} if c.foreign_keys else {})})
                out["models"].append({"class": f"{obj.__module__}.{obj.__qualname__}", "table": table.name,
                                      "fields": cols})
    for db in ("flask_sqlalchemy",):
        if db in sys.modules:
            out["extension"] = db
    out["models"] = out["models"][:MAX_MODELS]
    out["engines"] = sorted(set(out["engines"]))
    return out


def sqlite_files() -> list[str]:
    return sorted(p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*")
                  if p.suffix in (".sqlite", ".sqlite3", ".db") and not SKIP & set(p.relative_to(ROOT).parts))[:20]


def schema_files() -> list[str]:
    return sorted(p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.sql")
                  if not SKIP & set(p.relative_to(ROOT).parts))[:20]


# ---------------------------------------------------------------- Django

def manage_py() -> Path | None:
    for p in [ROOT / "manage.py", *sorted(ROOT.glob("*/manage.py"))]:
        if p.exists():
            return p
    return None


def django_facts(manage: Path) -> dict:
    text = manage.read_text("utf-8", "replace")
    m = re.search(r"""DJANGO_SETTINGS_MODULE['"]\s*,\s*['"]([\w.]+)""", text)
    sys.path.insert(0, str(manage.parent))
    if m:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", m.group(1))
    import django
    django.setup()
    from django.apps import apps
    from django.conf import settings
    out: dict = {"framework": "django", "version": django.get_version(), "manage": rel(str(manage)),
                 "settings_module": os.environ.get("DJANGO_SETTINGS_MODULE"),
                 "run": f"python {rel(str(manage))} runserver 0.0.0.0:$PORT --noreload",
                 "commands": [f"python {rel(str(manage))} migrate --noinput"]}
    out["database"] = {alias: {"engine": c.get("ENGINE", ""), "name": str(c.get("NAME", ""))}
                       for alias, c in settings.DATABASES.items()}
    user = apps.get_model(settings.AUTH_USER_MODEL)
    out["auth"] = {"user_model": settings.AUTH_USER_MODEL, "username_field": user.USERNAME_FIELD,
                   "required_fields": list(user.REQUIRED_FIELDS),
                   "login_url": str(getattr(settings, "LOGIN_URL", "")),
                   "backends": list(getattr(settings, "AUTHENTICATION_BACKENDS", [])),
                   "csrf": "django.middleware.csrf.CsrfViewMiddleware" in settings.MIDDLEWARE,
                   "create_user": f"{settings.AUTH_USER_MODEL.split('.')[-1]}.objects.create_user("
                                  f"{user.USERNAME_FIELD}=..., password=...)"}
    rest = getattr(settings, "REST_FRAMEWORK", {}) or {}
    if rest:
        out["auth"]["rest_framework"] = {k: v for k, v in rest.items() if "AUTH" in k or "PERMISSION" in k}
    out["routes"] = django_routes()
    models = []
    for model in apps.get_models():
        if model._meta.app_label in ("contenttypes", "sessions", "admin"):
            continue
        fields = []
        for f in model._meta.get_fields()[:MAX_FIELDS]:
            if not getattr(f, "concrete", False) or getattr(f, "auto_created", False):
                continue
            required = not f.null and not f.blank and not f.has_default() and not getattr(f, "primary_key", False)
            fields.append({"name": f.name, "type": f.get_internal_type(), "required": required,
                           **({"unique": True} if getattr(f, "unique", False) and not f.primary_key else {}),
                           **({"fk": f.related_model._meta.label} if f.is_relation and f.related_model else {})})
        models.append({"class": model._meta.label, "table": model._meta.db_table, "fields": fields})
    out["models"] = models[:MAX_MODELS]
    return out


def django_routes() -> list[dict]:
    from django.urls import URLPattern, URLResolver, get_resolver
    out: list[dict] = []

    def clean(p: str) -> str:
        p = re.sub(r"\^|\$|\\Z", "", p)
        p = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"{\1}", p)       # (?P<pk>[^/.]+) -> {pk}
        return "/" + p.replace("\\.", ".").lstrip("/")

    def walk(patterns, prefix=""):
        for p in patterns:
            if len(out) >= MAX_ROUTES:
                return
            if isinstance(p, URLResolver):
                walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern):
                if re.search(r"\(\?P<format>|format_suffix", str(p.pattern)):
                    continue  # DRF's .json/.api twins of every route
                cb = p.callback
                cls = getattr(cb, "view_class", None) or getattr(cb, "cls", None)
                actions = getattr(cb, "actions", None)
                if actions:
                    methods = [m.upper() for m in actions]
                elif cls is not None:
                    methods = [m.upper() for m in cls.http_method_names if m != "options" and hasattr(cls, m)]
                else:
                    methods = ["ANY"]
                target = cls if cls is not None else cb
                r = route(methods, clean(prefix + str(p.pattern)), target,
                          {"name": p.name} if p.name else None)
                if cls is not None:
                    r["view_class"] = f"{cls.__module__}.{cls.__qualname__}"
                    if any("LoginView" in b.__name__ or "AuthenticationForm" in str(getattr(cls, "form_class", ""))
                           for b in cls.__mro__):
                        r["looks_like_login"] = True
                        r["reads"] = sorted(set(r["reads"]) | {"form"})
                out.append(r)
    walk(get_resolver().url_patterns)
    return out


# ---------------------------------------------------------------- Flask

def python_files(limit: int = 400) -> list[Path]:
    out = []
    for p in sorted(ROOT.rglob("*.py")):
        parts = set(p.relative_to(ROOT).parts)
        if not SKIP & parts and p.name not in ("setup.py", "conftest.py", "probe_app.py"):
            out.append(p)
        if len(out) >= limit:
            break
    return out


def module_name(p: Path) -> str:
    parts = list(p.relative_to(ROOT).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


FLASK_APP_LINE = re.compile(r"^\s*(?:export\s+)?FLASK_APP\s*=\s*['\"]?([^'\"\s#]+)", re.M)


def flask_candidates() -> list[str | None]:
    cands: list[str | None] = []
    if os.environ.get("FLASK_APP"):
        cands.append(os.environ["FLASK_APP"])
    for env_file in (".flaskenv", ".env"):  # where `flask run` itself looks (python-dotenv)
        f = ROOT / env_file
        if f.exists():
            m = FLASK_APP_LINE.search(f.read_text("utf-8", "replace"))
            if m and m.group(1) not in cands:
                cands.append(m.group(1))
    for p in python_files():
        if len(p.relative_to(ROOT).parts) > 3:
            continue
        text = p.read_text("utf-8", "replace")
        if re.search(r"\bFlask\(|create_app\(", text):
            name = module_name(p)
            if name and name not in cands:
                cands.append(name)
    cands.append(None)  # Flask's own discovery: wsgi.py, app.py
    return cands


def flask_facts() -> dict:
    from flask.cli import ScriptInfo
    sys.path[:0] = [str(ROOT), str(ROOT / "src")]
    app, used = None, None
    for cand in flask_candidates():
        try:
            app = ScriptInfo(app_import_path=cand).load_app()
            used = cand
            break
        except Exception as e:  # noqa: BLE001
            note_error(f"flask: load {cand or '(default discovery)'}", e)
    if app is None:
        return {"framework": "flask", "loaded": False}
    from importlib.metadata import version
    errors[:] = [e for e in errors if not e["where"].startswith("flask: load")]  # a later candidate worked
    target = used or "app"
    out: dict = {"framework": "flask", "version": version("flask"), "app": target,
                 "run": f"flask --app {target} run --host 0.0.0.0 --port $PORT",
                 "instance_path": rel(app.instance_path) or app.instance_path, "root_path": rel(app.root_path)}
    cfg = {k: str(v) for k, v in app.config.items()
           if re.search(r"DATABASE|SQLALCHEMY_DATABASE_URI|SQLITE|_DB$|WTF_CSRF_ENABLED|SESSION_COOKIE_SECURE", k)}
    out["config"] = cfg
    db_path = app.config.get("DATABASE") or app.config.get("SQLALCHEMY_DATABASE_URI")
    out["database"] = {"configured": str(db_path) if db_path else "", "instance_path": app.instance_path,
                       "note": "Flask's instance folder sits next to the package unless the app sets instance_path"}
    out["routes"] = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or len(out["routes"]) >= MAX_ROUTES:
            continue
        fn = app.view_functions.get(rule.endpoint)
        if fn is not None:
            out["routes"].append(route(rule.methods or [], rule.rule, fn, {"endpoint": rule.endpoint}))
    cmds = sorted(app.cli.commands)
    out["commands"] = [f"flask --app {target} {c}" for c in cmds]
    CONTEXT.append(app.app_context)
    if "migrate" in app.extensions:
        out["commands"].append(f"flask --app {target} db upgrade")
    auth: dict = {}
    lm = getattr(app, "login_manager", None)
    if lm is not None:
        auth["flask_login"] = {"login_view": str(lm.login_view or "")}
    if "csrf" in app.extensions or "wtf_csrf" in str(app.extensions):
        auth["csrf"] = True
    out["auth"] = auth
    return out


# ---------------------------------------------------------------- FastAPI / Starlette

def fastapi_facts() -> dict:
    sys.path[:0] = [str(ROOT), str(ROOT / "src")]
    import importlib

    from fastapi import FastAPI
    app, used = None, None
    for p in python_files():
        text = p.read_text("utf-8", "replace")
        if "FastAPI(" not in text:
            continue
        name = module_name(p)
        try:
            mod = importlib.import_module(name)
        except Exception as e:  # noqa: BLE001
            note_error(f"fastapi: import {name}", e)
            continue
        for attr, obj in vars(mod).items():
            if isinstance(obj, FastAPI):
                app, used = obj, f"{name}:{attr}"
                break
        if app is None:
            for fn in ("create_app", "get_application", "get_app"):
                if callable(getattr(mod, fn, None)):
                    try:
                        app, used = getattr(mod, fn)(), f"{name}:{fn}"
                        break
                    except Exception as e:  # noqa: BLE001
                        note_error(f"fastapi: {name}.{fn}()", e)
        if app is not None:
            break
    if app is None:
        return {"framework": "fastapi", "loaded": False}
    import fastapi
    errors[:] = [e for e in errors if not e["where"].startswith("fastapi: import")]
    factory = used.split(":")[1] in ("create_app", "get_application", "get_app")
    out: dict = {"framework": "fastapi", "version": fastapi.__version__, "app": used,
                 "run": f"uvicorn {used}{' --factory' if factory else ''} --host 0.0.0.0 --port $PORT"}
    from fastapi.routing import APIRoute
    out["routes"] = []
    for r in app.routes:
        if isinstance(r, APIRoute) and len(out["routes"]) < MAX_ROUTES:
            deps = [str(getattr(d.call, "__name__", d.call)) for d in r.dependant.dependencies if d.call]
            info = route(r.methods, r.path, r.endpoint, {"dependencies": deps[:6]} if deps else None)
            if any(NEEDS_TOKEN.search(d) and "RequestForm" not in d for d in deps):
                info["needs_login"] = True  # a dependency that checks a token; a login FORM is how you get one
            out["routes"].append(info)
    try:
        schemes = (app.openapi().get("components") or {}).get("securitySchemes") or {}
        out["auth"] = {"security_schemes": schemes}
    except Exception as e:  # noqa: BLE001
        note_error("fastapi: openapi()", e)
    return out


# ---------------------------------------------------------------- main

def main() -> int:
    facts: dict = {"python": sys.version.split()[0]}
    try:
        manage = manage_py()
        has = {m: _importable(m) for m in ("django", "flask", "fastapi")}
        if manage and has["django"]:
            facts.update(django_facts(manage))
        elif has["fastapi"] and any("FastAPI(" in p.read_text("utf-8", "replace") for p in python_files()):
            facts.update(fastapi_facts())
        elif has["flask"]:
            facts.update(flask_facts())
        else:
            facts["framework"] = "unknown"
            facts["installed"] = {k: v for k, v in has.items() if v}
    except Exception as e:  # noqa: BLE001 - the failure to import IS the most useful fact
        note_error(f"import the {facts.get('framework') or 'app'}", e)
    try:
        if CONTEXT:
            with CONTEXT[0]():
                sa = sqlalchemy_facts()
        else:
            sa = sqlalchemy_facts()
        if sa.get("models") and not facts.get("models"):
            facts["models"] = sa["models"]
        if sa.get("engines"):
            facts.setdefault("database", {})["engines"] = sa["engines"]
    except Exception as e:  # noqa: BLE001
        note_error("sqlalchemy models", e)
    facts["requires_python"] = requires_python()
    if any("SyntaxError" in e["error"] for e in errors):
        facts["python_mismatch"] = (f"the app's code does not parse on Python {facts['python']}"
                                    + (f" (it declares requires-python {facts['requires_python']})"
                                       if facts["requires_python"] else "")
                                    + ": use an image with a newer Python")
    facts["sqlite_files"] = sqlite_files()
    facts["schema_files"] = schema_files()
    facts["errors"] = errors
    text = json.dumps({k: v for k, v in facts.items() if k != "cwd"}, default=str)
    for root in sorted({str(ROOT), str(ROOT.resolve())}, key=len, reverse=True):  # same in the sandbox and locally
        text = text.replace(root + "/", "<repo>/").replace(root, "<repo>")
    print(text)
    return 0


def requires_python() -> str:
    for f in (ROOT / "pyproject.toml", ROOT.parent / "pyproject.toml"):
        if f.exists():
            m = re.search(r"""requires-python\s*=\s*['"]([^'"]+)""", f.read_text("utf-8", "replace"))
            if m:
                return m.group(1)
    return ""


def _importable(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


if __name__ == "__main__":
    raise SystemExit(main())
