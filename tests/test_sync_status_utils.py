from util.sync_status import sync_status_fragments


def test_sync_status_with_issue_and_flash():
    snapshot = {"status_reason": "fail"}
    frags = sync_status_fragments(snapshot, enabled=False, flash=True, filter_flash=False)
    assert frags[0][1].startswith("Git Projects")
    assert any("!" in text for _, text in frags)


def test_sync_status_with_last_times():
    snapshot = {"status_reason": "", "last_pull": "10:00", "last_push": "11:00"}
    frags = sync_status_fragments(snapshot, enabled=True, flash=False, filter_flash=False)
    text = "".join(t for _, t in frags)
    assert "pull=10:00" in text
    assert "push=11:00" in text
    assert frags[0][0] == "class:icon.check"


def test_sync_status_filter_flash_short_circuit():
    frags = sync_status_fragments({"status_reason": ""}, enabled=True, flash=True, filter_flash=True)
    assert len(frags) == 1
    assert frags[0][1] == "Git Projects ■"


def test_sync_status_disabled_no_issue():
    frags = sync_status_fragments({"status_reason": ""}, enabled=False, flash=False, filter_flash=False)
    assert frags[0][0] == "class:text.dim"
    assert "□" in frags[0][1]
