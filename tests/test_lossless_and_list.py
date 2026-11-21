"""A: list-wide target without limiting. C: lossless MP3 gain (global_gain rewrite)."""
import threading
import subprocess
import pytest
from mutagen import File
from core.audio import OutputSettings, list_target, gain_filter, measure
from core.mp3gain import apply_gain, apply_gain_file, steps_for, _crc16, Mp3GainError
from core.loudness_job import normalize_file
from test_loudness_job import audio_source  # noqa: F401  (fixture)

S = OutputSettings(folder='.', lufs=-14, true_peak=-1, loudness_mode='list')


def m(i, tp):
    return {'input_i': str(i), 'input_tp': str(tp)}


def test_list_target_is_loudest_level_nobody_needs_limiting_for():
    # Song 2 can rise only to -16.5 (-1.5 headroom ceiling - peak 5 above loudness).
    assert list_target([m(-9, -0.5), m(-20, -15), m(-21.5, -16.5)], S) == (-14, [])
    assert list_target([m(-9, -0.5), m(-12, -5.5)], S) == (-14, [])
    assert list_target([m(-9, -0.5), m(-20, -4.5)], S) == (-17.0, [])
    # A song that cannot reach the floor is an outlier and does not drag the list down.
    target, outliers = list_target([m(-9, -0.5), m(-25, -1.6)], S)
    assert target == -14 and outliers == [1]
    assert gain_filter(m(-25, -1.6), S) == 'volume=0.10dB'  # stops at the peak ceiling, no limiter
    assert 'alimiter' not in gain_filter(m(-20, -4.5), S)


def test_steps_respect_peak_and_round_to_1_5_db():
    assert steps_for(-14, -1, -17.1, -9) == 2      # +3.1 dB wanted -> 2 steps
    assert steps_for(-14, -1, -20, -2) == 0        # peak forbids any boost
    assert steps_for(-14, -1, -8, -0.2) == -4      # -6 dB


def test_crc16_matches_mp3_polynomial():
    assert _crc16(b'123456789') == 0xAEE7         # CRC-16/CMS check value (poly 0x8005, init 0xFFFF)


def test_lossless_gain_is_exact_reversible_and_keeps_tags(audio_source, tmp_path):
    source = audio_source('mp3')
    original = source.read_bytes()
    out, back = tmp_path / 'up.mp3', tmp_path / 'back.mp3'
    assert apply_gain_file(source, out, 2) > 0
    apply_gain_file(out, back, -2)
    assert back.read_bytes() == original
    s = OutputSettings(folder=str(tmp_path))
    before, after = float(measure(source, s)['input_i']), float(measure(out, s)['input_i'])
    assert abs(after - before - 3.0) < 0.2
    assert File(out).tags['TIT2'].text == ['原始標題'] and File(out).tags.getall('APIC')


def test_lossless_mode_in_loudness_job(audio_source, tmp_path):
    source = audio_source('mp3')
    dest, before, after = normalize_file(source, -14, -1, False, tmp_path / 'out',
                                         threading.Event(), mode='lossless')
    notes = after['normalization']
    assert notes['method'] == 'lossless' and notes['gain_db'] == notes['steps'] * 1.5
    assert abs(float(after['input_i']) - (float(before['input_i']) + notes['gain_db'])) < 0.2
    with pytest.raises(ValueError, match='只支援 MP3'):
        normalize_file(audio_source('flac'), -14, -1, False, tmp_path, threading.Event(), mode='lossless')


def test_garbage_is_refused_not_written():
    with pytest.raises(Mp3GainError):
        apply_gain(b'\x00' * 2000, 1)
