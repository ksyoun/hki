"""Backend invariants for sentence through_index and ko_corrected vs source."""

from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")
_CLAUSE_END = re.compile(
    r"(습니다|ㅂ니다|세요|해요|어요|아요|죠|니다|[.!?]|다(?:\s|$))"
)
_MIN_MANUSCRIPT_SPAN = 16


def join_source(texts: list[str]) -> str:
    return " ".join(t.strip() for t in texts if (t or "").strip())


def normalize_ws(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").strip())


def parse_through_index(raw) -> int | None:
    """Exact integer index, or None if missing/non-integer/bool."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw.is_integer():
            return int(raw)
        return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        if s[0] in "+-" and s[1:].isdigit():
            return int(s)
        if s.isdigit():
            return int(s)
        return None
    return None


def resolve_release_index(raw, n: int, *, force: bool) -> int:
    """Relative 1-based prefix length. 0 means hold.

    Normal evaluate: only k in 1..n is a release. 0, negative, non-integer,
    and k > n are hold — never clamp k > n down to n.
    Force (timeout / max_pending / drain): always n.
    """
    if n <= 0:
        return 0
    if force:
        return n
    k = parse_through_index(raw)
    if k is None:
        return 0
    if 1 <= k <= n:
        return k
    return 0


def _tokens(text: str) -> list[str]:
    return [t for t in normalize_ws(text).split(" ") if t]


def _clause_count(text: str) -> int:
    found = _CLAUSE_END.findall(text or "")
    return max(1, len(found)) if normalize_ws(text) else 0


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


def select_translation_ko(
    source: str,
    ko_corrected: str,
    *,
    fragment_count: int,
    context: dict | None = None,
    manuscript: str = "",
) -> tuple[str, bool, bool]:
    """Pick KO for Translate. Returns (text, stt_repair, repair_rejected)."""
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

    if fragment_count > 0 and _clause_count(corr) > fragment_count + 1:
        rejected = True

    if _copied_manuscript_span(src, corr, _reference_blobs(context, manuscript)):
        rejected = True

    if rejected:
        return src, False, True
    return corr, True, False
