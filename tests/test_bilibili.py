"""bilibili support: URL handling, multi-part titles, short links, friendly errors."""
import pytest
from core.youtube import (normalize_url, is_bilibili, channel_tab, metadata, friendly_error,
                          YouTubeService)

BV = 'BV1GJ411x7h7'


def test_bilibili_urls_are_cleaned_per_mode():
    shared = f'https://www.bilibili.com/video/{BV}/?spm_id_from=333.1007&vd_source=abc&p=3'
    assert normalize_url(shared) == f'https://www.bilibili.com/video/{BV}?p=3'
    assert normalize_url(shared, 'single') == f'https://www.bilibili.com/video/{BV}?p=3'
    assert normalize_url(shared, 'playlist') == f'https://www.bilibili.com/video/{BV}'
    assert normalize_url(f'https://m.bilibili.com/video/{BV}?share_source=copy') == f'https://www.bilibili.com/video/{BV}'
    assert normalize_url('https://www.bilibili.com/audio/au123', 'single') == 'https://www.bilibili.com/audio/au123'
    assert normalize_url('https://b23.tv/abcd123') == 'https://b23.tv/abcd123'
    with pytest.raises(ValueError, match='bilibili'):
        normalize_url('https://space.bilibili.com/123/video', 'single')
    with pytest.raises(ValueError, match='YouTube 或 bilibili'):
        normalize_url('https://example.com/video/BV1GJ411x7h7')
    assert not is_bilibili('https://notbilibili.com/x') and is_bilibili('https://space.bilibili.com/1')


def test_space_uploads_number_oldest_first_but_collections_keep_order():
    assert channel_tab('https://space.bilibili.com/123/video') == 'videos'
    assert channel_tab('https://space.bilibili.com/123/upload/video') == 'videos'
    assert channel_tab('https://space.bilibili.com/123/lists/456?type=season') == ''


def test_multi_part_title_uses_part_name():
    info = {'title': '2025 翻唱合集 p03 夜に駆ける', 'uploader': '某UP主', 'extractor_key': 'BiliBili',
            'upload_date': '20250102', 'webpage_url': f'https://www.bilibili.com/video/{BV}?p=3'}
    track = metadata(info, 3)
    assert track.title == '夜に駆ける' and track.artist == '某UP主' and track.date == '2025-01-02'
    assert track.original_title == info['title']
    # YouTube titles that merely look similar are untouched.
    assert metadata({'title': 'Song p01 live', 'extractor_key': 'Youtube'}).title == 'Song p01 live'


def test_multi_part_video_lists_each_part(tmp_path, monkeypatch):
    url = f'https://www.bilibili.com/video/{BV}'
    class Fake:
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def extract_info(self, u, download=False):
            assert u == url and self.opts['ignoreerrors'] is False
            return {'title': '合集', 'entries': [{'url': f'{url}?p={n}', 'title': f'p{n}'} for n in (1, 2)]}
    monkeypatch.setattr('core.youtube.yt_dlp.YoutubeDL', Fake)
    service = YouTubeService(tmp_path)
    entries = service.entries(url)
    assert [(e.url, e.index, e.album) for e in entries] == [(f'{url}?p=1', 1, '合集'), (f'{url}?p=2', 2, '合集')]
    assert not service.playlist_info['channel_tab']


def test_short_link_is_expanded(tmp_path, monkeypatch):
    target = f'https://www.bilibili.com/video/{BV}?p=2&spm_id_from=x'
    seen = []
    class Response:
        url = target
        def __enter__(self): return self
        def __exit__(self, *a): pass
    class Fake:
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def urlopen(self, u): return Response()
        def extract_info(self, u, download=False):
            seen.append(u); return {'title': 'one', 'uploader': 'UP'}
    monkeypatch.setattr('core.youtube.yt_dlp.YoutubeDL', Fake)
    entries = YouTubeService(tmp_path).entries('https://b23.tv/abcd123')
    assert seen == [f'https://www.bilibili.com/video/{BV}?p=2'] and entries[0].url == seen[0]


def test_bilibili_errors_explain_what_to_do():
    url = f'https://www.bilibili.com/video/{BV}'
    assert '412' in str(friendly_error(Exception('HTTP Error 412: Precondition Failed'), url))
    assert 'cookies.txt' in str(friendly_error(Exception('This video requires login'), url))
    original = Exception('HTTP Error 412')
    assert friendly_error(original, 'https://www.youtube.com/watch?v=abcdefghijk') is original
