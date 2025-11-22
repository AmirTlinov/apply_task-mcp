from core.desktop.devtools.interface.constants import AI_HELP, LANG_PACK, TIMESTAMP_FORMAT, GITHUB_GRAPHQL


def test_constants_values_present():
    assert "apply_task — hardline rules" in AI_HELP
    assert "en" in LANG_PACK and "ru" in LANG_PACK
    assert LANG_PACK["en"]["TITLE"] == "TITLE"
    assert TIMESTAMP_FORMAT == "%Y-%m-%d %H:%M"
    assert GITHUB_GRAPHQL.endswith("/graphql")
