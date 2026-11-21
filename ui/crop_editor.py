import flet as ft
from .dialogs import open_dialog, close_dialog
from core.crop import open_image, auto_bbox, preview, avatar_bbox


def show_crop_editor(page, track, on_saved):
    if not track.cover:
        return
    w, h = open_image(track.cover).size
    box = list(track.bbox or auto_bbox(track.cover))
    state = {'x': box[0], 'y': box[1], 's': box[2]-box[0]}
    original = ft.Image(src_base64=preview(track.cover), width=280, height=230, fit=ft.ImageFit.CONTAIN)
    result = ft.Image(width=230, height=230, fit=ft.ImageFit.CONTAIN)
    note = ft.Text()
    error = ft.Text(color='red')
    fields = {}
    sliders = {}

    def refresh():
        s = max(1, min(state['s'], w, h))
        state.update(s=s, x=max(0, min(state['x'], w-s)), y=max(0, min(state['y'], h-s)))
        b = [state['x'], state['y'], state['x']+s, state['y']+s]
        result.src_base64 = preview(track.cover, b)
        note.value = f'原圖 {w}×{h}｜裁切 {s}×{s} px｜左 {b[0]} 上 {b[1]} 右 {b[2]} 下 {b[3]}'
        for key in fields:
            fields[key].value = str(state[key])
            sliders[key].value = state[key]
        error.value = ''

    def change(key, value):
        try:
            state[key] = int(float(value))
            refresh()
        except (ValueError, TypeError):
            error.value = '請輸入整數'
        page.update()

    for key, label, maximum in [('x', '左 L', w-1), ('y', '上 T', h-1), ('s', '正方形邊長', min(w, h))]:
        fields[key] = ft.TextField(label=label, width=120, value=str(state[key]),
                                  on_submit=lambda e, k=key: change(k, e.control.value),
                                  on_blur=lambda e, k=key: change(k, e.control.value))
        sliders[key] = ft.Slider(min=1 if key == 's' else 0, max=max(2, maximum), value=state[key],
                                 expand=True, on_change=lambda e, k=key: change(k, e.control.value))
    def automatic(e):
        b = auto_bbox(track.cover)
        state.update(x=b[0], y=b[1], s=b[2]-b[0])
        refresh()
        page.update()
    def center(e):
        s = min(w, h)
        state.update(x=(w-s)//2, y=(h-s)//2, s=s)
        refresh()
        page.update()
    def face(e):
        try:
            b = avatar_bbox(track.cover, int(face_size.value))
            state.update(x=b[0], y=b[1], s=b[2]-b[0])
            refresh()
        except Exception as exc:
            error.value = str(exc)
        page.update()
    face_size = ft.Dropdown(value="720", width=110, options=[ft.dropdown.Option(x) for x in ("720", "600", "480")])
    def save(e):
        try:
            for key in fields:
                state[key] = int(fields[key].value)
            refresh()
        except ValueError:
            error.value = '請輸入整數'
            page.update()
            return
        track.bbox = [state['x'], state['y'], state['x']+state['s'], state['y']+state['s']]
        close_dialog(page, dialog)
        on_saved()
    refresh()
    dialog = ft.AlertDialog(modal=True, title=ft.Text('封面裁切｜正方形像素鎖定'),
        content=ft.Container(width=650, content=ft.Column([
            ft.Row([original, result], wrap=True), note,
            ft.Text('拖曳滑桿或輸入座標後按 Enter。滿版圖片可調整左、上與邊長。'),
            *[ft.Row([fields[k], sliders[k]]) for k in fields], error,
            ft.Row([ft.TextButton('自動去色邊', on_click=automatic), ft.TextButton('置中最大正方形', on_click=center)], wrap=True),
            ft.Row([face_size, ft.TextButton('頭像置中（未偵測到則圖片置中）', on_click=face)], wrap=True)
        ], tight=True, scroll=ft.ScrollMode.AUTO)),
        actions=[ft.TextButton('取消', on_click=lambda e: close_dialog(page, dialog)), ft.FilledButton('套用裁切', on_click=save)])
    open_dialog(page, dialog)
