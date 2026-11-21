import pytest
from core.youtube import YouTubeService
from core.models import Track, AlbumInfo, effective_track
from core.date_filter import OutsideDateRange
from core.scan_cache import ScanCache

ROOT = 'https://www.youtube.com/@clive9855/releases'
SINGLE = 'https://www.youtube.com/playlist?list=OLAKsingle'
MULTI = 'https://www.youtube.com/playlist?list=OLAKmulti'
VIDEO = 'https://www.youtube.com/watch?v=abcdefghijk'

def mock_ydl(monkeypatch, payloads, calls):
    class Fake:
        def __init__(self, options): self.options = options
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download=False):
            calls.append((url, self.options, download))
            value = payloads[url]
            if isinstance(value, Exception): raise value
            return value
    monkeypatch.setattr('core.youtube.yt_dlp.YoutubeDL', Fake)

def test_releases_skip_album_and_reuse_across_sessions(tmp_path, monkeypatch):
    calls=[]
    mock_ydl(monkeypatch, {
        ROOT: {'title':'releases', 'entries':[
            {'url': MULTI, 'playlist_count': 10},
            {'url': SINGLE, 'title': 'Single album'}]},
        SINGLE: {'playlist_count':1, 'entries':[{'url':VIDEO}]},
        VIDEO: {'title':'A', 'channel':'Artist Official', 'upload_date':'20260926'}
    }, calls)
    first=YouTubeService(tmp_path/'temp')
    assert first.entries(ROOT, 'playlist') == [(VIDEO, 2, 'Single album')]
    assert MULTI not in [x[0] for x in calls]
    assert calls[1][1]['playlistend'] == 2
    track=Track(url=VIDEO, date_from='2026-10', date_to='2026-10')
    with pytest.raises(OutsideDateRange): first.check_date(track)
    assert len(calls)==3
    second=YouTubeService(tmp_path/'other')
    assert second.entries(ROOT)==[(VIDEO,2,'Single album')]
    second.check_date(Track(url=VIDEO, date_from='2026-09',date_to='2026-09'))
    assert len(calls)==3  # no network for catalog, child resolution, or date
    third=YouTubeService(tmp_path/'third',refresh_scan=True)
    third.entries(ROOT)
    third.check_date(Track(url=VIDEO,date_from='2026-09',date_to='2026-09'))
    assert len(calls)==6

def test_unknown_count_stops_at_two_and_never_fetches_videos(tmp_path,monkeypatch):
    calls=[]
    mock_ydl(monkeypatch, {ROOT:{'entries':[{'url':MULTI}]},
        MULTI:{'entries':[{'url':VIDEO},{'url':VIDEO+'2'}]}},calls)
    service=YouTubeService(tmp_path)
    assert service.entries(ROOT)==[]
    assert len(calls)==2
    assert calls[1][1]['extract_flat'] is True
    assert calls[1][1]['playlistend']==2

def test_failed_release_and_missing_date_are_retryable(tmp_path,monkeypatch):
    calls=[]
    payloads={ROOT:{'entries':[{'url':SINGLE}]},SINGLE:ValueError('temporary failure')}
    mock_ydl(monkeypatch,payloads,calls)
    service=YouTubeService(tmp_path)
    assert service.entries(ROOT)==[(SINGLE,1,'')]
    payloads[SINGLE]={'entries':[{'url':VIDEO}]}
    payloads[VIDEO]={'title':'A'}
    track=Track(url=SINGLE,date_from='2026-09',date_to='2026-09')
    with pytest.raises(ValueError): service.check_date(track)
    assert track.url==VIDEO
    payloads[VIDEO]={'title':'A','upload_date':'20260901'}
    service.check_date(track)
    assert track.source_date=='2026-09-01'

def test_cache_corruption_and_only_safe_fields(tmp_path,monkeypatch):
    calls=[]
    mock_ydl(monkeypatch,{VIDEO:{'title':'A','upload_date':'20260901',
        'url':'signed-secret','http_headers':{'Cookie':'secret'}}},calls)
    service=YouTubeService(tmp_path)
    service.fetch_metadata(VIDEO)
    path=service.scan_cache.path('videos',VIDEO)
    assert 'secret' not in path.read_text()
    path.write_text('broken')
    service.fetch_metadata(VIDEO)
    assert len(calls)==2

def test_releases_do_not_share_first_track_fields():
    from ui.main_view import Studio
    from types import SimpleNamespace
    album=AlbumInfo(is_playlist=True,is_release_collection=True,cover_source_uid='__per_track__')
    track=Track(title='song',album='one',date='2026-09-26',cover='own.jpg')
    Studio._update_album_from_track(SimpleNamespace(album=album),track)
    assert album.title==album.date==album.cover==''
    assert effective_track(track,album).cover=='own.jpg'
