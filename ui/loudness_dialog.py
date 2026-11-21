"""Independent folder / selected-track loudness workflow."""
from pathlib import Path
import math
import flet as ft
from core.audio import OutputSettings, measure
from core.loudness_job import SUPPORTED, folder_audio, normalize_file, validate_targets
from core.tags import import_audio
from core.youtube import check_cancel, Cancelled
from .dialogs import open_dialog, close_dialog


def show_loudness_dialog(studio, e=None):
    if studio.busy:
        return
    page=studio.page
    source_mode=ft.Dropdown(label='歌曲來源', value='selected', options=[
        ft.dropdown.Option('selected','目前清單勾選的歌曲'), ft.dropdown.Option('folder','匯入資料夾內歌曲')])
    source=ft.TextField(label='歌曲資料夾', read_only=True, expand=True)
    recursive=ft.Checkbox(label='包含子資料夾', value=False)
    mode=ft.Dropdown(label='檔案處理方式',value='save',options=[
        ft.dropdown.Option('save','另存新檔（保留原始檔案）'),ft.dropdown.Option('overwrite','覆蓋原始檔案')])
    target=ft.TextField(label='另存位置',value=studio.folder.value,expand=True)
    lufs=ft.TextField(label='目標響度 LUFS',value=studio.lufs.value,expand=True)
    peak=ft.TextField(label='True peak 上限 dBTP',value=studio.peak.value,expand=True)
    method=ft.Dropdown(label='校正方式',value=getattr(studio.loudness_mode,'value','gain') or 'gain',options=[
        ft.dropdown.Option('lossless','MP3 無損調整（不重新編碼，1.5 dB 一級）'),
        ft.dropdown.Option('gain','增益＋限幅（重新編碼）'),ft.dropdown.Option('loudnorm','FFmpeg loudnorm（舊方式）')])
    method.value='lossless'
    acknowledgment=ft.Checkbox(label='我確認以調整後的音訊覆蓋所選原檔',value=False,visible=False)
    error=ft.Text(color='red')
    def selected_folder(event):
        if event.path:
            source.value=event.path;source_mode.value='folder';page.update()
    def output_folder(event):
        if event.path: target.value=event.path;page.update()
    for picker in getattr(studio,'_loudness_pickers',[]):
        if picker in page.overlay: page.overlay.remove(picker)
    source_picker=ft.FilePicker(on_result=selected_folder)
    output_picker=ft.FilePicker(on_result=output_folder)
    studio._loudness_pickers=[source_picker,output_picker]
    page.overlay.extend(studio._loudness_pickers)
    def mode_changed(e):
        acknowledgment.visible=mode.value=='overwrite'
        acknowledgment.value=False
        target.disabled=mode.value=='overwrite'
        page.update()
    mode.on_change=mode_changed

    async def execute(scan_only):
        try:
            loudness, ceiling=float(lufs.value),float(peak.value)
            validate_targets(loudness,ceiling)
            overwrite=mode.value=='overwrite'
            folder=target.value or ''
            if not scan_only and overwrite and not acknowledgment.value:
                raise ValueError('請勾選確認覆蓋原檔，或改用另存新檔')
            if not scan_only and not overwrite and not folder.strip():
                raise ValueError('請指定另存位置')
            if source_mode.value=='folder':
                paths=folder_audio(source.value or '',recursive.value)
                # Do not feed an existing nested output folder back into this batch.
                if not scan_only and not overwrite and folder.strip():
                    out=Path(folder).resolve();base=Path(source.value).resolve()
                    if out!=base and out.is_relative_to(base):
                        paths=[p for p in paths if not p.resolve().is_relative_to(out)]
            else:
                paths=[Path(t.source) for t in studio.tracks if t.selected and t.source]
            paths=list(dict.fromkeys(p.resolve() for p in paths))
            if not paths: raise ValueError('沒有可處理的歌曲；請勾選清單歌曲或選擇資料夾')
        except (ValueError,OSError) as exc:
            error.value=str(exc);page.update();return
        close_dialog(page,dialog)
        def work():
            ok=failed=0
            for i,path in enumerate(paths,1):
                check_cancel(studio.cancel)
                try:
                    if path.suffix.lower() not in SUPPORTED:
                        raise ValueError('僅支援 MP3、M4A、FLAC、WAV、OGG、OPUS')
                    track=next((t for t in studio.tracks if t.source and Path(t.source).resolve()==path),None)
                    if track is None:
                        track=import_audio(path,studio.cache)
                        studio.tracks.append(track)
                    def progress(message): studio.emit(f'{i}/{len(paths)} {path.name}：{message}')
                    if scan_only:
                        progress('掃描響度')
                        track.measurement=measure(path,OutputSettings(folder=str(path.parent),lufs=loudness,true_peak=ceiling),studio.cancel)
                        track.status='響度掃描完成'
                    else:
                        dest,before,after=normalize_file(path,loudness,ceiling,overwrite,folder,studio.cancel,progress,
                                                         method.value or 'gain')
                        for text in after.get('warnings') or []:
                            track.warning+='；'+text
                        track.measurement=dict(after if overwrite else before)
                        track.measurement['output_verification']=after
                        track.output=str(dest)
                        track.status='響度已調整（原檔）' if overwrite else '響度已另存'
                        if abs(float(after['input_i'])-loudness)>1 or float(after['input_tp'])>ceiling+0.3:
                            track.warning+='；輸出響度／峰值與目標有差距，請查看響度紀錄'
                        progress(f'完成 → {dest}')
                    ok+=1
                except Cancelled:
                    raise
                except Exception as exc:
                    failed+=1
                    studio.emit(f'{path.name} 處理失敗：{exc}')
            studio.emit(f'響度處理完成：成功 {ok} 首、失敗 {failed} 首。清單響度顯示來源檔；另存結果見歌曲詳情。')
        await studio.job(work)

    async def scan(e): await execute(True)
    async def adjust(e): await execute(False)
    dialog=ft.AlertDialog(modal=True,title=ft.Text('響度調整'),content=ft.Container(width=650,
        content=ft.Column([source_mode,ft.Row([source,ft.IconButton(ft.icons.FOLDER_OPEN,
            tooltip='選擇歌曲資料夾',on_click=lambda e:source_picker.get_directory_path())]),recursive,
            ft.Row([lufs,peak]),method,mode,ft.Row([target,ft.IconButton(ft.icons.FOLDER_OPEN,
            tooltip='選擇另存位置',on_click=lambda e:output_picker.get_directory_path())]),acknowledgment,
            ft.Text('MP3 無損調整只改寫音量欄位，不解碼也不重新編碼，音質完全不變，可再反向調回；'
                    '以 1.5 dB 為一級，結果與目標最多差約 0.75 LU，且不壓縮峰值。非 MP3 檔案在此模式會略過。'),
            ft.Text('其他方式支援 MP3、M4A、FLAC、WAV、OGG、OPUS，保留格式與原內嵌封面；MP3／AAC 等有損格式會重新編碼。'),
            ft.Text('僅掃描不修改檔案。覆蓋會在驗證完成後替換原檔，沒有另留備份；另存檔名會加「_響度調整」。'),
            error],tight=True,scroll=ft.ScrollMode.AUTO)),actions=[
            ft.TextButton('取消',on_click=lambda e:close_dialog(page,dialog)),
            ft.OutlinedButton('僅掃描響度',on_click=scan),ft.FilledButton('確認並調整',on_click=adjust)])
    open_dialog(page,dialog)
