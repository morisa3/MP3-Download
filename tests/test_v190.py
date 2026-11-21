"""v1.9.0 regressions: stale album info, release artwork, release-order numbering, loudness."""
import asyncio
from pathlib import Path
from core.audio import OutputSettings, gain_filter, normalization_type, limiter_gain_reduction
from core.models import Track, AlbumInfo, effective_track
from core.youtube import (YouTubeService, ListEntry, renumber, card_thumbnail,
                          thumbnail_candidates, channel_tab)
from test_ui import FakePage
from ui.main_view import Studio

RELEASES = 'https://www.youtube.com/@koTetu-ai-music/releases'


def fake_ydl(monkeypatch, payloads, calls):
    class Fake:
        def __init__(self, options): self.options = options
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download=False):
            calls.append(url)
            return payloads[url]
    monkeypatch.setattr('core.youtube.yt_dlp.YoutubeDL', Fake)


def test_release_order_numbering_and_card_artwork(tmp_path, monkeypatch):
    # The releases tab lists newest first; with oldest = 1 a new release appends.
    entries = [{'url': f'https://www.youtube.com/watch?v=video{n:05d}', 'title': f'Song {n}',
                'thumbnails': [{'url': f'https://lh3.googleusercontent.com/a{n}=w120-h120-l90', 'width': 120, 'height': 120},
                               {'url': f'https://lh3.googleusercontent.com/b{n}=w544-h544-l90', 'width': 544, 'height': 544}]}
               for n in (3, 2, 1)]
    calls = []
    fake_ydl(monkeypatch, {RELEASES: {'title': 'koTetu - Releases', 'entries': entries}}, calls)
    result = YouTubeService(tmp_path).entries(RELEASES)
    assert [(e.album, e.index, e.position) for e in result] == [('Song 3', 3, 1), ('Song 2', 2, 2), ('Song 1', 1, 3)]
    assert result[0].thumbnail == 'https://lh3.googleusercontent.com/b3=w544-h544-l90'
    assert thumbnail_candidates(result[0].thumbnail) == [
        'https://lh3.googleusercontent.com/b3=w1200-h1200-l90', result[0].thumbnail]
    # A v1.8.0 cache without artwork is re-read once; the new cache is then reused.
    YouTubeService(tmp_path).entries(RELEASES, numbering='newest_first')
    assert len(calls) == 1


def test_renumber_modes_and_tabs():
    items = [ListEntry('a', 1, '', 1), ListEntry('b', 2, '', 2)]
    assert [e.index for e in renumber(items, 'auto', '')] == [1, 2]
    assert [e.index for e in renumber(items, 'auto', 'videos')] == [2, 1]
    assert [e.index for e in renumber(items, 'newest_first', 'releases')] == [1, 2]
    assert channel_tab('https://www.youtube.com/@PIKASONIC/releases') == 'releases'
    assert channel_tab('https://www.youtube.com/playlist') == ''
    assert card_thumbnail([{'url': '//i.ytimg.com/x.jpg'}]) == 'https://i.ytimg.com/x.jpg'


def test_release_card_cover_preferred_over_video_thumbnail(tmp_path, monkeypatch):
    import core.youtube as module
    from io import BytesIO
    from PIL import Image
    def jpeg(color):
        data = BytesIO(); Image.new('RGB', (300, 300), color).save(data, format='JPEG'); return data.getvalue()
    opened = []
    class Fake:
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download):
            p = Path(self.opts['outtmpl'].replace('%(ext)s', 'webm')); p.write_bytes(b'x')
            return {'title': 'Song', 'channel': 'koTetu', 'upload_date': '20260101',
                    'requested_downloads': [{'filepath': str(p)}], 'thumbnail': 'https://i.ytimg.com/new-mv.jpg'}
        def urlopen(self, url):
            opened.append(url)
            return BytesIO(jpeg('red' if 'card' in url else 'blue'))
    monkeypatch.setattr(module.yt_dlp, 'YoutubeDL', Fake)
    t = Track(url='https://www.youtube.com/watch?v=abcdefghijk', release_thumbnail='https://i.ytimg.com/card.jpg')
    YouTubeService(tmp_path).download(t)
    assert opened == ['https://i.ytimg.com/card.jpg'] and t.cover_origin == 'releases'


def test_renumbered_track_retries_original_position():
    assert Track(tracknumber='7', playlist_index=2).source_position() == 2
    assert Track(tracknumber='3/10').source_position() == 3


def test_album_info_does_not_leak_into_later_singles(tmp_path, monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'DATA', tmp_path / 'data')
    app = Studio(FakePage())
    app._scan_download = lambda t: None
    # A previous playlist left its shared fields (first song's artist/date/cover).
    app.album = AlbumInfo(is_playlist=True, title='Old album', artist='First', date='2020-01-01',
                          cover='first.jpg', playlist_url='https://www.youtube.com/playlist?list=PLold')
    service = YouTubeService(tmp_path / 'temp')
    service.entries = lambda *a: [ListEntry('https://www.youtube.com/watch?v=second00000', 1, '', 1)]
    def download(t):
        t.title, t.artist, t.date, t.cover = 'Second', 'Second artist', '2026-11-01', 'second.jpg'
    service.download = download
    monkeypatch.setattr(module, 'YouTubeService', lambda *a, **kw: service)
    asyncio.run(app.download_clicked(None))
    assert not app.album.is_playlist and app.album.title == ''
    result = effective_track(app.tracks[0], app.album)
    assert (result.artist, result.date, result.cover) == ('Second artist', '2026-11-01', 'second.jpg')


def test_playlist_album_skips_unrelated_singles():
    album = AlbumInfo(is_playlist=True, artist='Album artist', playlist_url='https://www.youtube.com/playlist?list=PL1')
    member = Track(artist='x', playlist_url='https://www.youtube.com/playlist?list=PL1')
    single = Track(artist='Solo')
    assert effective_track(member, album).artist == 'Album artist'
    assert effective_track(single, album).artist == 'Solo'
    assert effective_track(single, AlbumInfo(is_playlist=True, artist='Legacy')).artist == 'Legacy'


def test_export_that_empties_list_resets_album(tmp_path, monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'DATA', tmp_path / 'data')
    app = Studio(FakePage())
    app.album = AlbumInfo(is_playlist=True, title='Previous', artist='Previous artist')
    app.shared_cover_uid = 'abc'
    app.tracks = []
    assert app.reset_album_if_empty()
    assert app.album == AlbumInfo() and app.shared_cover_uid == ''
    app.update_album_summary()
    assert 'Previous' not in app.album_summary.value


def test_gain_filter_uses_limiter_only_when_needed():
    s = OutputSettings(folder='.', lufs=-14, true_peak=-1)
    assert gain_filter({'input_i': '-10', 'input_tp': '-0.5'}, s) == 'volume=-4.00dB'
    limited = gain_filter({'input_i': '-22', 'input_tp': '-2'}, s)
    assert limited.startswith('volume=8.00dB') and 'alimiter' in limited and 'level=0' in limited
    assert limiter_gain_reduction({'input_i': '-22', 'input_tp': '-2'}, s) == 7.5
    assert gain_filter({'input_i': '-inf', 'input_tp': '-inf'}, s) is None
    assert normalization_type('x {"normalization_type" : "dynamic"} y') == 'dynamic'
