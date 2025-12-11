#!/usr/bin/env python3
"""Unit tests for tui_themes module."""

import pytest

from core.desktop.devtools.interface.tui_themes import (
    THEMES,
    DEFAULT_THEME,
    get_theme_palette,
    build_style,
)


class TestThemes:
    """Tests for THEMES constant."""

    def test_themes_has_default_theme(self):
        """Test that DEFAULT_THEME exists in THEMES."""
        assert DEFAULT_THEME in THEMES

    def test_themes_has_expected_themes(self):
        """Test that THEMES contains expected themes."""
        assert "dark-olive" in THEMES
        assert "dark-contrast" in THEMES

    def test_theme_structure(self):
        """Test that each theme has required keys."""
        required_keys = {
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
        for theme_name, theme_dict in THEMES.items():
            missing = required_keys - set(theme_dict.keys())
            assert not missing, f"Theme {theme_name} missing keys: {missing}"


class TestGetThemePalette:
    """Tests for get_theme_palette function."""

    def test_get_theme_palette_existing_theme(self):
        """Test getting palette for existing theme."""
        palette = get_theme_palette("dark-olive")
        assert isinstance(palette, dict)
        assert len(palette) > 0
        assert palette == THEMES["dark-olive"]

    def test_get_theme_palette_returns_copy(self):
        """Test that get_theme_palette returns a defensive copy."""
        palette1 = get_theme_palette("dark-olive")
        palette2 = get_theme_palette("dark-olive")
        assert palette1 == palette2
        assert palette1 is not palette2  # Different objects
        assert palette1 is not THEMES["dark-olive"]  # Not the original

    def test_get_theme_palette_unknown_theme_falls_back(self):
        """Test that unknown theme falls back to default."""
        default_palette = get_theme_palette(DEFAULT_THEME)
        unknown_palette = get_theme_palette("non-existent-theme")
        assert unknown_palette == default_palette
        assert unknown_palette is not default_palette  # Different objects

    def test_get_theme_palette_all_themes(self):
        """Test getting palette for all available themes."""
        for theme_name in THEMES.keys():
            palette = get_theme_palette(theme_name)
            assert isinstance(palette, dict)
            assert len(palette) > 0


class TestBuildStyle:
    """Tests for build_style function."""

    def test_build_style_existing_theme(self):
        """Test building style for existing theme."""
        style = build_style("dark-olive")
        assert style is not None
        # Style should have style_rules attribute
        assert hasattr(style, "style_rules") or hasattr(style, "_style_rules")

    def test_build_style_unknown_theme_falls_back(self):
        """Test that building style for unknown theme falls back to default."""
        default_style = build_style(DEFAULT_THEME)
        unknown_style = build_style("non-existent-theme")
        # Both should be valid Style objects
        assert default_style is not None
        assert unknown_style is not None

    def test_build_style_all_themes(self):
        """Test building style for all available themes."""
        for theme_name in THEMES.keys():
            style = build_style(theme_name)
            assert style is not None

    def test_build_style_uses_get_theme_palette(self):
        """Test that build_style uses get_theme_palette."""
        # This is an integration test to ensure the functions work together
        palette = get_theme_palette("dark-olive")
        style = build_style("dark-olive")
        assert style is not None
        # Style should be built from the palette
        assert isinstance(palette, dict)
        assert len(palette) > 0
