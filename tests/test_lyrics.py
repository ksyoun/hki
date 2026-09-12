"""Praise lyrics parsing, caption wrapping, and slide cursor."""

import pytest

from hki.live.lyrics import (
    format_lyrics_caption,
    next_verse,
    normalize_songs,
    parse_json_object,
    parse_song_queries,
    prev_verse,
    select_song,
)


def test_parse_song_queries_strips_and_caps():
    raw = "\n새찬송가 310장\n\n  주는 사랑  \nHow Great Thou Art\n"
    assert parse_song_queries(raw) == [
        "새찬송가 310장",
        "주는 사랑",
        "How Great Thou Art",
    ]
    many = parse_song_queries("\n".join(f"c{i}" for i in range(20)))
    assert len(many) == 8


def test_format_lyrics_caption_kinds():
    assert format_lyrics_caption("mark") == "♪"
    assert format_lyrics_caption("title", "새찬송가 310장") == "♪ 새찬송가 310장 ♪"
    assert format_lyrics_caption("verse", "Gracia admirable") == "♪ Gracia admirable ♪"
    assert format_lyrics_caption("fin") == "♪ FIN ♪"
    assert format_lyrics_caption("fin", "ignored") == "♪ FIN ♪"


def test_normalize_songs_aligns_to_queries():
    raw = {
        "songs": [
            {
                "query": "새찬송가 310장",
                "label": "새찬송가 310장",
                "confidence": "high",
                "slides": ["A", "B"],
            }
        ]
    }
    songs = normalize_songs(raw, ["새찬송가 310장", "다른 곡"])
    assert len(songs) == 2
    assert songs[0]["slides"] == ["A", "B"]
    assert songs[1]["query"] == "다른 곡"
    assert songs[1]["slides"] == []
    assert songs[1]["confidence"] == "low"


def test_normalize_songs_accepts_multiline_string_slides():
    songs = normalize_songs(
        [{"query": "x", "slides": "Uno\n\nDos"}],
        ["x"],
    )
    assert songs[0]["slides"] == ["Uno", "Dos"]


def test_parse_json_object_extracts_embedded():
    assert parse_json_object('noise {"songs": []} trailing') == {"songs": []}
    assert parse_json_object("not json") == {}


def test_select_and_verse_cursor():
    songs = normalize_songs(
        [
            {
                "query": "310",
                "label": "310장",
                "slides": ["uno", "dos", "tres"],
            }
        ],
        ["310"],
    )
    idx, slide, label = select_song(songs, 0)
    assert idx == 0
    assert slide == -1
    assert label == "310장"
    assert next_verse(songs, 0, -1) == (0, "uno")
    assert next_verse(songs, 0, 0) == (1, "dos")
    assert next_verse(songs, 0, 2) is None
    assert prev_verse(songs, 0, -1) is None
    assert prev_verse(songs, 0, 2) == (1, "dos")
    assert prev_verse(songs, 0, 0) == (0, "uno")


def test_select_song_rejects_bad_index():
    with pytest.raises(ValueError):
        select_song([], 0)
