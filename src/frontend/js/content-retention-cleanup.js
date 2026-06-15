(function () {
    const STORAGE_KEY = 'content_retention_cleanup_policy_v1';

    const CATEGORY_META = {
        sync_raw_attachment: {
            label: '同步附件大文件',
            tone: 'blue',
            icon: 'fa-cloud-arrow-down',
        },
        content_pipeline_batch: {
            label: '内容流水线批次',
            tone: 'emerald',
            icon: 'fa-diagram-project',
        },
        manual_publish: {
            label: '手动发布临时目录',
            tone: 'amber',
            icon: 'fa-paper-plane',
        },
        pytest_batch: {
            label: 'pytest 清洗中转',
            tone: 'violet',
            icon: 'fa-flask',
        },
    };

    const TONE_CLASSES = {
        blue: 'bg-blue-50 text-blue-700 border-blue-100',
        emerald: 'bg-emerald-50 text-emerald-700 border-emerald-100',
        amber: 'bg-amber-50 text-amber-700 border-amber-100',
        violet: 'bg-violet-50 text-violet-700 border-violet-100',
        slate: 'bg-slate-50 text-slate-700 border-slate-100',
    };

    const state = {
        lastPreview: null,
        loading: false,
        schedulerLoading: false,
        schedulerStatus: null,
    };

    const els = {};

    document.addEventListener('DOMContentLoaded', async () => {
        bindElements();
        restorePolicy();
        bindEvents();
        renderEmptyBreakdown();
        await loadSchedulerStatus({ applyConfig: true });
        previewCleanup();
    });

    function bindElements() {
        [
            'notice',
            'refresh-preview-btn',
            'preview-btn',
            'cleanup-btn',
            'include-sync-raw',
            'sync-raw-days',
            'include-content-batches',
            'content-batch-days',
            'include-manual-publish',
            'manual-publish-days',
            'include-pytest-batches',
            'pytest-batch-days',
            'max-candidates',
            'metric-count',
            'metric-size',
            'metric-deleted',
            'metric-errors',
            'roots-list',
            'preview-subtitle',
            'preview-mode-badge',
            'category-breakdown',
            'candidate-list',
            'copy-all-btn',
            'error-panel',
            'error-list',
            'scheduler-enabled',
            'scheduler-interval-hours',
            'scheduler-dry-run',
            'scheduler-run-on-start',
            'save-scheduler-config-btn',
            'scheduler-run-once-btn',
            'scheduler-start-btn',
            'scheduler-stop-btn',
            'scheduler-status-badge',
            'scheduler-status-text',
            'scheduler-logs-btn',
            'scheduler-logs',
        ].forEach((id) => {
            els[toCamel(id)] = document.getElementById(id);
        });
    }

    function bindEvents() {
        els.refreshPreviewBtn?.addEventListener('click', previewCleanup);
        els.previewBtn?.addEventListener('click', previewCleanup);
        els.cleanupBtn?.addEventListener('click', executeCleanup);
        els.copyAllBtn?.addEventListener('click', copyAllPaths);
        els.saveSchedulerConfigBtn?.addEventListener('click', saveSchedulerConfig);
        els.schedulerRunOnceBtn?.addEventListener('click', runSchedulerOnce);
        els.schedulerStartBtn?.addEventListener('click', startScheduler);
        els.schedulerStopBtn?.addEventListener('click', stopScheduler);
        els.schedulerLogsBtn?.addEventListener('click', toggleSchedulerLogs);
        els.candidateList?.addEventListener('click', (event) => {
            const button = event.target.closest('[data-copy-path]');
            if (!button) return;
            copyText(button.dataset.copyPath || '');
        });
    }

    async function loadSchedulerStatus(options = {}) {
        try {
            const data = await apiGet('/content-pipeline/retention/scheduler/status');
            const scheduler = data?.scheduler || null;
            state.schedulerStatus = scheduler;
            if (options.applyConfig && scheduler?.config) {
                applySchedulerConfig(scheduler.config);
            }
            renderSchedulerStatus(scheduler);
            return scheduler;
        } catch (error) {
            renderSchedulerStatus(null, error);
            return null;
        }
    }

    async function saveSchedulerConfig() {
        const payload = readSchedulerPayload();
        setSchedulerBusy(true);
        try {
            const data = await apiPost('/content-pipeline/retention/scheduler/config', payload);
            const config = data?.config || null;
            if (config) {
                applySchedulerConfig(config);
            }
            await loadSchedulerStatus();
            showNotice('success', '计划已保存', '自动清理计划会使用当前页面的保留策略。');
        } catch (error) {
            showNotice('error', '保存计划失败', error.message || String(error));
        } finally {
            setSchedulerBusy(false);
        }
    }

    async function startScheduler() {
        setSchedulerBusy(true);
        try {
            await saveSchedulerConfig();
            const data = await apiPost('/content-pipeline/retention/scheduler/start', {});
            renderSchedulerStatus(data?.scheduler?.scheduler || data?.scheduler || null);
            showNotice('success', '计划任务已处理', data?.scheduler?.message || '启动请求已提交。');
            await loadSchedulerStatus();
        } catch (error) {
            showNotice('error', '启动计划失败', error.message || String(error));
        } finally {
            setSchedulerBusy(false);
        }
    }

    async function stopScheduler() {
        setSchedulerBusy(true);
        try {
            const data = await apiPost('/content-pipeline/retention/scheduler/stop', {});
            renderSchedulerStatus(data?.scheduler?.scheduler || data?.scheduler || null);
            showNotice('success', '计划任务已处理', data?.scheduler?.message || '停止请求已提交。');
            await loadSchedulerStatus();
        } catch (error) {
            showNotice('error', '停止计划失败', error.message || String(error));
        } finally {
            setSchedulerBusy(false);
        }
    }

    async function runSchedulerOnce() {
        const dryRun = Boolean(els.schedulerDryRun?.checked);
        const message = dryRun
            ? '立即执行一次 dry-run，只记录候选，不删除文件？'
            : '立即执行一次真实清理？这会删除符合当前策略的本地文件或目录。';
        if (!window.confirm(message)) return;

        setSchedulerBusy(true);
        try {
            await saveSchedulerConfig();
            const data = await apiPost('/content-pipeline/retention/scheduler/run-once', { dry_run: dryRun });
            const summary = data?.scheduler?.summary || {};
            showNotice(
                summary.error_count ? 'warning' : 'success',
                dryRun ? '试跑完成' : '清理完成',
                dryRun
                    ? `候选 ${summary.candidate_count || 0} 个，预计 ${summary.total_human || '0 B'}。`
                    : `删除 ${summary.deleted_count || 0} 个，释放 ${summary.deleted_human || '0 B'}。`
            );
            await loadSchedulerStatus();
            await loadSchedulerLogs();
            previewCleanup();
        } catch (error) {
            showNotice('error', '立即执行失败', error.message || String(error));
        } finally {
            setSchedulerBusy(false);
        }
    }

    async function toggleSchedulerLogs() {
        const hidden = els.schedulerLogs.classList.contains('hidden');
        els.schedulerLogs.classList.toggle('hidden', !hidden);
        if (hidden) {
            await loadSchedulerLogs();
        }
    }

    async function loadSchedulerLogs() {
        if (!els.schedulerLogs) return;
        try {
            const data = await apiGet('/content-pipeline/retention/scheduler/logs?lines=120');
            const lines = data?.lines || [];
            els.schedulerLogs.textContent = lines.length ? lines.join('\n') : '暂无日志。';
        } catch (error) {
            els.schedulerLogs.textContent = `读取日志失败：${error.message || error}`;
        }
    }

    async function previewCleanup() {
        const policy = readPolicy();
        persistPolicy(policy);
        setLoading(true, '正在扫描候选文件...');
        showNotice('info', '正在预览', '后端会根据保留天数和运行中任务保护规则计算候选，不会删除任何文件。');
        try {
            const query = new URLSearchParams();
            Object.entries(policy).forEach(([key, value]) => query.set(key, String(value)));
            const result = await apiGet(`/content-pipeline/retention/preview?${query.toString()}`);
            state.lastPreview = result;
            renderResult(result);
            showNotice('success', '预览完成', `发现 ${result.candidate_count || 0} 个候选，可释放 ${result.total_human || '0 B'}。`);
        } catch (error) {
            showNotice('error', '预览失败', error.message || String(error));
            renderFailure(error);
        } finally {
            setLoading(false);
        }
    }

    async function executeCleanup() {
        const policy = readPolicy();
        persistPolicy(policy);
        const preview = state.lastPreview;
        if (!preview || Number(preview.candidate_count || 0) === 0) {
            showNotice('warning', '没有可执行的候选', '请先预览，并确认当前策略下存在可清理对象。');
            return;
        }
        if (!preview.dry_run) {
            showNotice('warning', '需要重新预览', '上一次结果已经是执行结果。请重新预览后再执行下一次清理。');
            return;
        }
        if (!samePolicy(policy, preview.policy || {})) {
            showNotice('warning', '策略已变化', '当前页面策略和上一次预览不一致。请重新预览后再执行清理。');
            return;
        }

        const confirmed = window.confirm(
            `确认删除 ${preview.candidate_count} 个候选对象，预计释放 ${preview.total_human || '0 B'}？\n\n这个操作会删除本地文件或目录，数据库记录和 JSON 元数据不会删除。`
        );
        if (!confirmed) return;

        setLoading(true, '正在执行清理...');
        showNotice('info', '正在清理', '后端正在按当前策略删除候选文件。请不要重复点击。');
        try {
            const result = await apiPost('/content-pipeline/retention/cleanup', {
                dry_run: false,
                policy,
            });
            state.lastPreview = result;
            renderResult(result);
            if ((result.errors || []).length) {
                showNotice('warning', '清理完成但有错误', `已删除 ${result.deleted_count || 0} 个对象，错误 ${result.errors.length} 个。`);
            } else {
                showNotice('success', '清理完成', `已删除 ${result.deleted_count || 0} 个对象，释放 ${result.deleted_human || '0 B'}。`);
            }
        } catch (error) {
            showNotice('error', '清理失败', error.message || String(error));
            renderFailure(error);
        } finally {
            setLoading(false);
        }
    }

    function readPolicy() {
        return {
            include_sync_raw: Boolean(els.includeSyncRaw?.checked),
            sync_raw_days: readInt(els.syncRawDays, 3),
            include_content_pipeline_batches: Boolean(els.includeContentBatches?.checked),
            content_pipeline_batch_days: readInt(els.contentBatchDays, 3),
            include_manual_publish: Boolean(els.includeManualPublish?.checked),
            manual_publish_days: readInt(els.manualPublishDays, 3),
            include_pytest_batches: Boolean(els.includePytestBatches?.checked),
            pytest_batch_days: readInt(els.pytestBatchDays, 3),
            max_candidates: readInt(els.maxCandidates, 2000),
        };
    }

    function readSchedulerPayload() {
        return {
            enabled: Boolean(els.schedulerEnabled?.checked),
            interval_hours: readInt(els.schedulerIntervalHours, 24),
            dry_run: Boolean(els.schedulerDryRun?.checked),
            run_on_start: Boolean(els.schedulerRunOnStart?.checked),
            policy: readPolicy(),
        };
    }

    function applySchedulerConfig(config) {
        if (!config) return;
        setChecked(els.schedulerEnabled, config.enabled);
        setValue(els.schedulerIntervalHours, config.interval_hours);
        setChecked(els.schedulerDryRun, config.dry_run);
        setChecked(els.schedulerRunOnStart, config.run_on_start);
        if (config.policy) {
            applyPolicy(config.policy);
            persistPolicy(readPolicy());
        }
    }

    function applyPolicy(policy) {
        setChecked(els.includeSyncRaw, policy.include_sync_raw);
        setValue(els.syncRawDays, policy.sync_raw_days);
        setChecked(els.includeContentBatches, policy.include_content_pipeline_batches);
        setValue(els.contentBatchDays, policy.content_pipeline_batch_days);
        setChecked(els.includeManualPublish, policy.include_manual_publish);
        setValue(els.manualPublishDays, policy.manual_publish_days);
        setChecked(els.includePytestBatches, policy.include_pytest_batches);
        setValue(els.pytestBatchDays, policy.pytest_batch_days);
        setValue(els.maxCandidates, policy.max_candidates);
    }

    function renderSchedulerStatus(scheduler, error = null) {
        if (!els.schedulerStatusText) return;
        if (error) {
            els.schedulerStatusBadge.textContent = '不可用';
            els.schedulerStatusBadge.className = 'rounded-full bg-red-50 px-2 py-1 text-[10px] font-black text-red-700';
            els.schedulerStatusText.innerHTML = `<span class="text-red-700">读取计划状态失败：${escapeHtml(error.message || String(error))}</span>`;
            return;
        }
        const running = Boolean(scheduler?.running);
        const config = scheduler?.config || {};
        const last = scheduler?.last_result || config.last_result || {};
        els.schedulerStatusBadge.textContent = running ? '运行中' : '未运行';
        els.schedulerStatusBadge.className = running
            ? 'rounded-full bg-emerald-100 px-2 py-1 text-[10px] font-black text-emerald-700'
            : 'rounded-full bg-slate-100 px-2 py-1 text-[10px] font-black text-slate-500';
        els.schedulerStatusText.innerHTML = `
            <div>进程：${escapeHtml(running ? `运行中 · PID ${scheduler.pid || '-'}` : '未运行')}</div>
            <div>计划：${escapeHtml(config.enabled === false ? '已禁用' : '已启用')} · 每 ${escapeHtml(String(config.interval_hours || scheduler?.interval_hours || '-'))} 小时 · ${config.dry_run ? 'dry-run' : '真实清理'}</div>
            <div>下次执行：${escapeHtml(formatDateTime(scheduler?.next_run_at || config.next_run_at))}</div>
            <div>上次执行：${escapeHtml(formatDateTime(scheduler?.last_run_at || config.last_run_at))}</div>
            <div>上次结果：${escapeHtml(formatSchedulerResult(last))}</div>
            <div>日志：<span class="font-mono break-all">${escapeHtml(scheduler?.log_path || config.log_path || '-')}</span></div>
        `;
    }

    function restorePolicy() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) return;
            const policy = JSON.parse(raw);
            setChecked(els.includeSyncRaw, policy.include_sync_raw);
            setValue(els.syncRawDays, policy.sync_raw_days);
            setChecked(els.includeContentBatches, policy.include_content_pipeline_batches);
            setValue(els.contentBatchDays, policy.content_pipeline_batch_days);
            setChecked(els.includeManualPublish, policy.include_manual_publish);
            setValue(els.manualPublishDays, policy.manual_publish_days);
            setChecked(els.includePytestBatches, policy.include_pytest_batches);
            setValue(els.pytestBatchDays, policy.pytest_batch_days);
            setValue(els.maxCandidates, policy.max_candidates);
        } catch (error) {
            console.warn('[RETENTION] restore policy failed:', error);
        }
    }

    function persistPolicy(policy) {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(policy));
        } catch (error) {
            console.warn('[RETENTION] persist policy failed:', error);
        }
    }

    function samePolicy(left, right) {
        const keys = [
            'include_sync_raw',
            'sync_raw_days',
            'include_content_pipeline_batches',
            'content_pipeline_batch_days',
            'include_manual_publish',
            'manual_publish_days',
            'include_pytest_batches',
            'pytest_batch_days',
            'max_candidates',
        ];
        return keys.every((key) => normalizePolicyValue(left[key]) === normalizePolicyValue(right[key]));
    }

    function normalizePolicyValue(value) {
        if (typeof value === 'boolean') return value ? '1' : '0';
        if (value === undefined || value === null) return '';
        return String(value);
    }

    function renderResult(result) {
        const candidates = Array.isArray(result.candidates) ? result.candidates : [];
        els.metricCount.textContent = String(result.candidate_count || 0);
        els.metricSize.textContent = result.total_human || '0 B';
        els.metricDeleted.textContent = result.dry_run ? '-' : `${result.deleted_count || 0} / ${result.deleted_human || '0 B'}`;
        els.metricErrors.textContent = String((result.errors || []).length || 0);
        els.previewSubtitle.textContent = result.dry_run
            ? `预览时间：${formatNow()}，当前策略不会删除任何文件。`
            : `执行时间：${formatNow()}，候选列表为本次执行时的后端判定结果。`;
        els.previewModeBadge.textContent = result.dry_run ? '预览模式' : '已执行';
        els.previewModeBadge.className = result.dry_run
            ? 'w-fit rounded-full bg-blue-50 px-3 py-1 text-xs font-bold text-blue-700'
            : 'w-fit rounded-full bg-emerald-50 px-3 py-1 text-xs font-bold text-emerald-700';

        renderRoots(result.roots || {});
        renderBreakdown(candidates);
        renderCandidates(candidates);
        renderErrors(result.errors || []);
    }

    function renderRoots(roots) {
        const entries = Object.entries(roots).filter(([, value]) => value !== undefined && value !== null && String(value).trim());
        if (!entries.length) {
            els.rootsList.innerHTML = '<div class="rounded-lg bg-slate-50 px-3 py-2">后端未返回扫描根目录。</div>';
            return;
        }

        els.rootsList.innerHTML = entries.map(([key, value]) => `
            <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                <div class="font-bold text-slate-700">${escapeHtml(rootLabel(key))}</div>
                <div class="mt-1 break-all font-mono text-[11px] leading-5 text-slate-500">${escapeHtml(String(value))}</div>
            </div>
        `).join('');
    }

    function renderBreakdown(candidates) {
        if (!candidates.length) {
            renderEmptyBreakdown();
            return;
        }

        const grouped = candidates.reduce((acc, item) => {
            const category = item.category || 'unknown';
            if (!acc[category]) {
                acc[category] = { count: 0, bytes: 0 };
            }
            acc[category].count += 1;
            acc[category].bytes += Number(item.bytes || 0);
            return acc;
        }, {});

        els.categoryBreakdown.innerHTML = Object.entries(grouped).map(([category, data]) => {
            const meta = CATEGORY_META[category] || { label: category, tone: 'slate', icon: 'fa-box-archive' };
            return `
                <article class="rounded-lg border ${TONE_CLASSES[meta.tone] || TONE_CLASSES.slate} p-4">
                    <div class="flex items-center justify-between gap-3">
                        <div class="min-w-0">
                            <div class="truncate text-sm font-black">${escapeHtml(meta.label)}</div>
                            <div class="mt-1 text-xs opacity-75">${formatBytes(data.bytes)}</div>
                        </div>
                        <i class="fas ${meta.icon} text-lg opacity-80"></i>
                    </div>
                    <div class="mt-3 text-2xl font-black">${data.count}</div>
                </article>
            `;
        }).join('');
    }

    function renderEmptyBreakdown() {
        els.categoryBreakdown.innerHTML = `
            <div class="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-500 md:col-span-2 xl:col-span-4">
                当前没有分类统计。预览后会按清理类型展示数量和空间。
            </div>
        `;
    }

    function renderCandidates(candidates) {
        if (!candidates.length) {
            els.copyAllBtn.classList.add('hidden');
            els.candidateList.innerHTML = `
                <div class="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-8 text-center text-sm text-slate-500">
                    当前策略下没有候选文件。可以调小保留天数，或稍后再检查。
                </div>
            `;
            return;
        }

        els.copyAllBtn.classList.remove('hidden');
        els.candidateList.innerHTML = `
            <div class="overflow-hidden rounded-lg border border-slate-200">
                <div class="grid grid-cols-[130px_90px_90px_minmax(0,1fr)_44px] gap-0 bg-slate-50 px-3 py-2 text-xs font-black uppercase tracking-wide text-slate-500">
                    <div>类型</div>
                    <div>大小</div>
                    <div>天数</div>
                    <div>路径 / 原因</div>
                    <div></div>
                </div>
                <div class="divide-y divide-slate-200">
                    ${candidates.map(renderCandidateRow).join('')}
                </div>
            </div>
        `;
    }

    function renderCandidateRow(item) {
        const meta = CATEGORY_META[item.category] || { label: item.category || 'unknown', tone: 'slate', icon: 'fa-box-archive' };
        return `
            <div class="grid grid-cols-[130px_90px_90px_minmax(0,1fr)_44px] gap-0 px-3 py-3 text-sm">
                <div class="min-w-0">
                    <span class="inline-flex max-w-full items-center rounded-full border px-2 py-1 text-xs font-bold ${TONE_CLASSES[meta.tone] || TONE_CLASSES.slate}">
                        <i class="fas ${meta.icon} mr-1.5"></i>
                        <span class="truncate">${escapeHtml(meta.label)}</span>
                    </span>
                    <div class="mt-2 text-xs text-slate-400">${escapeHtml(item.kind || '-')}</div>
                </div>
                <div class="font-bold text-slate-700">${escapeHtml(item.human_size || formatBytes(item.bytes || 0))}</div>
                <div class="font-bold text-slate-700">${escapeHtml(String(item.age_days ?? '-'))}</div>
                <div class="min-w-0">
                    <div class="break-all font-mono text-xs leading-5 text-slate-700">${escapeHtml(item.path || '')}</div>
                    <div class="mt-1 text-xs leading-5 text-slate-500">${escapeHtml(item.reason || '')}</div>
                </div>
                <div class="text-right">
                    <button data-copy-path="${escapeAttr(item.path || '')}" class="rounded-md border border-slate-300 px-2 py-1 text-xs font-bold text-slate-600 hover:bg-slate-50" title="复制路径">
                        <i class="fas fa-copy"></i>
                    </button>
                </div>
            </div>
        `;
    }

    function renderErrors(errors) {
        if (!errors.length) {
            els.errorPanel.classList.add('hidden');
            els.errorList.innerHTML = '';
            return;
        }
        els.errorPanel.classList.remove('hidden');
        els.errorList.innerHTML = errors.map((item) => `
            <div class="rounded-lg border border-red-200 bg-white px-3 py-2 text-sm">
                <div class="font-bold text-red-700">${escapeHtml(item.error || '未知错误')}</div>
                <div class="mt-1 break-all font-mono text-xs text-red-500">${escapeHtml(item.path || '')}</div>
            </div>
        `).join('');
    }

    function renderFailure(error) {
        els.metricCount.textContent = '-';
        els.metricSize.textContent = '-';
        els.metricDeleted.textContent = '-';
        els.metricErrors.textContent = '1';
        els.previewModeBadge.textContent = '失败';
        els.previewModeBadge.className = 'w-fit rounded-full bg-red-50 px-3 py-1 text-xs font-bold text-red-700';
        els.previewSubtitle.textContent = error.message || String(error);
        els.candidateList.innerHTML = `
            <div class="rounded-lg border border-red-200 bg-red-50 p-5 text-sm text-red-700">
                ${escapeHtml(error.message || String(error))}
            </div>
        `;
    }

    async function apiGet(path) {
        const response = await fetch(`${apiBaseUrl()}${path}`, {
            method: 'GET',
            credentials: 'include',
        });
        return parseApiResponse(response);
    }

    async function apiPost(path, payload) {
        const response = await fetch(`${apiBaseUrl()}${path}`, {
            method: 'POST',
            credentials: 'include',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {}),
        });
        return parseApiResponse(response);
    }

    async function parseApiResponse(response) {
        let data = null;
        try {
            data = await response.json();
        } catch (error) {
            throw new Error(`HTTP ${response.status}: 响应不是 JSON`);
        }

        if (!response.ok || data.success === false) {
            throw new Error(data.message || `HTTP ${response.status}`);
        }
        return data.data || data;
    }

    function apiBaseUrl() {
        return window.API_BASE_URL || window.CONFIG?.API_BASE_URL || 'http://localhost:5001/api/v1';
    }

    function setLoading(loading, label) {
        state.loading = loading;
        [els.previewBtn, els.refreshPreviewBtn, els.cleanupBtn].forEach((button) => {
            if (!button) return;
            button.disabled = loading;
            button.classList.toggle('opacity-60', loading);
            button.classList.toggle('cursor-not-allowed', loading);
        });
        if (label) {
            els.previewSubtitle.textContent = label;
            els.previewModeBadge.textContent = '处理中';
            els.previewModeBadge.className = 'w-fit rounded-full bg-amber-50 px-3 py-1 text-xs font-bold text-amber-700';
        }
    }

    function setSchedulerBusy(loading) {
        state.schedulerLoading = loading;
        [
            els.saveSchedulerConfigBtn,
            els.schedulerRunOnceBtn,
            els.schedulerStartBtn,
            els.schedulerStopBtn,
            els.schedulerLogsBtn,
        ].forEach((button) => {
            if (!button) return;
            button.disabled = loading;
            button.classList.toggle('opacity-60', loading);
            button.classList.toggle('cursor-not-allowed', loading);
        });
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

    function copyAllPaths() {
        const paths = (state.lastPreview?.candidates || []).map((item) => item.path).filter(Boolean);
        if (!paths.length) return;
        copyText(paths.join('\n'));
    }

    async function copyText(text) {
        try {
            await navigator.clipboard.writeText(text);
            showNotice('success', '已复制', '路径已复制到剪贴板。');
        } catch (error) {
            showNotice('warning', '复制失败', '当前浏览器不允许直接写入剪贴板。');
        }
    }

    function readInt(input, fallback) {
        const value = Number.parseInt(input?.value, 10);
        if (!Number.isFinite(value)) return fallback;
        return Math.max(0, value);
    }

    function setChecked(input, value) {
        if (!input || value === undefined) return;
        input.checked = Boolean(value);
    }

    function setValue(input, value) {
        if (!input || value === undefined || value === null) return;
        input.value = String(value);
    }

    function rootLabel(key) {
        return {
            content_pipeline_root: '内容流水线根目录',
            zsxq_data_dir: '知识星球数据目录',
            pytest_project_dir: 'pytest 项目目录',
            pytest_batch_root: 'pytest 批次相对目录',
        }[key] || key;
    }

    function toCamel(value) {
        return value.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    }

    function formatNow() {
        return new Date().toLocaleString('zh-CN', { hour12: false });
    }

    function formatDateTime(value) {
        if (!value) return '-';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return date.toLocaleString('zh-CN', { hour12: false });
    }

    function formatSchedulerResult(result) {
        if (!result || !Object.keys(result).length) return '-';
        if (result.error) return `失败：${result.error}`;
        const mode = result.dry_run ? '预览' : '清理';
        return `${mode} · 候选 ${result.candidate_count || 0} · 删除 ${result.deleted_count || 0} · 空间 ${result.deleted_human || result.total_human || '0 B'} · 错误 ${result.error_count || 0}`;
    }

    function formatBytes(value) {
        let size = Number(value || 0);
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        for (const unit of units) {
            if (size < 1024 || unit === 'TB') {
                return unit === 'B' ? `${Math.round(size)} ${unit}` : `${size.toFixed(1)} ${unit}`;
            }
            size /= 1024;
        }
        return `${size.toFixed(1)} TB`;
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function escapeAttr(value) {
        return escapeHtml(value).replace(/`/g, '&#096;');
    }
})();
