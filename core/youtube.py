"""YouTube and bilibili via yt-dlp. Full per-video metadata; playlist owner is never the track artist."""
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlparse, parse_qs, urlencode
import re
import threading
import shutil
import yt_dlp
from .models import Track
from .crop import open_image, auto_bbox
from .system import ROOT
from .scan_cache import ScanCache
from .text_cleanup import clean_artist, clean_title
from .date_filter import DateRange, OutsideDateRange

class Cancelled(Exception):
    pass


def check_cancel(event):
    if event.is_set():
        raise Cancelled('已停止')


class ListEntry(NamedTuple):
    """One playable list item. index is the track number; position is the source list slot."""
    url: str
    index: int
    album: str
    position: int = 0
    thumbnail: str = ''


# Channel tabs that YouTube orders newest first (releases by release date, videos by upload).
CHANNEL_TABS = ('releases', 'videos', 'streams', 'shorts')
NUMBERING = ('auto', 'source', 'oldest_first', 'newest_first')


def channel_tab(url):
    if is_bilibili(url):
        # space.bilibili.com/<uid>[/upload]/video lists uploads newest first.
        p = urlparse(url)
        return 'videos' if (p.hostname or '').startswith('space.') and re.fullmatch(
            r'/\d+(?:/upload)?(?:/video)?/?', p.path) else ''
    parts = urlparse(url).path.rstrip('/').split('/')
    return parts[-1] if len(parts) > 2 and parts[-1] in CHANNEL_TABS else ''


def card_thumbnail(thumbnails):
    """Largest artwork shown on the releases card."""
    def area(item):
        return (item.get('width') or 0) * (item.get('height') or 0)
    for item in sorted((t for t in thumbnails or [] if isinstance(t, dict) and t.get('url')),
                       key=area, reverse=True):
        url = item['url']
        if url.startswith('//'):
            url = 'https:' + url
        if not url.startswith(('https://', 'http://')):
            continue
        return url
    return ''


def thumbnail_candidates(url):
    """Ask Google image hosts for a larger copy first, then fall back to the card URL itself."""
    larger = re.sub(r'=w\d+-h\d+', '=w1200-h1200', url) if (
        'googleusercontent.com' in url or 'ggpht.com' in url) else url
    return [larger] if larger == url else [larger, url]


def renumber(result, numbering, tab):
    """Track numbers follow release order so new songs append instead of shifting old ones."""
    if numbering == 'auto':
        numbering = 'oldest_first' if tab else 'source'
    if numbering == 'source':
        return result
    total = len(result)
    return [entry._replace(index=(total - i if numbering == 'oldest_first' else i + 1))
            for i, entry in enumerate(result)]


YOUTUBE_HOSTS = ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be')
BILIBILI_SHORT_HOSTS = ('b23.tv', 'bili2233.cn')
BILIBILI_VIDEO = re.compile(r'/(?:s/)?video/((?:BV|bv)[0-9A-Za-z]{10}|(?:av|AV)\d+)')


def is_bilibili(url):
    host = (urlparse(url.strip()).hostname or '').lower()
    return (host == 'bilibili.com' or host.endswith('.bilibili.com') or
            host in BILIBILI_SHORT_HOSTS or host.endswith(tuple('.' + h for h in BILIBILI_SHORT_HOSTS)))


def normalize_bilibili(url, mode='auto'):
    """Drop share/tracking parameters; keep only the part number (?p=) of multi-part videos.

    auto: a link with ?p= is that one part, without it a multi-part video becomes a list.
    single: one part (part 1 when none given). playlist: every part of the video."""
    p = urlparse(url.strip())
    if (p.hostname or '').lower().endswith(BILIBILI_SHORT_HOSTS):
        return url.strip()  # resolved by YouTubeService.entries (needs a network redirect)
    video = BILIBILI_VIDEO.search(p.path)
    if not video:
        if mode == 'single' and '/audio/au' not in p.path:
            raise ValueError('單曲模式請貼入 bilibili 影片（BV／av）或音樂（au）網址')
        return url.strip()
    part = (parse_qs(p.query).get('p') or [''])[0]
    clean = 'https://www.bilibili.com/video/' + video.group(1)
    if part.isdigit() and int(part) > 0 and mode != 'playlist':
        clean += '?' + urlencode({'p': part})
    return clean


