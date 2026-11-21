"""Format-specific metadata, including attached cover pictures."""
from pathlib import Path
from mutagen import File
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TPE2, TALB, TDRC, TRCK, TCON, TCOM, TPOS
from mutagen.mp4 import MP4, MP4Cover
from mutagen.flac import FLAC, Picture
from mutagen.wave import WAVE
from .models import Track


def write_tags(path, track, cover=None):
    ext = Path(path).suffix.lower()
    image = Path(cover).read_bytes() if cover else None
    fields = {'title': track.title, 'artist': track.artist, 'albumartist': track.albumartist,
              'album': track.album, 'date': track.date, 'tracknumber': track.tracknumber, 'genre': track.genre, 'composer': track.composer,
              'discnumber': track.discnumber+('/'+track.totaldiscs if track.discnumber and track.totaldiscs else ''),
              'disctotal': track.totaldiscs}
    if ext in ('.mp3', '.wav'):
        audio = None
        if ext == '.mp3':
            try:
                tags = ID3(path)
            except Exception:
                tags = ID3()
        else:
            audio = WAVE(path)
            if audio.tags is None:
                audio.add_tags()
            tags = audio.tags
        for key, frame in [('title', TIT2), ('artist', TPE1), ('albumartist', TPE2), ('album', TALB),
                           ('date', TDRC), ('tracknumber', TRCK), ('genre', TCON), ('composer', TCOM), ('discnumber', TPOS)]:
            tags.delall(frame.__name__)
            if fields[key]:
                tags.add(frame(encoding=3, text=str(fields[key])))
        tags.delall('APIC')
        if image:
            tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=image))
        if audio is not None:
            audio.save()
        else:
            tags.save(path, v2_version=3)
    elif ext == '.m4a':
        audio = MP4(path)
        for key, atom in [('title', '\xa9nam'), ('artist', '\xa9ART'), ('albumartist', 'aART'),
                          ('album', '\xa9alb'), ('date', '\xa9day'), ('genre', '\xa9gen'), ('composer', '\xa9wrt')]:
            audio.pop(atom, None)
            if fields[key]:
                audio[atom] = [fields[key]]
        audio.pop('trkn', None)
        if track.tracknumber:
            parts = track.tracknumber.split('/')
            audio['trkn'] = [(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)]
        audio.pop('disk', None)
        if track.discnumber or track.totaldiscs:
            audio['disk'] = [(int(track.discnumber or 0), int(track.totaldiscs or 0))]
        audio.pop('covr', None)
        if image:
            audio['covr'] = [MP4Cover(image, imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
    elif ext == '.flac':
        audio = FLAC(path)
        for key, value in fields.items():
            audio.pop(key, None)
            if value:
                audio[key] = str(value)
        audio.clear_pictures()
        if image:
            from PIL import Image
            picture = Picture()
            picture.type, picture.mime, picture.data = 3, 'image/jpeg', image
            with Image.open(cover) as im:
                picture.width, picture.height = im.size
            picture.depth = 24
            audio.add_picture(picture)
        audio.save()
    else:
        raise ValueError('不支援此標籤格式')


def verify_embedded_cover(path, cover):
    """Require the exported file to contain the exact prepared JPEG before completion."""
    expected = Path(cover).read_bytes()
    ext = Path(path).suffix.lower()
    if ext == '.mp3':
        frames = ID3(path).getall('APIC')
        pictures = [f.data for f in frames if f.type == 3]
    elif ext == '.wav':
        pictures = [f.data for f in WAVE(path).tags.getall('APIC')]
    elif ext == '.m4a':
        pictures = [bytes(p) for p in MP4(path).get('covr', [])]
    elif ext == '.flac':
        pictures = [p.data for p in FLAC(path).pictures if p.type == 3]
    else:
        pictures = []
    if expected not in pictures:
        raise RuntimeError('封面未正確內嵌至輸出音檔；已取消本次輸出，請重試或更換封面')


def import_audio(path, cache):
    p = Path(path).resolve()
    track = Track(title=p.stem, source=str(p), status='待確認')
    audio = File(p, easy=True)
    if audio:
        for key in ('title', 'artist', 'album', 'albumartist', 'date', 'tracknumber', 'genre', 'composer', 'discnumber'):
            value = audio.get(key)
            if value:
                setattr(track, key, str(value[0]))
    if '/' in track.discnumber:
        track.discnumber,track.totaldiscs=track.discnumber.split('/',1)
    if audio and audio.get('disctotal'):
        track.totaldiscs=str(audio['disctotal'][0])
    full = File(p)
    data = None
    if full:
        if hasattr(full, 'pictures') and full.pictures:
            data = full.pictures[0].data
        elif full.tags:
            if hasattr(full.tags, 'getall') and full.tags.getall('APIC'):
                data = full.tags.getall('APIC')[0].data
            elif full.tags.get('covr'):
                data = bytes(full.tags['covr'][0])
    if data:
        from .crop import open_image, auto_bbox
        folder = Path(cache)/track.uid
        folder.mkdir(parents=True, exist_ok=True)
        raw = folder/'embedded.raw'
        raw.write_bytes(data)
        target = folder/'cover.jpg'
        open_image(raw).save(target, quality=98)
        track.cover, track.bbox = str(target), auto_bbox(target)
    return track
