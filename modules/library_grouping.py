from __future__ import annotations

import re
from pathlib import Path
from typing import Any

RJ_RE = re.compile(r'(?i)\b(RJ\d{6,8})\b')
COVER_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
COVER_HINTS = ('cover', 'folder', '封面', 'ジャケット', 'main')


def identify_work(audio_path: Path, library_root: Path) -> dict[str, Any]:
    relative = audio_path.relative_to(library_root)
    parts = relative.parts[:-1]
    chosen_index = None
    rj_code = None
    for index, part in enumerate(parts):
        match = RJ_RE.search(part)
        if match:
            chosen_index = index
            rj_code = match.group(1).upper()
            break
    if chosen_index is None:
        chosen_index = 0 if parts else None
    work_dir = library_root if chosen_index is None else library_root.joinpath(*parts[:chosen_index + 1])
    title = work_dir.name or library_root.name
    cover = find_cover(work_dir)
    key = rj_code or str(work_dir.resolve()).casefold()
    return {
        'key': key,
        'title': title,
        'root_path': str(work_dir.resolve()),
        'rj_code': rj_code,
        'cover_path': None if cover is None else str(cover),
    }


def find_cover(work_dir: Path) -> Path | None:
    try:
        images = [path for path in work_dir.iterdir() if path.is_file() and path.suffix.lower() in COVER_EXTENSIONS]
    except OSError:
        return None
    if not images:
        return None
    return sorted(images, key=lambda path: (not any(hint in path.stem.casefold() for hint in COVER_HINTS), path.name.casefold()))[0]
