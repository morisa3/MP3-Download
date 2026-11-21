"""Two-pass EBU R128 loudness normalization and single final audio encode."""
from pathlib import Path
from dataclasses import dataclass, asdict, field
import json
import math
import os
import re
import tempfile
import shutil
from .system import ffmpeg_path, run, writable_directory, temp_directory
from .tags import write_tags, verify_embedded_cover
from .crop import save_cover
from .naming import filename, output_path, validate_template

@dataclass
class OutputSettings:
    folder: str
    format: str = 'mp3'
    bitrate: str = '320k'
    sample_rate: int = 48000
    normalize: bool = True
    lufs: float = -14.0
    true_peak: float = -1.0
    lra: float = 11.0
    cover_size: int = 720
    naming: str = '<曲目歌星>-<標題>'
    embed_cover: bool = True
    extra_covers: str = 'none'
    write_report: bool = False
    shared_cover: str = ''
    shared_bbox: list = field(default_factory=list)

    def validate(self):
        if self.format not in ('mp3', 'm4a', 'flac', 'wav'):
            raise ValueError('不支援的輸出格式')
        if not -70 <= self.lufs <= -5 or not -9 <= self.true_peak <= 0 or not 1 <= self.lra <= 50:
            raise ValueError('LUFS 範圍 -70～-5；True peak -9～0；LRA 1～50')
        if self.sample_rate not in (44100, 48000):
            raise ValueError('取樣率限 44100 或 48000')
        if self.bitrate not in ('128k', '192k', '256k', '320k'):
            raise ValueError('不支援的位元率')
        if self.cover_size not in (0, 540, 720, 1080):
            raise ValueError('封面尺寸限原裁切、540、720、1080')
        if not str(self.folder).strip():
            raise ValueError('請指定輸出位置')
        validate_template(self.naming)
        if self.extra_covers not in ('none', 'single', 'per_track'):
            raise ValueError('未知的封面另存方式')
        if self.extra_covers == 'single' and not self.shared_cover:
            raise ValueError('只存一張 Cover.jpg 時，請先從清單指定共用封面')
        return writable_directory(self.folder)


def validate_track(t):
    from datetime import date
    if not t.title.strip():
        raise ValueError('標題不能空白')
    if t.date:
        try:
            if re.fullmatch(r'\d{4}', t.date):
                date(int(t.date), 1, 1)
            else:
                date.fromisoformat(t.date)
        except ValueError:
            raise ValueError('日期請使用 YYYY 或 YYYY-MM-DD，或留白')
    for key in ('discnumber', 'totaldiscs'):
        value = getattr(t, key)
        if value and not re.fullmatch(r'[1-9]\d*', value):
            raise ValueError('碟號與總碟數請使用正整數或留白')
    if t.discnumber and t.totaldiscs and int(t.discnumber) > int(t.totaldiscs):
        raise ValueError('碟號不可大於總碟數')
    if t.tracknumber and not re.fullmatch(r'[1-9]\d*(/[1-9]\d*)?', t.tracknumber):
        raise ValueError('曲目請使用正整數或 1/10 格式')


def measure(source, settings, cancel=None):
    filt = f'loudnorm=I={settings.lufs}:TP={settings.true_peak}:LRA={settings.lra}:print_format=json'
    text = run([ffmpeg_path(), '-hide_banner', '-nostdin', '-i', source, '-map', '0:a:0',
                '-vn', '-af', filt, '-f', 'null', '-'], cancel)
    for block in reversed(re.findall(r'\{[^{}]*\}', text, re.S)):
        try:
            result = json.loads(block)
            if 'input_i' in result:
                return result
        except json.JSONDecodeError:
            continue
    raise RuntimeError('FFmpeg 未返回響度測量結果')


def loudness_filter(stats, s):
    keys = ('input_i', 'input_tp', 'input_lra', 'input_thresh', 'target_offset')
    if not all(math.isfinite(float(stats[k])) for k in keys):
        # Silence has -inf LUFS. Do not inject an invalid gain or manufacture audio.
        return None
    return (f'loudnorm=I={s.lufs}:TP={s.true_peak}:LRA={s.lra}'
            f':measured_I={stats["input_i"]}:measured_TP={stats["input_tp"]}'
            f':measured_LRA={stats["input_lra"]}:measured_thresh={stats["input_thresh"]}'
            f':offset={stats["target_offset"]}:linear=true:print_format=json')


