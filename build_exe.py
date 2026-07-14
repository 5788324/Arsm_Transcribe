from __future__ import annotations

from datetime import datetime
from pathlib import Path

import PyInstaller.__main__

REPO_ROOT = Path(__file__).resolve().parent
DIST_DIR = REPO_ROOT / 'dist'
BUILD_DIR = REPO_ROOT / 'build'


def main() -> None:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    build_name = f"Arsm-Transcribe-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    PyInstaller.__main__.run([
        '--noconfirm',
        '--clean',
        '--windowed',
        '--onefile',
        '--name', build_name,
        '--distpath', str(DIST_DIR),
        '--workpath', str(BUILD_DIR / 'pyinstaller'),
        '--specpath', str(BUILD_DIR),
        '--add-data', f'{REPO_ROOT / "config.yaml"};.',
        str(REPO_ROOT / 'desktop_app.py'),
    ])


if __name__ == '__main__':
    main()