def normalize_url(url, mode='auto'):
    p = urlparse(url.strip())
    if p.scheme in ('http', 'https') and is_bilibili(url):
        return normalize_bilibili(url, mode)
    if p.scheme not in ('http', 'https') or (p.hostname or '').lower() not in YOUTUBE_HOSTS:
        raise ValueError('請輸入有效的 YouTube 或 bilibili 網址')
    q = parse_qs(p.query)
    if mode == 'playlist' and not q.get('list') and not p.path.rstrip('/').endswith('/releases'):
        raise ValueError('此網址沒有播放清單 list 參數')
    if mode != 'single' and q.get('list'):
        return 'https://www.youtube.com/playlist?list=' + q['list'][0]
    if mode == 'single' and q.get('v'):
        return 'https://www.youtube.com/watch?v=' + q['v'][0]
    if mode == 'single' and p.path == '/playlist':
        raise ValueError('單曲模式請貼入影片網址')
    if p.path.rstrip('/').endswith('/releases'):
        if mode == 'single':
            raise ValueError('releases 頁面請使用自動或清單模式')
        return 'https://www.youtube.com' + p.path.rstrip('/')
    return url.strip()


def metadata(info, index=1, album='', auto_clean_titles=True):
    date = ''
    # upload_date corresponds to the video's publication/upload date, not album release.
    raw = info.get('upload_date')
    if raw:
        try:
            date = datetime.strptime(str(raw), '%Y%m%d').strftime('%Y-%m-%d')
        except ValueError:
            pass
    if not date and info.get('timestamp') is not None:
        try:
            date = datetime.fromtimestamp(float(info['timestamp']), timezone.utc).strftime('%Y-%m-%d')
        except (ValueError, OSError, OverflowError):
            pass
    original_artist = info.get('channel') or info.get('uploader') or ''
    artist = clean_artist(original_artist)
    raw_title = info.get('track') or info.get('title') or ''
    if auto_clean_titles and not info.get('track'):
        raw_title = bilibili_part_title(info, raw_title)
    title = clean_title(raw_title, artist) if auto_clean_titles and not info.get('track') else raw_title
    warnings = []
    if not date:
        warnings.append('YouTube 未提供發布日期，請手動填入')
    if not artist:
        warnings.append('YouTube 未提供頻道名稱，請手動填入')
    return Track(url=info.get('webpage_url') or info.get('original_url') or '',
                 title=title, artist=artist,
                 original_title=info.get('title') or raw_title, original_artist=original_artist,
                 original_albumartist=original_artist, source_date=date,
                 albumartist=artist, album=info.get('album') or album,
                 tracknumber=str(index), date=date, warning='；'.join(warnings))


def bilibili_part_title(info, title):
    """yt-dlp names multi-part videos '<video title> p03 <part name>'; a song needs the part name."""
    if 'bilibili' not in str(info.get('extractor_key') or info.get('webpage_url') or '').lower():
        return title
    match = re.fullmatch(r'.+ p\d{2,} (.+)', title or '')
    return match.group(1).strip() if match and match.group(1).strip() else title


def friendly_error(exc, url=''):
    """Explain bilibili's common refusals instead of showing a raw yt-dlp trace."""
    text = str(exc)
    if not is_bilibili(url):
        return exc
    if '412' in text or 'rate limit' in text.lower():
        return ValueError('bilibili 暫時拒絕請求（412／頻率限制），請稍候再重試；'
                          '或在「下載設定」指定已登入的 cookies.txt')
    if 'login' in text.lower() or '-403' in text or '大会员' in text or '大會員' in text:
        return ValueError('此 bilibili 內容需要登入或大會員，請在「下載設定」指定 cookies.txt')
    return exc


