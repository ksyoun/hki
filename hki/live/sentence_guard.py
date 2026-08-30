"""Backend invariants for KO recombine units vs source fragments."""

from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")
_MIN_MANUSCRIPT_SPAN = 16


def join_source(texts: list[str]) -> str:
    return " ".join(t.strip() for t in texts if (t or "").strip())


def normalize_ws(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip())


def _tokens(text: str) -> list[str]:
    return [t for t in normalize_ws(text).split(" ") if t]


def _korean_blob(raw) -> str:
    if isinstance(raw, dict):
        return normalize_ws(str(raw.get("ko") or ""))
    return ""


def _reference_blobs(context: dict | None, manuscript: str) -> list[str]:
    blobs: list[str] = []
    ms = normalize_ws(manuscript)
    if ms:
        blobs.append(ms)
    if not context:
        return blobs
    summary = _korean_blob(context.get("sermon_summary"))
    if summary:
        blobs.append(summary)
    for item in context.get("outline") or []:
        line = _korean_blob(item)
        if line:
            blobs.append(line)
    for item in context.get("critical_sentences") or []:
        if isinstance(item, dict):
            ko = normalize_ws(str(item.get("ko") or ""))
            if ko:
                blobs.append(ko)
        elif isinstance(item, str):
            ko = normalize_ws(item)
            if ko:
                blobs.append(ko)
    return blobs


_SENT_SPLIT = re.compile(r"[.\n!?]|습니다|ㅂ니다")


def _spans_from_blob(blob: str) -> list[str]:
    spans: list[str] = []
    for part in _SENT_SPLIT.split(blob):
        p = normalize_ws(part)
        if len(p) >= _MIN_MANUSCRIPT_SPAN:
            spans.append(p)
            if len(p) > 48:
                spans.append(p[:48])
    if len(blob) >= _MIN_MANUSCRIPT_SPAN and blob not in spans:
        spans.append(blob[: min(len(blob), 80)])
    return spans


def _copied_manuscript_span(source: str, corrected: str, blobs: list[str]) -> bool:
    src = normalize_ws(source)
    corr = normalize_ws(corrected)
    for blob in blobs:
        for span in _spans_from_blob(blob):
            if len(span) < _MIN_MANUSCRIPT_SPAN:
                continue
            if span in corr and span not in src:
                return True
    return False


def validate_fragment_indexes(groups: list[list[int]], n: int) -> bool:
    """union == {0..N-1} and no duplicates / overlap / empty / OOB."""
    if n <= 0 or not groups:
        return False
    seen: list[int] = []
    for group in groups:
        if not group:
            return False
        for idx in group:
            if not isinstance(idx, int) or isinstance(idx, bool):
                return False
            if idx < 0 or idx >= n:
                return False
            seen.append(idx)
    return sorted(seen) == list(range(n))


def parse_recombine_units(data, n: int) -> list[tuple[str, list[int]]] | None:
    """Parse LLM units. None → caller must fallback to join_source."""
    if n <= 0 or not isinstance(data, dict):
        return None
    raw = data.get("units")
    if not isinstance(raw, list) or not raw:
        return None
    if all(isinstance(unit, str) for unit in raw):
        texts = [normalize_ws(unit) for unit in raw if normalize_ws(unit)]
        if len(texts) == 1:
            return [(texts[0], list(range(n)))]
        return None

    parsed: list[tuple[str, list[int]]] = []
    groups: list[list[int]] = []
    for unit in raw:
        if not isinstance(unit, dict):
            return None
        text = normalize_ws(str(unit.get("text") or ""))
        idxs = unit.get("fragment_indexes")
        if not text or not isinstance(idxs, list):
            return None
        ints: list[int] = []
        for idx in idxs:
            if isinstance(idx, bool) or not isinstance(idx, int):
                return None
            ints.append(idx)
        parsed.append((text, ints))
        groups.append(ints)
    if not validate_fragment_indexes(groups, n):
        return None
    return parsed


def last_unit_open(data) -> bool:
    """Last unit `open` flag. Missing or non-bool → False."""
    if not isinstance(data, dict):
        return False
    raw = data.get("units")
    if not isinstance(raw, list) or not raw:
        return False
    last = raw[-1]
    if not isinstance(last, dict):
        return False
    return last.get("open") is True


def select_translation_ko(
    source: str,
    ko_corrected: str,
    *,
    fragment_count: int = 0,
    context: dict | None = None,
    manuscript: str = "",
) -> tuple[str, bool, bool]:
    """Pick KO for Translate. Returns (text, changed, rejected)."""
    src = normalize_ws(source)
    corr = normalize_ws(ko_corrected)
    if not src:
        return "", False, False
    if not corr or corr == src:
        return src, False, False

    rejected = False
    src_len = len(src)
    corr_len = len(corr)
    if corr_len > src_len * 1.45 + 12 and (corr_len - src_len) > 8:
        rejected = True

    src_toks = set(_tokens(src))
    corr_toks = _tokens(corr)
    extra = [t for t in corr_toks if t not in src_toks]
    extra_chars = sum(len(t) for t in extra)
    if extra_chars > max(12, int(src_len * 0.45)):
        rejected = True

    if _copied_manuscript_span(src, corr, _reference_blobs(context, manuscript)):
        rejected = True

    if rejected:
        return src, False, True
    return corr, True, False
