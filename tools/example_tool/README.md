# Example tool

This small tool shows the warehouse layout. Each directory in `tools/` owns a
`Makefile` with a `check` target. CI runs that target when the tool changes.

Run `python3 hello.py` to print a greeting. It uses only the Python standard
library and supports Python 3.9 or later.

Run `make check` in this directory to test the function and command output.
