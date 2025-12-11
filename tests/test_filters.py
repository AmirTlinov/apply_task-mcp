import io
import sys
import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout

from tasks import cmd_quick, cmd_next, cmd_suggest


def capture_output(func, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        func(args)
    return buf.getvalue()


class FilterHintTests(unittest.TestCase):
    def make_args(self, folder="", phase="", component=""):
        return SimpleNamespace(folder=folder, phase=phase, component=component)

    def test_quick_hints_filters_when_empty(self):
        out = capture_output(cmd_quick, self.make_args(folder="alpha/api"))
        self.assertIn("folder='alpha/api'", out)
        self.assertIn("phase='-'", out)
        self.assertIn("component='-'", out)

    def test_next_hints_filters_when_empty(self):
        out = capture_output(cmd_next, self.make_args(phase="alpha", component="api"))
        self.assertIn("phase='alpha'", out)
        self.assertIn("component='api'", out)

    def test_suggest_hints_filters_when_empty(self):
        out = capture_output(cmd_suggest, self.make_args(folder="phase1"))
        self.assertIn("folder='phase1'", out)


if __name__ == "__main__":
    unittest.main()
