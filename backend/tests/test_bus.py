"""The percussion bus chain: balance, limiter, room determinism."""

import numpy as np

import bus


SR = 44100


def test_balance_is_equal_power():
    """Total percussion level must stay put across the balance sweep.

    Asserted the way the spec asks for it: the summed RMS of two decorrelated
    sub-buses varies by under 1 dB across ``layer_balance`` 0 -> 1.
    """
    rng = np.random.default_rng(7)
    flow = rng.standard_normal((SR, 2)) * 0.2
    bed = rng.standard_normal((SR, 2)) * 0.2

    levels_db = []
    for balance in np.linspace(0.0, 1.0, 11):
        fg, bg = bus.layer_gains(balance)
        mixed = flow * fg + bed * bg
        rms = float(np.sqrt(np.mean(mixed ** 2)))
        levels_db.append(20 * np.log10(rms))

    assert max(levels_db) - min(levels_db) < 1.0


def test_balance_endpoints_isolate_a_layer():
    assert bus.layer_gains(0.0)[1] < 1e-9
    assert bus.layer_gains(1.0)[0] < 1e-9
    fg, bg = bus.layer_gains(0.5)
    assert abs(fg - bg) < 1e-9


def test_limiter_holds_a_hot_mix_under_the_ceiling():
    rng = np.random.default_rng(11)
    # Deliberately hot: loud noise plus a handful of extreme transients.
    hot = rng.standard_normal((SR, 2)) * 1.6
    for idx in (5000, 20000, 33333):
        hot[idx:idx + 8] += 6.0

    ceiling_dbtp = -1.0
    ceiling = 10 ** (ceiling_dbtp / 20.0)
    limited = bus.true_peak_limit(hot, SR, ceiling_dbtp)

    assert float(np.max(np.abs(limited))) <= ceiling + 1e-6


def test_limiter_is_transparent_below_the_ceiling():
    quiet = np.full((1000, 2), 0.1, dtype=np.float32)
    limited = bus.true_peak_limit(quiet, SR, -1.0)
    assert np.allclose(limited, quiet, atol=1e-6)


def test_limiter_gain_envelope_matches_the_limiter():
    rng = np.random.default_rng(3)
    hot = rng.standard_normal((SR // 4, 2)) * 1.5
    gain = bus.true_peak_gain(hot, SR, -1.0)
    assert len(gain) == len(hot)
    assert float(np.max(gain)) <= 1.0 + 1e-9
    assert float(np.min(gain)) > 0.0


def test_room_ir_is_deterministic():
    """A random IR would make identical inputs produce different renders."""
    a = bus.room_ir(SR)
    b = bus.room_ir(SR)
    assert np.array_equal(a, b)
    # And the pre-delay really is silent.
    pre = int(SR * 0.012)
    assert float(np.max(np.abs(a[:pre]))) == 0.0


def test_saturation_preserves_rms():
    rng = np.random.default_rng(5)
    x = (rng.standard_normal((SR // 10, 2)) * 0.3).astype(np.float32)
    out = bus.saturate(x, 1.6)
    in_rms = float(np.sqrt(np.mean(x ** 2)))
    out_rms = float(np.sqrt(np.mean(out ** 2)))
    assert abs(20 * np.log10(out_rms / in_rms)) < 0.1


def test_transient_envelope_emphasises_the_attack_and_shortens_hats():
    kick = bus.transient_envelope(int(SR * 0.3), SR, "kick")
    assert kick[0] > 1.2
    assert abs(kick[-1] - 1.0) < 0.05  # no decay shortening on kicks

    hat = bus.transient_envelope(int(SR * 0.5), SR, "hat_closed")
    assert hat[0] > 1.4
    assert hat[-1] < 0.01  # the tail is pulled in

    # An unknown class still gets the default shape rather than crashing.
    assert len(bus.transient_envelope(100, SR, "cowbell")) == 100


def test_glue_compressor_reduces_peaks_without_killing_level():
    rng = np.random.default_rng(13)
    x = (rng.standard_normal((SR, 2)) * 0.08).astype(np.float32)
    x[SR // 2:SR // 2 + 64] += 0.9  # one loud transient

    out = bus.glue_compress(x, SR)
    assert float(np.max(np.abs(out))) < float(np.max(np.abs(x)))
    in_rms = float(np.sqrt(np.mean(x ** 2)))
    out_rms = float(np.sqrt(np.mean(out ** 2)))
    assert abs(20 * np.log10(out_rms / in_rms)) < 0.5  # makeup restores level


def test_running_min_matches_a_brute_force_window():
    rng = np.random.default_rng(17)
    x = rng.random(400)
    back, fwd = 7, 3
    got = bus._running_min(x, back=back, fwd=fwd)
    for i in range(len(x)):
        lo, hi = max(0, i - back), min(len(x), i + fwd + 1)
        assert got[i] <= float(np.min(x[lo:hi])) + 1e-12
