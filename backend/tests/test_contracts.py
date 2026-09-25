"""Static contract checks across the Modal image and the frontend/backend seam.

The frontend (Next.js on Vercel) and the backend (Modal) deploy independently
and are usually changed in separate PRs, so the places they meet drift
silently: a stage renamed on one side, a callback field the other side never
reads, a new module that works locally but is missing from the Modal image.
Every check here parses source text (``ast`` for Python, regexes for the few
TypeScript literals involved), so none of it needs ``modal`` installed.
"""

import ast
import os
import re

import pytest

import workflow

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(BACKEND_DIR)
FRONTEND_SRC = os.path.join(REPO_DIR, "frontend", "src")

WORKER_PY = os.path.join(BACKEND_DIR, "worker.py")
STAGES_TS = os.path.join(FRONTEND_SRC, "app", "workflow-stages.ts")
ERRORS_TS = os.path.join(FRONTEND_SRC, "lib", "errors.ts")
ACTIONS_TS = os.path.join(FRONTEND_SRC, "app", "actions.ts")
CALLBACK_ROUTE_TS = os.path.join(
    FRONTEND_SRC, "app", "api", "jobs", "[id]", "complete", "route.ts"
)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _parse(path):
    return ast.parse(_read(path), filename=path)


def _worker_tree():
    return _parse(WORKER_PY)


def _requires_frontend():
    if not os.path.isdir(FRONTEND_SRC):
        pytest.skip("frontend/ not present in this checkout")


# ---------------------------------------------------------------------------
# Modal image contents
# ---------------------------------------------------------------------------

def _local_modules():
    return {
        name[:-3] for name in os.listdir(BACKEND_DIR)
        if name.endswith(".py") and not name.startswith("test_")
    }


def _imported_local_modules(module, local):
    """Local modules ``module`` imports anywhere (including inside functions)."""
    found = set()
    for node in ast.walk(_parse(os.path.join(BACKEND_DIR, module + ".py"))):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module.split(".")[0]]
        else:
            continue
        found.update(n for n in names if n in local)
    return found


def _image_python_sources():
    listed = set()
    for node in ast.walk(_worker_tree()):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_local_python_source"):
            listed.update(
                arg.value for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            )
    return listed


def test_modal_image_ships_every_module_the_worker_imports():
    """A module imported (transitively) by worker.py but absent from
    ``add_local_python_source`` imports fine locally and in CI, then fails with
    ModuleNotFoundError inside the Modal container after deploy."""
    local = _local_modules()
    needed, frontier = set(), ["worker"]
    while frontier:
        for mod in _imported_local_modules(frontier.pop(), local):
            if mod not in needed:
                needed.add(mod)
                frontier.append(mod)
    needed.discard("worker")

    missing = needed - _image_python_sources()
    assert not missing, (
        f"worker.py imports {sorted(missing)} but the Modal image does not ship "
        "them - add them to add_local_python_source(...) in worker.py"
    )


def test_modal_image_sources_exist():
    stale = _image_python_sources() - _local_modules()
    assert not stale, f"add_local_python_source lists missing modules: {sorted(stale)}"


def test_modal_image_installs_fastapi_for_web_endpoints():
    """Modal rejects a @modal.fastapi_endpoint (or asgi_app) at deploy time
    unless the image installs FastAPI itself; it no longer adds it implicitly.
    That failure only shows up in the deploy job on main."""
    tree = _worker_tree()
    web = [
        fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
        for dec in fn.decorator_list
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
        and dec.func.attr in ("fastapi_endpoint", "asgi_app", "web_endpoint")
    ]
    installed = {
        arg.value.split("[")[0].split("=")[0].split("<")[0].split(">")[0].strip().lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "pip_install"
        for arg in node.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    }
    if web:
        assert "fastapi" in installed, (
            f"{web} are FastAPI web endpoints but the Modal image doesn't "
            "pip_install fastapi - add \"fastapi[standard]\" in worker.py"
        )


# ---------------------------------------------------------------------------
# Workflow stages
# ---------------------------------------------------------------------------

def test_frontend_stage_list_matches_backend():
    _requires_frontend()
    m = re.search(r"WORKFLOW_STAGES\s*=\s*\[(.*?)\]", _read(STAGES_TS), re.S)
    assert m, "WORKFLOW_STAGES not found in workflow-stages.ts"
    frontend = re.findall(r"'([a-z_]+)'", m.group(1))
    assert frontend == workflow.STAGES


