from prompt_toolkit.keys import Keys

from tasks import TaskTrackerTUI


def test_escape_binding_is_eager(tmp_path):
    tui = TaskTrackerTUI(tasks_dir=tmp_path / ".tasks")
    bindings = tui.app.key_bindings.bindings
    esc_bindings = [
        b for b in bindings
        if any((getattr(k, "key", k) == "escape") or (getattr(k, "key", None) == Keys.Escape) or k == Keys.Escape for k in b.keys)
    ]
    assert esc_bindings, "Escape binding not found"
    assert all(b.eager() if callable(b.eager) else bool(b.eager) for b in esc_bindings)


def test_escape_does_not_wait_for_sequences(tmp_path):
    tui = TaskTrackerTUI(tasks_dir=tmp_path / ".tasks")
    assert tui.app.key_bindings.timeout == 0
