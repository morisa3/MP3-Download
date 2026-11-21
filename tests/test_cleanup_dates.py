from pathlib import Path
import sys
import asyncio
import threading
from types import SimpleNamespace as N
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.text_cleanup import clean_artist, clean_title
from core.date_filter import DateRange, OutsideDateRange
from core.models import Track, AlbumInfo, save_project, load_session, effective_track
from core.youtube import metadata, YouTubeService, Cancelled, ListEntry
from ui.main_view import Studio
from ui.album_view import PER_TRACK_COVER
from test_ui import FakePage

@pytest.mark.parametrize('name,expected', [
    ('REiNa Official Channel','REiNa'), ('REiNa Channel','REiNa'),
    ('REiNa Official','REiNa'), ('REiNa - OFFICAL CHANNEL','REiNa'),
    ('Official髭男dism','Official髭男dism'), ('Channel Orange','Channel Orange'),
    ('Official','Official'), ('REiNa','REiNa'),
])
def test_channel_cleanup(name,expected):
    assert clean_artist(name)==expected

@pytest.mark.parametrize('title,expected', [
    ('REiNa - IGNITION (Official Music Video)','IGNITION'),
    ('REiNa / IGNITION | Offical Music','IGNITION'),
    ('REiNa「IGNITION」【Official Music Video】','IGNITION'),
    ('IGNITION Official Music Video','IGNITION'),
    ('IGNITION (Live at Tokyo)','IGNITION (Live at Tokyo)'),
    ('IGNITION (Remix) [Official Audio]','IGNITION (Remix)'),
    ('IGNITION feat. A','IGNITION feat. A'), ('Part-One - Part Two','Part-One - Part Two'),
    ('Official Music','Official Music'), ('Music','Music'),
])
def test_title_cleanup(title,expected):
    assert clean_title(title,'REiNa Official Channel')==expected


def test_metadata_retains_source_and_optional_title_cleaning():
    info={'title':'REiNa - Song [Official Video]','channel':'REiNa Official Channel','upload_date':'20260903'}
    t=metadata(info)
    assert (t.title,t.artist,t.albumartist)==('Song','REiNa','REiNa')
    assert t.original_title==info['title'] and t.original_artist==info['channel']
    assert t.source_date=='2026-09-03'
    assert metadata(info,auto_clean_titles=False).title==info['title']
    assert metadata(dict(info,track='Official Music')).title=='Official Music'


def test_inclusive_month_range_and_open_boundaries():
    r=DateRange('2026-09','2026-09')
    assert r.contains('2026-09-01') and r.contains('2026-09-30')
    assert not r.contains('2026-08-31') and not r.contains('2026-10-01')
    assert DateRange('2024-02','2024-02').contains('2024-02-29')
    assert DateRange('','2026-09').contains('2020-01-01')
    assert DateRange('2026-09','').contains('2027-01-01')
    assert DateRange().contains('')
    with pytest.raises(ValueError):r.contains('')
    with pytest.raises(ValueError):DateRange('2026-10','2026-09').bounds()
    with pytest.raises(ValueError):DateRange('2026-13','').bounds()
    with pytest.raises(ValueError):DateRange('20260901','').bounds()


def test_filter_blocks_media_before_download_and_survives_project(tmp_path,monkeypatch):
    import core.youtube as module
    calls=[]
    class YDL:
        def __init__(self,opts): self.opts=opts
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def extract_info(self,url,download):
            calls.append(download)
            assert download is False
            return {'upload_date':'20260831','title':'Outside','channel':'A Official'}
    monkeypatch.setattr(module.yt_dlp,'YoutubeDL',YDL)
    t=Track(url='https://youtube.com/watch?v=abc',date='2026-09-15',date_from='2026-09',date_to='2026-09')
    service=YouTubeService(tmp_path/'cache')
    with pytest.raises(OutsideDateRange):service.download(t)
    assert calls==[False] and not t.source and t.download_attempts==0
    save_project(tmp_path/'project.json',[t])
    restored=load_session(tmp_path/'project.json')[0][0]
    assert restored.date_from=='2026-09' and restored.source_date=='2026-08-31'
    with pytest.raises(OutsideDateRange):service.download(restored)
    assert calls==[False]


def test_unknown_date_fails_closed_and_retry_resolves(tmp_path,monkeypatch):
    import core.youtube as module
    calls=[]
    class YDL:
        def __init__(self,opts):self.opts=opts
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def extract_info(self,url,download):
            calls.append(download)
            if not download:
                return {'title':'Song','upload_date':'' if len(calls)==1 else '20260901'}
            path=Path(self.opts['outtmpl'].replace('%(ext)s','webm'))
            path.write_bytes(b'fixture')
            return {'title':'A - Song [Official Video]','channel':'A Official','upload_date':'20260901',
                    'requested_downloads':[{'filepath':str(path)}]}
    monkeypatch.setattr(module.yt_dlp,'YoutubeDL',YDL)
    service=YouTubeService(tmp_path/'cache')
    t=Track(url='https://youtube.com/watch?v=abc',date_from='2026-09',date_to='2026-09')
    with pytest.raises(ValueError,match='無法確認影片發表日期'):service.download(t)
    assert calls==[False] and not t.source
    service.download(t)
    assert calls==[False,False,True] and t.title=='Song'
    assert t.source_date=='2026-09-01'


