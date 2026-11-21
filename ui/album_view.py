"""Shared metadata and artwork editor for playlist downloads (Flet 0.21.2)."""
from pathlib import Path
import flet as ft
from core.crop import auto_bbox, preview
from core.text_cleanup import clean_artist


PER_TRACK_COVER = '__per_track__'
LOCAL_COVER = '__local_cover__'


class AlbumView:
    def __init__(self, studio):
        self.studio = studio
        self.page = studio.page
        self.fields = {
            name: ft.TextField(label=label, value='', expand=True, dense=True,
                height=42, text_size=13, content_padding=ft.padding.symmetric(horizontal=10, vertical=10))
            for name, label in (
                ('title', '專輯名稱'), ('artist', '曲目歌手'),
                ('albumartist', '專輯歌手'), ('date', '日期 YYYY-MM-DD'),
                ('genre', '曲風'), ('composer', '作曲者'))
        }
        self.cover_list = ft.Dropdown(label='封面來源', expand=True, dense=True,
            height=42, text_size=13, content_padding=10, options=[], on_change=self.choose_cover)
        self.cover_image = ft.Image(width=76, height=76, visible=False, fit=ft.ImageFit.CONTAIN)
        self.cover_note = ft.Text('尚未設定封面', size=12, max_lines=2,
                                  overflow=ft.TextOverflow.ELLIPSIS)
        self.error = ft.Text(color='red', size=12)
        self.picker = ft.FilePicker(on_result=self.picked_cover)
        self.page.overlay.append(self.picker)
        self.content = ft.Column([
            ft.Row([
                ft.IconButton(ft.icons.ARROW_BACK, tooltip='儲存並返回', width=40, height=40,
                              icon_size=20, on_click=self.studio.close_album_editor),
                ft.Text('編輯資訊與封面', size=17, weight=ft.FontWeight.BOLD, expand=True),
                ft.TextButton('儲存並返回', icon=ft.icons.CHECK, height=40,
                              style=ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=10, vertical=4)),
                              on_click=self.studio.close_album_editor)
            ], spacing=3, height=44),
            ft.Column([
                ft.Text('留白沿用各首資料；標題與曲號由歌曲詳情修改。', size=12, color='#64748B'),
                ft.Row([self.fields['title'], self.fields['artist']], spacing=8),
                ft.Row([ft.TextButton('整理歌手名稱', on_click=self.tidy_artists),
                        ft.TextButton('復原整理', on_click=self.undo_artists)], spacing=4),
                ft.Row([self.fields['albumartist'], self.fields['date']], spacing=8),
                ft.Row([self.fields['genre'], self.fields['composer']], spacing=8),
                ft.Row([self.cover_list, ft.IconButton(ft.icons.FOLDER_OPEN,
                    tooltip='選取本機封面', on_click=lambda e: self.picker.pick_files(
                        allowed_extensions=['jpg','jpeg','png','webp']))], spacing=4),
                ft.Row([self.cover_image, ft.Column([self.cover_note, self.error],
                    spacing=2, expand=True)], spacing=10),
            ], spacing=9, scroll=ft.ScrollMode.AUTO, expand=True)
        ], spacing=8, expand=True)

    def tidy_artists(self, e=None):
        self._before_artists = {key: self.fields[key].value for key in ('artist', 'albumartist')}
        for key in self._before_artists:
            self.fields[key].value = clean_artist(self.fields[key].value)
        self.page.update()

    def undo_artists(self, e=None):
        for key, value in getattr(self, '_before_artists', {}).items():
            self.fields[key].value = value
        self._before_artists = {}
        self.page.update()

    def sync(self):
        self._before_artists = {}
        for name, field in self.fields.items():
            field.value = getattr(self.studio.album, name)
        self.cover_list.options = [ft.dropdown.Option(PER_TRACK_COVER, '各首使用自己的封面')]
        self.cover_list.options += [ft.dropdown.Option(t.uid,
            f'{t.tracknumber or "—"}｜{t.title or t.url}') for t in self.studio.tracks if t.cover]
        album = self.studio.album
        if album.cover and not album.cover_source_uid:
            self.cover_list.options.append(ft.dropdown.Option(LOCAL_COVER, '目前選取的本機封面'))
        self.cover_list.value = (album.cover_source_uid or LOCAL_COVER) if album.cover else PER_TRACK_COVER
        self.show_preview()
        self.page.update()

    def save(self, e=None, silent=False, apply_to_tracks=False):
        from core.audio import validate_track
        from core.models import Track
        album = self.studio.album
        try:
            date = (self.fields['date'].value or '').strip()
            if date:
                validate_track(Track(title='album', date=date))
            for name, field in self.fields.items():
                setattr(album, name, (field.value or '').strip())
            if apply_to_tracks and album.is_playlist:
                for track in self.studio.tracks:
                    for name in self.fields:
                        value = getattr(album, name)
                        if value:
                            target = 'album' if name == 'title' else name
                            setattr(track, target, value)
                            if target in track.metadata_overrides:
                                track.metadata_overrides.remove(target)
            self.error.value = '' if silent else '已儲存資訊'
            self.studio.persist()
            self.studio.render()
        except ValueError as exc:
            self.error.value = str(exc)
            self.page.update()
            if silent:
                raise

    def choose_cover(self, e):
        album = self.studio.album
        uid = self.cover_list.value
        if self.studio.busy:
            return
        if uid in (None, '', PER_TRACK_COVER, '各首使用自己的封面'):
            album.cover = ''
            album.bbox = []
            album.cover_source_uid = PER_TRACK_COVER
            self.studio.shared_cover_uid = ''
            self.studio.shared_cover_backup = None
            self.cover_list.value = PER_TRACK_COVER
        elif uid == LOCAL_COVER:
            return
        else:
            track = next((t for t in self.studio.tracks if t.uid == uid), None)
            if not track or not track.cover:
                self.error.value = '來源封面已不在清單，請重新選擇'
                self.page.update()
                return
            album.cover = track.cover
            album.bbox = list(track.bbox or auto_bbox(track.cover))
            album.cover_source_uid = uid
        self.error.value = ''
        self.show_preview()
        self.studio.persist()
        self.studio.update_album_summary()
        self.page.update()

    def picked_cover(self, e):
        if not e.files or self.studio.busy:
            return
        try:
            path = str(Path(e.files[0].path).resolve())
            self.studio.album.cover = path
            self.studio.album.bbox = auto_bbox(path)
            self.studio.album.cover_source_uid = ''
            self.cover_list.options = [o for o in self.cover_list.options if o.key != LOCAL_COVER]
            self.cover_list.options.append(ft.dropdown.Option(LOCAL_COVER, '目前選取的本機封面'))
            self.cover_list.value = LOCAL_COVER
            self.error.value = ''
            self.show_preview()
            self.studio.persist()
            self.studio.update_album_summary()
            self.page.update()
        except Exception as exc:
            self.error.value = f'封面讀取失敗：{exc}'
            self.page.update()

    def show_preview(self):
        album = self.studio.album
        self.cover_image.visible = False
        if album.cover:
            try:
                self.cover_image.src_base64 = preview(album.cover, album.bbox or auto_bbox(album.cover))
                self.cover_image.visible = True
                self.cover_note.value = f'封面：{Path(album.cover).name}'
            except Exception as exc:
                self.cover_note.value = f'封面遺失：{exc}'
        else:
            self.cover_note.value = '各歌曲使用自己的封面'
