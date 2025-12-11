from types import SimpleNamespace

from core.desktop.devtools.interface import templates


def test_load_template_default_and_fallback():
    class DummyManager:
        def __init__(self, cfg):
            self.config = cfg

    mgr = DummyManager({"templates": {"default": {"description": "desc", "tests": "tests"}}})
    desc, tests = templates.load_template("unknown", mgr)
    assert desc == "desc" and tests == "tests"

    mgr_empty = DummyManager({"templates": {}})
    desc2, tests2 = templates.load_template("none", mgr_empty)
    assert desc2 == "TBD" and tests2 == "acceptance"
