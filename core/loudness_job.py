"""Standalone normalization: preserve embedded artwork, commit only after verification."""
from copy import deepcopy
from pathlib import Path
import math
import os
import shutil
import tempfile
from mutagen import File
from mutagen.id3 import ID3
from mutagen.flac import FLAC
from .audio import OutputSettings, measure, build_loudness_filter, encode_normalized
from .system import ffmpeg_path, run, temp_directory
from .youtube import check_cancel

SUPPORTED = {'.mp3', '.m4a', '.flac', '.wav', '.ogg', '.opus'}

def validate_targets(lufs, peak):
    if not math.isfinite(lufs) or not -70 <= lufs <= -5:
        raise ValueError('目標 LUFS 請填 -70～-5')
    if not math.isfinite(peak) or not -9 <= peak <= 0:
        raise ValueError('True peak 請填 -9～0 dBTP')

def folder_audio(folder, recursive=False):
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError('請選擇有效的歌曲資料夾')
    return sorted(p for p in (root.rglob('*') if recursive else root.iterdir())
                  if p.is_file() and not p.is_symlink() and p.suffix.lower() in SUPPORTED
                  and not p.name.startswith('.ez-loudness-'))

def _copy_tags(source, dest):
    """Copy original tags and picture bytes without using editable/shared UI fields."""
    original, target = File(source), File(dest)
    if original is None or target is None:
        raise ValueError('無法確認來源／輸出檔案格式')
    tags = deepcopy(original.tags)
    if tags is not None:
        for key in list(tags.keys()):
            low = key.lower()
            if any(token in low for token in ('replaygain', 'r128_', 'itunnorm')) or low.startswith(('rva2', 'rvad')):
                del tags[key]  # obsolete gain values would apply gain a second time
        if isinstance(tags, ID3):
            if Path(source).suffix.lower() == '.mp3':
                version = 3 if original.tags.version[1] == 3 else 4
                tags.save(dest, v2_version=version)
            else:
                target.tags = tags
                target.save()
        else:
            if target.tags is None:
                target.add_tags()
            target.tags.clear()
            target.tags.update(tags)
            if isinstance(original, FLAC):
                target.clear_pictures()
                for picture in original.pictures:
                    target.add_picture(deepcopy(picture))
            target.save()
    elif isinstance(original, FLAC) and original.pictures:
        target.clear_pictures()
        for picture in original.pictures:
            target.add_picture(deepcopy(picture))
        target.save()
    def pictures(audio):
        if isinstance(audio, FLAC): return [p.data for p in audio.pictures]
        if audio.tags is None: return []
        if isinstance(audio.tags, ID3): return [p.data for p in audio.tags.getall('APIC')]
        return list(audio.tags.get('covr', audio.tags.get('metadata_block_picture', [])))
    if pictures(original) != pictures(File(dest)):
        raise RuntimeError('原始內嵌封面未完整保留，取消寫入')

def _encoding(source):
    audio = File(source)
    if audio is None:
        raise ValueError('無法解析音訊格式')
    info = audio.info
    ext = source.suffix.lower()
    rate = int(getattr(info, 'sample_rate', 48000))
    bits = int(getattr(info, 'bits_per_sample', 24) or 24)
    bitrate = max(32000, min(320000, int(getattr(info, 'bitrate', 192000) or 192000)))
    if ext == '.mp3':
        bitrate = min((32,40,48,56,64,80,96,112,128,160,192,224,256,320), key=lambda n:abs(n*1000-bitrate))
        codec = ['-c:a','libmp3lame','-b:a',f'{bitrate}k']
    elif ext == '.m4a':
        codec = ['-c:a','alac'] if 'alac' in str(getattr(info,'codec','')).lower() else ['-c:a','aac','-b:a',str(bitrate)]
    elif ext == '.flac': codec = ['-c:a','flac','-sample_fmt','s16' if bits<=16 else 's32']
    elif ext == '.wav': codec = ['-c:a', 'pcm_s16le' if bits<=16 else 'pcm_s24le' if bits<=24 else 'pcm_s32le']
    elif ext == '.opus': codec = ['-c:a','libopus','-b:a',str(bitrate)]; rate = 48000
    elif ext == '.ogg': codec = ['-c:a','libvorbis','-q:a','6']
    else: raise ValueError('響度調整不支援此格式')
    return codec + ['-ar',str(rate)], float(getattr(info,'length',0))

def normalize_file(source, lufs, peak, overwrite, folder, cancel, progress=lambda text:None, mode='gain'):
    validate_targets(lufs, peak)
    source = Path(source).resolve()
    if not source.is_file() or source.suffix.lower() not in SUPPORTED:
        raise ValueError('找不到來源或不支援的格式')
    initial = source.stat()
    settings = OutputSettings(folder=str(source.parent), lufs=lufs, true_peak=peak, loudness_mode=mode)
    check_cancel(cancel)
    progress('掃描原始響度')
    before = measure(source, settings, cancel)
    if not build_loudness_filter(before, settings):
        raise ValueError('靜音或無有效響度，原檔保持不變')
    encoding, duration = _encoding(source)
    with tempfile.TemporaryDirectory(prefix='loudness-', dir=temp_directory()) as tmp:
        encoded = Path(tmp)/('normalized'+source.suffix.lower())
        def encode(filt):
            progress('調整響度')
            return run([ffmpeg_path(),'-hide_banner','-nostdin','-y','-i',str(source),
                        '-map','0:a:0','-vn','-map_metadata','0','-af',filt,*encoding,str(encoded)],cancel)
        def verify():
            progress('驗證輸出響度')
            return measure(encoded, settings, cancel)
        after, notes, warnings = encode_normalized(encode, before, settings, verify, cancel)
        after = dict(after, normalization=notes, warnings=warnings)
        _copy_tags(source, encoded)
        result = File(encoded)
        if result is None or abs(result.info.length-duration)>max(0.25, duration*0.005):
            raise RuntimeError('輸出長度驗證失敗，原檔保持不變')
        if not math.isfinite(float(after['input_i'])):
            raise RuntimeError('輸出響度無效，原檔保持不變')
        check_cancel(cancel)
        target_dir = source.parent if overwrite else Path(folder).expanduser().resolve()
        target_dir.mkdir(parents=True,exist_ok=True)
        dest = source
        staged = None
        reserved = False
        committed = False
        try:
            if overwrite:
                fd, name = tempfile.mkstemp(prefix='.ez-loudness-',suffix=source.suffix,dir=source.parent)
                os.close(fd); staged = Path(name)
            else:
                number = 1
                while True:
                    suffix = '_響度調整' if number==1 else f'_響度調整 ({number})'
                    dest = target_dir/(source.stem+suffix+source.suffix)
                    try:
                        fd = os.open(dest,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o666)
                        os.close(fd); reserved=True; break
                    except FileExistsError: number+=1
                staged = dest
            shutil.copyfile(encoded, staged)
            check_cancel(cancel)
            if overwrite:
                current=source.stat()
                if (current.st_size,current.st_mtime_ns,current.st_ino)!=(initial.st_size,initial.st_mtime_ns,initial.st_ino):
                    raise RuntimeError('處理期間來源檔案已變動，取消覆蓋')
                shutil.copymode(source, staged)
                os.replace(staged, source)
            committed=True
            return dest, before, after
        finally:
            if not committed and staged is not None:
                staged.unlink(missing_ok=True)
