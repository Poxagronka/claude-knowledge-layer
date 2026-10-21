"""Windows compatibility: the hook scripts must import without fcntl."""
import os
import subprocess
import sys

from conftest import ROOT, load_script

SCRIPTS = ROOT / "plugins" / "knowledge" / "scripts"
BLOCK = (
    "import sys\n"
    "sys.modules['fcntl'] = None\n"
    "sys.path.insert(0, %r)\n"
    "import plugin_state, hook, autoupdate, capture\n"
)


def test_scripts_import_without_fcntl():
    done = subprocess.run([sys.executable, "-c", BLOCK % str(SCRIPTS)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_capture_scope_uses_posix_drive_form(monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    capture = load_script("capture_win", "plugins/knowledge/scripts/capture.py")
    monkeypatch.setattr(capture.plugin_state, "root", lambda: "C:\\Users\\a\\plugin")
    assert "Read(//c/Users/a/plugin/vault/**)" in capture.capture_command()[-1]


def test_detached_kwargs_per_os(monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    import plugin_state
    if os.name != "nt":
        assert plugin_state.detached() == {"start_new_session": True}
    monkeypatch.setattr(plugin_state.os, "name", "nt")
    assert "creationflags" in plugin_state.detached()


def test_capture_scope_posix_root_unchanged(monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    capture = load_script("capture_posix", "plugins/knowledge/scripts/capture.py")
    monkeypatch.setattr(capture.plugin_state, "root", lambda: "/opt/p")
    assert "Read(//opt/p/vault/**)" in capture.capture_command()[-1]
