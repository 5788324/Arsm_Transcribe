from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

try:
    from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, QTimer, Qt, Signal
    from PySide6.QtGui import QColor, QFont, QIcon, QPixmap
    from PySide6.QtWidgets import (
        QApplication, QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
        QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
        QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QStackedWidget,
        QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
    )
except ImportError as exc:
    raise SystemExit('缺少 PySide6。请运行：python -m pip install PySide6-Essentials') from exc

import app as pipeline_app
from modules.engine import EngineService, SAFE_ACTIONS

REPO_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = REPO_ROOT / 'config.yaml'
COLORS = {
    'ink': '#162A34', 'muted': '#667A83', 'paper': '#F3EFE7', 'card': '#FFFDF8',
    'nav': '#18353D', 'nav_hover': '#244A52', 'teal': '#168A83', 'mint': '#DDF1EB',
    'coral': '#E66B4F', 'gold': '#D8A73C', 'line': '#DDD8CE', 'white': '#FFFFFF',
}
ACTION_LABELS = {
    'convert_existing_subtitle': '直接转换', 'translate_existing_subtitle': '只需翻译',
    'transcribe_audio': '需要转录', 'skip_existing_lrc': '已有字幕', 'manual_review': '人工确认',
}


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:
            self.signals.failed.emit(f'{type(exc).__name__}: {exc}')
        else:
            self.signals.finished.emit(result)


