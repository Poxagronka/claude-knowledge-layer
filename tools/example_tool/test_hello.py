"""Check the public function and command output."""
import subprocess
import sys
import unittest
from pathlib import Path

from hello import hello


class HelloTests(unittest.TestCase):
    def test_greeting(self):
        self.assertEqual(hello(), "Hello, world!")
        self.assertEqual(hello("reader"), "Hello, reader!")

    def test_command(self):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("hello.py"))],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout, "Hello, world!\n")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
