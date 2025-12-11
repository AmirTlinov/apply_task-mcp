from core.desktop.devtools.application import context


def test_sanitize_domain_empty_returns_blank():
    assert context._sanitize_domain(None) == ""


def test_get_last_task_without_domain(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".last").write_text("TASK-123", encoding="utf-8")
    tid, domain = context.get_last_task()
    assert tid == "TASK-123" and domain is None


def test_resolve_task_reference_explicit(monkeypatch):
    monkeypatch.setattr(context, "derive_domain_explicit", lambda d, p, c: "explicit")
    tid, dom = context.resolve_task_reference("task-5", "explicit", None, None)
    assert tid == "TASK-005" and dom == "explicit"
