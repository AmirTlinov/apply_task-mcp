import tasks


def test_build_parser_has_core_commands():
    parser = tasks.build_parser()
    help_text = parser.format_help()
    assert "tui" in help_text
    assert "automation" in help_text
    assert "projects" in help_text
