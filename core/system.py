from pathlib import Path
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'.local'/'share'))) / 'EZAudioStudio'


def ffmpeg_path():
    candidates = [ROOT/'ffmpeg'/'bin'/'ffmpeg.exe', ROOT/'ffmpeg'/'bin'/'ffmpeg']
    if os.name != 'nt':
        candidates = [x for x in candidates if x.suffix != '.exe']
    found = next((str(p) for p in candidates if p.is_file()), None) or shutil.which('ffmpeg')
    if not found:
        raise FileNotFoundError('找不到 FFmpeg。請將舊程式的 ffmpeg 資料夾複製到本程式目錄，或安裝 FFmpeg 並加入 PATH。')
    return found


def temp_directory():
    path = ROOT / "temp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run(args, cancel=None):
    if cancel and cancel.is_set():
        from .youtube import Cancelled
        raise Cancelled('已停止')
    # File-backed stderr avoids deadlocks on long FFmpeg output and allows cancellation.
    with tempfile.TemporaryFile(dir=temp_directory()) as log:
        process = subprocess.Popen([str(x) for x in args], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=log,
            creationflags=0x08000000 if os.name == 'nt' else 0)
        try:
            while True:
                try:
                    code = process.wait(timeout=.2)
                    break
                except subprocess.TimeoutExpired:
                    if cancel and cancel.is_set():
                        from .youtube import Cancelled
                        raise Cancelled('已停止')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        log.seek(0)
        text = log.read().decode('utf-8', errors='replace')
    if code:
        raise RuntimeError(text[-3500:])
    return text


def writable_directory(path):
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path):
        pass
    return path