class YouTubeService:
    def __init__(self, cache, cancel=None, progress=None, cookiefile='', scan_cache_dir=None, refresh_scan=False):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.cancel = cancel or threading.Event()
        self.progress = progress or (lambda msg: None)
        self.cookiefile = cookiefile
        self.playlist_info = {}
        self.scan_cache = ScanCache(scan_cache_dir)
        self.refresh_scan = refresh_scan
        self._refreshed_dates = set()

    def options(self, **extra):
        opts = dict(quiet=True, no_warnings=False, socket_timeout=25, retries=3,
                    fragment_retries=3, noplaylist=True,
                    js_runtimes={'deno': {}, 'node': {}})
        if self.cookiefile:
            opts['cookiefile'] = self.cookiefile
        opts.update(extra)
        return opts

    def _cached(self, kind, url):
        return None if self.refresh_scan else self.scan_cache.read(kind, url)

    def _save_scan(self, kind, url, data):
        try:
            self.scan_cache.write(kind, url, data)
        except OSError as exc:
            self.progress(f'掃描快取無法寫入（本次仍繼續）：{exc}')

    @staticmethod
    def item_url(entry):
        url = entry.get('webpage_url') or entry.get('url') or ''
        if url.startswith(('https://', 'http://')):
            return url
        identity = entry.get('id') or url
        if identity and len(identity) == 11:
            return 'https://www.youtube.com/watch?v=' + identity
        if identity and (identity.startswith(('PL', 'OLAK', 'UU', 'RD')) or
                         entry.get('ie_key') == 'YoutubeTab'):
            return 'https://www.youtube.com/playlist?list=' + identity
        return ''

    def entries(self, url, mode='auto', numbering='auto'):
        url = normalize_url(url, mode)
        check_cancel(self.cancel)
        releases = urlparse(url).path.endswith('/releases')
        if (urlparse(url).hostname or '').lower().endswith(BILIBILI_SHORT_HOSTS):
            url = normalize_url(self.expand_short_url(url), mode)
        cached = self._cached('lists', url)
        if cached and releases and not any(
                isinstance(e, dict) and 'thumbnails' in e for e in cached['data'].get('entries') or []):
            # Caches from v1.8.0 lack release-card artwork; re-read the catalogue page once.
            cached = None
        if cached:
            info = cached['data']
            self.progress(f'讀取掃描快取：{cached["scanned_at"]}；如需新歌曲，請勾選重新掃描')
        else:
            # True leaves nested release playlists unopened.
            try:
                with yt_dlp.YoutubeDL(self.options(extract_flat=True, noplaylist=mode == 'single',
                                                   ignoreerrors=not is_bilibili(url))) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception as exc:
                raise friendly_error(exc, url) from exc
            if not info:
                raise ValueError('無法取得影片或清單資訊')
            if 'entries' in info:
                info = {'title': info.get('title') or '', 'entries': [
                    {**{k: entry.get(k) for k in ('id', 'url', 'webpage_url', 'title', 'ie_key', 'playlist_count')},
                     'thumbnails': [{k: t.get(k) for k in ('url', 'width', 'height')}
                                    for t in entry.get('thumbnails') or [] if isinstance(t, dict) and t.get('url')]}
                    if entry else None for entry in (info.get('entries') or [])]}
                self._save_scan('lists', url, info)
        if 'entries' not in info:
            self.playlist_info = {}
            return [ListEntry(url, 1, '', 1)]
        tab = channel_tab(url)
        self.playlist_info = {'url': url, 'title': info.get('title') or '', 'is_releases': releases,
                              'channel_tab': tab}
        result = []
        skipped = 0
        for i, entry in enumerate(info.get('entries') or [], 1):
            check_cancel(self.cancel)
            if not entry:
                self.progress(f'清單第 {i} 項無法解析，已保留重試位置')
                result.append(ListEntry('', i, '', i))
                continue
            item_url = self.item_url(entry)
            if releases and parse_qs(urlparse(item_url).query).get('list'):
                try:
                    item_url = self.resolve_single_release(item_url, entry.get('playlist_count'))
                except OutsideDateRange as exc:
                    skipped += 1
                    self.progress(f'略過 {entry.get("title") or i}：{exc}')
                    continue
                except Cancelled:
                    raise
                except Exception as exc:
                    self.progress(f'單曲清單第 {i} 項解析失敗，可重試：{exc}')
                    # Keep release URL so a retry can resolve this exact card.
            result.append(ListEntry(item_url, i, (entry.get('title') or '') if releases else info.get('title') or '',
                                    i, card_thumbnail(entry.get('thumbnails')) if releases else ''))
        result = renumber(result, numbering if numbering in NUMBERING else 'auto', tab)
        self.progress(f'清單解析完成：保留 {len(result)} 項，略過 {skipped} 個多首專輯')
        return result

    def expand_short_url(self, url):
        """b23.tv share links only redirect; follow once to the real bilibili page."""
        check_cancel(self.cancel)
        try:
            with yt_dlp.YoutubeDL(self.options()) as ydl, ydl.urlopen(url) as response:
                target = response.url
        except Exception as exc:
            raise ValueError(f'無法展開 bilibili 短網址：{exc}') from exc
        if not is_bilibili(target) or (urlparse(target).hostname or '').endswith(BILIBILI_SHORT_HOSTS):
            raise ValueError('短網址沒有指向 bilibili 頁面')
        return target

    def resolve_single_release(self, url, count=None):
        check_cancel(self.cancel)
        try:
            known_multi = int(count or 0) > 1
        except (ValueError, TypeError):
            known_multi = False
        cached = self._cached('releases', url)
        if known_multi:
            self._save_scan('releases', url, {'multiple': True, 'count': count})
            raise OutsideDateRange('專輯超過一首，略過')
        if cached:
            data = cached['data']
        else:
            # Read at most two flat entries; never request album tracks' metadata/audio.
            with yt_dlp.YoutubeDL(self.options(extract_flat=True, noplaylist=False,
                                             playlistend=2, ignoreerrors=False)) as ydl:
                info = ydl.extract_info(url, download=False)
            if not info or 'entries' not in info:
                raise ValueError('無法判斷專輯首數，請重試')
            entries = list(info.get('entries') or [])
            total = info.get('playlist_count')
            try:
                multiple = len(entries) > 1 or int(total or 0) > 1
            except (ValueError, TypeError):
                multiple = len(entries) > 1
            if multiple:
                data = {'multiple': True, 'count': total or len(entries)}
            elif len(entries) == 1 and entries[0] and self.item_url(entries[0]):
                video = self.item_url(entries[0])
                if parse_qs(urlparse(video).query).get('list'):
                    raise ValueError('單曲清單仍指向另一個清單，無法判讀')
                data = {'multiple': False, 'video_url': video}
            else:
                raise ValueError('單曲清單為空或無法讀取，請重試')
            self._save_scan('releases', url, data)
        if data.get('multiple'):
            raise OutsideDateRange('專輯超過一首，略過')
        return data['video_url']

    def resolve_track(self, track):
        if not track.url and track.playlist_url:
            track.url = self.resolve_playlist_item(track.playlist_url, track.source_position())
        if parse_qs(urlparse(track.url).query).get('list'):
            track.url = self.resolve_single_release(track.url)

    def resolve_playlist_item(self, playlist_url, index):
        """Re-read just one playlist position when the flat listing was incomplete."""
        if not playlist_url or index < 1:
            raise ValueError('這首歌曲沒有可重新解析的播放清單位置')
        check_cancel(self.cancel)
        with yt_dlp.YoutubeDL(self.options(extract_flat=True, noplaylist=False,
                                         playlist_items=str(index), ignoreerrors=True)) as ydl:
            info = ydl.extract_info(normalize_url(playlist_url, 'auto'), download=False)
        entries = (info or {}).get('entries') or []
        entry = next((item for item in entries if item), None)
        if not entry:
            raise ValueError(f'清單第 {index} 首仍無法解析，請稍後再試或手動填入影片網址')
        item_url = self.item_url(entry)
        if not item_url:
            raise ValueError('清單沒有提供該首影片網址')
        if parse_qs(urlparse(item_url).query).get('list'):
            return self.resolve_single_release(item_url)
        return item_url

    def fetch_metadata(self, url, index=1, album=''):
        check_cancel(self.cancel)
        cached = self._cached('videos', url)
        if cached:
            self.progress('使用已儲存的影片日期與資訊')
            return metadata(cached['data'], index, album)
        with yt_dlp.YoutubeDL(self.options()) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as exc:
                raise friendly_error(exc, url) from exc
        if not info or 'entries' in info:
            raise ValueError('影片資訊為空或仍為清單')
        self._save_video(url, info)
        return metadata(info, index, album)

    def _save_video(self, url, info):
        # Missing dates must remain retryable. Only keep safe, stable metadata.
        if metadata(info).source_date:
            self._save_scan('videos', url, {k: info.get(k) for k in (
                'title', 'track', 'channel', 'uploader', 'album', 'upload_date',
                'timestamp', 'webpage_url', 'original_url', 'extractor_key')})

    def check_date(self, track):
        self.resolve_track(track)
        limits = DateRange(track.date_from, track.date_to)
        if not limits.active:
            return
        check_cancel(self.cancel)
        if not track.url and track.playlist_url:
            track.url = self.resolve_playlist_item(track.playlist_url, track.source_position())
        self.progress(f'檢查第 {track.tracknumber} 首發表日期…')
        # Never use the editable album/tag date as evidence of publication date.
        if not track.source_date or (self.refresh_scan and track.url not in self._refreshed_dates):
            fresh = self.fetch_metadata(track.url, track.tracknumber or 1, track.album)
            track.source_date = fresh.source_date
            self._refreshed_dates.add(track.url)
        if not limits.contains(track.source_date):
            raise OutsideDateRange(f'{track.source_date} 不在指定區間')

    def download(self, track):
        check_cancel(self.cancel)
        self.resolve_track(track)
        if not track.url:
            raise ValueError('請先為此歌曲填入影片網址')
        self.check_date(track)
        track.download_attempts += 1
        folder = self.cache / track.uid
        folder.mkdir(exist_ok=True)
        def hook(data):
            check_cancel(self.cancel)
            if data.get('status') == 'downloading':
                total = data.get('total_bytes') or data.get('total_bytes_estimate')
                if total:
                    self.progress(f'{track.title or track.url}：下載 {data.get("downloaded_bytes", 0)/total:.0%}')
        with yt_dlp.YoutubeDL(self.options(format='bestaudio/best', outtmpl=str(folder/'source.%(ext)s'),
                                         progress_hooks=[hook])) as ydl:
            try:
                info = ydl.extract_info(track.url, download=True)
            except Exception as exc:
                raise friendly_error(exc, track.url) from exc
            check_cancel(self.cancel)
            if not info or 'entries' in info:
                raise ValueError('下載未返回單首音訊')
            self._save_video(track.url, info)
            requests = info.get('requested_downloads') or []
            path = requests[0].get('filepath') if requests else None
            path = path or ydl.prepare_filename(info)
            if not Path(path).is_file():
                raise FileNotFoundError('下載後找不到音訊檔')
            track.source = str(Path(path).resolve())
            fresh = metadata(info, track.tracknumber or 1, track.album, track.auto_clean_titles)
            for name in ('original_title', 'original_artist', 'original_albumartist', 'source_date'):
                if not getattr(track, name):
                    setattr(track, name, getattr(fresh, name))
            # Initial pending records only. Retrying a download preserves user edits.
            if track.status == '待下載' or not track.title:
                for name in ('title', 'artist', 'albumartist', 'album', 'date', 'warning'):
                    setattr(track, name, getattr(fresh, name))
            thumbs = sorted(info.get('thumbnails') or [], key=lambda x: (x.get('width') or 0)*(x.get('height') or 0), reverse=True)
            if info.get('thumbnail'):
                thumbs.append({'url': info['thumbnail']})
            if track.release_thumbnail:
                # The releases card artwork can differ from a later-updated MV thumbnail.
                thumbs[:0] = [{'url': u, 'release': True} for u in thumbnail_candidates(track.release_thumbnail)]
            for thumb in thumbs:
                check_cancel(self.cancel)
                try:
                    with ydl.urlopen(thumb['url']) as response:
                        data = response.read(20*1024*1024+1)
                    if len(data) > 20*1024*1024:
                        continue
                    raw = folder/'thumbnail.raw'
                    raw.write_bytes(data)
                    im = open_image(raw)
                    if min(im.size) < 90:
                        continue
                    cover = folder/'cover.jpg'
                    im.save(cover, 'JPEG', quality=98, subsampling=0)
                    track.cover = str(cover.resolve())
                    track.bbox = auto_bbox(cover)
                    track.cover_origin = 'releases' if thumb.get('release') else 'video'
                    break
                except Exception as exc:
                    self.progress(f'封面候選讀取失敗：{exc}')
            if not track.cover:
                track.warning += '；封面下載失敗，可手動選圖'
        track.status = '待確認'
        return track


def clean_download(track):
    """Remove only this application's completed download folder."""
    root = (ROOT / 'temp').resolve()
    target = (root / track.uid).resolve()
    if target.parent != root or not target.is_dir():
        return False
    if not track.source or not Path(track.source).resolve().is_relative_to(target):
        return False
    shutil.rmtree(target)
    return True
