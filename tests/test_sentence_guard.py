"""KO recombine mapping and source guards."""

from hki.live.sentence_guard import (
    join_source,
    last_unit_open,
    parse_recombine_units,
    select_translation_ko,
    validate_fragment_indexes,
)


def test_join_source():
    assert join_source(["오늘 우리가", " 이유를 생각해 봅시다 "]) == (
        "오늘 우리가 이유를 생각해 봅시다"
    )


def test_validate_fragment_indexes_partition():
    assert validate_fragment_indexes([[0, 1], [2]], 3) is True
    assert validate_fragment_indexes([[0, 1], [1, 2]], 3) is False
    assert validate_fragment_indexes([[0]], 3) is False
    assert validate_fragment_indexes([[0, 1, 2, 3]], 3) is False
    assert validate_fragment_indexes([[], [0, 1, 2]], 3) is False


def test_parse_units_valid_mapping():
    data = {
        "units": [
            {"text": "오늘 우리가 온유에 대해서", "fragment_indexes": [0, 1]},
            {"text": "생각해 보려고 합니다.", "fragment_indexes": [2]},
        ]
    }
    units = parse_recombine_units(data, 3)
    assert units is not None
    assert units[0][1] == [0, 1]
    assert units[1][1] == [2]


def test_parse_units_overlap_is_none():
    data = {
        "units": [
            {"text": "a", "fragment_indexes": [0, 1]},
            {"text": "b", "fragment_indexes": [1, 2]},
        ]
    }
    assert parse_recombine_units(data, 3) is None


def test_parse_single_string_unit_covers_all():
    data = {"units": ["오늘 우리가 온유에 대해서 생각해 보려고 합니다."]}
    units = parse_recombine_units(data, 3)
    assert units == [
        ("오늘 우리가 온유에 대해서 생각해 보려고 합니다.", [0, 1, 2])
    ]


def test_parse_multiple_strings_without_indexes_is_none():
    assert parse_recombine_units({"units": ["하나", "둘"]}, 2) is None


def test_short_stt_correction_passes():
    text, changed, rejected = select_translation_ko(
        "마태복음 오장",
        "마태복음 5장",
    )
    assert text == "마태복음 5장"
    assert changed is True
    assert rejected is False


def test_identical_source_is_not_repair():
    text, changed, rejected = select_translation_ko(
        "이유를 생각해 봅시다",
        "이유를 생각해 봅시다",
    )
    assert text == "이유를 생각해 봅시다"
    assert changed is False
    assert rejected is False


def test_manuscript_copy_rejected():
    manuscript = "오늘 우리가 하나님께 감사해야 하는 이유를 깊이 생각해 봅시다 그리고 순종합시다"
    source = "오늘 우리가"
    invented = "오늘 우리가 하나님께 감사해야 하는 이유를 깊이 생각해 봅시다"
    text, changed, rejected = select_translation_ko(
        source,
        invented,
        manuscript=manuscript,
    )
    assert rejected is True
    assert changed is False
    assert text == source


def test_split_is_allowed():
    source = "오늘 우리가 온유에 대해서 생각해 보려고 합니다. 그리고 중요합니다."
    text, changed, rejected = select_translation_ko(
        source,
        source,
        fragment_count=1,
    )
    assert rejected is False
    assert text == source


def test_empty_corrected_uses_source():
    text, changed, rejected = select_translation_ko(
        "안녕하세요",
        "",
    )
    assert text == "안녕하세요"
    assert changed is False
    assert rejected is False


def test_parse_units_open_missing_is_valid():
    data = {
        "units": [
            {"text": "오늘 우리가 온유에 대해서", "fragment_indexes": [0, 1]},
            {"text": "생각해 보려고 합니다.", "fragment_indexes": [2]},
        ]
    }
    units = parse_recombine_units(data, 3)
    assert units is not None
    assert last_unit_open(data) is False


def test_parse_units_open_true_on_last():
    data = {
        "units": [
            {"text": "첫 문장입니다.", "fragment_indexes": [0]},
            {"text": "갈망하는", "fragment_indexes": [1], "open": True},
        ]
    }
    units = parse_recombine_units(data, 2)
    assert units is not None
    assert units[1][0] == "갈망하는"
    assert last_unit_open(data) is True


def test_last_unit_open_non_bool_is_false():
    assert last_unit_open({"units": [{"text": "a", "fragment_indexes": [0], "open": "yes"}]}) is False
    assert last_unit_open(None) is False