class MetricCard(QFrame):
    def __init__(self, title: str, accent: str) -> None:
        super().__init__()
        self.setObjectName('metricCard')
        self.setProperty('accent', accent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        self.value = QLabel('—')
        self.value.setObjectName('metricValue')
        self.label = QLabel(title)
        self.label.setObjectName('metricLabel')
        layout.addWidget(self.value)
        layout.addWidget(self.label)

    def set_value(self, value: Any) -> None:
        self.value.setText(str(value))


class LibraryWindow(QMainWindow):
    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        super().__init__()
        self.config_path = config_path
        self.config = pipeline_app.load_config(config_path)
        self.engine = EngineService(self.config)
        self.pool = QThreadPool.globalInstance()
        self.current_job_id: int | None = None
        self.batch_process: subprocess.Popen[str] | None = None
        self.roots = [str(Path(value)) for value in self.config.get('library', {}).get('roots', [r'E:\arsm'])]
        self._build_window()
        self._build_ui()
        self._apply_style()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_runtime)
        self.timer.start(2000)
        QTimer.singleShot(100, self.refresh_all)

    def _build_window(self) -> None:
        self.setWindowTitle('Arsm Transcribe · 本地音声字幕资料库')
        self.resize(1480, 900)
        self.setMinimumSize(1180, 720)

    def _build_ui(self) -> None:
        root = QWidget()
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self._build_sidebar())
        self.pages = QStackedWidget()
        shell.addWidget(self.pages, 1)
        self.pages.addWidget(self._build_overview_page())
        self.pages.addWidget(self._build_library_page())
        self.pages.addWidget(self._build_tasks_page())
        self.pages.addWidget(self._build_quality_page())
        self.pages.addWidget(self._build_models_page())
        self.pages.addWidget(self._build_glossary_page())
        self.setCentralWidget(root)

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName('sidebar')
        side.setFixedWidth(235)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(18, 25, 18, 20)
        brand = QLabel('ARSM\nTRANSCRIBE')
        brand.setObjectName('brand')
        subtitle = QLabel('本地音声字幕资料库')
        subtitle.setObjectName('brandSubtitle')
        layout.addWidget(brand)
        layout.addWidget(subtitle)
        layout.addSpacing(28)
        self.nav_buttons: list[QPushButton] = []
        for index, (text, symbol) in enumerate([
            ('总览', '⌂'), ('作品资料库', '▦'), ('任务中心', '▶'),
            ('质量审查', '✓'), ('模型中心', '◈'), ('术语表', '字'),
        ]):
            button = QPushButton(f'{symbol}   {text}')
            button.setObjectName('navButton')
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, page=index: self.switch_page(page))
            self.nav_buttons.append(button)
            layout.addWidget(button)
        self.nav_buttons[0].setChecked(True)
        layout.addStretch()
        self.service_badge = QLabel('● 正在检查本地服务')
        self.service_badge.setObjectName('serviceBadge')
        layout.addWidget(self.service_badge)
        version = QLabel('API 1.1 · 本地处理')
        version.setObjectName('sidebarFoot')
        layout.addWidget(version)
        return side

    def _page_header(self, eyebrow: str, title: str, description: str) -> tuple[QWidget, QHBoxLayout]:
        frame = QWidget()
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 0, 0, 0)
        text = QVBoxLayout()
        eye = QLabel(eyebrow.upper())
        eye.setObjectName('eyebrow')
        heading = QLabel(title)
        heading.setObjectName('pageTitle')
        desc = QLabel(description)
        desc.setObjectName('pageDescription')
        text.addWidget(eye)
        text.addWidget(heading)
        text.addWidget(desc)
        row.addLayout(text)
        row.addStretch()
        return frame, row

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName('page')
        layout = QVBoxLayout(page)
        layout.setContentsMargins(34, 28, 34, 28)
        layout.setSpacing(18)
        return page, layout

    def _build_overview_page(self) -> QWidget:
        page, layout = self._page()
        header, actions = self._page_header('Library command center', '今天要处理什么？', '先扫描和确认，再让本地模型安静地完成剩余工作。')
        scan = self._button('扫描新文件', 'primary', self.choose_and_scan)
        process = self._button('一键处理安全项目', 'accent', self.confirm_and_process)
        actions.addWidget(scan)
        actions.addWidget(process)
        layout.addWidget(header)
        metrics = QGridLayout()
        metrics.setSpacing(12)
        self.metrics = {
            'works': MetricCard('作品', COLORS['teal']), 'total': MetricCard('媒体文件', COLORS['gold']),
            'convert_existing_subtitle': MetricCard('可直接转换', COLORS['teal']),
            'translate_existing_subtitle': MetricCard('只需翻译', COLORS['gold']),
            'transcribe_audio': MetricCard('需要转录', COLORS['coral']),
            'quality_flags': MetricCard('质量提醒', COLORS['coral']),
        }
        for index, card in enumerate(self.metrics.values()):
            metrics.addWidget(card, index // 3, index % 3)
        layout.addLayout(metrics)
        lower = QHBoxLayout()
        activity = self._card('当前批次')
        activity_layout = activity.layout()
        self.runtime_title = QLabel('没有正在运行的任务')
        self.runtime_title.setObjectName('sectionTitle')
        self.runtime_detail = QLabel('扫描资料库后，可以预览每个文件为什么会被处理。')
        self.runtime_detail.setObjectName('mutedText')
        self.runtime_detail.setWordWrap(True)
        activity_layout.addWidget(self.runtime_title)
        activity_layout.addWidget(self.runtime_detail)
        quick = self._card('处理原则')
        quick.layout().addWidget(self._rich_label('中文定时字幕直接转换\n日文定时字幕只翻译\n没有时间轴才运行 ASR\n未知配对留给人工确认'))
        lower.addWidget(activity, 2)
        lower.addWidget(quick, 1)
        layout.addLayout(lower)
        layout.addStretch()
        return page

    def _build_library_page(self) -> QWidget:
        page, layout = self._page()
        header, actions = self._page_header('Collection', '作品资料库', '按 RJ 编号或作品目录聚合，不移动原始文件。')
        actions.addWidget(self._button('刷新', 'soft', self.refresh_library))
        layout.addWidget(header)
        filters = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText('搜索 RJ 编号、作品名或文件名…')
        self.search_box.returnPressed.connect(self.refresh_library)
        self.action_filter = QComboBox()
        self.action_filter.addItem('全部状态', '')
        for key, label in ACTION_LABELS.items():
            self.action_filter.addItem(label, key)
        self.action_filter.currentIndexChanged.connect(self.refresh_library)
        filters.addWidget(self.search_box, 1)
        filters.addWidget(self.action_filter)
        layout.addLayout(filters)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.works_table = self._table(['作品', 'RJ', '音轨', '直接转换', '翻译', 'ASR', '确认'])
        self.works_table.setIconSize(QSize(42, 42))
        self.works_table.itemSelectionChanged.connect(self.load_selected_work)
        self.media_table = self._table(['文件', '格式', '处理动作', '状态', '质量', '来源字幕'])
        self.media_table.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        splitter.addWidget(self.works_table)
        splitter.addWidget(self.media_table)
        splitter.setSizes([430, 270])
        layout.addWidget(splitter, 1)
        return page

    def _build_tasks_page(self) -> QWidget:
        page, layout = self._page()
        header, actions = self._page_header('Processing queue', '任务中心', '停止不会丢失结果，恢复时自动跳过已完成阶段。')
        actions.addWidget(self._button('暂停', 'soft', self.pause_current_job))
        actions.addWidget(self._button('恢复', 'soft', self.resume_current_job))
        actions.addWidget(self._button('停止', 'danger', self.cancel_current_job))
        actions.addWidget(self._button('重试失败项', 'accent', self.retry_failed))
        layout.addWidget(header)
        self.jobs_table = self._table(['ID', '状态', '动作', '数量', '完成', '失败', '创建时间'])
        layout.addWidget(self.jobs_table, 1)
        return page

    def _build_quality_page(self) -> QWidget:
        page, layout = self._page()
        header, actions = self._page_header('Subtitle review', '质量审查', '快速找到空翻译、日文残留、超长行和时间轴异常。')
        actions.addWidget(self._button('检查已有结果', 'primary', self.run_quality_review))
        layout.addWidget(header)
        self.quality_table = self._table(['级别', '作品', '文件', '问题', '说明'])
        layout.addWidget(self.quality_table, 1)
        return page

    def _build_models_page(self) -> QWidget:
        page, layout = self._page()
        header, actions = self._page_header('Local models', '模型中心', '模型随时可以换，阶段 JSON 让重跑只发生在必要位置。')
        actions.addWidget(self._button('运行环境诊断', 'primary', self.run_doctor))
        actions.addWidget(self._button('保存模型档案', 'soft', self.open_profile_dialog))
        layout.addWidget(header)
        self.models_table = self._table(['类型', '名称', '当前使用', '地址 / 后端', '更新时间'])
        layout.addWidget(self.models_table, 1)
        self.doctor_box = QLabel('尚未运行诊断。')
        self.doctor_box.setObjectName('diagnosticBox')
        self.doctor_box.setWordWrap(True)
        layout.addWidget(self.doctor_box)
        return page

    def _build_glossary_page(self) -> QWidget:
        page, layout = self._page()
        header, _actions = self._page_header('Terminology', '术语表', '固定角色名、称呼和专有名词，减少同一作品前后不一致。')
        layout.addWidget(header)
        editor = QHBoxLayout()
        self.term_source = QLineEdit()
        self.term_source.setPlaceholderText('日文原词')
        self.term_target = QLineEdit()
        self.term_target.setPlaceholderText('中文译法')
        editor.addWidget(self.term_source)
        editor.addWidget(self.term_target)
        editor.addWidget(self._button('添加术语', 'primary', self.add_glossary_term))
        layout.addLayout(editor)
        self.glossary_table = self._table(['范围', '日文原词', '中文译法', '更新时间'])
        layout.addWidget(self.glossary_table, 1)
        return page

    def switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)
        if index == 1:
            self.refresh_library()
        elif index == 2:
            self.refresh_jobs()
        elif index == 3:
            self.refresh_quality()
        elif index == 4:
            self.refresh_models()
        elif index == 5:
            self.refresh_glossary()

    def refresh_all(self) -> None:
        self._run_async(self.engine.list_works, self._apply_overview)
        self.refresh_models()
        self.run_doctor(silent=True)

    def choose_and_scan(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, '选择资料库根目录', self.roots[0] if self.roots else str(Path.home()))
        if not selected:
            return
        self.roots = [selected]
        self._scan_roots()

    def _scan_roots(self) -> None:
        self.runtime_title.setText('正在扫描资料库…')
        self.runtime_detail.setText('只更新索引和处理计划，不会修改音频目录。')
        self._run_async(lambda: self.engine.scan([Path(value) for value in self.roots]), self._scan_finished)

    def _scan_finished(self, result: dict[str, Any]) -> None:
        summary = result.get('summary', {})
        self.runtime_title.setText(f"扫描完成 · {len(result.get('items', []))} 个媒体文件")
        self.runtime_detail.setText(
            f"直接转换 {summary.get('convert_existing_subtitle', 0)} · 翻译 {summary.get('translate_existing_subtitle', 0)} · "
            f"ASR {summary.get('transcribe_audio', 0)} · 人工确认 {summary.get('manual_review', 0)}"
        )
        self.refresh_library()
        self._apply_summary(result.get('catalog', {}))

    def confirm_and_process(self) -> None:
        self.runtime_title.setText('正在生成处理计划…')
        self._run_async(lambda: self.engine.plan([Path(value) for value in self.roots]), self._confirm_plan_result)

    def _confirm_plan_result(self, preview: dict[str, Any]) -> None:
        summary = preview.get('summary', {})
        safe_total = sum(int(summary.get(action, 0)) for action in SAFE_ACTIONS)
        message = (
            f'将处理 {safe_total} 个安全项目：\n\n'
            f"直接转换 {summary.get('convert_existing_subtitle', 0)}\n"
            f"只需翻译 {summary.get('translate_existing_subtitle', 0)}\n"
            f"需要 ASR {summary.get('transcribe_audio', 0)}\n\n"
            f"人工确认 {summary.get('manual_review', 0)} 个不会处理。"
        )
        if QMessageBox.question(self, '确认处理计划', message) != QMessageBox.StandardButton.Yes:
            self.runtime_title.setText('已取消处理')
            return
        if bool(self.config.get('translate', {}).get('enabled', True)):
            doctor = self.engine.doctor()
            translation = next((item for item in doctor.get('checks', []) if item['name'] == 'translation_service'), None)
            if translation is not None and not translation['ok']:
                QMessageBox.warning(self, '翻译服务不可用', '请先启动 LM Studio/Ollama，再开始包含翻译或 ASR 的任务。')
                return
        job_result = self.engine.enqueue(actions=SAFE_ACTIONS)
        self.current_job_id = int(job_result['job']['id'])
        self.engine.set_job_state(self.current_job_id, 'running')
        self._launch_batch(retry=False)
        self.switch_page(2)

    def _launch_batch(self, *, retry: bool) -> None:
        if self.batch_process is not None and self.batch_process.poll() is None:
            QMessageBox.information(self, '任务运行中', '已有处理任务正在运行。')
            return
        command = [sys.executable, '-B', str(REPO_ROOT / 'app.py'), '--config', str(self.config_path)]
        if retry:
            command.append('retry-failed')
        else:
            command.extend(['run-batch', *self.roots])
        log_dir = Path(self.config.get('paths', {}).get('log_dir', 'logs')).resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout = open(log_dir / 'qt_batch_stdout.log', 'a', encoding='utf-8')
        stderr = open(log_dir / 'qt_batch_stderr.log', 'a', encoding='utf-8')
        self.batch_process = subprocess.Popen(command, cwd=REPO_ROOT, stdout=stdout, stderr=stderr, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), text=True)
        self.runtime_title.setText('本地处理任务正在运行')

    def pause_current_job(self) -> None:
        if self.current_job_id is None:
            QMessageBox.information(self, '没有任务', '当前没有可暂停的任务。')
            return
        self.engine.cancel()
        self.engine.set_job_state(self.current_job_id, 'paused')
        self.runtime_title.setText('已请求暂停')
        self.runtime_detail.setText('当前文件完成后停止；点击恢复会从磁盘阶段结果继续。')
        self.refresh_jobs()

    def resume_current_job(self) -> None:
        if self.current_job_id is None:
            QMessageBox.information(self, '没有任务', '请选择或创建一个任务后再恢复。')
            return
        self.engine.set_job_state(self.current_job_id, 'running')
        self._launch_batch(retry=False)
        self.refresh_jobs()

    def cancel_current_job(self) -> None:
        self.engine.cancel()
        if self.current_job_id is not None:
            self.engine.set_job_state(self.current_job_id, 'cancelled')
        self.runtime_title.setText('已请求停止')
        self.runtime_detail.setText('当前文件完成后安全退出，不会删除已有结果。')
        self.refresh_jobs()

    def retry_failed(self) -> None:
        result = self.engine.enqueue(actions=SAFE_ACTIONS)
        self.current_job_id = int(result['job']['id'])
        self.engine.set_job_state(self.current_job_id, 'running')
        self._launch_batch(retry=True)

    def refresh_runtime(self) -> None:
        result = self.engine.status()
        status = result.get('status') or {}
        if status:
            state = status.get('state', '-')
            total = int(status.get('total', 0) or 0)
            current = int(status.get('current_index', 0) or 0)
            self.runtime_title.setText(f'批次状态：{state} · {current}/{total}')
            self.runtime_detail.setText(
                f"成功 {status.get('succeeded', 0)} · 跳过 {status.get('skipped', 0)} · 失败 {status.get('failed', 0)}\n"
                f"{status.get('current_audio_path') or '等待下一项'}"
            )
            if self.current_job_id is not None:
                job_state = 'completed' if state == 'completed' else ('cancelled' if state == 'cancelled' else None)
                self.engine.update_job_progress(self.current_job_id, completed=current, failed=int(status.get('failed', 0) or 0), state=job_state)
        if self.batch_process is not None and self.batch_process.poll() is not None:
            self.batch_process = None

    def refresh_library(self) -> None:
        search = self.search_box.text().strip() if hasattr(self, 'search_box') else ''
        action = self.action_filter.currentData() if hasattr(self, 'action_filter') else ''
        self._run_async(lambda: self.engine.list_works(search=search, action=action), self._apply_works)

    def _apply_overview(self, result: dict[str, Any]) -> None:
        self._apply_summary(result.get('summary', {}))

    def _apply_summary(self, summary: dict[str, Any]) -> None:
        for key, card in self.metrics.items():
            card.set_value(summary.get(key, 0))

    def _apply_works(self, result: dict[str, Any]) -> None:
        self._apply_summary(result.get('summary', {}))
        works = result.get('works', [])
        self.works_table.setRowCount(len(works))
        for row, work in enumerate(works):
            values = [work['title'], work.get('rj_code') or '—', work['media_count'], work['convert_count'], work['translate_count'], work['asr_count'], work['review_count']]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, int(work['id']))
                    item.setToolTip(work['root_path'])
                    cover = work.get('cover_path')
                    if cover and Path(cover).is_file():
                        item.setIcon(QIcon(cover))
                self.works_table.setItem(row, column, item)
        if works:
            self.works_table.selectRow(0)

    def load_selected_work(self) -> None:
        row = self.works_table.currentRow()
        if row < 0 or self.works_table.item(row, 0) is None:
            return
        work_id = int(self.works_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
        self._run_async(lambda: self.engine.list_media(work_id), self._apply_media)

    def _apply_media(self, result: dict[str, Any]) -> None:
        media = result.get('media', [])
        self.media_table.setRowCount(len(media))
        for row, item in enumerate(media):
            values = [Path(item['path']).name, item.get('extension') or Path(item['path']).suffix, ACTION_LABELS.get(item['action'], item['action']), item['status'], item['quality_count'], item.get('source_path') or '—']
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setToolTip(item['path'] if column == 0 else str(value))
                self.media_table.setItem(row, column, cell)

    def refresh_jobs(self) -> None:
        self._run_async(self.engine.jobs, self._apply_jobs)

    def _apply_jobs(self, result: dict[str, Any]) -> None:
        jobs = result.get('jobs', [])
        self.jobs_table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            values = [job['id'], job['state'], job['action'], job['total'], job['completed'], job['failed'], job['created_at']]
            for column, value in enumerate(values):
                self.jobs_table.setItem(row, column, QTableWidgetItem(str(value)))

    def run_quality_review(self) -> None:
        self._run_async(self.engine.review, lambda result: (self._apply_quality(result), QMessageBox.information(self, '检查完成', f"检查 {result['checked']} 个媒体，发现 {result['flag_count']} 个提醒。")))

    def refresh_quality(self) -> None:
        self._run_async(self.engine.quality_flags, self._apply_quality)

    def _apply_quality(self, result: dict[str, Any]) -> None:
        flags = result.get('flags', [])
        self.quality_table.setRowCount(len(flags))
        for row, flag in enumerate(flags):
            values = [flag['severity'], flag['work_title'], Path(flag['media_path']).name, flag['code'], flag['message']]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setForeground(QColor(COLORS['coral'] if value == 'error' else COLORS['gold']))
                self.quality_table.setItem(row, column, item)

    def run_doctor(self, *, silent: bool = False) -> None:
        def done(result: dict[str, Any]) -> None:
            checks = result.get('checks', [])
            self.doctor_box.setText('\n'.join(f"{'✓' if check['ok'] else '×'} {check['name']}：{check['detail']}" for check in checks))
            service = next((check for check in checks if check['name'] == 'translation_service'), None)
            if service and service['ok']:
                self.service_badge.setText('● 本地翻译服务可用')
                self.service_badge.setProperty('available', True)
            else:
                self.service_badge.setText('● 翻译服务未连接')
                self.service_badge.setProperty('available', False)
            self.service_badge.style().unpolish(self.service_badge)
            self.service_badge.style().polish(self.service_badge)
            if not silent:
                QMessageBox.information(self, '环境诊断', '全部检查通过。' if result['ok'] else '存在未通过项目，请查看模型中心。')
        self._run_async(self.engine.doctor, done)

    def open_profile_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle('保存模型配置档案')
        dialog.resize(620, 480)
        form = QFormLayout(dialog)
        kind = QComboBox()
        kind.addItems(['translate', 'asr'])
        name = QLineEdit(str(self.config.get('translate', {}).get('model', '新配置')))
        settings = QPlainTextEdit(json.dumps(self.config.get('translate', {}), ensure_ascii=False, indent=2))
        active = QCheckBox('设为当前使用档案')
        active.setChecked(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        form.addRow('类型', kind)
        form.addRow('档案名称', name)
        form.addRow('配置 JSON', settings)
        form.addRow('', active)
        form.addRow(buttons)
        buttons.rejected.connect(dialog.reject)

        def save() -> None:
            try:
                payload = json.loads(settings.toPlainText())
                self.engine.save_profile(name.text().strip(), kind.currentText(), payload, active=active.isChecked())
            except Exception as exc:
                QMessageBox.warning(dialog, '无法保存', f'{type(exc).__name__}: {exc}')
                return
            dialog.accept()
            self.refresh_models()

        buttons.accepted.connect(save)
        dialog.exec()
    def refresh_models(self) -> None:
        self._run_async(self.engine.profiles, self._apply_models)

    def _apply_models(self, result: dict[str, Any]) -> None:
        profiles = result.get('profiles', [])
        self.models_table.setRowCount(len(profiles))
        for row, profile in enumerate(profiles):
            settings = json.loads(profile['settings_json'])
            endpoint = settings.get('base_url') or settings.get('backend') or settings.get('model') or '—'
            values = [profile['kind'], profile['name'], '当前' if profile['active'] else '', endpoint, profile['updated_at']]
            for column, value in enumerate(values):
                self.models_table.setItem(row, column, QTableWidgetItem(str(value)))

    def add_glossary_term(self) -> None:
        result = self.engine.save_glossary(self.term_source.text(), self.term_target.text())
        if not result['ok']:
            QMessageBox.warning(self, '无法保存', result.get('error', '未知错误'))
            return
        self.term_source.clear()
        self.term_target.clear()
        self._apply_glossary(result)

    def refresh_glossary(self) -> None:
        self._run_async(self.engine.glossary, self._apply_glossary)

    def _apply_glossary(self, result: dict[str, Any]) -> None:
        terms = result.get('terms', [])
        self.glossary_table.setRowCount(len(terms))
        for row, term in enumerate(terms):
            values = ['全局' if term.get('work_id') is None else f"作品 {term['work_id']}", term['source'], term['target'], term['updated_at']]
            for column, value in enumerate(values):
                self.glossary_table.setItem(row, column, QTableWidgetItem(str(value)))

    def _run_async(self, fn: Callable[[], Any], done: Callable[[Any], None]) -> None:
        worker = Worker(fn)
        worker.signals.finished.connect(done)
        worker.signals.failed.connect(lambda error: QMessageBox.critical(self, '操作失败', error))
        self.pool.start(worker)

    def _button(self, text: str, kind: str, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(text)
        button.setProperty('kind', kind)
        button.clicked.connect(callback)
        return button

    def _card(self, title: str) -> QFrame:
        card = QFrame()
        card.setObjectName('contentCard')
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        label = QLabel(title)
        label.setObjectName('cardEyebrow')
        layout.addWidget(label)
        return card

    def _rich_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName('richText')
        label.setWordWrap(True)
        return label

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _apply_style(self) -> None:
        QApplication.instance().setFont(QFont('Microsoft YaHei UI', 10))
        self.setStyleSheet(f'''
            QMainWindow, QWidget#page {{ background: {COLORS['paper']}; color: {COLORS['ink']}; }}
            QFrame#sidebar {{ background: {COLORS['nav']}; }}
            QLabel#brand {{ color: {COLORS['white']}; font: 700 24px Georgia; letter-spacing: 2px; }}
            QLabel#brandSubtitle {{ color: #A9C3C5; font-size: 12px; }}
            QPushButton#navButton {{ border: 0; border-radius: 10px; color: #C8D7D8; text-align: left; padding: 12px 14px; font-weight: 600; }}
            QPushButton#navButton:hover {{ background: {COLORS['nav_hover']}; color: white; }}
            QPushButton#navButton:checked {{ background: {COLORS['teal']}; color: white; }}
            QLabel#serviceBadge {{ background: #244A52; border-radius: 9px; color: #F0C66B; padding: 9px; font-size: 11px; }}
            QLabel#serviceBadge[available="true"] {{ color: #8CE0C5; }}
            QLabel#sidebarFoot {{ color: #719094; font-size: 10px; padding-top: 8px; }}
            QLabel#eyebrow, QLabel#cardEyebrow {{ color: {COLORS['teal']}; font-size: 10px; font-weight: 700; letter-spacing: 1px; }}
            QLabel#pageTitle {{ color: {COLORS['ink']}; font: 700 30px Georgia; }}
            QLabel#pageDescription, QLabel#mutedText {{ color: {COLORS['muted']}; font-size: 12px; }}
            QFrame#metricCard, QFrame#contentCard {{ background: {COLORS['card']}; border: 1px solid {COLORS['line']}; border-radius: 14px; }}
            QLabel#metricValue {{ color: {COLORS['ink']}; font: 700 28px Georgia; }}
            QLabel#metricLabel {{ color: {COLORS['muted']}; font-size: 11px; }}
            QLabel#sectionTitle {{ color: {COLORS['ink']}; font-size: 17px; font-weight: 700; }}
            QLabel#richText {{ color: {COLORS['ink']}; font-size: 13px; line-height: 1.5; }}
            QPushButton {{ min-height: 34px; border-radius: 9px; padding: 0 16px; font-weight: 600; }}
            QPushButton[kind="primary"] {{ background: {COLORS['teal']}; color: white; border: 0; }}
            QPushButton[kind="primary"]:hover {{ background: #0F746E; }}
            QPushButton[kind="accent"] {{ background: {COLORS['coral']}; color: white; border: 0; }}
            QPushButton[kind="soft"] {{ background: {COLORS['mint']}; color: {COLORS['teal']}; border: 0; }}
            QPushButton[kind="danger"] {{ background: #F7DED7; color: #A43C27; border: 0; }}
            QLineEdit, QComboBox {{ background: {COLORS['card']}; border: 1px solid {COLORS['line']}; border-radius: 9px; padding: 8px 11px; min-height: 20px; }}
            QLineEdit:focus, QComboBox:focus {{ border: 1px solid {COLORS['teal']}; }}
            QTableWidget {{ background: {COLORS['card']}; alternate-background-color: #F8F5EE; border: 1px solid {COLORS['line']}; border-radius: 12px; gridline-color: #ECE7DE; selection-background-color: {COLORS['mint']}; selection-color: {COLORS['ink']}; }}
            QHeaderView::section {{ background: #ECE8DF; color: {COLORS['muted']}; border: 0; border-bottom: 1px solid {COLORS['line']}; padding: 10px; font-weight: 700; }}
            QLabel#diagnosticBox {{ background: #E9E4D9; border-radius: 11px; padding: 16px; color: {COLORS['ink']}; font-family: Consolas; }}
            QSplitter::handle {{ background: transparent; height: 10px; }}
        ''')


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName('Arsm Transcribe')
    window = LibraryWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
