"""Backend through_index and ko_corrected vs source guards."""

from hki.live.sentence_guard import (
    join_source,
    parse_through_index,
    resolve_release_index,
    select_translation_ko,
)


def test_join_source():
    assert join_source(["오늘 우리가", " 이유를 생각해 봅시다 "]) == (
        "오늘 우리가 이유를 생각해 봅시다"
    )


def test_parse_through_index_rejects_non_int():
    assert parse_through_index(2) == 2
    assert parse_through_index(0) == 0
    assert parse_through_index(-1) == -1
    assert parse_through_index("3") == 3
    assert parse_through_index(2.0) == 2
    assert parse_through_index(2.7) is None
    assert parse_through_index(True) is None
    assert parse_through_index(None) is None
    assert parse_through_index("k") is None


def test_resolve_invalid_is_hold_not_clamp():
    n = 3
    assert resolve_release_index(0, n, force=False) == 0
    assert resolve_release_index(-1, n, force=False) == 0
    assert resolve_release_index(4, n, force=False) == 0
    assert resolve_release_index(1, n, force=False) == 1
    assert resolve_release_index(3, n, force=False) == 3
    assert resolve_release_index(2.5, n, force=False) == 0
    assert resolve_release_index(99, n, force=True) == 3
    assert resolve_release_index(0, n, force=True) == 3
    assert resolve_release_index(None, n, force=True) == 3


def test_short_stt_correction_passes():
    text, repair, rejected = select_translation_ko(
        "마태복음 오장",
        "마태복음 5장",
        fragment_count=1,
    )
    assert text == "마태복음 5장"
    assert repair is True
    assert rejected is False


def test_identical_source_is_not_repair():
    text, repair, rejected = select_translation_ko(
        "이유를 생각해 봅시다",
        "이유를 생각해 봅시다",
        fragment_count=1,
    )
    assert text == "이유를 생각해 봅시다"
    assert repair is False
    assert rejected is False


def test_manuscript_copy_rejected():
    manuscript = "오늘 우리가 하나님께 감사해야 하는 이유를 깊이 생각해 봅시다 그리고 순종합시다"
    source = "오늘 우리가"
    invented = "오늘 우리가 하나님께 감사해야 하는 이유를 깊이 생각해 봅시다"
    text, repair, rejected = select_translation_ko(
        source,
        invented,
        fragment_count=1,
        manuscript=manuscript,
    )
    assert rejected is True
    assert repair is False
    assert text == source


def test_empty_corrected_uses_source():
    text, repair, rejected = select_translation_ko(
        "안녕하세요",
        "",
        fragment_count=1,
    )
    assert text == "안녕하세요"
    assert repair is False
    assert rejected is False