def test_no_range_skips_date_probe_and_keeps_manual_edits(tmp_path,monkeypatch):
    import core.youtube as module
    calls=[]
    class YDL:
        def __init__(self,opts):self.opts=opts
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def extract_info(self,url,download):
            calls.append(download)
            path=Path(self.opts['outtmpl'].replace('%(ext)s','webm'));path.write_bytes(b'x')
            return {'title':'A - Song [Official Video]','channel':'A Official',
                    'requested_downloads':[{'filepath':str(path)}]}
    monkeypatch.setattr(module.yt_dlp,'YoutubeDL',YDL)
    t=Track(url='https://youtube.com/watch?v=abc',title='My manual title',status='下載失敗')
    YouTubeService(tmp_path).download(t)
    assert calls==[True] and t.title=='My manual title' and t.original_title


def test_filter_cancel_stops_probe(tmp_path):
    cancel=threading.Event();cancel.set()
    service=YouTubeService(tmp_path,cancel)
    with pytest.raises(Cancelled):service.check_date(Track(date_from='2026-09'))


def test_ui_scans_unsorted_list_and_downloads_only_matches(tmp_path,monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(module,'DATA',tmp_path/'data')
    service=YouTubeService(tmp_path/'temp')
    service.playlist_info={'url':'https://youtube.com/playlist?list=PL123','title':'Album'}
    service.entries=lambda *a:[ListEntry('https://youtube.com/watch?v='+str(i),i,'Album',i) for i in (1,2,3,4)]
    dates={1:'2026-08-31',2:'2026-09-30',3:'2026-10-01',4:'2026-09-01'}
    service.fetch_metadata=lambda url,index,album:Track(source_date=dates[int(index)])
    downloaded=[]
    def download(t):
        service.check_date(t);downloaded.append(t.tracknumber);t.title='Song';t.artist='A';t.date=t.source_date
    service.download=download
    monkeypatch.setattr(module,'YouTubeService',lambda *a, **kw:service)
    app=Studio(FakePage());app._scan_download=lambda t:None
    app.date_from=app.date_to='2026-09'
    asyncio.run(app.download_clicked(None))
    assert downloaded==['2','4'] and [t.tracknumber for t in app.tracks]==['2','4']
    assert '略過 2 首' in app.status.value


def test_new_controls_and_cleanup_undo(tmp_path,monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(module,'DATA',tmp_path/'data')
    app=Studio(FakePage())
    app.download_options_clicked()
    assert app.page.dialog._build_add_commands(indent=0,index={},added_controls=[])
    columns=app.page.dialog.content.content.controls
    row=columns[1];row.controls[0].value=row.controls[2].value='2026-09'
    app.page.dialog.actions[1].on_click(None)
    assert app.date_from=='2026-09-01' and app.date_to=='2026-09-30'
    app.album_view.fields['artist'].value='REiNa Official'
    app.album_view.tidy_artists();assert app.album_view.fields['artist'].value=='REiNa'
    app.album_view.undo_artists();assert app.album_view.fields['artist'].value=='REiNa Official'
    track=metadata({'title':'REiNa - Song [Official Video]','channel':'REiNa Official'})
    app.edit(track)
    actions=app.page.dialog.content.content.controls[0]
    assert app.page.dialog._build_add_commands(indent=0,index={},added_controls=[])
    actions.controls[2].on_click(None)
    fields=app.page.dialog.content.content.controls[1:4]
    assert fields[0].value==track.original_title
    actions.controls[0].on_click(None)
    assert fields[0].value=='Song'
    assert track.title=='Song'  # No saved-track mutation before Save.


def test_individual_cover_choice_still_works(tmp_path,monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(module,'DATA',tmp_path/'data')
    app=Studio(FakePage());app.album=AlbumInfo(is_playlist=True,cover='old.jpg')
    app.tracks=[Track(title='A',cover='a.jpg'),Track(title='B',cover='b.jpg')]
    app.album_view.cover_list.value=PER_TRACK_COVER;app.album_view.choose_cover(None)
    assert [effective_track(t,app.album).cover for t in app.tracks]==['a.jpg','b.jpg']
    app._update_album_from_track(app.tracks[0]);assert app.album.cover==''


def test_manual_artist_correction_wins_until_next_explicit_batch_save(tmp_path,monkeypatch):
    import ui.main_view as module
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(module,'DATA',tmp_path/'data')
    app=Studio(FakePage())
    app.album=AlbumInfo(is_playlist=True,artist='Global',title='Album')
    t=Track(title='Song',artist='Global');app.tracks=[t]
    app.edit(t)
    app.page.dialog.content.content.controls[2].value='Corrected artist'
    app.page.dialog.actions[1].on_click(None)
    assert effective_track(t,app.album).artist=='Corrected artist'
    app.album_view.sync();app.album_view.save(silent=True)
    assert effective_track(t,app.album).artist=='Corrected artist'
    app.album_view.fields['artist'].value='New batch artist'
    app.album_view.save(silent=True,apply_to_tracks=True)
    assert effective_track(t,app.album).artist=='New batch artist'