def test_every_stage_has_a_label():
    assert set(workflow.STAGE_LABELS) == set(workflow.STAGES)


# ---------------------------------------------------------------------------
# Error prefixes
# ---------------------------------------------------------------------------

def _classify_error_prefixes():
    for node in ast.walk(_worker_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == "_classify_error":
            return {
                call.args[0].value
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "startswith"
                and isinstance(call.args[0], ast.Constant)
            }
    raise AssertionError("_classify_error not found in worker.py")


def test_every_backend_error_prefix_has_frontend_copy():
    """An unmapped prefix falls through to the generic 'Processing failed'."""
    _requires_frontend()
    errors_ts = _read(ERRORS_TS)
    unhandled = {
        p for p in _classify_error_prefixes()
        if not re.search(rf"\b{p}\b\s*:", errors_ts)  # a KNOWN entry
        and f"'{p}'" not in errors_ts                # an explicit special case
    }
    assert not unhandled, f"frontend/src/lib/errors.ts has no copy for {sorted(unhandled)}"


# ---------------------------------------------------------------------------
# Worker -> frontend progress callbacks
# ---------------------------------------------------------------------------

def _dict_keys(node, scope):
    """String keys of a dict-valued expression, following the few shapes the
    worker uses: literals, ``**spread``, ``a if c else b``, a dict
    comprehension over ``{...}.items()``, and a local variable."""
    if isinstance(node, ast.Dict):
        keys = set()
        for k, v in zip(node.keys, node.values):
            if k is None:
                keys |= _dict_keys(v, scope)
            elif isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.add(k.value)
        return keys
    if isinstance(node, ast.IfExp):
        return _dict_keys(node.body, scope) | _dict_keys(node.orelse, scope)
    if isinstance(node, ast.DictComp):
        it = node.generators[0].iter
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute):
            return _dict_keys(it.func.value, scope)
    if isinstance(node, ast.Name):
        for stmt in ast.walk(scope):
            if (isinstance(stmt, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == node.id for t in stmt.targets)):
                return _dict_keys(stmt.value, scope)
    return set()


def _worker_callback_fields():
    fields = set()
    for fn in ast.walk(_worker_tree()):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for call in ast.walk(fn):
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "_post_callback"
                    and call.args):
                fields |= _dict_keys(call.args[0], fn)
    return fields


def test_callback_fields_match_between_worker_and_frontend_route():
    """Both directions: a field the worker sends but the route never reads is
    silently dropped; a field the route reads but no worker sends is dead or a
    typo."""
    _requires_frontend()
    sent = _worker_callback_fields() - {"jobId"}  # the route takes the id from its URL
    read = set(re.findall(r"\bdata\.([A-Za-z]+)", _read(CALLBACK_ROUTE_TS)))
    assert sent, "found no _post_callback payloads in worker.py"
    assert sent - read == set(), f"worker sends fields the callback route ignores: {sorted(sent - read)}"
    assert read - sent == set(), f"callback route reads fields no worker sends: {sorted(read - sent)}"


# ---------------------------------------------------------------------------
# Frontend -> worker trigger
# ---------------------------------------------------------------------------

def _web_trigger_fields():
    for node in ast.walk(_worker_tree()):
        if isinstance(node, ast.FunctionDef) and node.name == "web_trigger":
            return {
                call.args[0].value
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "get"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "data"
            }
    raise AssertionError("web_trigger not found in worker.py")


def test_trigger_payload_matches_web_trigger():
    _requires_frontend()
    src = _read(ACTIONS_TS)
    m = re.search(r"function triggerWorker\(.*?\n}\n", src, re.S)
    assert m, "triggerWorker not found in actions.ts"
    fn = m.group(0)
    literal = re.search(r"const body[^=]*=\s*\{(.*?)\n  \}", fn, re.S)
    assert literal, "triggerWorker's body literal not found"
    sent = set(re.findall(r"^\s+(\w+):", literal.group(1), re.M))
    sent |= set(re.findall(r"\bbody\.(\w+)\s*=", fn))
    assert sent == _web_trigger_fields()
