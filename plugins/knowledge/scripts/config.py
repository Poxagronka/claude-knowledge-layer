"""Plugin config: reads knowledge.json. Stdlib only, Python 3.9.

The file lives in the plugin root. The fallback is the file next to the scripts folder.
A missing or broken file gives the defaults, so issue filing stays off.
"""
import json
import os

NAME = "knowledge.json"
DEFAULTS = {
    "org": "your organization",
    "scope": "Knowledge specific to your organization. No generic tool facts.",
    "issues_repo": "",
    "marketplace": "knowledge-layer",
    "root_hub": "",
}


def candidates():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return ([os.path.join(root, NAME)] if root else []) + [os.path.join(here, NAME)]


def load():
    """Defaults overlaid with the first readable config file."""
    cfg = dict(DEFAULTS)
    for path in candidates():
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            cfg.update(data)
            break
    return cfg


def get(key):
    return load().get(key, DEFAULTS.get(key, ""))


def text(key):
    """A config string, stripped. Anything else counts as empty."""
    value = get(key)
    return value.strip() if isinstance(value, str) else ""


def issues_repo():
    return text("issues_repo")


def plugin_id():
    return "knowledge@%s" % (text("marketplace") or DEFAULTS["marketplace"])
