import flet as ft
from core.crop import preview, auto_bbox
from .dialogs import open_dialog, close_dialog


def show_cover_selector(page, tracks, current_uid, on_apply):
    candidates = [t for t in tracks if t.cover]
    choices = ft.Dropdown(label='封面來源（可選任一首，不一定要勾選該首輸出）',
        value=current_uid or '', options=[ft.dropdown.Option('', '各歌曲使用自己的封面')]+[
            ft.dropdown.Option(t.uid,f'{t.tracknumber or "—"}｜{t.title}') for t in candidates],width=590)
    img=ft.Image(width=280,height=280,fit=ft.ImageFit.CONTAIN,visible=False)
    note=ft.Text('')
    def refresh(e=None):
        t=next((t for t in candidates if t.uid==choices.value),None)
        img.visible=False
        if t:
            try:
                img.src_base64=preview(t.cover,t.bbox or auto_bbox(t.cover))
                img.visible=True
                note.value=f'共用封面：第 {t.tracknumber or "—"} 首 {t.title}\n所有勾選輸出的歌曲都使用這張圖，不修改各首原本的封面。'
            except Exception as exc:
                note.value=str(exc)
        else:
            note.value='各首音檔使用自己那列指定的圖片與裁切範圍。'
        page.update()
    choices.on_change=refresh
    def apply(e):
        t=next((t for t in candidates if t.uid==choices.value),None)
        if t:
            try:
                preview(t.cover,t.bbox or auto_bbox(t.cover))
            except Exception as exc:
                note.value=str(exc);page.update();return
        close_dialog(page,dialog)
        on_apply(t.uid if t else '')
    dialog=ft.AlertDialog(modal=True,title=ft.Text('從清單選擇共用封面'),
        content=ft.Container(width=610,content=ft.Column([choices,img,note,
            ft.Text('例如第 1 首是 MV 畫面，可以選第 2 首的專輯圖。需要裁切時，先調整來源歌曲那一列的裁切。')],tight=True,scroll=ft.ScrollMode.AUTO)),
        actions=[ft.TextButton('取消',on_click=lambda e:close_dialog(page,dialog)),ft.FilledButton('套用封面來源',on_click=apply)])
    open_dialog(page,dialog)
    refresh()
