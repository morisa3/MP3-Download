from pathlib import Path
import threading
import subprocess
import pytest
from PIL import Image
from mutagen import File
from core.models import Track
from core.tags import write_tags
from core.loudness_job import normalize_file

@pytest.fixture
def audio_source(tmp_path,monkeypatch):
    monkeypatch.setattr('core.loudness_job.temp_directory',lambda:tmp_path)
    def create(ext):
        path=tmp_path/('song.'+ext)
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=4',str(path)],check=True)
        image=tmp_path/'cover.jpg';Image.new('RGB',(60,60),'blue').save(image)
        if ext in ('mp3','m4a','flac','wav'):
            write_tags(path,Track(title='原始標題',artist='Artist',album='Album'),image)
        else:
            f=File(path);f['title']='原始標題';f.save()
        return path
    return create

@pytest.mark.parametrize('ext',['mp3','m4a','flac','wav','ogg','opus'])
def test_save_preserves_source_and_tags(audio_source,tmp_path,ext):
    source=audio_source(ext);original=source.read_bytes()
    dest,before,after=normalize_file(source,-14,-1,False,tmp_path,threading.Event())
    assert source.read_bytes()==original
    assert dest!=source and dest.is_file()
    assert abs(float(after['input_i'])+14)<1
    assert File(dest).tags
    second,_,_=normalize_file(source,-14,-1,False,tmp_path,threading.Event())
    assert second!=dest and dest.is_file()

def test_overwrite_and_failed_commit_leave_original(audio_source,tmp_path,monkeypatch):
    source=audio_source('flac');original=source.read_bytes()
    def fail(*a): raise PermissionError('locked')
    with monkeypatch.context() as context:
        context.setattr('core.loudness_job.os.replace',fail)
        with pytest.raises(PermissionError): normalize_file(source,-14,-1,True,'',threading.Event())
    assert source.read_bytes()==original
    assert not list(tmp_path.glob('.ez-loudness-*'))
    dest,_,after=normalize_file(source,-14,-1,True,'',threading.Event())
    assert dest==source and source.read_bytes()!=original
    assert File(dest)['title']==['原始標題']

def test_cancel_before_commit_preserves_original(audio_source,tmp_path):
    from core.youtube import Cancelled
    source=audio_source('mp3');original=source.read_bytes();cancel=threading.Event()
    def progress(message):
        if message=='驗證輸出響度':cancel.set()
    with pytest.raises(Cancelled):normalize_file(source,-14,-1,True,'',cancel,progress)
    assert source.read_bytes()==original
