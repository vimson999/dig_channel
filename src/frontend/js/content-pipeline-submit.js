(function () {
    const SUPPORTED_EXTENSIONS = new Map([
        ['.pdf', 'pdf'],
        ['.mp3', 'mp3'],
    ]);
    const WEB_SUBMIT_ENDPOINT = '/content-pipeline/web-submit';

    const state = {
        mode: 'folder',
        files: [],
        skipped: [],
        submitting: false,
    };

    const els = {};

    document.addEventListener('DOMContentLoaded', () => {
        bindElements();
        bindEvents();
        render();
    });

    function bindElements() {
        [
            'notice', 'folder-mode', 'upload-mode', 'folder-mode-card', 'upload-mode-card',
            'folder-picker-panel', 'file-picker-panel', 'folder-input', 'file-input',
            'include-subdirectories', 'selection-badge', 'preview-summary', 'file-preview',
            'clear-selection-btn', 'submit-btn', 'submit-help', 'result-panel',
        ].forEach((id) => {
            els[toCamel(id)] = document.getElementById(id);
        });
    }

    function bindEvents() {
        els.folderMode?.addEventListener('change', () => setMode('folder'));
        els.uploadMode?.addEventListener('change', () => setMode('upload'));
        els.folderInput?.addEventListener('change', () => selectFiles(els.folderInput.files, 'folder'));
        els.fileInput?.addEventListener('change', () => selectFiles(els.fileInput.files, 'upload'));
        els.includeSubdirectories?.addEventListener('change', render);
        els.clearSelectionBtn?.addEventListener('click', clearSelection);
        els.submitBtn?.addEventListener('click', submitSelection);
    }

    function setMode(mode) {
        state.mode = mode === 'upload' ? 'upload' : 'folder';
        els.folderMode.checked = state.mode === 'folder';
        els.uploadMode.checked = state.mode === 'upload';
        els.folderPickerPanel.classList.toggle('hidden', state.mode !== 'folder');
        els.filePickerPanel.classList.toggle('hidden', state.mode !== 'upload');
        els.folderModeCard.className = modeCardClass(state.mode === 'folder', 'blue');
        els.uploadModeCard.className = modeCardClass(state.mode === 'upload', 'indigo');
        clearSelection();
    }

    function modeCardClass(selected, tone) {
        if (selected) return 'cursor-pointer rounded-xl border border-slate-900 bg-slate-900 p-4 text-white transition';
        const border = tone === 'blue' ? 'border-blue-200 bg-white text-slate-700 hover:border-blue-400' : 'border-indigo-200 bg-white text-slate-700 hover:border-indigo-400';
        return `cursor-pointer rounded-xl border ${border} p-4 transition`;
    }

    function selectFiles(fileList, mode) {
        const accepted = [];
        const skipped = [];
        for (const file of Array.from(fileList || [])) {
            const filename = String(file?.name || '').trim();
            const mediaType = mediaTypeFor(filename);
            const relativePath = mode === 'folder' && file?.webkitRelativePath ? file.webkitRelativePath : filename;
            if (!filename || !mediaType) {
                skipped.push({ filename: filename || '未命名文件', reason: '仅支持 MP3 或 PDF' });
                continue;
            }
            if (!Number.isFinite(file.size) || file.size <= 0) {
                skipped.push({ filename, reason: '空文件' });
                continue;
            }
            accepted.push({ file, filename, relativePath, mediaType, bytes: file.size });
        }
        state.mode = mode;
        state.files = accepted;
        state.skipped = skipped;
        render();
    }

    function clearSelection() {
        state.files = [];
        state.skipped = [];
        if (els.folderInput) els.folderInput.value = '';
        if (els.fileInput) els.fileInput.value = '';
        render();
    }

    function effectiveFiles() {
        const includeNested = Boolean(els.includeSubdirectories?.checked);
        return state.files.filter((item) => {
            if (state.mode !== 'folder' || includeNested) return true;
            return !folderRelativePath(item).includes('/');
        });
    }

    function nestedFilesExcludedCount() {
        return Math.max(0, state.files.length - effectiveFiles().length);
    }

    function render() {
        const files = effectiveFiles();
        const nestedExcluded = nestedFilesExcludedCount();
        const pdfCount = files.filter((item) => item.mediaType === 'pdf').length;
        const mp3Count = files.filter((item) => item.mediaType === 'mp3').length;
        const totalBytes = files.reduce((sum, item) => sum + item.bytes, 0);

        els.selectionBadge.textContent = files.length ? `待投递 ${files.length} 个` : '尚未选择';
        els.selectionBadge.className = files.length
            ? 'w-fit rounded-full bg-emerald-100 px-3 py-1 text-xs font-black text-emerald-700'
            : 'w-fit rounded-full bg-slate-100 px-3 py-1 text-xs font-black text-slate-500';
        els.clearSelectionBtn.classList.toggle('hidden', !state.files.length && !state.skipped.length);
        els.submitBtn.disabled = !files.length || state.submitting;
        els.submitHelp.textContent = files.length
            ? `本次将提交 PDF ${pdfCount} 个、MP3 ${mp3Count} 个，共 ${formatBytes(totalBytes)}。`
            : '选择至少一个有效文件后可提交。';

        if (!state.files.length && !state.skipped.length) {
            els.previewSummary.textContent = '选择文件夹或文件后，在此确认本次待投递内容。';
            els.filePreview.innerHTML = '<div class="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-8 text-center text-sm text-slate-500">尚未选择内容。</div>';
            return;
        }

        els.previewSummary.textContent = `可投递 ${files.length} 个（PDF ${pdfCount} / MP3 ${mp3Count}）${nestedExcluded ? `，未勾选子目录而暂不提交 ${nestedExcluded} 个` : ''}${state.skipped.length ? `，已忽略 ${state.skipped.length} 个不合规文件` : ''}。`;
        const preview = files.slice(0, 120).map((item) => renderFileRow(item)).join('');
        const remaining = files.length - Math.min(files.length, 120);
        const excluded = [
            ...state.skipped,
            ...(nestedExcluded ? state.files.filter((item) => !files.includes(item)).map((item) => ({ filename: folderRelativePath(item), reason: '未选择包含子文件夹' })) : []),
        ];
        els.filePreview.innerHTML = `
            <div class="overflow-hidden rounded-xl border border-slate-200">
                <div class="grid grid-cols-[92px_minmax(0,1fr)_92px] gap-3 bg-slate-50 px-4 py-2.5 text-xs font-black uppercase tracking-wide text-slate-500">
                    <div>类型</div><div>文件</div><div class="text-right">大小</div>
                </div>
                <div class="divide-y divide-slate-100">${preview || '<div class="px-4 py-5 text-sm text-slate-500">没有可投递文件。</div>'}</div>
            </div>
            ${remaining > 0 ? `<div class="mt-3 text-center text-xs text-slate-500">另有 ${remaining} 个文件将在本次投递中一并处理。</div>` : ''}
            ${excluded.length ? `<div class="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800">${excluded.slice(0, 10).map((item) => `${escapeHtml(item.filename)}：${escapeHtml(item.reason)}`).join('<br>')}${excluded.length > 10 ? `<br>另有 ${excluded.length - 10} 个已忽略文件。` : ''}</div>` : ''}
        `;
    }

    function renderFileRow(item) {
        const label = item.mediaType === 'pdf' ? 'PDF' : 'MP3';
        const tone = item.mediaType === 'pdf' ? 'bg-red-50 text-red-700 border-red-100' : 'bg-indigo-50 text-indigo-700 border-indigo-100';
        return `<div class="grid grid-cols-[92px_minmax(0,1fr)_92px] gap-3 px-4 py-3 text-sm">
            <div><span class="inline-flex rounded-full border px-2 py-1 text-[11px] font-black ${tone}">${label}</span></div>
            <div class="min-w-0"><div class="truncate font-bold text-slate-800" title="${escapeAttr(folderRelativePath(item))}">${escapeHtml(item.filename)}</div><div class="mt-1 truncate text-xs text-slate-400">${escapeHtml(folderRelativePath(item))}</div></div>
            <div class="text-right text-xs font-bold text-slate-600">${formatBytes(item.bytes)}</div>
        </div>`;
    }

    async function submitSelection() {
        const files = effectiveFiles();
        if (!files.length || state.submitting) return;
        state.submitting = true;
        render();
        showNotice('info', '正在上传', `正在投递 ${files.length} 个文件；请勿重复点击。`);
        els.resultPanel.innerHTML = '<div class="rounded-lg bg-slate-50 px-3 py-3 text-sm text-slate-500"><i class="fas fa-spinner fa-spin mr-2"></i>文件正在上传并创建清洗任务…</div>';

        const formData = new FormData();
        formData.append('input_mode', state.mode);
        formData.append('include_subdirectories', String(Boolean(els.includeSubdirectories?.checked)));
        if (state.mode === 'folder' && folderRootName()) {
            formData.append('folder_root', folderRootName());
        }
        files.forEach((item) => {
            formData.append('files', item.file, item.filename);
            formData.append('relative_paths', folderRelativePath(item));
        });

        try {
            const response = await fetch(`${apiBaseUrl()}${WEB_SUBMIT_ENDPOINT}`, {
                method: 'POST',
                credentials: 'include',
                body: formData,
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.success === false) {
                throw new Error(payload.message || `HTTP ${response.status}`);
            }
            renderResult(payload.data || {});
            showNotice(payload.data?.errors?.length ? 'warning' : 'success', '投递已提交', payload.message || '文件已进入统一清洗发布链路。');
            clearSelection();
        } catch (error) {
            showNotice('error', '投递失败', error.message || String(error));
            els.resultPanel.innerHTML = `<div class="rounded-lg border border-red-200 bg-red-50 px-3 py-3 text-sm text-red-700">${escapeHtml(error.message || String(error))}</div>`;
        } finally {
            state.submitting = false;
            render();
        }
    }

    function renderResult(data) {
        const tasks = data?.tasks && typeof data.tasks === 'object' ? data.tasks : {};
        const labels = { pdf: 'PDF 清洗发布', mp3: 'MP3 清洗发布' };
        const taskRows = Object.entries(tasks).map(([kind, task]) => {
            const dedupe = task?.dedupe || {};
            return `<div class="rounded-lg border border-emerald-200 bg-emerald-50/60 p-3">
                <div class="font-black text-emerald-900">${labels[kind] || kind}</div>
                <div class="mt-1 break-all font-mono text-[11px] text-emerald-800">任务 ${escapeHtml(task?.task_id || '-')}</div>
                <div class="mt-1 text-xs text-emerald-700">去重：新增 ${Number(dedupe.unique_count || 0)}，重复 ${Number(dedupe.duplicate_count || 0)}</div>
            </div>`;
        }).join('');
        const errors = Array.isArray(data?.errors) ? data.errors : [];
        els.resultPanel.innerHTML = `
            <div class="space-y-2">
                ${taskRows || '<div class="rounded-lg bg-slate-50 px-3 py-3 text-sm text-slate-500">本次没有创建任务。</div>'}
                ${errors.map((item) => `<div class="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-800">${escapeHtml(item.kind || '未知类型')}：${escapeHtml(item.error || '提交失败')}</div>`).join('')}
            </div>
        `;
    }

    function showNotice(type, title, message) {
        const tone = {
            info: 'border-blue-200 bg-blue-50 text-blue-800',
            success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
            warning: 'border-amber-200 bg-amber-50 text-amber-800',
            error: 'border-red-200 bg-red-50 text-red-800',
        }[type] || 'border-slate-200 bg-white text-slate-700';
        els.notice.className = `mb-4 rounded-lg border px-4 py-3 text-sm ${tone}`;
        els.notice.innerHTML = `<strong>${escapeHtml(title)}</strong><span class="ml-2">${escapeHtml(message || '')}</span>`;
    }

    function mediaTypeFor(filename) {
        const dot = filename.lastIndexOf('.');
        return dot >= 0 ? SUPPORTED_EXTENSIONS.get(filename.slice(dot).toLowerCase()) : null;
    }

    function folderRootName() {
        if (state.mode !== 'folder') return '';
        for (const item of state.files) {
            const parts = String(item?.relativePath || '').replace(/\\/g, '/').split('/').filter(Boolean);
            if (parts.length >= 2) return parts[0];
        }
        return '';
    }

    function folderRelativePath(item) {
        const raw = String(item?.relativePath || item?.filename || '').replace(/\\/g, '/').replace(/^\/+/, '');
        const rootName = folderRootName();
        if (state.mode === 'folder' && rootName && raw.startsWith(`${rootName}/`)) {
            return raw.slice(rootName.length + 1) || String(item?.filename || '');
        }
        return raw || String(item?.filename || '');
    }

    function apiBaseUrl() {
        return window.API_BASE_URL || window.CONFIG?.API_BASE_URL || 'http://localhost:5001/api/v1';
    }

    function formatBytes(value) {
        let size = Number(value || 0);
        for (const unit of ['B', 'KB', 'MB', 'GB', 'TB']) {
            if (size < 1024 || unit === 'TB') return unit === 'B' ? `${Math.round(size)} ${unit}` : `${size.toFixed(1)} ${unit}`;
            size /= 1024;
        }
        return `${size.toFixed(1)} TB`;
    }

    function toCamel(value) {
        return value.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
    }

    function escapeAttr(value) {
        return escapeHtml(value).replace(/`/g, '&#096;');
    }
})();
