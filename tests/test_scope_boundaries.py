"""The production V1 boundary that no other test enforces: the core stays offline (README section 3).

ADR 002 keeps the case pipeline offline in every deployment, synthetic V1 and any commercial
pilot alike: the service layer may add a database, object storage, and an identity provider
*around* the workflow, never inside it. Nothing failed when that rule was broken, because the
core simply imported what it liked. These tests read the import graph statically, so a module
that reaches for the network, or for the service layer, fails CI before anyone runs it.

What counts as an import: `import x`, `from x import y` (relative imports resolved against the
importing module's own package, and `y` followed when it is itself a module), lazy imports in
function bodies, and `importlib.import_module`/`__import__` called with a string literal. An
import built from a runtime string is invisible to any static check.

"Core" is everything reachable from `workflow` and `case_assembly`, which includes `cli` because
`case_assembly.main()` imports its report printer lazily. That is deliberate: a service import
in `cli` would run whenever `rxauth-run-case` does.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.util import resolve_name
from pathlib import Path

import rxauth_ai

_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = Path(rxauth_ai.__file__).resolve().parent

# The workflow and the stages it calls; everything they reach is "core".
_ENTRY_POINTS = ("rxauth_ai.workflow", "rxauth_ai.case_assembly")

# Service-layer modules may import the core. The reverse would put a database, object store,
# or identity provider inside a case run.
_SERVICE_MODULES = {
    "api",
    "auth",
    "storage",
    "uploads",
    "jobs",
    "worker",
    "case_jobs",
    "persistence",
}

# Standard-library modules that open sockets or speak a network protocol. Dotted entries name
# the networking part of an otherwise harmless package (`http.HTTPStatus` is fine; its client
# is not).
_NETWORK_STDLIB = {
    "asyncio",
    "ftplib",
    "http.client",
    "http.server",
    "imaplib",
    "logging.handlers",
    "multiprocessing.connection",
    "poplib",
    "smtplib",
    "socket",
    "socketserver",
    "ssl",
    "urllib.request",
    "webbrowser",
    "xmlrpc",
}

# Distribution names whose import name differs.
_IMPORT_NAMES = {"opencv_python_headless": "cv2", "pillow": "PIL", "scikit_learn": "sklearn"}


def _source(module: str) -> tuple[Path, bool] | None:
    """The file behind a package module and whether it is a package, or None if it is not one."""
    parts = module.split(".")[1:]
    directory = _PACKAGE.joinpath(*parts)
    if (directory / "__init__.py").exists():
        return directory / "__init__.py", True
    if parts and directory.with_suffix(".py").exists():
        return directory.with_suffix(".py"), False
    return None


def _owning_module(name: str) -> str:
    """The longest prefix of a dotted name that is an actual package module."""
    while _source(name) is None:
        name = name.rpartition(".")[0]
    return name


def _imports_in(source: str, module: str, is_package: bool) -> set[str]:
    """Absolute names imported anywhere in `source`, the text of package module `module`."""
    package = module if is_package else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = resolve_name("." * node.level + (node.module or ""), package)
            found.add(base)
            # `from pkg import name` may bind a submodule; record it so it is walked too.
            found.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", None))
            in {"import_module", "__import__"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            name = node.args[0].value
            if name.startswith("."):
                anchor = node.args[1] if len(node.args) > 1 else None
                name = resolve_name(
                    name, anchor.value if isinstance(anchor, ast.Constant) else package
                )
            found.add(name)
    return found


def _core_closure() -> tuple[set[str], dict[str, set[str]]]:
    """Package modules reachable from the workflow, and which of them import each external name."""
    seen: set[str] = set()
    external: dict[str, set[str]] = {}
    pending = list(_ENTRY_POINTS)
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        path, is_package = _source(module)
        for name in _imports_in(path.read_text(encoding="utf-8"), module, is_package):
            if name == "rxauth_ai" or name.startswith("rxauth_ai."):
                pending.append(_owning_module(name))
            else:
                external.setdefault(name, set()).add(module)
    return seen, external


def _is_network(name: str) -> bool:
    return any(name == banned or name.startswith(f"{banned}.") for banned in _NETWORK_STDLIB)


def _declared_core_imports() -> set[str]:
    """Import names of the core runtime dependencies, plus the optional OCR engine."""
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    names = set()
    for requirement in [*project["dependencies"], *project["optional-dependencies"]["ocr"]]:
        distribution = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement).group()
        normalized = re.sub(r"[-_.]+", "_", distribution.lower())
        names.add(_IMPORT_NAMES.get(normalized, normalized))
    return names


def test_the_walker_sees_every_import_form_that_could_reach_the_service_layer():
    """The guarantee is only as good as the parser; each of these once slipped past it."""
    forms = {
        "from rxauth_ai import storage": "rxauth_ai.storage",
        "from . import storage": "rxauth_ai.storage",
        "from .persistence import repository": "rxauth_ai.persistence.repository",
        "def f():\n    from .api import create_app": "rxauth_ai.api",
        "import_module('.jobs', 'rxauth_ai')": "rxauth_ai.jobs",
        "__import__('socket')": "socket",
    }
    for source, expected in forms.items():
        assert expected in _imports_in(source, "rxauth_ai.matching", False), source
    # Relative imports resolve against the importing module's own package, not the root.
    nested = _imports_in("from .tables import X\nfrom .. import api", "rxauth_ai.persistence", True)
    assert {"rxauth_ai.persistence.tables", "rxauth_ai.api"} <= nested
    assert _owning_module("rxauth_ai.models.Case") == "rxauth_ai.models"


def test_the_network_check_reaches_inside_otherwise_harmless_packages():
    assert _is_network("logging.handlers")
    assert _is_network("urllib.request")
    assert not _is_network("http")
    assert not _is_network("urllib.parse")


def test_requirement_strings_parse_to_import_names():
    assert _declared_core_imports() >= {"numpy", "cv2", "PIL", "pydantic", "pypdf", "sklearn"}


def test_the_closure_covers_every_workflow_stage():
    """Guard against a vacuous pass: the walk must actually reach the spine."""
    modules, _ = _core_closure()
    spine = {
        "models",
        "ingestion",
        "classifier",
        "extraction",
        "policy_retrieval",
        "policy_corpus",
        "criteria_extraction",
        "matching",
        "pipeline",
        "groundedness",
        "generation",
    }
    reached = {module.removeprefix("rxauth_ai.") for module in modules}
    assert spine <= reached, f"not reached: {sorted(spine - reached)}"


def test_the_core_never_imports_the_service_layer():
    modules, _ = _core_closure()
    leaked = {module.split(".")[1] for module in modules if "." in module} & _SERVICE_MODULES
    assert not leaked, f"core reaches service modules: {sorted(leaked)}"


def test_the_core_imports_no_network_module():
    _, external = _core_closure()
    networked = {name: sorted(users) for name, users in external.items() if _is_network(name)}
    assert not networked, f"core imports network modules: {networked}"


def test_the_core_imports_only_the_standard_library_and_declared_core_dependencies():
    """A new third-party import in the core must be declared, and argued for, in pyproject."""
    _, external = _core_closure()
    allowed = sys.stdlib_module_names | _declared_core_imports()
    undeclared = {
        name: sorted(users) for name, users in external.items() if name.split(".")[0] not in allowed
    }
    assert not undeclared, f"core imports undeclared packages: {undeclared}"
