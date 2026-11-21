"""Editable filename tokens and safe, optional relative folder templates."""
from pathlib import Path
import re
from datetime import date

TOKENS = ['<曲目歌星>', '<專輯歌星>', '<專輯>', '<年份>', '<Composer>',
          '<Disc Number>', '<標題>', '<號碼>', '<流派>', '<Total Discs>', '<日期>']
ALIASES = ['<曲目>', '<創作者>', '<曲目歌手>', '<專輯歌手>']
PRESETS = ['<曲目歌星>-<標題>', '<號碼> - <標題>', '<標題>',
           '<專輯歌星> - <專輯> - <號碼> - <標題>',
           '<專輯歌星>\\<專輯>\\<號碼> - <標題>']


def validate_template(template):
    if not template or not template.strip():
        raise ValueError('請選擇或輸入檔名格式')
    if template.startswith(('\\', '/')) or re.match(r'^[A-Za-z]:', template):
        raise ValueError('檔名格式只能指定相對子資料夾')
    for part in re.split(r'[\\/]', template):
        if not part.strip() or part.strip() in ('.', '..'):
            raise ValueError('子資料夾不可空白或使用 . / ..')
    for token in re.findall(r'<[^>]*>', template):
        if token not in TOKENS + ALIASES:
            raise ValueError(f'不支援的命名欄位：{token}')
    remainder = re.sub(r'<[^>]*>', '', template)
    if '<' in remainder or '>' in remainder:
        raise ValueError('欄位括號不完整')


def clean_component(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')[:150].rstrip(' .') or '未命名'
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?', value, re.I):
        value = '_' + value
    return value


def values(track):
    number = track.tracknumber.split('/')[0].zfill(2) if track.tracknumber else ''
    return {'<曲目歌星>': track.artist, '<曲目歌手>': track.artist, '<創作者>': track.artist,
            '<專輯歌星>': track.albumartist, '<專輯歌手>': track.albumartist,
            '<專輯>': track.album, '<年份>': track.date[:4], '<日期>': track.date,
            '<Composer>': track.composer, '<Disc Number>': track.discnumber,
            '<標題>': track.title, '<號碼>': number, '<曲目>': number,
            '<流派>': track.genre, '<Total Discs>': track.totaldiscs}


def relative_name(track, template):
    validate_template(template)
    tokens = values(track)
    # Split only the template, never separators supplied by metadata.
    return Path(*[clean_component(re.sub(r'<[^>]+>', lambda m: tokens[m.group()], part))
                  for part in re.split(r'[\\/]', template)])


def filename(track, template):
    return clean_component(str(relative_name(track, template)))


def output_path(folder, track, template, extension):
    root = Path(folder).resolve()
    rel = relative_name(track, template)
    target = (root / rel.parent / (rel.name + '.' + extension)).resolve()
    if not target.is_relative_to(root):
        raise ValueError('輸出子資料夾不能連結到指定輸出位置以外')
    return target


def album_directory(album):
    """[260926] REiNa-REVIVE IN COLOR from an explicitly confirmed album date."""
    if not album.is_playlist:
        raise ValueError('專輯資料夾模式僅適用多首播放清單')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', album.date or ''):
        raise ValueError('請到「共同資訊與封面」填入完整日期 YYYY-MM-DD')
    try:
        published = date.fromisoformat(album.date)
    except (TypeError, ValueError):
        raise ValueError('請到「共同資訊與封面」填入完整日期 YYYY-MM-DD') from None
    artist = album.albumartist or album.artist
    if not artist or not album.title:
        raise ValueError('請到「共同資訊與封面」填入專輯歌手與專輯名稱')
    return clean_component(f'[{published:%y%m%d}] {artist}-{album.title}')
