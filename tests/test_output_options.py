from pathlib import Path
import sys
import subprocess
import pytest
from PIL import Image
from mutagen.id3 import ID3
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.models import Track
from core.audio import OutputSettings,export_track
from core.naming import relative_name,validate_template
from core.system import ffmpeg_path
from ui.naming_editor import show_naming_editor
from ui.cover_selector import show_cover_selector
from test_ui import FakePage


def test_naming_fields_and_folders():
    t=Track(title='IGNITION',artist='REiNa',albumartist='REiNa',album='AFTER THE END',date='2026-08-12',tracknumber='2',composer='Writer',discnumber='1',totaldiscs='2',genre='Pop')
    assert str(relative_name(t,'<曲目歌星>-<標題>'))=='REiNa-IGNITION'
    assert relative_name(t,r'<專輯歌星>\<專輯>\<號碼> - <標題>')==Path('REiNa','AFTER THE END','02 - IGNITION')
    assert str(relative_name(t,'<年份>-<Composer>-<Disc Number>-<Total Discs>-<流派>'))=='2026-Writer-1-2-Pop'
    t.artist='../untrusted/name'
    assert len(relative_name(t,'<曲目歌星>-<標題>').parts)==1


@pytest.mark.parametrize('template',[r'..\<標題>',r'C:\<標題>',r'\<標題>','<未知>','<標題',''])
def test_invalid_templates(template):
    with pytest.raises(ValueError):validate_template(template)


def make_track(tmp_path):
    src=tmp_path/'source.wav'
    subprocess.run([ffmpeg_path(),'-v','error','-f','lavfi','-i','sine=duration=1',str(src)],check=True)
    cover=tmp_path/'original.jpg';shared=tmp_path/'shared.jpg'
    Image.new('RGB',(540,540),'red').save(cover)
    Image.new('RGB',(540,540),'blue').save(shared)
    t=Track(title='Song',artist='Artist',source=str(src),cover=str(cover),bbox=[0,0,540,540],composer='Composer',discnumber='1',totaldiscs='2')
    return t,shared


def test_clean_default_and_embedded_picture(tmp_path):
    t,_=make_track(tmp_path)
    dest=export_track(t,OutputSettings(str(tmp_path/'out'),normalize=False))
    assert list(dest.parent.iterdir())==[dest]
    tags=ID3(dest)
    assert tags.getall('APIC') and str(tags['TCOM'])=='Composer' and str(tags['TPOS'])=='1/2'


def test_shared_picture_and_single_cover(tmp_path):
    t,shared=make_track(tmp_path)
    s=OutputSettings(str(tmp_path/'out'),normalize=False,shared_cover=str(shared),shared_bbox=[0,0,540,540],extra_covers='single')
    first=export_track(t,s)
    t.title='Song 2'
    second=export_track(t,s)
    assert sorted(p.name for p in first.parent.iterdir())==['Artist-Song 2.mp3','Artist-Song.mp3','Cover.jpg']
    assert ID3(first).getall('APIC')[0].data==ID3(second).getall('APIC')[0].data==(first.parent/'Cover.jpg').read_bytes()
    assert t.cover!=str(shared)


def test_no_embed_and_relative_folders(tmp_path):
    t,_=make_track(tmp_path)
    dest=export_track(t,OutputSettings(str(tmp_path/'out'),normalize=False,embed_cover=False,naming=r'<曲目歌星>\<標題>'))
    assert dest.parent.name=='Artist'
    assert not ID3(dest).getall('APIC')
    assert not list((tmp_path/'out').rglob('*.json'))


def test_single_cover_requires_explicit_shared_source(tmp_path):
    with pytest.raises(ValueError):OutputSettings(str(tmp_path),extra_covers='single').validate()


def test_new_dialogs_021(tmp_path):
    t,shared=make_track(tmp_path)
    page=FakePage()
    show_naming_editor(page,[t],'<曲目歌星>-<標題>','mp3',tmp_path/'presets.json',lambda x:None)
    assert page.dialog._build_add_commands(indent=0,index={},added_controls=[])
    show_cover_selector(page,[t],t.uid,lambda x:None)
    assert page.dialog._build_add_commands(indent=0,index={},added_controls=[])


def test_export_uses_app_temp_and_supports_other_drive(tmp_path, monkeypatch):
    import core.system as system
    import core.audio as audio
    app_root = tmp_path / 'app'
    monkeypatch.setattr(system, 'ROOT', app_root)
    track, _ = make_track(tmp_path)
    real_run = audio.run
    encoded_paths = []
    def recording_run(args, cancel=None):
        encoded_paths.append(Path(args[-1]))
        return real_run(args, cancel)
    monkeypatch.setattr(audio, 'run', recording_run)
    # Moving via rename would fail across Windows drives; prohibit that route here.
    def no_rename(*args, **kwargs):
        raise OSError('simulated cross-device rename')
    monkeypatch.setattr(Path, 'replace', no_rename)
    dest = export_track(track, OutputSettings(str(tmp_path/'other_drive'), normalize=False))
    assert dest.stat().st_size > 0
    assert encoded_paths[0].is_relative_to(app_root/'temp')
    assert list((app_root/'temp').iterdir()) == []
    assert ID3(dest).getall('APIC')


def test_copy_failure_cleans_temp_and_output(tmp_path, monkeypatch):
    import core.system as system
    import core.audio as audio
    monkeypatch.setattr(system, 'ROOT', tmp_path/'app')
    track, _ = make_track(tmp_path)
    def partial_copy(source, dest):
        Path(dest).write_bytes(b'partial')
        raise OSError('simulated full output drive')
    monkeypatch.setattr(audio.shutil, 'copyfile', partial_copy)
    out = tmp_path/'out'
    with pytest.raises(OSError, match='full output drive'):
        export_track(track, OutputSettings(str(out), normalize=False))
    assert list(out.iterdir()) == []
    assert list((tmp_path/'app'/'temp').iterdir()) == []
