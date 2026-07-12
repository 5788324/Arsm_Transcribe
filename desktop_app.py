from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

import app as pipeline_app

REPO_ROOT = Path(__file__).resolve().parent
LOG_DIR = REPO_ROOT / 'logs'
STATUS_PATH = LOG_DIR / 'batch_status.json'
RUNNER_PATH = LOG_DIR / 'batch_runner.json'
STDOUT_LOG = LOG_DIR / 'gui_batch_stdout.log'
STDERR_LOG = LOG_DIR / 'gui_batch_stderr.log'


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.batch_worker:
        return run_batch_worker(args)

    launcher = DesktopLauncher(Path(args.config).resolve())
    launcher.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='RJ-LRC-Local desktop launcher')
    parser.add_argument('--config', default='config.yaml')
    parser.add_argument('--batch-worker', action='store_true')
    parser.add_argument('--overwrite', action='store_true')
    parser.add_argument('roots', nargs='*')
    return parser


def run_batch_worker(args: argparse.Namespace) -> int:
    config = pipeline_app.load_config(Path(args.config))
    summary = pipeline_app.run_batch([Path(value) for value in args.roots], config, overwrite=args.overwrite)
    print(
        f'summary: total={summary.total}, succeeded={summary.succeeded}, '
        f'skipped={summary.skipped}, failed={summary.failed}'
    )
    return 0 if summary.failed == 0 else 1


