"""Full per-video metadata; playlist owner is never used as the track artist."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs
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


def normalize_url(url, mode='auto'):
    p = urlparse(url.strip())
    if p.scheme not in ('http', 'https') or (p.hostname or '').lower() not in (
        'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'):
        raise ValueError('請輸入有效的 YouTube 網址')
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

    def entries(self, url, mode='auto'):
        url = normalize_url(url, mode)
        check_cancel(self.cancel)
        cached = self._cached('lists', url)
        if cached:
            info = cached['data']
            self.progress(f'讀取掃描快取：{cached["scanned_at"]}；如需新歌曲，請勾選重新掃描')
        else:
            # True leaves nested release playlists unopened.
            with yt_dlp.YoutubeDL(self.options(extract_flat=True, noplaylist=mode == 'single', ignoreerrors=True)) as ydl:
                info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError('無法取得影片或清單資訊')
            if 'entries' in info:
                info = {'title': info.get('title') or '', 'entries': [
                    {k: entry.get(k) for k in ('id', 'url', 'webpage_url', 'title', 'ie_key', 'playlist_count')}
                    if entry else None for entry in (info.get('entries') or [])]}
                self._save_scan('lists', url, info)
        if 'entries' not in info:
            self.playlist_info = {}
            return [(url, 1, '')]
        releases = urlparse(url).path.endswith('/releases')
        self.playlist_info = {'url': url, 'title': info.get('title') or '', 'is_releases': releases}
        result = []
        skipped = 0
        for i, entry in enumerate(info.get('entries') or [], 1):
            check_cancel(self.cancel)
            if not entry:
                self.progress(f'清單第 {i} 項無法解析，已保留重試位置')
                result.append(('', i, ''))
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
            result.append((item_url, i, (entry.get('title') or '') if releases else info.get('title') or ''))
        self.progress(f'清單解析完成：保留 {len(result)} 項，略過 {skipped} 個多首專輯')
        return result

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
            track.url = self.resolve_playlist_item(track.playlist_url, int(track.tracknumber))
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
            info = ydl.extract_info(url, download=False)
        if not info or 'entries' in info:
            raise ValueError('影片資訊為空或仍為清單')
        self._save_video(url, info)
        return metadata(info, index, album)

    def _save_video(self, url, info):
        # Missing dates must remain retryable. Only keep safe, stable metadata.
        if metadata(info).source_date:
            self._save_scan('videos', url, {k: info.get(k) for k in (
                'title', 'track', 'channel', 'uploader', 'album', 'upload_date',
                'timestamp', 'webpage_url', 'original_url')})

    def check_date(self, track):
        self.resolve_track(track)
        limits = DateRange(track.date_from, track.date_to)
        if not limits.active:
            return
        check_cancel(self.cancel)
        if not track.url and track.playlist_url:
            track.url = self.resolve_playlist_item(track.playlist_url, int(track.tracknumber))
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
            info = ydl.extract_info(track.url, download=True)
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
