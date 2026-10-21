"""Every folder under skills/ and tools/ has a tool-* hub in the vault."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUBS = [p.read_text(encoding="utf-8") for p in (ROOT / "plugins/knowledge/vault/entities").glob("tool-*.md")]
FOLDERS = [
    f"{top}/{d.name}"
    for top in ("skills", "tools")
    if (ROOT / top).is_dir()
    for d in sorted((ROOT / top).iterdir())
    if d.is_dir() and not d.name.startswith((".", "__"))
]


@pytest.mark.parametrize("folder", FOLDERS)
def test_folder_has_tool_hub(folder):
    assert any(folder in text for text in HUBS), (
        f"{folder}: add a tool-* hub in plugins/knowledge/vault/entities so the plugin can suggest it"
    )
