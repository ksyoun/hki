"""Audio input helpers."""

import numpy as np

from hki.live.audio import pick_input_mono, rms_db


def test_pick_input_mono_uses_louder_channel():
    quiet = np.zeros(100, dtype=np.float32)
    loud = np.full(100, 0.5, dtype=np.float32)
    stereo = np.column_stack([quiet, loud])
    mono = pick_input_mono(stereo)
    assert rms_db(mono) > -20


def test_pick_input_mono_single_channel():
    mono_in = np.full(50, 0.25, dtype=np.float32)
    out = pick_input_mono(mono_in)
    assert out.shape == (50,)
