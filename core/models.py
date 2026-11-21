"""Serializable project state. Source media is never overwritten."""
from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import uuid

@dataclass
class Track:
    uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    url: str = ''
    title: str = ''
    artist: str = ''
    album: str = ''
    albumartist: str = ''
    date: str = ''
    tracknumber: str = ''
    genre: str = ''
    composer: str = ''
    discnumber: str = ''
    totaldiscs: str = ''
    source: str = ''
    cover: str = ''
    bbox: list = field(default_factory=list)
    selected: bool = True
    status: str = '待下載'
    warning: str = ''
    output: str = ''
    measurement: dict = field(default_factory=dict)
    playlist_url: str = ''
    download_attempts: int = 0
    original_title: str = ''
    original_artist: str = ''
    original_albumartist: str = ''
    source_date: str = ''
    date_from: str = ''
    date_to: str = ''
    auto_clean_titles: bool = True
    metadata_overrides: list = field(default_factory=list)


@dataclass
class AlbumInfo:
    """Shared playlist fields; blank fields leave per-track metadata unchanged."""
    is_playlist: bool = False
    is_release_collection: bool = False
    title: str = ''
    artist: str = ''
    albumartist: str = ''
    date: str = ''
    genre: str = ''
    composer: str = ''
    discnumber: str = ''
    totaldiscs: str = ''
    cover: str = ''
    bbox: list = field(default_factory=list)
    cover_source_uid: str = ''
    completed_temp: list = field(default_factory=list)


def effective_track(track, album):
    """Keep song fields while applying the explicitly edited shared fields."""
    from copy import copy
    result = copy(track)
    if album and album.is_playlist:
        for name in ('artist', 'albumartist', 'date', 'genre', 'composer',
                     'discnumber', 'totaldiscs'):
            value = getattr(album, name)
            if value and name not in track.metadata_overrides:
                setattr(result, name, value)
        if album.title and 'album' not in track.metadata_overrides:
            result.album = album.title
        if album.cover:
            result.cover = album.cover
            result.bbox = list(album.bbox)
    return result


def save_project(path, tracks, album=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps({'version': 1, 'tracks': [asdict(t) for t in tracks],
                               'album': asdict(album) if album else {}}, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def load_project(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('version') != 1:
        raise ValueError('不支援的專案版本')
    return [Track(**{k: v for k, v in x.items() if k in Track.__dataclass_fields__}) for x in data['tracks']]


def load_session(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return (load_project(path), AlbumInfo(**{
        k: v for k, v in data.get('album', {}).items() if k in AlbumInfo.__dataclass_fields__}))
