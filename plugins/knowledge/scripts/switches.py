"""One definition of an env opt-out switch. Stdlib only, Python 3.9."""
import os

OFF_VALUES = ("0", "false", "off", "no")
ON_VALUES = ("1", "true", "on", "yes")


def env_off(name):
    return os.environ.get(name, "").strip().lower() in OFF_VALUES


def env_on(name):
    return os.environ.get(name, "").strip().lower() in ON_VALUES
