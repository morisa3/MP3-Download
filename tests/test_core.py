from pathlib import Path
import sys
import threading
import subprocess
import json
import numpy as np
import pytest
from PIL import Image
from mutagen import File
from mutagen.id3 import ID3
from mutagen.mp4 import MP4
from mutagen.flac import FLAC
from mutagen.wave import WAVE
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.crop import auto_bbox, save_cover, validate_bbox
from core.youtube import metadata, normalize_url, YouTubeService, Cancelled
from core.models import Track, AlbumInfo, effective_track, save_project, load_project, load_session
from core.naming import album_directory
from core.audio import OutputSettings, export_track, measure, loudness_filter, filename
from core.tags import verify_embedded_cover
from core.system import run, ffmpeg_path
from ui.main_view import list_height

@pytest.mark.parametrize('height,width,side,color', [(720,1280,720,(12,70,170)),(540,960,540,(200,30,70)),(720,1280,722,(250,250,250)),(540,960,542,(0,0,0))])
def test_colored_borders(tmp_path,height,width,side,color):
    a=np.empty((height,width,3),dtype=np.uint8)
    a[:]=color
    l=(width-side)//2
    a[:,l:l+side]=np.random.default_rng(7).integers(40,220,(height,side,3),dtype=np.uint8)
    p=tmp_path/'border.jpg'
    Image.fromarray(a).save(p,quality=98,subsampling=0)
    box=auto_bbox(p)
    assert box[2]-box[0] == box[3]-box[1] == height
    assert abs(box[0]-(width-height)//2)<=1
    out=tmp_path/'result.jpg'
    save_cover(p,box,out,540)
    assert Image.open(out).size==(540,540)


def test_full_bleed_and_manual(tmp_path):
    p=tmp_path/'full.png'
    Image.fromarray(np.random.default_rng(2).integers(0,255,(720,1280,3),dtype=np.uint8)).save(p)
    assert auto_bbox(p)==[280,0,1000,720]
    validate_bbox(p,[50,50,590,590])
    with pytest.raises(ValueError): validate_bbox(p,[-1,0,539,540])
    with pytest.raises(ValueError): validate_bbox(p,[0,0,722,720])


def test_metadata_dates_and_channel():
    t=metadata({'title':'Song','channel':'正確頻道','uploader':'other','upload_date':'20190807','release_date':'20260915'})
    assert t.artist=='正確頻道' and t.date=='2019-08-07'
    assert metadata({'title':'x'}).date==''
    assert metadata({'uploader':'fallback','upload_date':'invalid'}).artist=='fallback'
    assert metadata({'timestamp':0}).date=='1970-01-01'


def test_urls():
    url='https://www.youtube.com/watch?v=abc&list=PL123'
    assert normalize_url(url,'single').endswith('watch?v=abc')
    assert normalize_url(url).endswith('playlist?list=PL123')
    with pytest.raises(ValueError): normalize_url('https://youtube.com.evil.test/watch?v=abc')


def test_project_and_names(tmp_path):
    t=Track(title='中文/Title?', artist='頻道',date='2020-01-02',bbox=[0,0,540,540])
    save_project(tmp_path/'session.json',[t])
    assert load_project(tmp_path/'session.json')[0]==t
    assert '/' not in filename(t,'<標題>')
    assert filename(Track(title='CON'),'<標題>')=='_CON'
    assert list_height(1000,768)<=378
    assert list_height(1,768)<list_height(5,768)


@pytest.fixture
def audio_fixture(tmp_path):
    src=tmp_path/'source.wav'
    subprocess.run([ffmpeg_path(),'-v','error','-f','lavfi','-i','sine=frequency=700:duration=3',str(src)],check=True)
    cover=tmp_path/'cover.jpg'
    Image.new('RGB',(540,540),'teal').save(cover)
    return Track(title='測試標題',artist='頻道',albumartist='頻道',album='專輯',date='2018-06-03',tracknumber='2',source=str(src),cover=str(cover),bbox=[0,0,540,540])

@pytest.mark.parametrize('fmt',['mp3','m4a','flac','wav'])
def test_real_export_formats(tmp_path,audio_fixture,fmt):
    t=audio_fixture
    original=Path(t.source).read_bytes()
    s=OutputSettings(str(tmp_path/'out'),format=fmt,cover_size=540,extra_covers='per_track',write_report=True)
    out=export_track(t,s)
    assert out.is_file()
    assert Path(t.source).read_bytes()==original
    assert abs(float(t.measurement['output_verification']['input_i'])-s.lufs)<1
    assert Image.open(out.with_suffix(out.suffix+'.cover.jpg')).size==(540,540)
    if fmt=='mp3':
        tags=ID3(out)
        assert str(tags['TPE1'])=='頻道' and str(tags['TDRC'])=='2018-06-03'
        assert len(tags.getall('APIC'))==1
    elif fmt=='m4a':
        tags=MP4(out)
        assert tags['\xa9ART']==['頻道'] and tags['covr']
    elif fmt=='flac':
        tags=FLAC(out)
        assert tags['date']==['2018-06-03'] and len(tags.pictures)==1
    else:
        assert WAVE(out).tags.getall('APIC')
    second=export_track(t,OutputSettings(str(tmp_path/'out'),format=fmt,normalize=False))
    assert second!=out and out.exists()


def test_cancel_cleanup(tmp_path,audio_fixture):
    e=threading.Event()
    e.set()
    with pytest.raises(Cancelled):
        run([ffmpeg_path(),'-re','-f','lavfi','-i','sine=duration=10','-f','null','-'],e)


def test_silence():
    s=OutputSettings('x')
    assert loudness_filter(dict(input_i='-inf',input_tp='-inf',input_lra='0',input_thresh='-70',target_offset='inf'),s) is None


def test_failed_export_does_not_leave_placeholder(tmp_path,audio_fixture):
    audio_fixture.bbox=[0,0,600,600]
    folder=tmp_path/'bad'
    with pytest.raises(ValueError): export_track(audio_fixture,OutputSettings(str(folder),normalize=False))
    assert list(folder.iterdir())==[]


def test_embed_verification_rejects_missing_cover(tmp_path,audio_fixture,monkeypatch):
    import core.audio as module
    monkeypatch.setattr(module,'write_tags',lambda path,track,cover:None)
    out=tmp_path/'out'
    with pytest.raises(RuntimeError,match='封面未正確內嵌'):
        export_track(audio_fixture,OutputSettings(str(out),normalize=False))
    assert list(out.iterdir())==[]


def test_playlist_full_download_metadata_mock(tmp_path,monkeypatch):
    # Exercise real orchestration with unavailable thumbnail, missing date and playlist entries.
    import core.youtube as module
    calls=[]
    class FakeYDL:
        def __init__(self,opts): self.opts=opts
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def extract_info(self,url,download):
            calls.append((url,download,self.opts))
            if 'playlist?' in url:
                return {'title':'Playlist','uploader':'Wrong owner','entries':[{'id':'abcdefghijk','url':'abcdefghijk'},None]}
            if download:
                p=Path(self.opts['outtmpl'].replace('%(ext)s','webm'))
                p.write_bytes(b'fixture')
                return {'title':'Actual song','channel':'Actual channel','upload_date':'20120103', 'requested_downloads':[{'filepath':str(p)}]}
            return {'title':'Actual song','channel':'Actual channel','upload_date':'20120103'}
    monkeypatch.setattr(module.yt_dlp,'YoutubeDL',FakeYDL)
    service=YouTubeService(tmp_path)
    entries=service.entries('https://youtube.com/playlist?list=PL123')
    assert len(entries)==2 and entries[1][0]=='' and entries[1][1]==2
    t=Track(url=entries[0][0],album=entries[0][2])
    service.download(t)
    assert t.artist=='Actual' and t.date=='2012-01-03'
    assert t.original_artist=='Actual channel'
    assert t.album=='Playlist' and t.source
    assert calls[-1][2].get('extract_flat') is None


def test_playlist_retry_unresolved_position(tmp_path, monkeypatch):
    import core.youtube as module
    from io import BytesIO
    from PIL import Image
    picture=BytesIO()
    Image.new('RGB',(200,200),'blue').save(picture,format='JPEG')
    class FakeYDL:
        def __init__(self, opts): self.opts=opts
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def extract_info(self,url,download):
            if 'playlist?' in url:
                assert self.opts.get('playlist_items')=='2'
                return {'entries':[{'id':'abcdefghijk'}]}
            p=Path(self.opts['outtmpl'].replace('%(ext)s','webm'))
            p.write_bytes(b'fixture')
            return {'title':'Recovered','channel':'Creator', 'requested_downloads':[{'filepath':str(p)}],
                    'thumbnail':'https://example.com/cover.jpg'}
        def urlopen(self,url): return BytesIO(picture.getvalue())
    monkeypatch.setattr(module.yt_dlp,'YoutubeDL',FakeYDL)
    t=Track(playlist_url='https://youtube.com/playlist?list=PL123', tracknumber='2', status='下載失敗')
    service=YouTubeService(tmp_path)
    service.download(t)
    assert t.url.endswith('watch?v=abcdefghijk') and t.title=='Recovered'
    assert t.artist=='Creator' and t.cover and t.download_attempts==1


def test_album_folder_and_shared_metadata(tmp_path):
    a=AlbumInfo(is_playlist=True,title='REVIVE IN COLOR',artist='REiNa',date='2026-09-26')
    assert album_directory(a)=='[260926] REiNa-REVIVE IN COLOR'
    t=Track(title='Song',artist='Video channel',album='Video playlist')
    result=effective_track(t,a)
    assert result.artist=='REiNa' and result.album=='REVIVE IN COLOR' and t.artist=='Video channel'
    save_project(tmp_path/'session.json',[t],a)
    assert load_session(tmp_path/'session.json')[1]==a
    a.date='20260926'
    with pytest.raises(ValueError): album_directory(a)
