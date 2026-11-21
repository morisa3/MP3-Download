"""Conservative coloured-bar detection and integer square crops (right/bottom exclusive)."""
from pathlib import Path
import base64
import io
import numpy as np
from PIL import Image, ImageOps


def open_image(path):
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert('RGB')


def auto_bbox(path, tolerance=24):
    im = open_image(path)
    a = np.asarray(im).astype(np.int16)
    h, w = a.shape[:2]
    # Each side gets its own colour. No Gaussian blur, so boundaries do not grow.
    def scan(lines):
        color = np.median(lines[0], axis=0)
        count = 0
        limit = max(1, int(len(lines) * .4))
        for line in lines[:limit]:
            distance = np.max(np.abs(line - color), axis=1)
            if np.mean(distance <= tolerance) < .985:
                break
            count += 1
        return count if 3 <= count < limit else 0
    left, right = scan(a.transpose(1, 0, 2)), scan(a.transpose(1, 0, 2)[::-1])
    top, bottom = scan(a), scan(a[::-1])
    # Only paired bars are removed automatically; asymmetric art stays conservative.
    if not (left and right and abs(left-right) <= max(6, w*.06)):
        left = right = 0
    if not (top and bottom and abs(top-bottom) <= max(6, h*.06)):
        top = bottom = 0
    x1, y1, x2, y2 = left, top, w-right, h-bottom
    side = min(x2-x1, y2-y1)
    # Snap near canonical size by trimming, never include extra border pixels.
    for target in (1080, 720, 540, 480, 360):
        if target <= side <= target+4:
            side = target
            break
    x = x1 + (x2-x1-side)//2
    y = y1 + (y2-y1-side)//2
    return [int(x), int(y), int(x+side), int(y+side)]


def validate_bbox(path, bbox):
    w, h = open_image(path).size
    if len(bbox) != 4 or any(int(x) != x for x in bbox):
        raise ValueError('裁切座標必須是四個整數')
    l, t, r, b = map(int, bbox)
    if not (0 <= l < r <= w and 0 <= t < b <= h):
        raise ValueError(f'裁切座標超過原圖範圍 {w}×{h}')
    if r-l != b-t:
        raise ValueError('封面必須為正方形，請調整邊長')
    return l, t, r, b


def cropped_image(path, bbox, size=720):
    im = open_image(path).crop(validate_bbox(path, bbox))
    if size:
        im = im.resize((int(size), int(size)), Image.Resampling.LANCZOS)
    return im


def save_cover(path, bbox, dest, size=720):
    im = cropped_image(path, bbox, size)
    im.save(dest, 'JPEG', quality=95, subsampling=0)
    return str(dest)


def preview(path, bbox=None):
    im = cropped_image(path, bbox, 0) if bbox else open_image(path)
    im.thumbnail((500, 500))
    out = io.BytesIO()
    im.save(out, 'PNG')
    return base64.b64encode(out.getvalue()).decode('ascii')


def avatar_bbox(path, side=720):
    """Keep the original face-centred modes; fall back to the image centre."""
    import cv2
    im = open_image(path)
    w, h = im.size
    gray = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2GRAY)
    detector = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades)/'haarcascade_frontalface_default.xml'))
    faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4)
    cx, cy = w//2, h//2
    if len(faces):
        x,y,fw,fh = max(faces, key=lambda f: f[2]*f[3])
        cx,cy = int(x+fw//2), int(y+fh//2)
    side = min(int(side),w,h)
    x,y = max(0,min(cx-side//2,w-side)), max(0,min(cy-side//2,h-side))
    return [x,y,x+side,y+side]
