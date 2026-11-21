"""Conservative, reversible cleanup of YouTube labels; no arbitrary title splitting."""
import re

_OFFICIAL = r'(?:official|offical)'
_CHANNEL_SUFFIX = re.compile(r'(?:\s+|\s*[-–—|｜:：]\s*)(?:youtube\s+)?(?:' + _OFFICIAL + r'(?:\s+(?:youtube\s+)?channel)?|channel)\s*$', re.I)
_PROMO = re.compile(r'(?:' + _OFFICIAL + r'(?:\s+(?:music|lyric|lyrics))?(?:\s+(?:video|audio|visualizer))?|(?:music|lyric|lyrics)\s+video|visualizer|MV|M/V|官方(?:音樂)?(?:影片|視頻)|官方MV)(?:\s*[-–—|｜]?\s*(?:HD|HQ|4K|8K|1080p))?', re.I)


def clean_artist(value):
    original = (value or '').strip()
    result = original
    while True:
        updated = _CHANNEL_SUFFIX.sub('', result).strip(' \t-–—|｜:：')
        if not updated or updated == result:
            return result or original
        result = updated


def clean_title(value, artist=''):
    original = (value or '').strip()
    result = original
    # Delete only a whole promotional bracket, preserving Live/Remix/feat/version labels.
    def bracket(match):
        return '' if _PROMO.fullmatch(match.group(1).strip()) else match.group(0)
    for pattern in (r'\(([^()]*)\)', r'\[([^\[\]]*)\]', r'【([^【】]*)】', r'（([^（）]*)）'):
        result = re.sub(pattern, bracket, result)
    result = re.sub(r'\s*[-–—|｜:：]+\s*(' + _PROMO.pattern + r')\s*$', '', result, flags=re.I)
    # Bare suffix must start with Official/Offical; "Music" alone may be the song title.
    result = re.sub(r'\s+' + _OFFICIAL + r'\s+(?:music(?:\s+video)?|lyric(?:s)?(?:\s+video)?|video|audio|visualizer)(?:\s+(?:HD|4K|1080p))?\s*$', '', result, flags=re.I)
    result = re.sub(r'\s+', ' ', result).strip(' \t-–—|｜:：')
    artist = clean_artist(artist)
    if artist:
        result = re.sub(r'^' + re.escape(artist) + r'\s+[-–—|｜:：/]\s*', '', result, flags=re.I).strip()
        # Artist「Title」: unambiguous quote boundary, never split a plain hyphenated title.
        quoted = re.fullmatch(re.escape(artist) + r'\s*[「『](.+)[」』]', result, re.I)
        if quoted:
            result = quoted.group(1).strip()
    for left, right in [('「','」'), ('『','』'), ('"','"'), ('“','”')]:
        if result.startswith(left) and result.endswith(right) and len(result) > 2:
            result = result[1:-1].strip()
            break
    return result or original
