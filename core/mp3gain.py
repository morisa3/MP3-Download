"""Lossless MP3 volume change (the MP3Gain method).

Every Layer III granule stores an 8-bit global_gain; one step is 1.5 dB. Changing only those
bits alters the decoded volume without decoding or re-encoding the audio, so it adds no quality
loss and is reversible by applying the opposite number of steps. Tags and all other bytes stay
untouched. Frames protected by CRC get their checksum recomputed; the Xing/Info (LAME) frame
is left alone so its own CRC stays valid.
"""
from pathlib import Path

STEP_DB = 1.5

_BITRATES = {  # kbps, Layer III
    1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


class Mp3GainError(ValueError):
    pass


def steps_for(target_lufs, peak_limit, input_i, input_tp, max_steps=40):
    """Whole 1.5 dB steps toward the target that never push the true peak above the limit."""
    wanted = round((float(target_lufs) - float(input_i)) / STEP_DB)
    while wanted > 0 and float(input_tp) + wanted * STEP_DB > float(peak_limit):
        wanted -= 1
    return max(-max_steps, min(max_steps, wanted))


def _crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def _get_bits(buf, pos, n):
    value = 0
    for i in range(n):
        bit = pos + i
        value = (value << 1) | ((buf[bit >> 3] >> (7 - (bit & 7))) & 1)
    return value


def _set_bits(buf, pos, n, value):
    for i in range(n):
        bit = pos + i
        mask = 1 << (7 - (bit & 7))
        if (value >> (n - 1 - i)) & 1:
            buf[bit >> 3] |= mask
        else:
            buf[bit >> 3] &= ~mask & 0xFF


def _header(data, pos):
    """Parse a Layer III frame header; None when pos is not a valid frame start."""
    if pos + 4 > len(data) or data[pos] != 0xFF or (data[pos + 1] & 0xE0) != 0xE0:
        return None
    version = (data[pos + 1] >> 3) & 3          # 3 = MPEG1, 2 = MPEG2, 0 = MPEG2.5
    layer = (data[pos + 1] >> 1) & 3            # 1 = Layer III
    if version == 1 or layer != 1:
        return None
    protected = not (data[pos + 1] & 1)
    bitrate_index = data[pos + 2] >> 4
    rate_index = (data[pos + 2] >> 2) & 3
    if bitrate_index in (0, 15) or rate_index == 3:
        return None                              # free-format or reserved
    padding = (data[pos + 2] >> 1) & 1
    channels = 1 if (data[pos + 3] >> 6) == 3 else 2
    mpeg1 = version == 3
    bitrate = _BITRATES[1 if mpeg1 else 2][bitrate_index] * 1000
    rate = _RATES[version][rate_index]
    size = (144 if mpeg1 else 72) * bitrate // rate + padding
    side = (17 if channels == 1 else 32) if mpeg1 else (9 if channels == 1 else 17)
    return {'mpeg1': mpeg1, 'channels': channels, 'protected': protected,
            'size': size, 'side': side}


def _gain_offsets(h):
    """Bit offsets of every global_gain field inside the side information."""
    ch = h['channels']
    if h['mpeg1']:
        start = 9 + (5 if ch == 1 else 3) + 4 * ch
        return [start + k * 59 + 21 for k in range(2 * ch)]
    start = 8 + (1 if ch == 1 else 2)
    return [start + c * 63 + 21 for c in range(ch)]


def _audio_bounds(data):
    start = 0
    if data[:3] == b'ID3' and len(data) >= 10:
        size = 0
        for byte in data[6:10]:
            size = (size << 7) | (byte & 0x7F)
        start = 10 + size + (10 if data[5] & 0x10 else 0)
    end = len(data)
    if end - 128 >= start and data[end - 128:end - 125] == b'TAG':
        end -= 128
    if end - 32 >= start and data[end - 32:end - 24] == b'APETAGEX':
        ape_size = int.from_bytes(data[end - 20:end - 16], 'little')
        end -= ape_size + (32 if data[end - 9] & 0x80 else 0)
    return start, max(start, end)


def apply_gain(data, steps):
    """Return (new bytes, frames changed). Raises Mp3GainError instead of producing a bad file."""
    if not steps:
        return bytes(data), 0
    buf = bytearray(data)
    pos, end = _audio_bounds(buf)
    # Find the first frame whose successor also lines up (avoids false sync in junk bytes).
    while pos < end:
        h = _header(buf, pos)
        if h and (pos + h['size'] >= end or _header(buf, pos + h['size'])):
            break
        pos += 1
    frames, first = 0, True
    while pos + 4 <= end:
        h = _header(buf, pos)
        if not h:
            if end - pos < 4 or not any(buf[pos:end]):
                break  # trailing padding
            raise Mp3GainError(f'第 {frames + 1} 個 MP3 框架位置無法辨識，未修改')
        if pos + h['size'] > end:
            break  # truncated last frame: leave as is
        side_at = pos + 4 + (2 if h['protected'] else 0)
        if first and bytes(buf[side_at + h['side']:side_at + h['side'] + 4]) in (b'Xing', b'Info'):
            first = False
            pos += h['size']
            continue
        first = False
        bits = side_at * 8
        for offset in _gain_offsets(h):
            value = _get_bits(buf, bits + offset, 8) + steps
            if not 0 <= value <= 255:
                raise Mp3GainError('調整幅度超出 MP3 增益範圍，未修改')
            _set_bits(buf, bits + offset, 8, value)
        if h['protected']:
            crc = _crc16(bytes(buf[pos + 2:pos + 4]) + bytes(buf[side_at:side_at + h['side']]))
            buf[pos + 4:pos + 6] = crc.to_bytes(2, 'big')
        frames += 1
        pos += h['size']
    if not frames:
        raise Mp3GainError('找不到可調整的 MP3 音訊框架')
    return bytes(buf), frames


def apply_gain_file(source, dest, steps):
    data = Path(source).read_bytes()
    result, frames = apply_gain(data, steps)
    if len(result) != len(data):
        raise Mp3GainError('檔案長度不一致，未修改')
    Path(dest).write_bytes(result)
    return frames
