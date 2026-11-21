import importlib.metadata
import shutil
import subprocess
import sys
from core.system import DATA, ffmpeg_path, writable_directory


def main():
    errors = []
    print('EZ Audio Studio - Environment check')
    print('Python:', sys.version)
    for package in ('flet', 'yt-dlp', 'Pillow', 'numpy', 'mutagen', 'opencv-python-headless'):
        try:
            installed = importlib.metadata.version(package)
            print(package, installed)
            if package == 'flet' and installed != '0.21.2':
                errors.append('Flet must be 0.21.2. Run install.bat again.')
        except importlib.metadata.PackageNotFoundError:
            errors.append('Missing package: '+package)
    try:
        command = ffmpeg_path()
        r = subprocess.run([command, '-version'], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=15)
        if r.returncode:
            errors.append('FFmpeg cannot run')
        print('FFmpeg:', command)
        filters = subprocess.run([command, '-filters'], capture_output=True, text=True, timeout=15)
        if 'loudnorm' not in filters.stdout:
            errors.append('FFmpeg loudnorm filter missing')
        writable_directory(DATA)
    except Exception as exc:
        errors.append(str(exc))
    deno, node = shutil.which('deno'), shutil.which('node')
    print('JavaScript runtime:', deno or node or 'NOT FOUND')
    if not deno and not node:
        print('YouTube downloads may fail without a JS runtime. Install Deno then reopen this window:')
        print('winget install --id DenoLand.Deno -e')
    for err in errors:
        print('ERROR:', err)
    return bool(errors)

if __name__ == '__main__':
    sys.exit(main())
