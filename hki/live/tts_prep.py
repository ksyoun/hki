"""Deprecated shim — use hki.live.output_composer.

Kept so older imports of PrepItem / oralize helpers still resolve.
"""

from hki.live.output_composer import (  # noqa: F401
    FragmentItem as PrepItem,
    OutputComposer,
    _fallback_join,
    _is_faithful as _oralize_is_faithful,
    recombine_for_output,
    release_interval_ms,
)


async def oralize_for_speech(items: list) -> str:
    """Back-compat: accept PrepItem(es-only) or FragmentItem."""
    fragments = []
    for it in items:
        if isinstance(it, PrepItem) or hasattr(it, "es"):
            ko = getattr(it, "ko", "") or ""
            fragments.append(
                PrepItem(item_id=it.item_id, ko=ko, es=it.es)
            )
    return await recombine_for_output(fragments)


# Legacy name — prefer OutputComposer
TTSPrepBuffer = OutputComposer
