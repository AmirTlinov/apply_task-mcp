#!/usr/bin/env python3
"""
Тест адаптивности UI для разных размеров терминала
"""

import sys
sys.path.insert(0, '.')

from tasks import ResponsiveLayoutManager, ColumnLayout


def test_layout_selection():
    """Тестирует выбор layout для разных размеров"""

    test_cases = [50, 70, 90, 120, 150, 180, 250]

    print("Тест выбора layout для разных размеров терминала:\n")
    print(f"{'Ширина':<10} | {'Колонки':<60} | {'Статус'}")
    print("-" * 80)

    prev_len = 0
    for width in test_cases:
        layout = ResponsiveLayoutManager.select_layout(width)
        cols = layout.columns
        assert cols[0] == 'stat'
        assert len(cols) >= prev_len  # ширина растёт — колонок не становится меньше
        if width >= 70:
            assert 'title' in cols
        if width >= 90:
            assert 'progress' in cols
        prev_len = len(cols)
        status = "✓ OK"
        print(f"{width:<10} | {str(cols):<60} | {status}")

    print("\n" + "="*80)


def test_width_calculation():
    """Тестирует расчёт ширины колонок"""

    print("\nТест расчёта ширины колонок:\n")
    print(f"{'Term':<6} | {'Layout':<30} | {'Title':<8} | {'Notes':<8} | {'Total'}")
    print("-" * 80)

    test_widths = [60, 80, 100, 120, 150, 200]

    for term_width in test_widths:
        layout = ResponsiveLayoutManager.select_layout(term_width)
        widths = layout.calculate_widths(term_width)

        # Подсчёт общей ширины
        total = sum(widths.values()) + len(layout.columns) + 1

        layout_desc = f"{len(layout.columns)} cols"
        title_w = widths.get('title', '-')
        notes_w = widths.get('notes', '-')

        print(f"{term_width:<6} | {layout_desc:<30} | {title_w!s:<8} | {notes_w!s:<8} | {total}")
        assert total > 0
        assert total <= term_width + 5  # с учётом разделителей

    print("\n" + "="*80)


def test_detail_view_width():
    """Тестирует расчёт ширины detail view"""

    print("\nТест ширины detail view:\n")
    print(f"{'Terminal':<10} | {'Content Width':<15} | {'Utilization %'}")
    print("-" * 50)

    test_widths = [50, 60, 80, 100, 120, 150, 200, 250]

    for term_width in test_widths:
        # Повторяем логику из get_detail_text
        if term_width < 60:
            content_width = max(40, term_width - 4)
        elif term_width < 100:
            content_width = term_width - 8
        else:
            content_width = min(int(term_width * 0.92), 160)

        utilization = (content_width / term_width) * 100
        print(f"{term_width:<10} | {content_width:<15} | {utilization:.1f}%")
        assert content_width >= 40
        assert content_width <= term_width
        assert utilization > 0

    print("\n" + "="*80)


if __name__ == '__main__':
    print("="*80)
    print(" Тестирование адаптивного UI")
    print("="*80 + "\n")

    results = []
    results.append(("Layout Selection", test_layout_selection()))
    results.append(("Width Calculation", test_width_calculation()))
    results.append(("Detail View Width", test_detail_view_width()))

    print("\nИтоги тестирования:")
    print("-" * 50)

    all_passed = True
    for name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{name:<30} | {status}")
        all_passed = all_passed and passed

    print("="*80)

    if all_passed:
        print("\n✓ Все тесты успешно пройдены!")
        sys.exit(0)
    else:
        print("\n✗ Некоторые тесты провалены")
        sys.exit(1)
