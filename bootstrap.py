"""Install missing dependencies once, then open Flet in the same launch.

The shared per-user virtual environment is reused across unpacked app updates. The expensive
diagnostic is intentionally a separate, optional check_environment.py command.
"""
from importlib import metadata
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
LAUNCHED = False
REQUIRED = ('flet', 'yt-dlp', 'Pillow', 'numpy', 'mutagen', 'opencv-python-headless')


def needs_install():
    for name in REQUIRED:
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            return True
        if name == 'flet' and version != '0.21.2':
            return True
    return False


def windowed():
    """start.vbs runs pythonw.exe: there is no console for pip output or error text."""
    return '--windowed' in sys.argv[1:]


def attach_log():
    # pythonw leaves sys.stdout/stderr as None; yt-dlp and print() still need somewhere to write.
    from core.system import DATA
    DATA.mkdir(parents=True, exist_ok=True)
    log = open(DATA / 'console.log', 'a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def message_box(text):
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, 'EZ Audio Studio', 16)


def main():
    if windowed():
        attach_log()
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError('請使用 Python 3.12 建立 .venv')
    if needs_install() and windowed():
        # Installing needs a visible console; start.bat installs and then opens the app.
        subprocess.Popen(['cmd', '/c', str(ROOT / 'start.bat')], cwd=str(ROOT),
                         creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
        return
    if needs_install():
        print('Installing missing dependencies into the app environment…', flush=True)
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r',
                               str(ROOT / 'requirements.txt')])
        if needs_install():
            raise RuntimeError('套件尚未安裝完整，請查看上方安裝訊息')
    from main import main as launch
    global LAUNCHED
    LAUNCHED = True  # main.py shows its own error dialog from here on
    launch()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'EZ Audio Studio 啟動失敗：{exc}', file=sys.stderr)
        if windowed() and not LAUNCHED:
            message_box(f'EZ Audio Studio 啟動失敗：{exc}\n請改用 start.bat 查看詳細訊息。')
        sys.exit(1)
