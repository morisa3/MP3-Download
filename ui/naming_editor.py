"""Flet 0.21.2 template builder with ordering, saved presets and live previews."""
import json
import re
import flet as ft
from core.naming import TOKENS, PRESETS, relative_name, validate_template
from .dialogs import open_dialog, close_dialog


def show_naming_editor(page, tracks, current, extension, storage, on_apply):
    presets = list(PRESETS)
    try:
        saved = json.loads(storage.read_text(encoding='utf-8'))
        presets += [p for p in saved if isinstance(p, str) and p not in presets]
    except (OSError, ValueError, TypeError):
        pass
    template = ft.TextField(label='檔名／相對資料夾格式（可直接編輯）', value=current, expand=True)
    choices = ft.Dropdown(label='儲存的格式', options=[ft.dropdown.Option(p) for p in presets], expand=True)
    parts_view = ft.Column(spacing=0)
    preview = ft.Text(selectable=True)
    error = ft.Text(color='red')

    def tokens():
        return re.findall(r'<[^>]+>|[^<]+|<', template.value or '')

    def move(index, offset):
        parts = tokens()
        other = index+offset
        if 0 <= other < len(parts):
            parts[index], parts[other] = parts[other], parts[index]
            template.value = ''.join(parts)
            refresh()

    def remove(index):
        parts = tokens()
        parts.pop(index)
        template.value = ''.join(parts)
        refresh()

    def refresh(e=None):
        parts_view.controls = [ft.Row([ft.Text(part, expand=True),
            ft.IconButton(ft.icons.ARROW_UPWARD, tooltip='往前', on_click=lambda e,i=i:move(i,-1)),
            ft.IconButton(ft.icons.ARROW_DOWNWARD, tooltip='往後', on_click=lambda e,i=i:move(i,1)),
            ft.IconButton(ft.icons.DELETE_OUTLINE, tooltip='移除此片段', on_click=lambda e,i=i:remove(i))]) for i,part in enumerate(tokens())]
        try:
            validate_template(template.value)
            examples = [t for t in tracks if t.selected and t.source] or tracks
            preview.value = '\n'.join(str(relative_name(t,template.value))+'.'+extension for t in examples[:12]) or '先下載或匯入歌曲，即可預覽實際檔名。'
            error.value = ''
        except ValueError as exc:
            error.value = str(exc)
            preview.value = ''
        page.update()

    def append(value):
        template.value = (template.value or '')+value
        refresh()

    def choose(e):
        template.value = choices.value
        refresh()

    def save_preset(e):
        try:
            validate_template(template.value)
            if template.value not in presets:
                presets.append(template.value)
            storage.parent.mkdir(parents=True,exist_ok=True)
            storage.write_text(json.dumps(presets,ensure_ascii=False,indent=2),encoding='utf-8')
            choices.options = [ft.dropdown.Option(p) for p in presets]
            choices.value = template.value
            error.value = '已儲存格式'
        except (ValueError,OSError) as exc:
            error.value = str(exc)
        page.update()

    def apply(e):
        try:
            validate_template(template.value)
        except ValueError as exc:
            error.value=str(exc)
            page.update()
            return
        close_dialog(page,dialog)
        on_apply(template.value)

    template.on_change=refresh
    choices.on_change=choose
    dialog=ft.AlertDialog(modal=True,title=ft.Text('排列檔名與預覽'),content=ft.Container(width=740,
        content=ft.Column([ft.Row([choices,ft.TextButton('儲存目前格式',on_click=save_preset)]),
            ft.Row([template,ft.TextButton('清空',on_click=lambda e:(setattr(template,'value',''),refresh()))]),
            ft.Row([ft.OutlinedButton(token,on_click=lambda e,t=token:append(t)) for token in TOKENS],wrap=True),
            ft.Row([ft.TextButton(label,on_click=lambda e,v=value:append(v)) for label,value in [('空格',' '),('減號','-'),('句點','.'),('子資料夾 \\','\\')]],wrap=True),
            ft.Text('按欄位按鈕加入末端；用箭頭調整片段順序，或直接編輯上方文字。'),
            parts_view,ft.Text('輸出檔名預覽（前 12 首；同名時另加序號）',weight=ft.FontWeight.BOLD),preview,
            ft.Text('沒有填寫的欄位會留空；音訊副檔名自動加入。'),error],scroll=ft.ScrollMode.AUTO,tight=True)),
        actions=[ft.TextButton('取消',on_click=lambda e:close_dialog(page,dialog)),ft.FilledButton('套用格式',on_click=apply)])
    open_dialog(page,dialog)
    refresh()
