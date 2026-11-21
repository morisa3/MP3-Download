"""Install missing dependencies once, then open Flet in the same launch.

The shared per-user virtual environment is reused across unpacked app updates. The expensive
diagnostic is intentionally a separate, optional check_environment.py command.
"""
from importlib import metadata
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
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


def main():
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError('請使用 Python 3.12 建立 .venv')
    if needs_install():
        print('Installing missing dependencies into the app environment…', flush=True)
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r',
                               str(ROOT / 'requirements.txt')])
        if needs_install():
            raise RuntimeError('套件尚未安裝完整，請查看上方安裝訊息')
    from main import main as launch
    launch()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'EZ Audio Studio 啟動失敗：{exc}', file=sys.stderr)
        sys.exit(1)