def export_track(track, settings, cancel=None, progress=lambda x: None):
    folder = settings.validate()
    validate_track(track)
    if not Path(track.source).is_file():
        raise FileNotFoundError('找不到來源音訊，請重新下載或匯入')
    dest = output_path(folder, track, settings.naming, settings.format)
    dest.parent.mkdir(parents=True, exist_ok=True)
    output_folder = dest.parent
    base = dest.stem
    i = 2
    # Reserve a new filename exclusively; do not overwrite a source or previous output.
    while True:
        try:
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            os.close(fd)
            break
        except FileExistsError:
            dest = output_folder / f'{base} ({i}).{settings.format}'
            i += 1
    succeeded = False
    track.measurement = {}
    try:
        with tempfile.TemporaryDirectory(prefix='.ez-', dir=temp_directory()) as tmp:
            temp = Path(tmp)
            encoded = temp / ('output.'+settings.format)
            args = [ffmpeg_path(), '-hide_banner', '-nostdin', '-y', '-i', track.source,
                    '-map', '0:a:0', '-vn', '-map_metadata', '-1']
            if settings.normalize:
                progress('掃描響度')
                track.measurement = measure(track.source, settings, cancel)
                filt = loudness_filter(track.measurement, settings)
                if filt:
                    args += ['-af', filt]
                else:
                    track.warning += '；靜音或無有效響度，未套用增益'
            codecs = {'mp3': ['-c:a', 'libmp3lame', '-b:a', settings.bitrate],
                      'm4a': ['-c:a', 'aac', '-b:a', settings.bitrate],
                      'flac': ['-c:a', 'flac', '-sample_fmt', 's32'],
                      'wav': ['-c:a', 'pcm_s24le']}
            args += codecs[settings.format] + ['-ar', str(settings.sample_rate), str(encoded)]
            progress('校正響度／轉檔')
            run(args, cancel)
            cover = None
            source_cover = settings.shared_cover or track.cover
            source_bbox = settings.shared_bbox if settings.shared_cover else track.bbox
            if source_cover and (settings.embed_cover or settings.extra_covers != 'none'):
                cover = save_cover(source_cover, source_bbox, temp/'cover.jpg', settings.cover_size)
            progress('寫入標籤與封面')
            write_tags(encoded, track, cover if settings.embed_cover else None)
            if cover and settings.embed_cover:
                verify_embedded_cover(encoded, cover)
            # Measure encoded result as lossy codecs can shift loudness and true peak.
            if settings.normalize:
                progress('驗證輸出響度')
                after = measure(encoded, settings, cancel)
                track.measurement['output_verification'] = after
                if math.isfinite(float(after['input_i'])) and abs(float(after['input_i'])-settings.lufs) > 1:
                    track.warning += '；輸出響度與目標差距 >1 LU，請查看歌曲資訊／響度紀錄'
                if float(after['input_tp']) > settings.true_peak+.3:
                    track.warning += '；編碼後峰值偏高，可降低 True peak 後重試'
            # The app temp directory and chosen output may be on different drives.
            # Copy into our exclusively reserved destination; finally removes it on failure.
            shutil.copyfile(encoded, dest)
            succeeded = True
            track.output = str(dest)
            report = {'title': track.title, 'url': track.url, 'source': track.source,
                      'output': str(dest), 'settings': asdict(settings), 'metadata': {k: getattr(track,k) for k in ('title','artist','albumartist','album','date','tracknumber','genre')}, 'warning': track.warning, 'loudness': track.measurement}
            try:
                if settings.write_report:
                    dest.with_suffix(dest.suffix+'.report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                if cover and settings.extra_covers != 'none':
                    sidecar = folder/'Cover.jpg' if settings.extra_covers == 'single' else dest.with_suffix(dest.suffix+'.cover.jpg')
                    data = Path(cover).read_bytes()
                    try:
                        with sidecar.open('xb') as target:
                            target.write(data)
                    except FileExistsError:
                        if sidecar.read_bytes() != data:
                            track.warning += f'；已有不同封面 {sidecar.name}，已保留原檔（音檔仍內嵌本次所選封面）'
            except OSError as exc:
                track.warning += f'；音檔已完成，附屬檔案寫入失敗：{exc}'
        return dest
    finally:
        if not succeeded:
            dest.unlink(missing_ok=True)
