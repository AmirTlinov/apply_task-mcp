from types import SimpleNamespace

from core.desktop.devtools.interface import cli_create


def test_cmd_create_requires_tests(monkeypatch, capsys):
    class DummyManager:
        def __init__(self):
            self.saved = None

        def create_task(self, *args, **kwargs):
            task = type("T", (), {})()
            task.subtasks = []
            task.success_criteria = []
            task.risks = []
            task.dependencies = []
            task.next_steps = []
            task.tags = []
            task.risks = []
            task.blocked = False
            task.events = []  # Add events list
            def update():
                return None
            task.update_status_from_progress = update
            return task

        def save_task(self, task):
            self.saved = task

    monkeypatch.setattr(cli_create, "TaskManager", lambda: DummyManager())
    args = SimpleNamespace(
        title="t",
        status="FAIL",
        priority="LOW",
        parent="TASK-1",
        description="desc",
        context=None,
        tags=None,
        subtasks='[{"title":"Subtask one is long enough","criteria":["c"],"tests":["t"],"blockers":["b"]}, {"title":"Subtask two is also long","criteria":["c"],"tests":["t"],"blockers":["b"]}, {"title":"Subtask three is long","criteria":["c"],"tests":["t"],"blockers":["b"]}]',
        dependencies=None,
        next_steps=None,
        tests=None,
        risks=None,
        validate_only=True,
        domain="",
        phase=None,
        component=None,
    )
    rc = cli_create.cmd_create(args)
    assert rc == 1
    assert "Provide tests" in capsys.readouterr().out


def test_cmd_smart_create_uses_template(monkeypatch, capsys):
    class DummyManager:
        def __init__(self):
            self.saved = None
            self.config = {"templates": {"default": {"description": "tpl desc", "tests": "tpl test"}}}

        def create_task(self, *args, **kwargs):
            task = type("T", (), {})()
            task.id = "TASK-1"
            task.title = "Title"
            task.status = "FAIL"
            task.priority = "LOW"
            task.domain = ""
            task.phase = ""
            task.component = ""
            task.parent = "TASK-1"
            task.tags = []
            task.assignee = None
            task.blocked = False
            task.blockers = []
            task.description = ""
            task.context = ""
            task.success_criteria = []
            task.dependencies = []
            task.next_steps = []
            task.problems = []
            task.risks = []
            task.history = []
            task.project_remote_updated = False
            task.subtasks = []
            task.events = []  # Add events list
            task.calculate_progress = lambda: 0
            task.update_status_from_progress = lambda: None
            return task

        def save_task(self, task):
            self.saved = task

    monkeypatch.setattr(cli_create, "TaskManager", lambda: DummyManager())
    args = SimpleNamespace(
        title="Title #tag",
        status="FAIL",
        priority="LOW",
        parent="TASK-1",
        description="desc",
        context=None,
        tags="",
        subtasks='[{"title":"Subtask one is long enough","criteria":["c"],"tests":["t"],"blockers":["b"]}, {"title":"Subtask two is also long","criteria":["c"],"tests":["t"],"blockers":["b"]}, {"title":"Subtask three is long","criteria":["c"],"tests":["t"],"blockers":["b"]}]',
        dependencies=None,
        next_steps=None,
        tests=None,
        risks="risk",
        validate_only=True,
        domain="",
        phase=None,
        component=None,
    )
    rc = cli_create.cmd_smart_create(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "validate" in out


def test_cmd_create_validate_only_false(monkeypatch, capsys):
    class DummyManager:
        def __init__(self):
            self.saved = None
            self.config = {}

        def create_task(self, *args, **kwargs):
            task = SimpleNamespace(
                id="TASK-1",
                title="t",
                status="FAIL",
                priority="LOW",
                domain="",
                phase="",
                component="",
                parent="TASK-0",
                tags=[],
                assignee=None,
                blocked=False,
                blockers=[],
                description="desc",
                context="",
                success_criteria=["t"],
                dependencies=[],
                next_steps=[],
                problems=[],
                risks=["r"],
                history=[],
                subtasks=[],
                events=[],  # Add events list
                project_remote_updated=False,
            )
            task.calculate_progress = lambda: 0
            task.update_status_from_progress = lambda: None
            return task

        def save_task(self, task):
            self.saved = task

    monkeypatch.setattr(cli_create, "TaskManager", lambda: DummyManager())
    args = SimpleNamespace(
        title="t",
        status="FAIL",
        priority="LOW",
        parent="TASK-1",
        description="desc",
        context=None,
        tags=None,
        subtasks='[{"title":"Subtask long enough one","criteria":["c"],"tests":["t"],"blockers":["b"]}, {"title":"Subtask long enough two","criteria":["c"],"tests":["t"],"blockers":["b"]}, {"title":"Subtask long enough three","criteria":["c"],"tests":["t"],"blockers":["b"]}]',
        dependencies=None,
        next_steps=None,
        tests="unit",
        risks="r",
        validate_only=False,
        domain="",
        phase=None,
        component=None,
    )
    rc = cli_create.cmd_create(args)
    assert rc == 0
