from pathlib import Path
import asyncio
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image
from core.models import Track, AlbumInfo
from ui.main_view import Studio, list_height
from ui.crop_editor import show_crop_editor

class Window:
    pass

class FakePage:
    def __init__(self):
        self.window=Window()
        self.overlay=[]
        self.height=850
        self.width=1200
        self.controls=[]
    def add(self,*controls): self.controls.extend(controls)
    def update(self): pass
    def open(self,dialog): self.dialog=dialog
    def close(self,dialog): pass


def test_flet_controls_and_dialogs(tmp_path,monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module,'DATA',tmp_path)
    page=FakePage()
    studio=Studio(page)
    img=tmp_path/'picture.png'
    Image.new('RGB',(1280,720),'orange').save(img)
    track=Track(title='測試',cover=str(img),bbox=[280,0,1000,720])
    studio.tracks=[track]
    studio.render()
    for control in page.controls+page.overlay:
        assert control._build_add_commands(indent=0,index={},added_controls=[])
    studio.edit(track)
    assert page.dialog._build_add_commands(indent=0,index={},added_controls=[])
    show_crop_editor(page,track,lambda:None)
    assert page.dialog._build_add_commands(indent=0,index={},added_controls=[])
    studio.batch_edit(None)
    assert page.dialog._build_add_commands(indent=0,index={},added_controls=[])


def test_job_finally_unlocks_controls(tmp_path,monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module,'DATA',tmp_path)
    studio=Studio(FakePage())
    def fail(): raise ValueError('test failure')
    asyncio.run(studio.job(fail))
    assert not studio.busy
    assert not studio.toolbar.disabled
    assert studio.stop_btn.disabled
    assert 'test failure' in studio.status.value


def test_small_and_large_layout():
    assert list_height(500,768,1366)<=300
    assert list_height(500,1080,1920)>list_height(500,768,1366)
    assert list_height(1,1080,1920)<list_height(5,1080,1920)


def test_flet_021_api_and_dialog_lifecycle():
    import importlib.metadata
    import flet as ft
    from ui.dialogs import open_dialog, close_dialog
    assert importlib.metadata.version('flet') == '0.21.2'
    for name in ('window_width', 'window_height', 'on_resize', 'dialog'):
        assert hasattr(ft.Page, name)
    assert ft.icons.EDIT
    page = FakePage()
    dialog = ft.AlertDialog(title=ft.Text('Test'))
    open_dialog(page, dialog)
    assert page.dialog is dialog and dialog.open
    close_dialog(page, dialog)
    assert not dialog.open


def test_default_editable_output_and_clear_list(tmp_path,monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module,'DATA',tmp_path)
    app=Studio(FakePage())
    assert app.toolbar_row is app.toolbar.controls[0]
    assert app.toolbar is app.body.controls[1]
    assert app.body.controls[2] is app.workspace
    assert app.auto_clear in app.settings_panel.controls
    assert 'v1.11.0' in app.page.title
    assert app.folder.value == r'E:\download' and not app.folder.read_only
    app.folder.value=str(tmp_path/'another path')
    assert app.settings().folder == str(tmp_path/'another path')
    source=tmp_path/'original.mp3'
    source.write_bytes(b'original')
    app.tracks=[Track(title='A',source=str(source))]
    app.clear_all()
    assert not app.tracks and source.read_bytes()==b'original'


def test_playlist_shared_tab_folder_and_temp_cleanup(tmp_path,monkeypatch):
    import ui.main_view as module
    import core.youtube as youtube
    monkeypatch.setattr(module,'DATA',tmp_path)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(youtube,'ROOT',tmp_path)
    app=Studio(FakePage())
    app.folder.value=str(tmp_path/'output')
    app.album=AlbumInfo(is_playlist=True,title='REVIVE IN COLOR',artist='REiNa',
                        date='2026-09-26')
    app.album_view.sync()
    app.album_group.value=True
    assert Path(app.settings().folder).name=='[260926] REiNa-REVIVE IN COLOR'
    a=app.cache/'a'
    a.mkdir()
    audio=a/'source.webm'
    audio.write_bytes(b'fixture')
    t=Track(uid='a',title='Song',source=str(audio),status='完成')
    app.tracks=[t]
    app._cleanup_exported([t])
    assert not a.exists() and not t.source


def test_defer_shared_cover_cleanup_until_pending_track_finishes(tmp_path,monkeypatch):
    import ui.main_view as module
    import core.youtube as youtube
    monkeypatch.setattr(module,'DATA',tmp_path)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(youtube,'ROOT',tmp_path)
    app=Studio(FakePage())
    folder=app.cache/'art'
    folder.mkdir()
    source=folder/'source.webm'
    source.write_bytes(b'fixture')
    cover=folder/'cover.jpg'
    Image.new('RGB',(540,540),'red').save(cover)
    done=Track(uid='art', title='Album cover',source=str(source),cover=str(cover),status='完成')
    pending=Track(uid='later',title='Later',source='missing')
    app.tracks=[pending]
    app.album=AlbumInfo(is_playlist=True,cover=str(cover),cover_source_uid=done.uid)
    app._cleanup_exported([done])
    assert folder.exists() and len(app.album.completed_temp)==1
    app.tracks.clear()
    app._cleanup_exported([])
    assert not folder.exists() and app.album.cover=='' and not app.album.completed_temp


