(function () {
    const SUPPORTED_EXTENSIONS = new Map([
        ['.pdf', 'pdf'],
        ['.mp3', 'mp3'],
    ]);
    const WEB_SUBMIT_ENDPOINT = '/content-pipeline/web-submit';
    const WEB_SUBMISSION_HISTORY_ENDPOINT = '/content-pipeline/web-submissions';
    const HISTORY_PAGE_SIZE = 20;
    const HISTORY_REFRESH_INTERVAL_MS = 10000;

    const state = {
        mode: 'folder',
        files: [],
        skipped: [],
        submitting: false,
        history: {
            items: [],
            limit: HISTORY_PAGE_SIZE,
            nextOffset: 0,
            hasMore: false,
            loading: false,
            error: '',
        },
    };

    const els = {};

    document.addEventListener('DOMContentLoaded', () => {
        bindElements();
        bindEvents();
        render();
        renderSubmissionHistory();
        loadSubmissionHistory();
        window.setInterval(() => {
            if (!document.hidden && hasActiveSubmission()) loadSubmissionHistory();
        }, HISTORY_REFRESH_INTERVAL_MS);
    });

    function bindElements() {
        [
            'notice', 'folder-mode', 'upload-mode', 'folder-mode-card', 'upload-mode-card',
            'folder-picker-panel', 'file-picker-panel', 'folder-input', 'file-input',
            'include-subdirectories', 'selection-badge', 'preview-summary', 'file-preview',
            'clear-selection-btn', 'submit-btn', 'submit-help', 'result-panel',
            'submission-history', 'history-summary', 'history-refresh-btn',
            'history-load-more-btn',
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
        els.historyRefreshBtn?.addEventListener('click', () => loadSubmissionHistory());
        els.historyLoadMoreBtn?.addEventListener('click', () => loadSubmissionHistory({ append: true }));
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
            loadSubmissionHistory();
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

    async function loadSubmissionHistory({ append = false } = {}) {
        if (state.history.loading) return;
        const hadHistoryItems = state.history.items.length > 0;
        const previousHasMore = state.history.hasMore;
        state.history.loading = true;
        state.history.error = '';
        renderSubmissionHistory();
        try {
            const offset = append ? state.history.nextOffset : 0;
            const response = await fetch(`${apiBaseUrl()}${WEB_SUBMISSION_HISTORY_ENDPOINT}?limit=${state.history.limit}&offset=${offset}`, {
                credentials: 'include',
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.success === false) {
                throw new Error(payload.message || `HTTP ${response.status}`);
            }
            const fetchedItems = Array.isArray(payload.data?.items) ? payload.data.items : [];
            const combinedItems = append
                ? [...state.history.items, ...fetchedItems]
                : [...fetchedItems, ...state.history.items];
            state.history.items = combinedItems.filter((item, index, items) => (
                items.findIndex((candidate) => candidate?.submission_id === item?.submission_id) === index
            ));
            state.history.hasMore = append || !hadHistoryItems
                ? Boolean(payload.data?.has_more)
                : previousHasMore && Boolean(payload.data?.has_more);
            const responseNextOffset = Number(payload.data?.next_offset || 0);
            state.history.nextOffset = append
                ? responseNextOffset
                : Math.max(state.history.items.length, responseNextOffset);
        } catch (error) {
            state.history.error = error.message || String(error);
        } finally {
            state.history.loading = false;
            renderSubmissionHistory();
        }
    }

    function renderSubmissionHistory() {
        if (!els.submissionHistory) return;
        const history = state.history;
        const activeCount = history.items.filter((item) => ['pending', 'processing'].includes(item?.status)).length;
        els.historyRefreshBtn.disabled = history.loading;
        els.historyLoadMoreBtn.disabled = history.loading;
        els.historyLoadMoreBtn.classList.toggle('hidden', !history.hasMore);
        els.historySummary.textContent = activeCount
            ? `${activeCount} 条处理中，将自动刷新`
            : `仅显示通过本页面提交的记录${history.items.length ? ` · 最近 ${history.items.length} 条` : ''}`;

        if (history.loading && !history.items.length) {
            els.submissionHistory.innerHTML = `<div class="animate-pulse space-y-3" aria-label="正在加载投递历史">
                <div class="h-28 rounded-xl bg-slate-100"></div>
                <div class="h-28 rounded-xl bg-slate-100"></div>
            </div>`;
            return;
        }
        if (history.error && !history.items.length) {
            els.submissionHistory.innerHTML = `<div class="rounded-xl border border-amber-200 bg-amber-50 p-4 text-center text-xs leading-5 text-amber-800">
                <i class="fas fa-triangle-exclamation mr-1.5"></i>暂时无法加载投递历史<br>
                <span class="text-amber-700">${escapeHtml(history.error)}</span>
            </div>`;
            return;
        }
        if (!history.items.length) {
            els.submissionHistory.innerHTML = `<div class="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-8 text-center">
                <i class="fas fa-clock-rotate-left text-xl text-slate-300"></i>
                <div class="mt-2 text-sm font-bold text-slate-600">暂无投递记录</div>
                <div class="mt-1 text-xs text-slate-400">从左侧提交 PDF 或 MP3 后会出现在这里。</div>
            </div>`;
            return;
        }

        const warning = history.error
            ? `<div class="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">刷新失败，当前显示上次结果。</div>`
            : '';
        els.submissionHistory.innerHTML = `${warning}${history.items.map(renderHistoryItem).join('')}`;
    }

    function renderHistoryItem(item) {
        const status = historyStatusMeta(item?.status);
        const inputMode = item?.input_mode === 'folder' ? '文件夹投递' : '文件上传';
        const inputIcon = item?.input_mode === 'folder' ? 'fa-folder-tree' : 'fa-cloud-arrow-up';
        const tasks = Array.isArray(item?.tasks) ? item.tasks : [];
        const progress = Math.max(0, Math.min(100, Number(item?.progress || 0)));
        const taskRows = tasks.map((task) => {
            const taskStatus = historyStatusMeta(task?.status);
            const kind = task?.kind === 'pdf' ? 'PDF' : 'MP3';
            const kindTone = task?.kind === 'pdf'
                ? 'border-red-100 bg-red-50 text-red-700'
                : 'border-indigo-100 bg-indigo-50 text-indigo-700';
            const filesCount = Math.max(0, Number(task?.files_count || 0));
            const successCount = Math.max(0, Number(task?.success_count || 0));
            const failedCount = Math.max(0, Number(task?.failed_count || 0));
            const countLabel = filesCount || failedCount
                ? `<div class="mt-0.5 text-[10px] font-medium text-slate-400">成功 ${successCount} · 失败 ${failedCount}</div>`
                : '';
            return `<div class="flex items-start gap-2 text-[11px]">
                <span class="inline-flex min-w-10 justify-center rounded-md border px-1.5 py-0.5 font-black ${kindTone}">${kind}</span>
                <span class="min-w-0 flex-1"><span class="block truncate font-bold ${taskStatus.textClass}">${taskStatus.label}</span>${countLabel}</span>
                <span class="font-bold tabular-nums text-slate-500">${Math.max(0, Math.min(100, Number(task?.progress || 0)))}%</span>
            </div>`;
        }).join('');
        const mediaLabel = (Array.isArray(item?.media_types) ? item.media_types : [])
            .map((kind) => kind === 'pdf' ? 'PDF' : 'MP3')
            .join(' + ') || '内容';

        return `<article class="rounded-xl border border-slate-200 bg-white p-3.5 transition hover:border-slate-300">
            <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                    <div class="flex items-center gap-2">
                        <span class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-xs text-slate-500"><i class="fas ${inputIcon}"></i></span>
                        <div class="min-w-0">
                            <div class="truncate text-sm font-black text-slate-800">${escapeHtml(mediaLabel)}</div>
                            <div class="mt-0.5 text-[11px] text-slate-400">${escapeHtml(inputMode)} · ${escapeHtml(formatHistoryTime(item?.submitted_at))}</div>
                        </div>
                    </div>
                </div>
                <span class="inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-black ${status.pillClass}">
                    <i class="fas ${status.icon}${item?.status === 'processing' ? ' fa-spin' : ''}"></i>${status.label}
                </span>
            </div>
            <div class="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100" aria-label="投递进度 ${progress}%">
                <div class="h-full rounded-full ${status.barClass}" style="width:${progress}%"></div>
            </div>
            <div class="mt-2.5 space-y-1.5">${taskRows}</div>
        </article>`;
    }

    function historyStatusMeta(value) {
        return {
            pending: {
                label: '排队中', icon: 'fa-clock',
                pillClass: 'bg-slate-100 text-slate-600', textClass: 'text-slate-600', barClass: 'bg-slate-400',
            },
            processing: {
                label: '处理中', icon: 'fa-spinner',
                pillClass: 'bg-blue-50 text-blue-700', textClass: 'text-blue-700', barClass: 'bg-blue-500',
            },
            completed: {
                label: '已完成', icon: 'fa-circle-check',
                pillClass: 'bg-emerald-50 text-emerald-700', textClass: 'text-emerald-700', barClass: 'bg-emerald-500',
            },
            partial_failed: {
                label: '部分失败', icon: 'fa-triangle-exclamation',
                pillClass: 'bg-amber-50 text-amber-700', textClass: 'text-amber-700', barClass: 'bg-amber-500',
            },
            failed: {
                label: '失败', icon: 'fa-circle-xmark',
                pillClass: 'bg-red-50 text-red-700', textClass: 'text-red-700', barClass: 'bg-red-500',
            },
        }[String(value || '')] || {
            label: '处理中', icon: 'fa-spinner',
            pillClass: 'bg-blue-50 text-blue-700', textClass: 'text-blue-700', barClass: 'bg-blue-500',
        };
    }

    function hasActiveSubmission() {
        return state.history.items.some((item) => ['pending', 'processing'].includes(item?.status));
    }

    function formatHistoryTime(value) {
        if (!value) return '时间未知';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return '时间未知';
        return new Intl.DateTimeFormat('zh-CN', {
            month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
        }).format(date);
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
