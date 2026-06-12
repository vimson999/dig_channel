import os
import platform
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request


bp = Blueprint('batch_transcription', __name__, url_prefix='/api/v1/batch-transcription')

AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.wav', '.aac', '.flac', '.ogg'}
DEFAULT_MODEL_SIZE = 'medium'

_tasks = {}
_tasks_lock = threading.Lock()


def _format_timestamp(seconds):
    total_seconds = max(0, int(float(seconds or 0)))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours:
        return f'{hours:02d}:{minutes:02d}:{secs:02d}'
    return f'{minutes:02d}:{secs:02d}'


def _get_audio_duration(source_path):
    try:
        from pydub.utils import mediainfo

        info = mediainfo(source_path)
        return float(info.get('duration') or 0)
    except Exception:
        return None


def _write_transcript(output_path, segments):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        for segment in segments:
            text = str(segment.get('text') or '').strip()
            if not text:
                continue
            f.write(f"{_format_timestamp(segment.get('start', 0))}\n\n")
            f.write(f"{text}\n\n")


def _format_elapsed_for_filename(seconds):
    if seconds is None:
        return 'pending'
    value = max(0, float(seconds))
    if value < 60:
        return f'{value:.1f}s'.replace('.0s', 's')
    minutes = int(value // 60)
    secs = int(round(value % 60))
    return f'{minutes}m{secs:02d}s'


def _transcript_relative_path(relative_audio_path, model_size, elapsed_seconds=None):
    path = Path(relative_audio_path)
    elapsed_label = _format_elapsed_for_filename(elapsed_seconds)
    return str(path.with_name(f'{path.stem}.{model_size}.{elapsed_label}.txt'))


def _task_snapshot(task):
    return {
        'id': task['id'],
        'status': task['status'],
        'input_dir': task['input_dir'],
        'output_dir': task['output_dir'],
        'model_size': task['model_size'],
        'total': task['total'],
        'completed': task['completed'],
        'succeeded': task['succeeded'],
        'failed': task['failed'],
        'current_file': task.get('current_file'),
        'error': task.get('error'),
        'created_at': task['created_at'],
        'updated_at': task['updated_at'],
        'files': task['files'],
    }


def _update_task(task_id, **updates):
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return None
        task.update(updates)
        task['updated_at'] = datetime.utcnow().isoformat()
        return _task_snapshot(task)


def _update_file(task_id, index, **updates):
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return
        task['files'][index].update(updates)
        task['updated_at'] = datetime.utcnow().isoformat()


def _run_batch_task(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return
        input_dir = task['input_dir']
        output_dir = task['output_dir']
        model_size = task['model_size']
        files = list(task['files'])
        task['status'] = 'running'
        task['updated_at'] = datetime.utcnow().isoformat()

    try:
        for index, item in enumerate(files):
            source_path = item['source_path']
            relative_path = item['relative_path']

            _update_task(task_id, current_file=relative_path)
            started_at = datetime.utcnow().isoformat()
            start_time = time.time()
            _update_file(
                task_id,
                index,
                status='processing',
                started_at=started_at,
            )

            try:
                from services.asr_cache_service import transcribe_with_cache

                segments = transcribe_with_cache(
                    source_path,
                    model_size=model_size,
                )
                elapsed_seconds = round(time.time() - start_time, 2)
                output_relative = _transcript_relative_path(relative_path, model_size, elapsed_seconds)
                output_path = os.path.join(output_dir, output_relative)
                _write_transcript(output_path, segments)

                with _tasks_lock:
                    task = _tasks[task_id]
                    task['completed'] += 1
                    task['succeeded'] += 1
                    task['files'][index].update({
                        'status': 'success',
                        'output_path': output_path,
                        'elapsed_seconds': elapsed_seconds,
                        'completed_at': datetime.utcnow().isoformat(),
                    })
                    task['updated_at'] = datetime.utcnow().isoformat()
            except Exception as exc:
                with _tasks_lock:
                    task = _tasks[task_id]
                    elapsed_seconds = round(time.time() - start_time, 2)
                    task['completed'] += 1
                    task['failed'] += 1
                    task['files'][index].update({
                        'status': 'failed',
                        'error': str(exc),
                        'elapsed_seconds': elapsed_seconds,
                        'completed_at': datetime.utcnow().isoformat(),
                    })
                    task['updated_at'] = datetime.utcnow().isoformat()

        with _tasks_lock:
            task = _tasks[task_id]
            task['status'] = 'completed' if task['failed'] == 0 else 'completed_with_errors'
            task['current_file'] = None
            task['updated_at'] = datetime.utcnow().isoformat()
    except Exception as exc:
        _update_task(task_id, status='failed', error=str(exc), current_file=None)


def _scan_audio_files(input_dir):
    root = Path(input_dir)
    files = []
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            files.append({
                'source_path': str(path),
                'relative_path': str(path.relative_to(root)),
                'status': 'waiting',
                'output_path': None,
                'duration_seconds': _get_audio_duration(str(path)),
                'elapsed_seconds': None,
                'error': None,
            })
    return files


def _pick_directory(title, initial_dir=None):
    if platform.system() == 'Darwin':
        script_parts = [f'choose folder with prompt "{title}"']
        if initial_dir and os.path.isdir(initial_dir):
            script_parts.append(f'default location POSIX file "{initial_dir}"')
        script = ' '.join(script_parts)
        result = subprocess.run(
            ['osascript', '-e', f'POSIX path of ({script})'],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        if result.stderr and 'User canceled' in result.stderr:
            return ''

    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        selected = filedialog.askdirectory(
            title=title,
            initialdir=initial_dir if initial_dir and os.path.isdir(initial_dir) else os.path.expanduser('~'),
            mustexist=True,
        )
        return selected
    finally:
        root.destroy()


@bp.route('/select-directory', methods=['POST'])
def select_batch_transcription_directory():
    data = request.get_json(silent=True) or {}
    purpose = str(data.get('purpose') or 'input').strip()
    title_override = str(data.get('title') or '').strip()
    initial_dir_raw = str(data.get('initial_dir') or '').strip()
    initial_dir = os.path.abspath(os.path.expanduser(initial_dir_raw)) if initial_dir_raw else None
    title = title_override or ('选择音频输入目录' if purpose == 'input' else '选择文案输出目录')

    try:
        selected = _pick_directory(title, initial_dir)
        if not selected:
            return jsonify({'success': False, 'error': '未选择目录'}), 400
        return jsonify({'success': True, 'data': {'path': selected}})
    except Exception as exc:
        return jsonify({'success': False, 'error': f'无法打开目录选择器: {exc}'}), 500


@bp.route('/start', methods=['POST'])
def start_batch_transcription():
    data = request.get_json(silent=True) or {}
    raw_input_dir = str(data.get('input_dir') or '').strip()
    raw_output_dir = str(data.get('output_dir') or '').strip()
    input_dir = os.path.abspath(os.path.expanduser(raw_input_dir)) if raw_input_dir else ''
    output_dir = os.path.abspath(os.path.expanduser(raw_output_dir)) if raw_output_dir else ''
    model_size = str(data.get('model_size') or DEFAULT_MODEL_SIZE).strip()

    if not input_dir or not output_dir:
        return jsonify({'success': False, 'error': '输入目录和输出目录不能为空'}), 400
    if not os.path.isdir(input_dir):
        return jsonify({'success': False, 'error': f'输入目录不存在: {input_dir}'}), 400
    if model_size not in {'tiny', 'base', 'small', 'medium', 'large-v3'}:
        return jsonify({'success': False, 'error': f'不支持的模型: {model_size}'}), 400

    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as exc:
        return jsonify({'success': False, 'error': f'无法创建输出目录: {exc}'}), 400

    files = _scan_audio_files(input_dir)
    if not files:
        return jsonify({'success': False, 'error': '输入目录下没有找到支持的音频文件'}), 400

    task_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    task = {
        'id': task_id,
        'status': 'queued',
        'input_dir': input_dir,
        'output_dir': output_dir,
        'model_size': model_size,
        'total': len(files),
        'completed': 0,
        'succeeded': 0,
        'failed': 0,
        'current_file': None,
        'error': None,
        'created_at': now,
        'updated_at': now,
        'files': files,
    }

    with _tasks_lock:
        _tasks[task_id] = task

    worker = threading.Thread(target=_run_batch_task, args=(task_id,), daemon=True)
    worker.start()

    return jsonify({'success': True, 'data': _task_snapshot(task)})


@bp.route('/task/<task_id>', methods=['GET'])
def get_batch_transcription_task(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return jsonify({'success': False, 'error': '任务不存在'}), 404
        return jsonify({'success': True, 'data': _task_snapshot(task)})


@bp.route('/scan', methods=['POST'])
def scan_batch_transcription_files():
    data = request.get_json(silent=True) or {}
    raw_input_dir = str(data.get('input_dir') or '').strip()
    input_dir = os.path.abspath(os.path.expanduser(raw_input_dir)) if raw_input_dir else ''

    if not input_dir:
        return jsonify({'success': False, 'error': '输入目录不能为空'}), 400
    if not os.path.isdir(input_dir):
        return jsonify({'success': False, 'error': f'输入目录不存在: {input_dir}'}), 400

    files = _scan_audio_files(input_dir)
    return jsonify({
        'success': True,
        'data': {
            'input_dir': input_dir,
            'total': len(files),
            'files': files,
            'extensions': sorted(AUDIO_EXTENSIONS),
        }
    })
