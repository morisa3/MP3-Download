import asyncio
from types import SimpleNamespace
from PIL import Image
from core.models import Track
from test_ui import FakePage
from ui.main_view import Studio
from ui.loudness_dialog import show_loudness_dialog
from ui.track_thumbnail import track_thumbnail

def test_thumbnail_hover_and_crop(tmp_path,monkeypatch):
    image=tmp_path/'cover.png';Image.new('RGB',(100,100),'red').save(image)
    thumb=track_thumbnail(Track(cover=str(image),bbox=[10,10,90,90]))
    monkeypatch.setattr(thumb,'update',lambda:None)
    assert thumb.width==48 and thumb.content.src_base64
    thumb.on_hover(SimpleNamespace(data='true'));assert thumb.width==200
    thumb.on_hover(SimpleNamespace(data='false'));assert thumb.width==48

def test_dialog_and_overwrite_confirmation(tmp_path,monkeypatch):
    monkeypatch.setattr('ui.main_view.DATA',tmp_path)
    app=Studio(FakePage())
    buttons=[getattr(c,'text','') for c in app.toolbar_row.controls]
    assert buttons[buttons.index('清空清單')+1]=='響度調整'
    show_loudness_dialog(app)
    dialog=app.page.dialog
    assert dialog._build_add_commands(indent=0,index={},added_controls=[])
    fields=dialog.content.content.controls
    assert fields[4].value=='gain'  # new loudness method selector
    fields[5].value='overwrite';fields[5].on_change(None)
    assert fields[7].visible
    asyncio.run(dialog.actions[2].on_click(None))
    assert '勾選確認' in fields[-1].value
