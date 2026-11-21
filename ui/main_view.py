"""Flet 0.21.2 UI. Slow work runs in a worker; control updates stay on the event loop."""
from datetime import datetime
import asyncio
import copy
import logging
from pathlib import Path
import queue
import threading
import time
from functools import partial
import flet as ft
from core.models import Track, AlbumInfo, effective_track, save_project, load_session
from core.system import DATA, ROOT, ffmpeg_path, writable_directory, temp_directory
from core.youtube import YouTubeService, Cancelled, check_cancel, clean_download
from core.crop import auto_bbox, preview, save_cover
from core.audio import OutputSettings, export_track, validate_track, measure
from core.tags import import_audio
from .crop_editor import show_crop_editor
from .batch_actions import BatchActions
from .dialogs import open_dialog, close_dialog
from .track_thumbnail import track_thumbnail
from .loudness_dialog import show_loudness_dialog
from .naming_editor import show_naming_editor
from .cover_selector import show_cover_selector
from .album_view import AlbumView, PER_TRACK_COVER
from core.naming import album_directory
from core.text_cleanup import clean_artist, clean_title
from core.date_filter import DateRange, OutsideDateRange


# Same button colors as the user's original Flet application.
COLOR_PRIMARY = '#28a745'
COLOR_SECONDARY = '#5bc0de'
COLOR_WARNING = '#f0ad4e'
COLOR_PURPLE = '#6f42c1'


def panel_height(viewport_height):
    # Reserve space for the toolbar, track list and status; only pane contents scroll.
    return max(230, min(360, float(viewport_height or 850) * .47))


def list_height(count, viewport_height, viewport_width=1200):
    # The live list expands; this helper estimates its available height for older callers.
    return min(max(50, count * 48), max(50, float(viewport_height or 850) - panel_height(viewport_height) - 215))


def action_button(text, icon, handler, color=COLOR_PRIMARY):
    return ft.ElevatedButton(text, icon=icon, on_click=handler, bgcolor=color,
        color='white', height=34,
        style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=12),
                             shape=ft.RoundedRectangleBorder(radius=6)))


OUTPUT_DIRECTORY = r'E:\download'
APP_VERSION = '1.9.0'


