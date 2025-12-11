import unittest

from tasks import TaskTrackerTUI, DEFAULT_THEME, THEMES


REQUIRED_KEYS = {
    "",
    "status.ok",
    "status.warn",
    "status.fail",
    "status.unknown",
    "text",
    "text.dim",
    "text.dimmer",
    "selected",
    "selected.ok",
    "selected.warn",
    "selected.fail",
    "selected.unknown",
    "header",
    "border",
    "icon.check",
    "icon.warn",
    "icon.fail",
}


class ThemeTests(unittest.TestCase):
    def test_all_themes_have_required_keys(self):
        for name in THEMES.keys():
            palette = TaskTrackerTUI.get_theme_palette(name)
            missing = REQUIRED_KEYS - set(palette.keys())
            self.assertFalse(missing, f"theme {name} missing {missing}")

    def test_unknown_theme_falls_back_to_default(self):
        palette_default = TaskTrackerTUI.get_theme_palette(DEFAULT_THEME)
        palette_unknown = TaskTrackerTUI.get_theme_palette("non-existent")
        self.assertEqual(palette_unknown, palette_default)
        self.assertIsNot(palette_unknown, palette_default)

    def test_style_builds_without_errors(self):
        style = TaskTrackerTUI.build_style(DEFAULT_THEME)
        self.assertTrue(getattr(style, "style_rules", None))


if __name__ == "__main__":
    unittest.main()
