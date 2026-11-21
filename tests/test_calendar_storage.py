from datetime import datetime
import json
from core.scan_cache import ScanCache
from ui.main_view import Studio
from test_ui import FakePage

def test_calendar_range_clear_reopen(tmp_path, monkeypatch):
    monkeypatch.setattr('ui.main_view.DATA',tmp_path)
    app=Studio(FakePage())
    app.download_options_clicked()
    row=app.page.dialog.content.content.controls[1]
    start,end=app._date_pickers
    monkeypatch.setattr(start,'update',lambda:None)
    row.controls[1].on_click(None)
    assert start.open
    start.value=datetime(2026,9,1);start.on_change(None)
    end.value=datetime(2026,9,30);end.on_change(None)
    app.page.dialog.actions[1].on_click(None)
    assert (app.date_from,app.date_to)==('2026-09-01','2026-09-30')
    count=len(app.page.overlay)
    app.download_options_clicked()
    assert len(app.page.overlay)==count
    app.page.dialog.content.content.controls[2].on_click(None)
    app.page.dialog.actions[1].on_click(None)
    assert app.date_from==app.date_to==''

def test_list_location_and_legacy_copy(tmp_path, monkeypatch):
    monkeypatch.setattr('core.scan_cache.ROOT',tmp_path/'app')
    monkeypatch.setattr('core.scan_cache.DATA',tmp_path/'old')
    old=ScanCache(tmp_path/'old'/'scan_cache')
    old.write('lists','url',{'entries':[]})
    service=ScanCache()
    assert service.root==tmp_path/'app'/'list'
    assert service.read('lists','url')==old.read('lists','url')
    assert service.path('lists','url').is_file()
    service.write('lists','url',{'entries':['new']})
    assert service.read('lists','url')['data']['entries']==['new']
    assert old.read('lists','url')['data']['entries']==[]