class Studio(BatchActions):
    def __init__(self, page):
        self.page, self.tracks = page, []
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.busy = False
        self.cache = ROOT/'temp'
        self.project = DATA/'session.json'
        self.album = AlbumInfo()
        self.cache.mkdir(parents=True, exist_ok=True)
        page.title = f'EZ Audio Studio v{APP_VERSION}'
        page.theme_mode = ft.ThemeMode.LIGHT
        page.theme = ft.Theme(color_scheme_seed=COLOR_PRIMARY)
        page.padding = 12
        page.spacing = 0
        page.window_min_width, page.window_min_height = 860, 640
        page.window_width, page.window_height = self.window_size()
        page.on_resize = self.resize
        self.date_from = ''
        self.date_to = ''
        self.auto_clean_titles = True
        self.refresh_scan = False
        self.numbering = 'auto'
        self.download_options_btn = ft.OutlinedButton('下載設定', height=40,
            icon=ft.icons.DATE_RANGE, on_click=self.download_options_clicked)
        self.url = ft.TextField(label='YouTube 單曲／播放清單網址', expand=True)
        self.mode = ft.Dropdown(label='網址模式', value='auto', width=140,
            options=[ft.dropdown.Option('auto', '自動／含清單'), ft.dropdown.Option('single', '只取單曲'), ft.dropdown.Option('playlist', '整份清單')])
        self.folder = ft.TextField(label='輸出位置（預設 E:\\download，可自行修改）', value=OUTPUT_DIRECTORY, expand=True)
        self.format = ft.Dropdown(label='格式', value='mp3', width=100, options=[ft.dropdown.Option(x) for x in ('mp3','m4a','flac','wav')])
        self.bitrate = ft.Dropdown(label='位元率', value='320k', width=110, options=[ft.dropdown.Option(x) for x in ('128k','192k','256k','320k')])
        self.rate = ft.Dropdown(label='取樣率', value='48000', width=120, options=[ft.dropdown.Option(x) for x in ('44100','48000')])
        self.size = ft.Dropdown(label='封面像素', value='720', width=120,
            options=[ft.dropdown.Option('0', '裁切原尺寸'), *[ft.dropdown.Option(x, x+'×'+x) for x in ('540','720','1080')]])
        self.norm = ft.Checkbox(label='自動校正響度', value=True)
        self.lufs = ft.TextField(label='LUFS', value='-14', width=95)
        self.peak = ft.TextField(label='True peak', value='-1', width=105)
        self.loudness_mode = ft.Dropdown(label='校正方式', value='gain', width=150,
            tooltip='增益＋限幅：整首只調一個音量，峰值超出才限幅，並驗證後再微調一次。\n'
                    'loudnorm：舊方式，部分歌曲會被自動改成動態壓縮，音量可能不一致。',
            options=[ft.dropdown.Option('gain', '增益＋限幅'), ft.dropdown.Option('loudnorm', 'loudnorm（舊）')])
        self.naming = ft.TextField(label='檔名格式', value='<曲目歌星>-<標題>', expand=True)
        self.shared_cover_uid = ''
        self.shared_cover_backup = None
        self.cover_description = ft.Text('封面來源：各歌曲自己的封面')
        self.embed = ft.Checkbox(label='將封面內嵌到音檔', value=True)
        self.extra_covers = ft.Dropdown(label='另外輸出 JPG', value='none', width=245,
            options=[ft.dropdown.Option('none','不另存圖片（只有音檔）'),
                     ft.dropdown.Option('single','只存一張共用 Cover.jpg'),
                     ft.dropdown.Option('per_track','每首各存一張 JPG')])
        self.reports = ft.Checkbox(label='另外輸出每首 JSON 報告',value=False)
        self.auto_clear = ft.Checkbox(label='成功轉檔後自動清理清單', value=True)
        self.album_group = ft.Checkbox(label='多首清單放進 [YYMMDD] 歌手-專輯 子資料夾', value=False)
        self.status = ft.Text('貼入網址先下載，修改資料後再確認輸出。', selectable=True,
                              expand=True, size=12, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.bar = ft.ProgressBar(value=0, height=3)
        self.items = ft.ListView(spacing=3, expand=True)
        self.count = ft.Text('0 個項目')
        self.log = ft.TextField(label='處理紀錄', multiline=True, read_only=True, min_lines=3, max_lines=8)
        self.download_btn = action_button('下載音訊與封面', ft.icons.DOWNLOAD, self.download_clicked)
        self.output_btn = action_button('確認並輸出', ft.icons.SAVE_ALT, self.output_clicked)
        self.stop_btn = ft.TextButton('停止', icon=ft.icons.STOP, height=40, style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10, vertical=4)), disabled=True, on_click=lambda e: self.cancel.set())
        self.audio_picker = ft.FilePicker(on_result=self.audio_picked)
        self.image_picker = ft.FilePicker(on_result=self.images_picked)
        self.cover_picker = ft.FilePicker(on_result=self.cover_picked)
        self.project_picker = ft.FilePicker(on_result=self.project_picked)
        self.folder_picker = ft.FilePicker(on_result=self.folder_picked)
        self.cookies_picker = ft.FilePicker(on_result=self.cookies_picked)
        self.cookiefile = ''
        self.cover_target = None
        page.overlay.extend([self.audio_picker, self.image_picker, self.cover_picker, self.project_picker, self.cookies_picker, self.folder_picker])
        for control in (self.url, self.mode, self.folder, self.format, self.bitrate,
                        self.rate, self.size, self.lufs, self.peak, self.loudness_mode, self.naming, self.extra_covers):
            control.dense = True
            control.height = 42
            control.text_size = 13
            control.content_padding = ft.padding.symmetric(horizontal=10, vertical=10)
        for control in (self.format, self.bitrate, self.rate, self.size):
            control.width = None
            control.expand = True
        for control in (self.norm, self.embed, self.reports, self.auto_clear, self.album_group):
            control.height = 30
        self.album_group.label = '建立 [YYMMDD] 歌手-專輯 資料夾'
        self.folder.label = '輸出位置'
        self.folder.tooltip = r'預設 E:\download，可輸入或選取其他資料夾'
        self.embed.label = '內嵌封面'
        self.extra_covers.width = None
        self.extra_covers.expand = True
        self.album_editing = False
        self.toolbar_row = ft.Row([
            self.download_btn,
            action_button('重試失敗項目', ft.icons.REFRESH, self.retry_failed_clicked, COLOR_WARNING),
            action_button('匯入音檔', ft.icons.AUDIO_FILE,
                lambda e: self.audio_picker.pick_files(allow_multiple=True, allowed_extensions=['mp3','m4a','flac','wav','ogg','opus','webm']), '#455A64'),
            action_button('匯入圖片', ft.icons.IMAGE,
                lambda e: self.image_picker.pick_files(allow_multiple=True, allowed_extensions=['jpg','jpeg','png','webp']), COLOR_SECONDARY),
            action_button('網址標籤配對', ft.icons.AUTO_AWESOME, self.match_clicked, COLOR_WARNING),
            action_button('清空清單', ft.icons.CLEAR_ALL, self.clear_all, '#607D8B'),
            action_button('響度調整', ft.icons.VOLUME_UP, partial(show_loudness_dialog, self), '#607D8B'),
            self.add_batch_tools(),
        ], spacing=8, wrap=False, scroll=ft.ScrollMode.AUTO, height=42)
        self.toolbar = ft.Column([self.toolbar_row, ft.Row([self.url, self.mode, self.download_options_btn], spacing=8)], spacing=8)
        self.settings_panel = ft.Column([
            ft.Row([self.format, self.bitrate, self.rate, self.size], spacing=7),
            ft.Row([self.naming, ft.OutlinedButton('檔名／預覽', height=34, on_click=self.naming_clicked)], spacing=7),
            ft.Row([self.folder, ft.IconButton(ft.icons.FOLDER_OPEN, tooltip='選擇輸出位置',
                on_click=lambda e: self.folder_picker.get_directory_path())], spacing=4),
            self.album_group, self.auto_clear,
        ], spacing=9, scroll=ft.ScrollMode.AUTO, expand=True)
        self.album_summary = ft.Text('尚未匯入清單', selectable=True, size=13,
            max_lines=3, overflow=ft.TextOverflow.ELLIPSIS)
        self.cover_preview = ft.Image(width=80, height=80, visible=False, fit=ft.ImageFit.CONTAIN)
        self.misc_panel = ft.Column([
            ft.Row([self.cover_preview, ft.Column([self.album_summary,
                ft.Row([ft.TextButton('編輯資訊／封面', icon=ft.icons.EDIT, height=40,
                                     style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10, vertical=4)),
                                     on_click=self.open_album_tab),
                        ft.TextButton('歌曲詳情', height=40, style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10, vertical=4)), on_click=self.edit_selected)], spacing=0)
                ], expand=True, spacing=2)], spacing=10),
            ft.Row([self.norm, self.lufs, self.peak, self.loudness_mode], spacing=8, wrap=True),
            ft.Row([self.embed, self.extra_covers], spacing=8),
            self.reports,
            ft.TextButton('只存圖片（不產生音檔）', icon=ft.icons.IMAGE,
                          height=40, style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10, vertical=4)), on_click=self.covers_clicked),
        ], spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        self.album_view = AlbumView(self)
        self.overview = ft.Column([
            ft.Text('資訊與其他選項', size=17, weight=ft.FontWeight.BOLD),
            self.misc_panel,
        ], spacing=10, expand=True)
        self.left_content = ft.AnimatedSwitcher(content=self.overview, expand=True,
            duration=180, reverse_duration=180, transition=ft.AnimatedSwitcherTransition.FADE)
        self.left_sheet = ft.Container(content=self.left_content, expand=True, padding=12,
            bgcolor='#F2F5F7', border=ft.border.all(1, '#DCE3E8'), border_radius=8)
        self.right_sheet = ft.Container(content=ft.Column([
            ft.Text('音訊輸出', size=17, weight=ft.FontWeight.BOLD), self.settings_panel,
            ft.Row([self.output_btn], alignment=ft.MainAxisAlignment.END)
        ], spacing=10, expand=True), expand=True, padding=12,
            bgcolor='#F2F5F7', border=ft.border.all(1, '#DCE3E8'), border_radius=8)
        self.bottom_row = ft.Row([self.left_sheet, self.right_sheet], spacing=10,
            height=panel_height(page.height), vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        self.list_header = ft.Row([
            ft.Text('封面 / 曲號 / 標題 / 目前響度（LUFS）', size=13, weight=ft.FontWeight.BOLD, expand=True),
            self.count,
            ft.TextButton('全選', height=40, style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10, vertical=4)), on_click=lambda e: self.select_all(True)),
            ft.TextButton('全不選', height=40, style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10, vertical=4)), on_click=lambda e: self.select_all(False)),
            ft.IconButton(ft.icons.DELETE_OUTLINE, tooltip='移除勾選', icon_size=19,
                          width=40, height=40, on_click=self.remove_selected)
        ], spacing=3, height=44)
        self.workspace = ft.Column([self.list_header, self.items, self.bottom_row], spacing=8, expand=True)
        self.body = ft.Column([
            ft.Row([ft.Text('EZ Audio Studio', size=22, weight=ft.FontWeight.BOLD),
                    ft.Text(f'v{APP_VERSION}', size=12, color='#64748B')], height=30),
            self.toolbar, self.workspace,
            ft.Row([self.stop_btn, self.status,
                ft.TextButton('處理紀錄', icon=ft.icons.RECEIPT_LONG, height=40,
                              style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10, vertical=4)),
                              on_click=self.show_log)], height=44, spacing=8), self.bar,
        ], spacing=8, expand=True)
        page.add(self.body)
        self.render()

    @staticmethod
    def window_size():
        try:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
            return min(1250, rect.right-rect.left-30), min(920, rect.bottom-rect.top-40)
        except (AttributeError, OSError):
            return 1200, 850

    def download_options_clicked(self, e=None):
        start = ft.TextField(label='發表日期起（留白不限）', value=self.date_from,
                             hint_text='2026-09 或 2026-09-01', expand=True)
        end = ft.TextField(label='發表日期迄（留白不限）', value=self.date_to,
                           hint_text='2026-09 或 2026-09-30', expand=True)
        # DatePickers live in overlay so the settings dialog stays in place.
        for picker in getattr(self, '_date_pickers', []):
            if picker in self.page.overlay:
                self.page.overlay.remove(picker)
        self._date_pickers = []
        calendar_buttons = []
        def calendar(field, is_end):
            def selected(e):
                if picker.value:
                    field.value = picker.value.strftime('%Y-%m-%d')
                    error.value = ''
                    self.page.update()
            picker = ft.DatePicker(first_date=datetime(1900, 1, 1),
                last_date=datetime(2100, 12, 31), on_change=selected,
                help_text='選擇結束日期' if is_end else '選擇開始日期',
                cancel_text='取消', confirm_text='確定')
            self._date_pickers.append(picker)
            self.page.overlay.append(picker)
            def choose(e):
                try:
                    bounds = DateRange('', field.value or '').bounds() if is_end else DateRange(field.value or '', '').bounds()
                    value = bounds[1] if is_end else bounds[0]
                    picker.value = datetime.combine(value, datetime.min.time()) if value else datetime.now()
                except ValueError:
                    picker.value = datetime.now()
                picker.pick_date()
            calendar_buttons.append(ft.IconButton(ft.icons.CALENDAR_MONTH, tooltip='選擇結束日期' if is_end else '選擇開始日期', on_click=choose))
        calendar(start, False)
        calendar(end, True)
        cleanup = ft.Checkbox(label='下載時自動整理歌曲標題（可在歌曲詳情還原）',
                              value=self.auto_clean_titles)
        refresh = ft.Checkbox(label='重新掃描網址與日期（忽略舊快取，取得新增／變更內容）', value=self.refresh_scan)
        numbering = ft.Dropdown(label='曲目編號', value=self.numbering, dense=True, options=[
            ft.dropdown.Option('auto', '自動：頻道 releases／videos 依發行時間（最舊為 1），清單照原順序'),
            ft.dropdown.Option('oldest_first', '依發行時間：最舊為 1（新歌接在最後）'),
            ft.dropdown.Option('newest_first', '依發行時間：最新為 1'),
            ft.dropdown.Option('source', '照來源清單順序')])
        error = ft.Text(color='red')
        def clear(e):
            start.value, end.value = '', ''
            error.value = ''
            self.page.update()
        def save(e):
            try:
                limits = DateRange(start.value or '', end.value or '')
                first, last = limits.bounds()
                self.date_from = first.isoformat() if first else ''
                self.date_to = last.isoformat() if last else ''
                self.auto_clean_titles = bool(cleanup.value)
                self.refresh_scan = bool(refresh.value)
                self.numbering = numbering.value or 'auto'
                self.download_options_btn.text = '下載設定（日期已限）' if limits.active else '下載設定'
                self.download_options_btn.tooltip = f'{self.date_from or "不限"} ～ {self.date_to or "不限"}'
                close_dialog(self.page, dialog)
            except ValueError as exc:
                error.value = str(exc)
                self.page.update()
        dialog = ft.AlertDialog(modal=True, title=ft.Text('下載設定'),
            content=ft.Container(width=650, content=ft.Column([
                ft.Text('點選日期欄右側日曆選擇起訖日（含當日）；留白不限，也可手動輸入。'),
                ft.Row([start, calendar_buttons[0], end, calendar_buttons[1]]), ft.TextButton('清除日期限制', on_click=clear),
                ft.Text('依 YouTube 發表／上傳日期篩選，不採用手動編輯的專輯日期。'),
                ft.Text('清單日期不完整時需逐首查詢資訊，只有符合日期的影片才下載音訊。'),
                ft.Text("掃描結果自動存入程式資料夾下的 list 資料夾；下次相同網址直接重用。"),
                ft.Text("releases 頁面只讀單曲；多首專輯略過。首數未知時最多讀前兩個項目。"),
                ft.Text('頻道 releases／videos 頁由新到舊排列；依發行時間編號時以頁面順序推算，'
                        '重新掃描後已在清單的歌曲也會更新曲號（手動改過曲號者保留）。'),
                numbering, cleanup, refresh, error,
            ], tight=True, scroll=ft.ScrollMode.AUTO)),
            actions=[ft.TextButton('取消', on_click=lambda e: close_dialog(self.page, dialog)),
                     ft.FilledButton('套用設定', on_click=save)])
        open_dialog(self.page, dialog)

    def resize(self, e=None):
        self.bottom_row.height = panel_height(self.page.height)
        self.page.update()

    def open_album_tab(self, e=None):
        if self.busy:
            return
        if not self.album_editing:
            self.album_view.sync()
        self.album_editing = True
        self.left_content.content = self.album_view.content
        self.page.update()

    def close_album_editor(self, e=None):
        # Return behaves like leaving the original volume page: keep edited values.
        try:
            self.album_view.save(silent=True, apply_to_tracks=self.album_editing)
        except ValueError:
            return
        self.album_editing = False
        self.left_content.content = self.overview
        self.page.update()

    def show_log(self, e=None):
        dialog = ft.AlertDialog(title=ft.Text('處理紀錄'),
            content=ft.Container(width=720, height=360,
                content=ft.Column([self.log], scroll=ft.ScrollMode.AUTO)),
            actions=[ft.TextButton('關閉', on_click=lambda e: close_dialog(self.page, dialog))])
        open_dialog(self.page, dialog)

    def edit_selected(self, e=None):
        track = next((t for t in self.tracks if t.selected), None)
        if track:
            self.edit(track)
        else:
            self.message('先勾選要修改的歌曲')

    def update_album_summary(self):
        a = self.album
        self.album_summary.value = (f'{a.albumartist or a.artist or "未填歌手"}\n'
            f'{a.title or "未填專輯名稱"}\n{a.date or "未填日期"}' if a.is_playlist
            else '單曲模式：使用歌曲各自的資訊與封面')
        self.cover_preview.visible = False
        if a.cover:
            try:
                self.cover_preview.src_base64 = preview(a.cover, a.bbox or auto_bbox(a.cover))
                self.cover_preview.visible = True
            except (OSError, ValueError) as exc:
                self.album_summary.value += f'\n封面無法預覽：{exc}'

    def naming_clicked(self, e):
        def apply(value):
            self.naming.value=value
            self.page.update()
        show_naming_editor(self.page,self.tracks,self.naming.value,self.format.value,DATA/'naming_presets.json',apply)

    def shared_cover_clicked(self, e):
        def apply(uid):
            self.shared_cover_uid=uid
            self.update_cover_description()
            self.page.update()
        candidates = list(self.tracks)
        if self.shared_cover_backup and not any(t.uid == self.shared_cover_backup.uid for t in candidates):
            candidates.append(self.shared_cover_backup)
        show_cover_selector(self.page,candidates,self.shared_cover_uid,apply)

    def shared_cover_track(self):
        found = next((t for t in self.tracks if t.uid == self.shared_cover_uid), None)
        if not found and self.shared_cover_backup and self.shared_cover_backup.uid == self.shared_cover_uid:
            found = self.shared_cover_backup
        return found

    def update_cover_description(self):
        t=self.shared_cover_track()
        self.cover_description.value = (f'共用封面：第 {t.tracknumber or "—"} 首 {t.title}（套用至所有勾選輸出歌曲）' if t else
            '共用封面來源已被移除，請重新選擇' if self.shared_cover_uid else '封面來源：各歌曲自己的封面')

    def settings(self):
        shared=self.shared_cover_track() if not self.album.is_playlist else None
        if self.shared_cover_uid and (not shared or not shared.cover):
            if not self.album.is_playlist:
                raise ValueError('共用封面來源不存在，請重新選擇')
        folder = (self.folder.value or '').strip()
        if self.album_group.value:
            folder = str(Path(folder) / album_directory(self.album))
        common_cover = self.album.cover if self.album.is_playlist else (shared.cover if shared else '')
        common_bbox = self.album.bbox if self.album.is_playlist else (shared.bbox if shared else [])
        return OutputSettings(folder=folder, format=self.format.value, bitrate=self.bitrate.value,
            sample_rate=int(self.rate.value), normalize=self.norm.value, lufs=float(self.lufs.value),
            true_peak=float(self.peak.value), loudness_mode=self.loudness_mode.value or 'gain', cover_size=int(self.size.value), naming=self.naming.value,
            embed_cover=self.embed.value,extra_covers=self.extra_covers.value,write_report=self.reports.value,
            shared_cover=common_cover,shared_bbox=list(common_bbox or auto_bbox(common_cover)) if common_cover else [])

    def folder_picked(self, e):
        if e.path and not self.busy:
            self.folder.value = e.path
            self.page.update()

    def message(self, text):
        self.status.value = str(text)
        self.page.update()

    def emit(self, text):
        self.events.put(str(text))

    def persist(self, notify=False):
        try:
            save_project(self.project, self.tracks, self.album)
            if notify:
                self.message(f'專案已儲存：{self.project}')
        except Exception as exc:
            self.message(f'專案儲存失敗：{exc}')

    def _thumbnail_track(self, track):
        shared = self.shared_cover_track()
        if shared and shared.cover:
            result = copy.copy(track)
            result.cover, result.bbox = shared.cover, shared.bbox
            return result
        return track

    def render(self):
        self.update_cover_description()
        self.update_album_summary()
        self.items.controls.clear()
        for track in self.tracks:
            measurement = track.measurement.get('input_i') or '—'
            self.items.controls.append(ft.Container(padding=8, bgcolor='#F0F5F4', border_radius=8,
                content=ft.Row([
                    ft.Checkbox(value=track.selected, on_change=lambda e, t=track: setattr(t, 'selected', e.control.value)),
                    track_thumbnail(effective_track(track, self.album) if self.album.applies_to(track) else self._thumbnail_track(track)),
                    ft.Text(track.tracknumber or '—', width=48),
                    ft.Text(track.title or track.url or f'第 {track.tracknumber} 首待重試',
                            expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                            tooltip='\n'.join(filter(None, [track.artist, track.album, track.date, track.genre, track.composer, track.warning or track.status]))),
                    ft.Text(f'{measurement} LUFS' if measurement != '—' else '—', width=110),
                    ft.IconButton(ft.icons.REFRESH, tooltip='重試這首下載',
                                  on_click=partial(self.retry_one_clicked, track),
                                  disabled=(not bool(track.url or track.playlist_url) or
                                    bool(track.source and Path(track.source).is_file() and
                                         track.cover and Path(track.cover).is_file()))),
                    ft.IconButton(ft.icons.EDIT, tooltip='修改這首詳細資料', on_click=lambda e, t=track: self.edit(t)),
                    ft.IconButton(ft.icons.CROP, tooltip='裁切這首封面', disabled=not bool(track.cover),
                                  on_click=lambda e, t=track: self.crop(t))], vertical_alignment=ft.CrossAxisAlignment.START)))
        self.count.value = f'{len(self.tracks)} 個項目'
        self.resize()

    def pick_cover(self, track):
        self.cover_target = track
        self.cover_picker.pick_files(allowed_extensions=['jpg','jpeg','png','webp'])

    def cover_picked(self, e):
        if self.busy or not e.files or not self.cover_target:
            return
        try:
            t = self.cover_target
            t.bbox = auto_bbox(e.files[0].path)
            t.cover = e.files[0].path
            self.persist()
            self.render()
            self.crop(t)
        except Exception as exc:
            self.message(exc)

    def crop(self, track):
        try:
            def saved():
                if self.album.cover_source_uid == track.uid:
                    self.album.cover = track.cover
                    self.album.bbox = list(track.bbox)
                    self.album_view.show_preview()
                self.persist()
                self.render()
            show_crop_editor(self.page, track, saved)
        except Exception as exc:
            self.message(exc)

    def cookies_picked(self, e):
        if e.files and not self.busy:
            self.cookiefile = e.files[0].path
            self.message('已指定 cookies.txt；內容不會存入專案。')

    def project_picked(self, e):
        if e.files and not self.busy:
            try:
                self.tracks, self.album = load_session(e.files[0].path)
                self.album_view.sync()
                self.render()
            except Exception as exc:
                self.message(exc)

    def restore(self, e):
        if self.busy:
            return
        try:
            self.tracks, self.album = load_session(self.project)
            self.album_view.sync()
            self.render()
            self.message('已還原上次工作；請確認來源及封面檔案仍存在。')
        except Exception as exc:
            self.message(f'無法還原：{exc}')

    def select_all(self, value):
        for t in self.tracks:
            t.selected = value
        self.render()

    def clear_all(self, e=None):
        if self.busy:
            return
        self.tracks.clear()
        self.album = AlbumInfo()
        self.album_view.sync()
        self.shared_cover_uid = ''
        self.shared_cover_backup = None
        self.persist()
        self.render()
        self.message('清單已清空；原始音檔、輸出檔案與圖片均保留。')

    def reset_album_if_empty(self):
        """An empty list must not pass the previous song's album info or cover to the next download."""
        if self.tracks:
            return False
        self.album = AlbumInfo(completed_temp=list(self.album.completed_temp))
        self.shared_cover_uid = ''
        self.shared_cover_backup = None
        self.album_editing = False
        return True

    def remove_completed(self, track):
        # Remove only a confirmed successful export, including before a later cancellation.
        if track.status != '完成':
            return
        if track.uid == self.shared_cover_uid:
            self.shared_cover_backup = copy.deepcopy(track)
        self.tracks = [t for t in self.tracks if t.uid != track.uid]
        if not self.tracks:
            self.shared_cover_uid = ''
            self.shared_cover_backup = None

    def remove_selected(self, e):
        self.tracks = [t for t in self.tracks if not t.selected]
        if self.reset_album_if_empty():
            self.left_content.content = self.overview
            self.album_view.sync()
        self.persist()
        self.render()

    def edit(self, track):
        fields = {key: ft.TextField(label=label, value=getattr(track, key)) for key, label in [
            ('title','標題'),('artist','創作者／YouTube 頻道'),('albumartist','專輯創作者'),
            ('album','專輯'),('date','發布日期 YYYY-MM-DD 或 YYYY'),('tracknumber','曲目'),('genre','曲風'),
            ('composer','Composer／作曲者'),('discnumber','Disc Number／碟號'),('totaldiscs','Total Discs／總碟數')]}
        before_names = {k: fields[k].value for k in ('title', 'artist', 'albumartist')}
        def tidy_names(e):
            fields['artist'].value = clean_artist(fields['artist'].value)
            fields['albumartist'].value = clean_artist(fields['albumartist'].value)
            fields['title'].value = clean_title(fields['title'].value, fields['artist'].value)
            self.page.update()
        def undo_names(e):
            for key, value in before_names.items():
                fields[key].value = value
            self.page.update()
        def source_names(e):
            for key in before_names:
                value = getattr(track, 'original_' + key, '')
                if value:
                    fields[key].value = value
            self.page.update()
        name_actions = ft.Row([
            ft.TextButton('整理名稱', icon=ft.icons.AUTO_FIX_HIGH, on_click=tidy_names),
            ft.TextButton('復原本次整理', on_click=undo_names),
            ft.TextButton('還原來源名稱', on_click=source_names,
                          disabled=not bool(track.original_title or track.original_artist)),
        ], wrap=True)
        error = ft.Text(track.warning, color='orange')
        def save(e):
            draft = copy.deepcopy(track)
            for k, v in fields.items():
                setattr(draft, k, (v.value or '').strip())
            try:
                validate_track(draft)
            except ValueError as exc:
                error.value = str(exc)
                self.page.update()
                return
            for k in fields:
                if k in ('artist','albumartist','album','date','tracknumber','genre','composer','discnumber','totaldiscs'):
                    if getattr(track, k) != getattr(draft, k) and k not in track.metadata_overrides:
                        track.metadata_overrides.append(k)
                setattr(track, k, getattr(draft, k))
            close_dialog(self.page, dialog)
            self.persist()
            self.render()
        dialog = ft.AlertDialog(modal=True, title=ft.Text('編輯歌曲資訊'),
            content=ft.Container(width=520, content=ft.Column([name_actions,*fields.values(),error,ft.Text('響度紀錄：'+str(track.measurement),selectable=True),ft.Text(track.url, selectable=True),ft.Text(track.source, selectable=True)], tight=True, scroll=ft.ScrollMode.AUTO)),
            actions=[ft.TextButton('取消', on_click=lambda e: close_dialog(self.page, dialog)),ft.FilledButton('儲存修改', on_click=save)])
        open_dialog(self.page, dialog)

    def batch_edit(self, e):
        selected = [t for t in self.tracks if t.selected]
        fields = {k: ft.TextField(label=label+'（空白不修改）') for k, label in [('artist','創作者'),('albumartist','專輯創作者'),('album','專輯'),('date','日期')]}
        error = ft.Text()
        def apply(e):
            drafts = copy.deepcopy(selected)
            try:
                for t in drafts:
                    for k, field in fields.items():
                        if field.value:
                            setattr(t, k, field.value.strip())
                    validate_track(t)
            except ValueError as exc:
                error.value = str(exc)
                self.page.update()
                return
            for target, draft in zip(selected, drafts):
                for key in fields:
                    setattr(target, key, getattr(draft, key))
            close_dialog(self.page, dialog)
            self.persist()
            self.render()
        dialog = ft.AlertDialog(title=ft.Text(f'批次修改 {len(selected)} 首'), content=ft.Column([*fields.values(), error], tight=True),
            actions=[ft.TextButton('取消', on_click=lambda e: close_dialog(self.page, dialog)), ft.FilledButton('套用', on_click=apply)])
        open_dialog(self.page, dialog)

    async def job(self, work):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        for control in (self.toolbar,self.workspace):
            control.disabled = True
        self.stop_btn.disabled = False
        self.bar.value = None
        self.page.update()
        future = asyncio.create_task(asyncio.to_thread(work))
        try:
            while not future.done():
                await asyncio.sleep(.2)
                self.drain()
            await future
        except Cancelled:
            self.emit('已停止，完成的項目已保留。')
        except Exception as exc:
            logging.exception('Operation failed')
            self.emit(f'處理失敗：{exc}')
        finally:
            self.drain()
            self.busy = False
            for control in (self.toolbar,self.workspace):
                control.disabled = False
            self.stop_btn.disabled = True
            self.bar.value = 1
            self.persist()
            self.render()

    def drain(self):
        msgs = []
        while not self.events.empty():
            msgs.append(self.events.get_nowait())
        if msgs:
            self.status.value = msgs[-1]
            self.log.value = ((self.log.value or '')+'\n'+'\n'.join(msgs))[-18000:]
            self.page.update()

    async def download_clicked(self, e):
        url, mode, cookies = self.url.value or '', self.mode.value, self.cookiefile
        start, end, cleanup = self.date_from, self.date_to, self.auto_clean_titles
        refresh_scan = self.refresh_scan
        try:
            DateRange(start, end).bounds()
        except ValueError as exc:
            self.message(exc)
            return
        numbering = self.numbering
        def work():
            service = YouTubeService(self.cache, self.cancel, self.emit, cookies, refresh_scan=refresh_scan)
            self.emit('解析清單中…')
            entries = service.entries(url, mode, numbering)
            info = service.playlist_info
            # A one-song release playlist (OLAK…) is a single, not a shared album.
            playlist = bool(info) and (len(entries) > 1 or info.get('is_releases') or
                                       bool(info.get('channel_tab')) or not (entries and entries[0].url))
            playlist_url = info.get('url', '') if playlist else ''
            if playlist:
                same_playlist = self.album.is_playlist and self.album.playlist_url in ('', playlist_url) and any(
                    t.playlist_url == playlist_url for t in self.tracks)
                if not same_playlist or (info.get('is_releases') and not self.album.is_release_collection):
                    releases = info.get('is_releases', False)
                    per_track = releases or bool(info.get('channel_tab'))
                    self.album = AlbumInfo(is_playlist=True, is_release_collection=per_track,
                                           title='' if per_track else info.get('title') or '',
                                           cover_source_uid=PER_TRACK_COVER if per_track else '',
                                           playlist_url=playlist_url,
                                           completed_temp=list(self.album.completed_temp))
                    self.album_group.value = False
            elif self.album.is_playlist and not any(self.album.applies_to(t) for t in self.tracks):
                # Nothing left from the old playlist: drop its shared artist/date/cover.
                self.album = AlbumInfo(completed_temp=list(self.album.completed_temp))
                self.shared_cover_uid = ''
                self.shared_cover_backup = None
            success = skipped = failed = 0
            for entry in entries:
                video_url, index = entry.url, entry.index
                check_cancel(self.cancel)
                if playlist:
                    t = next((item for item in self.tracks if item.playlist_url == playlist_url and (
                        item.url == video_url if video_url else
                        (item.playlist_index or int(item.tracknumber or 0)) == entry.position)), None)
                else:
                    t = next((item for item in self.tracks if item.url == video_url and not item.source), None)
                is_new = t is None
                if is_new:
                    t = Track(url=video_url, tracknumber=str(index), album=entry.album,
                              playlist_url=playlist_url)
                elif 'tracknumber' not in t.metadata_overrides and t.tracknumber != str(index):
                    self.emit(f'{t.title or video_url}：曲號 {t.tracknumber} → {index}')
                    t.tracknumber = str(index)
                t.playlist_index = entry.position if playlist else 0
                if entry.thumbnail:
                    t.release_thumbnail = entry.thumbnail
                t.date_from, t.date_to = start, end
                if is_new:
                    t.auto_clean_titles = cleanup
                try:
                    service.check_date(t)
                    if is_new:
                        self.tracks.append(t)
                    elif t.source and Path(t.source).is_file() and t.cover and Path(t.cover).is_file():
                        success += 1
                        continue
                    self.emit(f'下載清單第 {index}/{len(entries)} 首')
                    service.download(t)
                    self._update_album_from_track(t)
                    self._scan_download(t)
                    success += 1
                except OutsideDateRange as exc:
                    skipped += 1
                    self.emit(f'略過第 {index} 首：{exc}')
                    continue
                except Cancelled:
                    t.status = '已停止，可重試下載'
                    raise
                except Exception as exc:
                    check_cancel(self.cancel)
                    if is_new and t not in self.tracks:
                        self.tracks.append(t)
                    t.status = '下載失敗'
                    t.warning = str(exc)
                    failed += 1
                    self.emit(f'第 {index} 首失敗：{exc}')
                save_project(self.project, self.tracks, self.album)
            if playlist:
                self.tracks.sort(key=lambda item: (item.playlist_url != playlist_url,
                                                   int(str(item.tracknumber or 0).split('/')[0] or 0)
                                                   if item.playlist_url == playlist_url else 0))
            self.emit(f'處理完成：成功 {success} 首、日期範圍外／多首專輯略過 {skipped} 首、失敗 {failed} 首。'
                      '無日期或讀取失敗項目可重試。')
        await self.job(work)
        self.album_view.sync()

    def _update_album_from_track(self, track):
        if not self.album.is_playlist or self.album.is_release_collection:
            return
        if not self.album.artist:
            self.album.artist = track.artist
        if not self.album.albumartist:
            self.album.albumartist = track.artist
        if not self.album.title:
            self.album.title = track.album
        if not self.album.date:
            self.album.date = track.date
        if not self.album.cover and track.cover and self.album.cover_source_uid != PER_TRACK_COVER:
            self.album.cover = track.cover
            self.album.bbox = list(track.bbox)
            self.album.cover_source_uid = track.uid

    def _scan_download(self, track):
        try:
            track.measurement = measure(track.source, OutputSettings(folder=self.folder.value), self.cancel)
        except Cancelled:
            raise
        except Exception as exc:
            track.warning += f'；初次響度掃描失敗：{exc}'
            self.emit(f'{track.title}：響度稍後可重新掃描')

    async def retry_one_clicked(self, track, e=None):
        await self._retry_tracks([track])

    async def retry_failed_clicked(self, e=None):
        targets = [t for t in self.tracks if (t.url or t.playlist_url) and
                   (not t.source or not Path(t.source).is_file() or not t.cover or not Path(t.cover).is_file())]
        if not targets:
            self.message('目前沒有需要重新下載的歌曲')
            return
        await self._retry_tracks(targets)

    async def _retry_tracks(self, tracks):
        cookies = self.cookiefile
        def work():
            service = YouTubeService(self.cache, self.cancel, self.emit, cookies)
            ok = 0
            for t in tracks:
                check_cancel(self.cancel)
                try:
                    service.download(t)
                    self._update_album_from_track(t)
                    self._scan_download(t)
                    ok += 1
                    self.emit(f'第 {t.tracknumber} 首重新下載成功')
                except OutsideDateRange as exc:
                    t.status = '日期範圍外，未下載'
                    t.selected = False
                    t.warning = str(exc)
                    self.emit(f'第 {t.tracknumber} 首略過：{exc}')
                except Cancelled:
                    t.status = '已停止，可重試下載'
                    raise
                except Exception as exc:
                    t.status = '下載失敗'
                    t.warning = str(exc)
                    self.emit(f'第 {t.tracknumber} 首重試失敗：{exc}')
                save_project(self.project, self.tracks, self.album)
            self.emit(f'重試完成：{ok}/{len(tracks)} 首成功')
        await self.job(work)
        self.album_view.sync()

    async def audio_picked(self, e):
        if not e.files:
            return
        paths = [f.path for f in e.files]
        def work():
            for path in paths:
                check_cancel(self.cancel)
                try:
                    if not any(t.source == str(Path(path).resolve()) for t in self.tracks):
                        self.tracks.append(import_audio(path, self.cache))
                except Exception as exc:
                    self.emit(f'匯入失敗 {Path(path).name}：{exc}')
            self.emit('音檔匯入完成')
        await self.job(work)

    async def images_picked(self, e):
        if not e.files:
            return
        paths = [f.path for f in e.files]
        def work():
            for path in paths:
                check_cancel(self.cancel)
                try:
                    self.tracks.append(Track(title=Path(path).stem, cover=path, bbox=auto_bbox(path), status='僅封面'))
                except Exception as exc:
                    self.emit(f'圖片匯入失敗：{exc}')
            self.emit('圖片匯入完成，可手動裁切或僅輸出封面。')
        await self.job(work)

    async def output_clicked(self, e):
        selected = [t for t in self.tracks if t.selected and t.source]
        if not selected:
            self.message('請先下載或匯入音檔並勾選。')
            return
        try:
            self.album_view.save(silent=True, apply_to_tracks=self.album_editing)
            settings = self.settings()
            settings.validate()
            ffmpeg_path()
            for t in selected:
                item = effective_track(t, self.album)
                validate_track(item)
                from core.naming import output_path
                output_path(settings.folder,item,settings.naming,settings.format)
                if settings.embed_cover and not (settings.shared_cover or item.cover):
                    raise ValueError(f'{t.title} 沒有封面，請選圖／指定共用封面，或取消內嵌封面勾選。')
        except Exception as exc:
            self.message(exc)
            return
        auto_clear = self.auto_clear.value
        def work():
            success = 0
            completed = []
            try:
                for i,t in enumerate(selected, 1):
                    check_cancel(self.cancel)
                    try:
                        item = effective_track(t, self.album)
                        dest = export_track(item, settings, self.cancel,
                            lambda msg: self.emit(f'{i}/{len(selected)} {t.title}：{msg}'))
                        t.status, t.output, t.measurement = '完成', str(dest), item.measurement
                        success += 1
                        completed.append(t)
                        if auto_clear:
                            self.remove_completed(t)
                    except Cancelled:
                        t.status = '已停止'
                        raise
                    except Exception as exc:
                        t.status = '輸出失敗'
                        t.warning = str(exc)
                        self.emit(f'{t.title}：{exc}')
                    save_project(self.project, self.tracks, self.album)
                self.emit(f'輸出完成 {success}/{len(selected)} 首。'
                          +('成功項目已移出清單；' if auto_clear else '')+'失敗與未處理項目保留。')
            finally:
                self._cleanup_exported(completed)
                if self.reset_album_if_empty():
                    self.emit('清單已清空，專輯資訊與共用封面已重設，下一首不會沿用上一首資料')
                save_project(self.project, self.tracks, self.album)
        await self.job(work)
        if not self.tracks:
            self.left_content.content = self.overview
        self.album_view.sync()

    def _cleanup_exported(self, completed):
        if not completed and not self.album.completed_temp:
            return
        deferred = [Track(uid=item['uid'], source=item['source'], title=item.get('title', ''),
                          status='完成') for item in self.album.completed_temp]
        all_completed = {t.uid: t for t in deferred + completed}
        pending = [t for t in self.tracks if t.uid not in all_completed]
        protected = {str(Path(p).resolve()) for t in pending
                     for p in (t.source, t.cover) if p}
        if pending and self.album.cover:
            protected.add(str(Path(self.album.cover).resolve()))
        still_needed = []
        for t in all_completed.values():
            folder = (self.cache / t.uid).resolve()
            if any(Path(p).is_relative_to(folder) for p in protected):
                self.emit(f'{t.title}：封面仍供待處理歌曲使用，暫存稍後清理')
                still_needed.append({'uid': t.uid, 'source': t.source, 'title': t.title})
                continue
            try:
                if clean_download(t):
                    t.source = ''
            except OSError as exc:
                self.emit(f'{t.title}：暫存清理失敗：{exc}')
                still_needed.append({'uid': t.uid, 'source': t.source, 'title': t.title})
        self.album.completed_temp = still_needed
        if not pending and self.album.cover:
            cover = Path(self.album.cover).resolve()
            if cover.is_relative_to(self.cache.resolve()) and not cover.is_file():
                self.album.cover = ''
                self.album.bbox = []
                self.album.cover_source_uid = ''
        self.emit('成功輸出的下載暫存已清理；需供失敗歌曲重試的封面會保留')

    async def covers_clicked(self, e):
        selected = [t for t in self.tracks if t.selected and t.cover]
        folder, size = self.folder.value, int(self.size.value)
        def work():
            from core.audio import filename
            import os
            import tempfile
            root = writable_directory(folder)
            success = 0
            for t in selected:
                check_cancel(self.cancel)
                dest = root/(filename(t, '<標題>')+'.jpg')
                base, n = dest.stem, 2
                while dest.exists():
                    dest = root/f'{base} ({n}).jpg'
                    n += 1
                try:
                    with tempfile.TemporaryDirectory(dir=temp_directory()) as tmp:
                        cover = Path(tmp)/'cover.jpg'
                        save_cover(t.cover, t.bbox, cover, size)
                        with dest.open('xb') as target:
                            target.write(cover.read_bytes())
                    success += 1
                except Exception as exc:
                    self.emit(f'{t.title} 封面失敗：{exc}')
            self.emit(f'已輸出 {success}/{len(selected)} 張封面')
        await self.job(work)

    async def match_clicked(self, e):
        from core.matching import match_titles
        selected = [t for t in self.tracks if t.selected and t.source]
        if not selected:
            self.message('請先匯入並勾選要配對的音檔。')
            return
        url, mode, cookies = self.url.value or '', self.mode.value, self.cookiefile
        def work():
            service = YouTubeService(self.cache, self.cancel, self.emit, cookies)
            available = list(selected)
            count = 0
            for video_url,index,album,*_ in service.entries(url, mode, 'source'):
                check_cancel(self.cancel)
                try:
                    meta = service.fetch_metadata(video_url,index,album)
                    candidates = sorted([(max(match_titles(meta.title,t.title), match_titles(meta.title,Path(t.source).stem)),i,t)
                                         for i,t in enumerate(available)], key=lambda x:x[0], reverse=True)
                    if not candidates or candidates[0][0] < .8 or (len(candidates)>1 and candidates[0][0]-candidates[1][0]<.08):
                        self.emit(f'無可靠唯一配對：{meta.title}')
                        continue
                    target = candidates[0][2]
                    for key in ('url','title','artist','albumartist','album','date','tracknumber','warning'):
                        setattr(target,key,getattr(meta,key))
                    available.remove(target)
                    count += 1
                except Exception as exc:
                    check_cancel(self.cancel)
                    self.emit(f'讀取資訊失敗：{exc}')
            self.emit(f'已預填 {count} 首；尚未寫入音檔，請檢查後輸出。')
        await self.job(work)