def test_success_removed_failure_retained_with_shared_cover(tmp_path,monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module,'DATA',tmp_path)
    monkeypatch.setattr(module,'OUTPUT_DIRECTORY',str(tmp_path/'out'))
    app=Studio(FakePage())
    app.loudness_mode.value='gain'  # fake sources: skip list-wide measuring
    cover=tmp_path/'cover.jpg'
    Image.new('RGB',(540,540),'blue').save(cover)
    first=Track(title='Success',source='source1',cover=str(cover),bbox=[0,0,540,540])
    failed=Track(title='Failure',source='source2')
    untouched=Track(title='Unselected',source='source3',selected=False)
    app.tracks=[first,failed,untouched]
    app.shared_cover_uid=first.uid
    def export(t,*args):
        if t.uid==failed.uid: raise ValueError('test failure')
        t.output=str(tmp_path/'finished.mp3')
    monkeypatch.setattr(module,'export_track',export)
    monkeypatch.setattr(module,'ffmpeg_path',lambda:'ffmpeg')
    asyncio.run(app.output_clicked(None))
    assert app.tracks==[failed,untouched]
    assert failed.status=='輸出失敗'
    assert app.settings().shared_cover==str(cover)
    from core.models import load_project
    assert len(load_project(app.project))==2


def test_auto_clear_can_be_disabled(tmp_path,monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module,'DATA',tmp_path)
    monkeypatch.setattr(module,'OUTPUT_DIRECTORY',str(tmp_path/'out'))
    app=Studio(FakePage())
    app.loudness_mode.value='gain'  # fake sources: skip list-wide measuring
    app.embed.value=False
    app.auto_clear.value=False
    app.tracks=[Track(title='Keep',source='fake')]
    monkeypatch.setattr(module,'export_track',lambda *a:None)
    monkeypatch.setattr(module,'ffmpeg_path',lambda:'ffmpeg')
    asyncio.run(app.output_clicked(None))
    assert len(app.tracks)==1 and app.tracks[0].status=='完成'


def test_missing_cover_enables_single_and_batch_retry(tmp_path, monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'DATA', tmp_path/'data')
    app = Studio(FakePage())
    source = tmp_path/'source.webm'
    source.write_bytes(b'fixture')
    track = Track(title='Missing cover', url='https://youtube.com/watch?v=abcdefghijk',
                  source=str(source), cover=str(tmp_path/'gone.jpg'))
    app.tracks = [track]
    app.render()
    assert app.items.controls[0].content.controls[4].disabled is False
    received = []
    async def retry(tracks):
        received.extend(tracks)
    app._retry_tracks = retry
    asyncio.run(app.retry_failed_clicked())
    assert received == [track]


def test_album_editing_stays_in_left_sheet_and_keeps_values(tmp_path, monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'DATA', tmp_path/'data')
    app = Studio(FakePage())
    app.album = AlbumInfo(is_playlist=True, title='Before')
    right = app.right_sheet.content
    items = app.items
    app.open_album_tab()
    assert app.left_content.content is app.album_view.content
    assert set(app.album_view.fields) == {'title','artist','albumartist','date','genre','composer'}
    assert all('共同' not in f.label for f in app.album_view.fields.values())
    app.album_view.fields['title'].value = 'REVIVE IN COLOR'
    app.album_view.fields['date'].value = '2026-09-26'
    app.close_album_editor()
    assert app.left_content.content is app.overview
    assert app.album.title == 'REVIVE IN COLOR'
    app.open_album_tab()
    assert app.album_view.fields['title'].value == 'REVIVE IN COLOR'
    app.album_view.fields['date'].value = '2026-99-26'
    app.close_album_editor()
    assert app.album_editing and app.album_view.error.value
    assert app.left_content.content is app.album_view.content
    assert app.right_sheet.content is right and app.items is items
    for control in app.page.controls + app.page.overlay:
        assert control._build_add_commands(indent=0, index={}, added_controls=[])


def test_compact_layout_and_original_palette(tmp_path, monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'DATA', tmp_path/'data')
    app = Studio(FakePage())
    import flet as ft
    assert app.toolbar_row.wrap is False
    assert app.download_btn.bgcolor == '#28a745'
    assert app.left_sheet.expand == app.right_sheet.expand
    assert app.bottom_row.vertical_alignment == ft.CrossAxisAlignment.STRETCH
    assert app.body.scroll is None and app.items.expand
    assert app.output_btn in app.right_sheet.content.controls[-1].controls
    for width, height in [(1235, 880), (1366, 680), (1000, 640)]:
        app.page.width, app.page.height = width, height
        app.resize()
        assert app.bottom_row.height + 215 + 50 <= height
    labels = [getattr(c, 'text', '') for c in app.toolbar_row.controls]
    assert all(x not in labels for x in ['批次修改','還原上次工作','開啟專案','選擇 cookies.txt'])
