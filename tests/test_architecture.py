"""Architecture guard: the import graph and the size of every Python file. Stdlib only.
Runs on the CI Python (3.11): it needs sys.stdlib_module_names (3.10+). The plugin itself must run on 3.9.

1. No import cycle. A function-level import may close a cycle only inside LAZY_CYCLE_FILES.
2. Nothing imports an entry point (a hooks.json command) except the pairs in KNOWN_ENTRY_IMPORTS.
3. Plugin code (except runtime/rank.py) imports the stdlib and its own folder only. ruff.toml pins Python 3.9 syntax for plugins/.
4. Plugin code never imports repo scripts or tests.
5. A file has at most MAX_LINES lines and a function at most MAX_FN lines.
The three allow-lists (LAZY_CYCLE_FILES, KNOWN_ENTRY_IMPORTS, OVER_LIMIT) must equal what the code does today.
The test fails when a violation is fixed: delete the entry then. The lists only shrink.
"""
import ast
import os
from pathlib import Path
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_LINES, MAX_FN = 600, 100
PLUGIN = "plugins/knowledge/"
SCANNED = ("plugins/", "scripts/", "tests/")
ENTRY = {PLUGIN + "scripts/hook.py", PLUGIN + "scripts/knowledge_select.py"}
KNOWN_ENTRY_IMPORTS = {(PLUGIN + "scripts/knowledge_select.py", PLUGIN + "scripts/hook.py"),
                       (PLUGIN + "runtime/rank.py", PLUGIN + "scripts/knowledge_select.py")}
LAZY_CYCLE_FILES = set()
OVER_LIMIT = {"tests/test_hook.py": 712, "tests/test_knowledge_select.py": 623}
RANKER_PACKAGES = {"numpy", "onnxruntime", "tokenizers"}  # runtime/rank.py runs in the ranker venv

FILES = sorted(str(p.relative_to(ROOT)) for directory in SCANNED
               for p in (Path(ROOT) / directory).rglob("*.py"))
SOURCES = {p: open(os.path.join(ROOT, p), encoding="utf-8").read() for p in FILES}
TREES = {p: ast.parse(s) for p, s in SOURCES.items()}


def names_of(path):
    """Name under which other files import this file: the stem."""
    return {os.path.basename(path)[:-3]}


NAMES = {n: p for p in reversed(FILES) for n in names_of(p)}


def imports(path):
    """Yield (name, lazy, maybe) per import. maybe=True marks `pkg.name` of `from pkg import name`: a module or not."""
    def walk(node, lazy):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If) and "TYPE_CHECKING" in ast.dump(child.test):
                continue  # a type-only import is no runtime edge
            if isinstance(child, ast.Import):
                yield from ((a.name, lazy, False) for a in child.names)
            elif isinstance(child, ast.ImportFrom):
                base = child.module or ""
                yield base, lazy, False
                yield from ((base + "." + a.name, lazy, True) for a in child.names)
            else:
                yield from walk(child, lazy or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return walk(TREES[path], False)


def edges(with_lazy):
    graph = {p: set() for p in FILES}
    for p in FILES:
        for name, lazy, _maybe in imports(p):
            dst = NAMES.get(name)
            if dst and dst != p and (with_lazy or not lazy):
                graph[p].add(dst)
    return graph


def in_cycle(graph):
    def reach(start):
        seen, todo = set(), list(graph[start])
        while todo:
            node = todo.pop()
            if node not in seen:
                seen.add(node)
                todo.extend(graph[node])
        return seen
    return {p for p in graph if p in reach(p) and not p.startswith("tests/")}


def test_no_top_level_import_cycle():
    assert in_cycle(edges(False)) == set()


def test_function_level_imports_close_no_new_cycle():
    assert in_cycle(edges(True)) == LAZY_CYCLE_FILES


def test_entry_points_are_not_imported():
    found = {(s, d) for s, ds in edges(True).items() for d in ds if d in ENTRY and not s.startswith("tests/")}
    assert found == KNOWN_ENTRY_IMPORTS


def test_plugin_is_self_contained_and_stdlib():
    for p in (p for p in FILES if p.startswith(PLUGIN)):
        for name, _lazy, maybe in imports(p):
            top = name.split(".")[0]
            ok = maybe or top in sys.stdlib_module_names or NAMES.get(name, "").startswith(PLUGIN)
            assert ok or (p.endswith("runtime/rank.py") and top in RANKER_PACKAGES), "%s imports %s" % (p, name)


def test_plugin_imports_no_repo_code():
    for p, dsts in edges(True).items():
        for d in dsts:
            assert not (p.startswith(PLUGIN) and not d.startswith(PLUGIN)), "%s -> %s" % (p, d)


def test_size_limits():
    lines = {p: len(s.splitlines()) for p, s in SOURCES.items()}
    long_files = {p: n for p, n in lines.items() if n > MAX_LINES}
    assert all(n <= OVER_LIMIT.get(p, MAX_LINES) for p, n in long_files.items()), long_files
    assert set(OVER_LIMIT) <= set(long_files), "delete from OVER_LIMIT: %s" % (set(OVER_LIMIT) - set(long_files))
    for p, tree in TREES.items():
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert fn.end_lineno - fn.lineno + 1 <= MAX_FN, "%s:%d %s" % (p, fn.lineno, fn.name)
