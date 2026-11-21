from pathlib import Path
import copy
import flet as ft
from core.crop import auto_bbox, open_image, validate_bbox
from core.tags import import_audio
from core.models import Track
from core.matching import match_titles
from core.youtube import check_cancel, Cancelled
from core.audio import measure


class BatchActions:
    def add_batch_tools(self):
        self.import_dir_picker = ft.FilePicker(on_result=self.import_dir_picked)
        self.match_cover_picker = ft.FilePicker(on_result=self.match_cover_picked)
        self.page.overlay.extend([self.import_dir_picker, self.match_cover_picker])
        return ft.PopupMenuButton(
            content=ft.Container(ft.Row([ft.Icon(ft.icons.BUILD, size=17, color='white'),
                ft.Text('輔助工具', size=13, color='white'),
                ft.Icon(ft.icons.ARROW_DROP_DOWN, size=17, color='white')], spacing=5, tight=True),
                bgcolor='#6f42c1', border_radius=6, padding=ft.padding.symmetric(horizontal=12), height=34),
            items=[
                ft.PopupMenuItem(text='匯入資料夾（音檔與圖片）', icon=ft.icons.FOLDER_OPEN,
                    on_click=lambda e: self.import_dir_picker.get_directory_path()),
                ft.PopupMenuItem(text='從本機資料夾依檔名找封面',
                    on_click=lambda e: self.match_cover_picker.get_directory_path()),
                ft.PopupMenuItem(text='複製第一張裁切座標（同尺寸圖）', on_click=self.copy_crop),
                ft.PopupMenuItem(text='選清單中任一張作為共用封面', on_click=self.shared_cover_clicked),
                ft.PopupMenuItem(text='僅掃描響度', icon=ft.icons.VOLUME_UP, on_click=self.scan_clicked),
            ])

    async def import_dir_picked(self, e):
        if not e.path:
            return
        folder = Path(e.path)
        def work():
            count = 0
            for path in sorted(folder.iterdir()):
                check_cancel(self.cancel)
                if not path.is_file():
                    continue
                try:
                    if path.suffix.lower() in ('.mp3','.m4a','.flac','.wav','.ogg','.opus','.webm'):
                        if not any(t.source==str(path.resolve()) for t in self.tracks):
                            self.tracks.append(import_audio(path,self.cache))
                            count += 1
                    elif path.suffix.lower() in ('.jpg','.jpeg','.png','.webp'):
                        self.tracks.append(Track(title=path.stem,cover=str(path.resolve()),bbox=auto_bbox(path),status='僅封面'))
                        count += 1
                except Exception as exc:
                    self.emit(f'{path.name}：{exc}')
            self.emit(f'已匯入 {count} 個項目（只讀取此資料夾，不含子資料夾）。')
        await self.job(work)

    async def match_cover_picked(self, e):
        if not e.path:
            return
        selected = [t for t in self.tracks if t.selected and t.source]
        folder = Path(e.path)
        def work():
            available = [p for p in folder.iterdir() if p.suffix.lower() in ('.jpg','.jpeg','.png','.webp')]
            count=0
            for t in selected:
                check_cancel(self.cancel)
                candidates=sorted([(max(match_titles(t.title,p.stem),match_titles(Path(t.source).stem,p.stem)),str(p),p) for p in available],reverse=True)
                if not candidates or candidates[0][0]<.8 or (len(candidates)>1 and candidates[0][0]-candidates[1][0]<.08):
                    self.emit(f'無可靠唯一封面配對：{t.title}')
                    continue
                p=candidates[0][2]
                try:
                    box=auto_bbox(p)
                    t.cover,t.bbox=str(p.resolve()),box
                    available.remove(p)
                    count+=1
                except Exception as exc:
                    self.emit(f'{p.name}：{exc}')
            self.emit(f'已預先配對 {count} 張封面；請確認後輸出。')
        await self.job(work)

    def copy_crop(self,e):
        selected=[t for t in self.tracks if t.selected and t.cover]
        if not selected:
            self.message('請勾選有封面的項目。')
            return
        first=selected[0]
        count=0
        try:
            dims=open_image(first.cover).size
            box=first.bbox or auto_bbox(first.cover)
            for t in selected:
                if open_image(t.cover).size==dims:
                    validate_bbox(t.cover,box)
                    t.bbox=list(box)
                    count+=1
            self.persist()
            self.message(f'已套用至 {count} 張同尺寸原圖；其他原圖尺寸不同，請個別裁切。')
        except Exception as exc:
            self.message(exc)

    def copy_cover(self,e):
        selected=[t for t in self.tracks if t.selected]
        first=next((t for t in selected if t.cover),None)
        if not first:
            self.message('請先為一個勾選項目選擇封面。')
            return
        for t in selected:
            t.cover,t.bbox=first.cover,list(first.bbox)
        self.persist()
        self.render()
        self.message(f'已套用第一張封面至 {len(selected)} 個項目。')

    async def scan_clicked(self,e):
        selected=[t for t in self.tracks if t.selected and t.source]
        try:
            settings=self.settings()
            settings.validate()
        except Exception as exc:
            self.message(exc)
            return
        def work():
            for t in selected:
                check_cancel(self.cancel)
                try:
                    t.measurement=measure(t.source,settings,self.cancel)
                    t.status=f'{t.measurement["input_i"]} LUFS / {t.measurement["input_tp"]} dBTP'
                    self.emit(f'{t.title}：{t.status}')
                except Cancelled:
                    raise
                except Exception as exc:
                    self.emit(f'{t.title}：{exc}')
            self.emit('響度掃描完成，來源檔案保持原樣。')
        await self.job(work)