class DesktopLauncher:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = pipeline_app.load_config(config_path)
        self.root = tk.Tk()
        self.root.title('RJ-LRC Local')
        self.root.geometry('1180x760')
        self.root.minsize(1080, 700)
        self.root.configure(bg='#eef5ff')

        self.status_var = tk.StringVar(value='Idle')
        self.progress_var = tk.DoubleVar(value=0.0)
        self.summary_var = tk.StringVar(value='No batch status yet')
        self.current_var = tk.StringVar(value='Current file: -')
        self.model_var = tk.StringVar(value=str(self.config.get('translate', {}).get('model', '')))
        self.output_var = tk.StringVar(value='Primary output: same-name Chinese .lrc')
        self.pid_var = tk.StringVar(value='Background task: not started')
        self.overwrite_var = tk.BooleanVar(value=False)

        self.roots: list[str] = [r'E:\arsm']

        self._build_styles()
        self._build_ui()
        self._refresh_status()

    def run(self) -> None:
        self.root.mainloop()

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Card.TFrame', background='#ffffff')
        style.configure('Hero.TFrame', background='#f8fbff')
        style.configure('Title.TLabel', background='#eef5ff', foreground='#20304f', font=('Microsoft YaHei UI', 26, 'bold'))
        style.configure('Subtitle.TLabel', background='#eef5ff', foreground='#60708f', font=('Microsoft YaHei UI', 11))
        style.configure('CardTitle.TLabel', background='#ffffff', foreground='#20304f', font=('Microsoft YaHei UI', 14, 'bold'))
        style.configure('CardBody.TLabel', background='#ffffff', foreground='#4f6285', font=('Microsoft YaHei UI', 10))
        style.configure('Primary.TButton', font=('Microsoft YaHei UI', 11, 'bold'))
        style.map('Primary.TButton', background=[('active', '#91bcff'), ('!disabled', '#7eaef8')], foreground=[('!disabled', '#ffffff')])
        style.configure('Soft.TButton', font=('Microsoft YaHei UI', 10), background='#edf4ff', foreground='#34507d')
        style.map('Soft.TButton', background=[('active', '#dce9ff'), ('!disabled', '#edf4ff')])
        style.configure('Water.Horizontal.TProgressbar', troughcolor='#dce8fb', background='#7eaef8', bordercolor='#dce8fb', lightcolor='#9cc4ff', darkcolor='#7eaef8')

    def _build_ui(self) -> None:
        canvas = tk.Canvas(self.root, bg='#eef5ff', highlightthickness=0)
        canvas.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.root.update_idletasks()
        width = max(self.root.winfo_width(), 1180)
        height = max(self.root.winfo_height(), 760)
        self._paint_background(canvas, width, height)

        outer = ttk.Frame(self.root, style='Hero.TFrame', padding=24)
        outer.pack(fill='both', expand=True)

        header = ttk.Frame(outer, style='Hero.TFrame')
        header.pack(fill='x', pady=(0, 18))
        ttk.Label(header, text='RJ-LRC Local', style='Title.TLabel').pack(anchor='w')
        ttk.Label(header, text='Local ASR / translation / subtitle batching workstation', style='Subtitle.TLabel').pack(anchor='w', pady=(4, 0))

        content = ttk.Frame(outer, style='Hero.TFrame')
        content.pack(fill='both', expand=True)
        content.columnconfigure(0, weight=5)
        content.columnconfigure(1, weight=4)
        content.rowconfigure(0, weight=1)

        left = ttk.Frame(content, style='Hero.TFrame')
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        right = ttk.Frame(content, style='Hero.TFrame')
        right.grid(row=0, column=1, sticky='nsew')

        self._build_roots_card(left)
        self._build_actions_card(left)
        self._build_status_card(right)
        self._build_runtime_card(right)

    def _paint_background(self, canvas: tk.Canvas, width: int, height: int) -> None:
        drops = [
            (width * 0.86, height * 0.14, 180, '#dcecff'),
            (width * 0.73, height * 0.72, 240, '#d9ecff'),
            (width * 0.16, height * 0.22, 120, '#f5fbff'),
            (width * 0.20, height * 0.82, 160, '#e3f1ff'),
        ]
        for cx, cy, r, color in drops:
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline='')

    def _build_roots_card(self, parent: ttk.Frame) -> None:
        card = ttk.Frame(parent, style='Card.TFrame', padding=18)
        card.pack(fill='both', expand=True, pady=(0, 12))
        ttk.Label(card, text='Batch Status', style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(card, text='Choose one or more library roots. Default starts with E:\arsm.', style='CardBody.TLabel').pack(anchor='w', pady=(4, 12))

        self.root_list = tk.Listbox(card, font=('Microsoft YaHei UI', 10), relief='flat', bg='#f8fbff', fg='#284066', selectbackground='#d8e8ff', selectforeground='#20304f', height=12)
        self.root_list.pack(fill='both', expand=True)
        for value in self.roots:
            self.root_list.insert('end', value)

        row = ttk.Frame(card, style='Card.TFrame')
        row.pack(fill='x', pady=(12, 0))
        ttk.Button(row, text='Add Folder', style='Soft.TButton', command=self._add_root).pack(side='left')
        ttk.Button(row, text='Remove Selected', style='Soft.TButton', command=self._remove_selected_root).pack(side='left', padx=8)
        ttk.Button(row, text='Clear', style='Soft.TButton', command=self._clear_roots).pack(side='left')

    def _build_actions_card(self, parent: ttk.Frame) -> None:
        card = ttk.Frame(parent, style='Card.TFrame', padding=18)
        card.pack(fill='x')
        ttk.Label(card, text='Actions', style='CardTitle.TLabel').pack(anchor='w')

        actions = ttk.Frame(card, style='Card.TFrame')
        actions.pack(fill='x', pady=(12, 10))
        ttk.Button(actions, text='Start Background Batch', style='Primary.TButton', command=self._start_batch).pack(side='left')
        ttk.Button(actions, text='Refresh', style='Soft.TButton', command=self._refresh_status).pack(side='left', padx=8)
        ttk.Button(actions, text='Open Logs', style='Soft.TButton', command=lambda: self._open_path(LOG_DIR)).pack(side='left')

        actions2 = ttk.Frame(card, style='Card.TFrame')
        actions2.pack(fill='x')
        ttk.Button(actions2, text='Build EXE', style='Soft.TButton', command=self._build_exe).pack(side='left')
        ttk.Button(actions2, text='Open Dist', style='Soft.TButton', command=lambda: self._open_path(REPO_ROOT / 'dist')).pack(side='left', padx=8)
        ttk.Checkbutton(actions2, text='Overwrite', variable=self.overwrite_var).pack(side='left', padx=(12, 0))

    def _build_status_card(self, parent: ttk.Frame) -> None:
        card = ttk.Frame(parent, style='Card.TFrame', padding=18)
        card.pack(fill='x', pady=(0, 12))
        ttk.Label(card, text='Batch Status', style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(card, textvariable=self.status_var, style='CardBody.TLabel').pack(anchor='w', pady=(6, 2))
        ttk.Label(card, textvariable=self.summary_var, style='CardBody.TLabel').pack(anchor='w', pady=(0, 2))
        ttk.Label(card, textvariable=self.current_var, style='CardBody.TLabel', wraplength=420).pack(anchor='w', pady=(0, 8))
        ttk.Progressbar(card, variable=self.progress_var, maximum=100, style='Water.Horizontal.TProgressbar').pack(fill='x', pady=(4, 8))
        ttk.Label(card, textvariable=self.pid_var, style='CardBody.TLabel').pack(anchor='w')

    def _build_runtime_card(self, parent: ttk.Frame) -> None:
        card = ttk.Frame(parent, style='Card.TFrame', padding=18)
        card.pack(fill='both', expand=True)
        ttk.Label(card, text='Runtime Settings', style='CardTitle.TLabel').pack(anchor='w')

        rows = [
            ('Config file', str(self.config_path)),
            ('Translate model', self.model_var.get() or '-'),
            ('Output mode', self.output_var.get()),
            ('LM Studio', str(self.config.get('translate', {}).get('base_url', ''))),
            ('ASR backend', str(self.config.get('asr', {}).get('backend', ''))),
            ('ASR executable', str(self.config.get('asr', {}).get('faster_whisper', {}).get('runner', {}).get('executable_path', ''))),
        ]
        for label, value in rows:
            row = ttk.Frame(card, style='Card.TFrame')
            row.pack(fill='x', pady=5)
            ttk.Label(row, text=label, style='CardBody.TLabel').pack(anchor='w')
            ttk.Label(row, text=value, style='CardBody.TLabel', wraplength=420).pack(anchor='w', pady=(2, 0))

    def _add_root(self) -> None:
        selected = filedialog.askdirectory(title='Choose batch root folder')
        if not selected:
            return
        if selected not in self.roots:
            self.roots.append(selected)
            self.root_list.insert('end', selected)

    def _remove_selected_root(self) -> None:
        selection = list(self.root_list.curselection())
        for index in reversed(selection):
            self.root_list.delete(index)
            self.roots.pop(index)

    def _clear_roots(self) -> None:
        self.roots.clear()
        self.root_list.delete(0, 'end')

    def _start_batch(self) -> None:
        roots = list(self.root_list.get(0, 'end'))
        if not roots:
            messagebox.showwarning('No folders', 'Please add at least one folder to process.')
            return
        if self._runner_is_alive():
            messagebox.showinfo('Already running', 'A background batch is already running. Wait for it to finish first.')
            return

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if getattr(sys, 'frozen', False):
            command = [sys.executable, '--batch-worker', '--config', str(self.config_path)]
        else:
            command = [sys.executable, '-B', str(Path(__file__).resolve()), '--batch-worker', '--config', str(self.config_path)]
        if self.overwrite_var.get():
            command.append('--overwrite')
        command.extend(roots)

        stdout_handle = open(STDOUT_LOG, 'a', encoding='utf-8')
        stderr_handle = open(STDERR_LOG, 'a', encoding='utf-8')
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'DETACHED_PROCESS', 0)
        process = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=creationflags,
            close_fds=False,
        )
        runner_payload = {
            'pid': process.pid,
            'command': command,
            'roots': roots,
            'started_at': datetime.now().isoformat(timespec='seconds'),
            'overwrite': self.overwrite_var.get(),
        }
        RUNNER_PATH.write_text(json.dumps(runner_payload, ensure_ascii=False, indent=2), encoding='utf-8')
        self.pid_var.set(f'Background PID: {process.pid}')
        self.status_var.set('Background batch started')
        self.root.after(1000, self._refresh_status)

    def _refresh_status(self) -> None:
        payload = self._load_json(STATUS_PATH)
        runner = self._load_json(RUNNER_PATH)
        if payload:
            total = int(payload.get('total', 0) or 0)
            completed = int(payload.get('current_index', 0) or 0)
            progress = 0 if total <= 0 else completed / total * 100
            self.progress_var.set(progress)
            self.status_var.set(f"State: {payload.get('state', 'unknown')}")
            self.summary_var.set(
                f"Progress {completed}/{total} | Succeeded {payload.get('succeeded', 0)} | Skipped {payload.get('skipped', 0)} | Failed {payload.get('failed', 0)}"
            )
            current = payload.get('current_audio_path') or '-'
            self.current_var.set(f'Current file: {current}')
        else:
            self.progress_var.set(0)
            self.summary_var.set('No batch status yet')
            self.current_var.set('Current file: -')

        if runner and self._runner_is_alive(runner.get('pid')):
            self.pid_var.set(f"Background PID: {runner.get('pid')}")
        elif runner:
            self.pid_var.set(f"Background task ended, last PID: {runner.get('pid')}")
        else:
            self.pid_var.set('Background task: not started')

        self.root.after(2000, self._refresh_status)

    def _build_exe(self) -> None:
        if getattr(sys, 'frozen', False):
            messagebox.showinfo('Already an EXE', 'The current process is already a packaged executable.')
            return
        command = [sys.executable, str(REPO_ROOT / 'build_exe.py')]
        creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        subprocess.Popen(command, cwd=str(REPO_ROOT), creationflags=creationflags)
        messagebox.showinfo('Build started', 'PyInstaller started in the background. Check the dist folder later.')

    def _runner_is_alive(self, pid: Any | None = None) -> bool:
        if pid is None:
            runner = self._load_json(RUNNER_PATH)
            pid = None if not runner else runner.get('pid')
        if not pid:
            return False
        try:
            completed = subprocess.run(
                ['tasklist', '/FI', f'PID eq {int(pid)}'],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return False
        return str(int(pid)) in completed.stdout

    def _open_path(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))

    def _load_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None


if __name__ == '__main__':
    raise SystemExit(main())
