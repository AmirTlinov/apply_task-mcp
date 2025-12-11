from core.desktop.devtools.interface.tui_focus import focusable_line_indices


def test_focusable_skips_borders_and_headers():
    lines = [
        [("class:border", "---")],
        [("class:header", "Title")],
        [(None, "  ")],
        [("class:text", "○ bullet")],
        [("class:text", "real")],
    ]

    def extract(line):
        return None

    assert focusable_line_indices(lines, extract) == [4]


def test_focusable_unique_groups_and_arrows():
    lines = [
        [(None, "Group1")],
        [(None, "Group1 dup")],
        [(None, "↑ up")],
        [(None, "real2")],
    ]

    def extract(line):
        if "dup" in line[0][1]:
            return 1
        if "Group1" in line[0][1]:
            return 1
        return None

    assert focusable_line_indices(lines, extract) == [0, 3]
