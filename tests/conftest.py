"""Shared helpers for script tests."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SID = "s1"


def session(data):
    return json.loads((data / "sessions" / (SID + ".json")).read_text())


def load_script(name, rel, register=False):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


CONFIG = {"org": "Acme", "scope": "Acme teams, products and vendors.", "issues_repo": "acme/knowledge-layer",
          "marketplace": "knowledge-layer", "root_hub": "org-example"}


def write_config(plugin, **over):
    """Write a knowledge.json into a test plugin root. Pass issues_repo="" for the filing-off case."""
    Path(plugin).mkdir(parents=True, exist_ok=True)
    (Path(plugin) / "knowledge.json").write_text(json.dumps(dict(CONFIG, **over)))
