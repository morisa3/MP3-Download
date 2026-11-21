"""Persistent metadata only; no cookies, signed media URLs or local edits."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from .system import DATA, ROOT
import shutil

class ScanCache:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else ROOT / 'list'
        self.legacy_root = DATA / 'scan_cache' if root is None else None

    def path(self, kind, url):
        digest = hashlib.sha256(url.encode('utf-8')).hexdigest()
        return self.root / kind / (digest + '.json')

    def read(self, kind, url):
        try:
            path = self.path(kind, url)
            if not path.exists() and self.legacy_root is not None:
                old = self.legacy_root / kind / path.name
                if old.is_file():
                    # Preserve the original timestamp and keep the old copy intact.
                    value = json.loads(old.read_text(encoding='utf-8'))
                    if value.get('schema') == 1 and value.get('url') == url:
                        try:
                            path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(old, path)
                        except OSError:
                            pass
                        return value
            value = json.loads(path.read_text(encoding='utf-8'))
            if value.get('schema') == 1 and value.get('url') == url:
                return value
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return None

    def write(self, kind, url, data):
        path = self.path(kind, url)
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {'schema': 1, 'url': url, 'scanned_at': datetime.now(timezone.utc).isoformat(), 'data': data}
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, path)
