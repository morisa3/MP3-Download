"""Preview the effective crop; hovering enlarges it within the current row."""
from functools import lru_cache
from pathlib import Path
import flet as ft
from core.crop import preview, auto_bbox

@lru_cache(maxsize=96)
def _image(path, modified, bbox):
    return preview(path, list(bbox))

def track_thumbnail(track):
    try:
        path = Path(track.cover)
        if not track.cover or not path.is_file():
            raise ValueError('沒有封面')
        data = _image(str(path), path.stat().st_mtime_ns, tuple(track.bbox or auto_bbox(path)))
    except Exception:
        return ft.Container(ft.Icon(ft.icons.IMAGE_NOT_SUPPORTED, size=22, color='#90A4AE'),
                            width=48, height=48, tooltip='沒有可預覽的封面')
    picture = ft.Image(src_base64=data, width=48, height=48, fit=ft.ImageFit.CONTAIN)
    def hover(e):
        side = 200 if e.data == 'true' else 48
        picture.width = picture.height = side
        box.width = box.height = side
        box.update()
    box = ft.Container(content=picture, width=48, height=48, on_hover=hover,
                       tooltip='目前套用的封面裁切；滑入放大、移開縮回', border_radius=4)
    return box
