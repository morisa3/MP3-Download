"""Desktop entry point (Python 3.12, Flet 0.21.2)."""
import sys
import logging
from pathlib import Path


def main():
    try:
        import flet as ft
        from importlib.metadata import version
        if version('flet') != '0.21.2':
            raise RuntimeError('本程式需要 Flet 0.21.2，請先執行 install.bat；請勿直接沿用其他專案環境。')
        from core.system import DATA
        DATA.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=DATA/'studio.log', level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
        from ui.main_view import Studio
        async def app(page):
            Studio(page)
        ft.app(target=app)
    except Exception as exc:
        logging.exception('Startup failed')
        message = f'啟動失敗：{exc}\n請先執行 install.bat，再使用 start.bat。'
        print(message)
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, 'EZ Audio Studio', 16)
        raise

if __name__ == '__main__':
    main()
