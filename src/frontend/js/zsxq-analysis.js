(() => {
    const state = {
        kind: 'pdf',
        selectedFile: null,
        historyPage: 1,
        historyPerPage: 20,
        historyPagination: null,
        historyByKind: new Map(),
        currentRunByKind: new Map(),
        charts: new Map(),
        requestSerial: 0,
        detailRequestSerial: 0,
    };

    const elements = {};

    document.addEventListener('DOMContentLoaded', () => {
        cacheElements();
        bindEvents();
        switchKind('pdf');
    });

    function cacheElements() {
        [
            'active-kind-label', 'upload-title', 'upload-hint', 'analysis-upload-form',
            'analysis-file-input', 'file-selection-title', 'file-selection-meta',
            'analysis-submit-button', 'analysis-status', 'analysis-current-section',
            'current-analysis-title', 'current-analysis-meta', 'download-source-button',
            'analysis-metric-grid', 'analysis-keyword-cloud', 'analysis-keyword-list',
            'analysis-trend-chart', 'trend-title', 'trend-unit-note', 'analysis-topic-table',
            'analysis-top-item-table', 'analysis-run-id', 'analysis-run-hash',
            'analysis-run-version', 'history-title', 'history-count', 'analysis-history-list',
            'history-prev-button', 'history-next-button', 'history-pagination-label',
            'load-items-button',
        ].forEach((id) => { elements[id] = document.getElementById(id); });
    }

    function bindEvents() {
        document.querySelectorAll('[data-analysis-kind]').forEach((button) => {
            button.addEventListener('click', () => switchKind(button.dataset.analysisKind));
        });
        elements['analysis-file-input'].addEventListener('change', (event) => {
            setSelectedFile(event.target.files?.[0] || null);
        });
        const dropzone = document.querySelector('.file-dropzone');
        ['dragenter', 'dragover'].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
            event.preventDefault();
            dropzone.classList.add('is-dragging');
        }));
        ['dragleave', 'drop'].forEach((eventName) => dropzone.addEventListener(eventName, (event) => {
            event.preventDefault();
            dropzone.classList.remove('is-dragging');
        }));
        dropzone.addEventListener('drop', (event) => {
            const file = event.dataTransfer?.files?.[0];
            if (!file) return;
            setSelectedFile(file);
        });
        elements['analysis-upload-form'].addEventListener('submit', submitAnalysis);
        elements['analysis-history-list'].addEventListener('click', (event) => {
            const row = event.target.closest('[data-run-id]');
            if (row) loadRunDetail(row.dataset.runId);
        });
        elements['history-prev-button'].addEventListener('click', () => changeHistoryPage(-1));
        elements['history-next-button'].addEventListener('click', () => changeHistoryPage(1));
        elements['download-source-button'].addEventListener('click', downloadCurrentSource);
        elements['load-items-button'].addEventListener('click', loadCurrentItems);
    }

    function switchKind(kind) {
        if (!['pdf', 'audio'].includes(kind)) return;
        state.requestSerial += 1;
        state.detailRequestSerial += 1;
        state.kind = kind;
        state.historyPage = 1;
        state.selectedFile = null;
        elements['analysis-file-input'].value = '';
        setSelectedFile(null);
        setBusy(false);
        document.querySelectorAll('[data-analysis-kind]').forEach((button) => {
            const active = button.dataset.analysisKind === kind;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-selected', String(active));
        });
        const isAudio = kind === 'audio';
        elements['active-kind-label'].textContent = isAudio ? '音频资料' : 'PDF 资料';
        elements['upload-title'].textContent = `上传${isAudio ? '音频' : 'PDF'} Tab 的 JSON`;
        elements['upload-hint'].textContent = isAudio
            ? '支持 zsxq-list-export/v1。当前只分析音频清单元数据，不读取音频转写。'
            : '支持 zsxq-list-export/v1。当前只分析 PDF 清单元数据，不读取 PDF 正文。';
        elements['history-title'].textContent = `当前 Tab 的${isAudio ? '音频' : 'PDF'}分析批次`;
        renderCurrent(state.currentRunByKind.get(kind) || null);
        loadHistory();
    }

    function setSelectedFile(file) {
        state.selectedFile = file;
        if (!file) {
            elements['file-selection-title'].textContent = '选择 JSON 文件';
            elements['file-selection-meta'].textContent = '或将文件拖到这里；文件类型会再次由服务端校验';
            return;
        }
        elements['file-selection-title'].textContent = file.name || '已选择 JSON 文件';
        elements['file-selection-meta'].textContent = `${formatBytes(file.size)} · 等待分析`;
    }

    async function submitAnalysis(event) {
        event.preventDefault();
        if (!state.selectedFile) {
            setStatus('请先选择 JSON 文件。', 'error');
            return;
        }
        const kindAtRequest = state.kind;
        const fileAtRequest = state.selectedFile;
        const serial = ++state.requestSerial;
        setBusy(true);
        setStatus('正在校验、分析并写入结构化历史…');
        const formData = new FormData();
        formData.append('kind', kindAtRequest);
        formData.append('file', fileAtRequest);
        try {
            const response = await fetch(`${apiBaseUrl()}/zsxq-analysis/runs`, {
                method: 'POST',
                credentials: 'include',
                headers: authHeaders(),
                body: formData,
            });
            const payload = await readJson(response);
            if (!response.ok || !payload.success) throw new Error(payload?.error?.message || '分析失败');
            if (serial !== state.requestSerial || kindAtRequest !== state.kind) return;
            const detail = payload.data;
            state.currentRunByKind.set(kindAtRequest, detail);
            renderCurrent(detail);
            setStatus('分析完成，已生成新的独立批次。', 'success');
            await loadHistory();
        } catch (error) {
            if (serial !== state.requestSerial || kindAtRequest !== state.kind) return;
            setStatus(error.message || '分析失败，请稍后重试。', 'error');
        } finally {
            if (serial === state.requestSerial && kindAtRequest === state.kind) setBusy(false);
        }
    }

    async function loadHistory() {
        const kindAtRequest = state.kind;
        const params = new URLSearchParams({
            kind: kindAtRequest,
            page: String(state.historyPage),
            per_page: String(state.historyPerPage),
        });
        renderHistoryLoading();
        try {
            const response = await fetch(`${apiBaseUrl()}/zsxq-analysis/runs?${params}`, {
                credentials: 'include',
                headers: authHeaders(),
            });
            const payload = await readJson(response);
            if (!response.ok || !payload.success) throw new Error(payload?.error?.message || '历史读取失败');
            if (kindAtRequest !== state.kind) return;
            const data = payload.data || {};
            state.historyByKind.set(kindAtRequest, data.items || []);
            state.historyPagination = data.pagination || null;
            renderHistory(data.items || [], data.pagination || null);
        } catch (error) {
            if (kindAtRequest !== state.kind) return;
            elements['analysis-history-list'].innerHTML = '';
            const empty = document.createElement('div');
            empty.className = 'history-empty';
            empty.textContent = error.message || '历史读取失败，请稍后重试。';
            elements['analysis-history-list'].appendChild(empty);
        }
    }

    async function loadRunDetail(runId) {
        const kindAtRequest = state.kind;
        const serial = ++state.detailRequestSerial;
        setStatus('正在打开历史批次…');
        try {
            const response = await fetch(`${apiBaseUrl()}/zsxq-analysis/runs/${encodeURIComponent(runId)}`, {
                credentials: 'include',
                headers: authHeaders(),
            });
            const payload = await readJson(response);
            if (serial !== state.detailRequestSerial || kindAtRequest !== state.kind) return;
            if (!response.ok || !payload.success) throw new Error(payload?.error?.message || '批次读取失败');
            if (payload.data?.run?.kind !== kindAtRequest) throw new Error('该批次不属于当前 Tab');
            state.currentRunByKind.set(kindAtRequest, payload.data);
            renderCurrent(payload.data);
            setStatus('已打开历史批次。', 'success');
        } catch (error) {
            if (serial !== state.detailRequestSerial || kindAtRequest !== state.kind) return;
            setStatus(error.message || '批次读取失败，请稍后重试。', 'error');
        }
    }

    async function downloadCurrentSource() {
        const detail = state.currentRunByKind.get(state.kind);
        const runId = detail?.run?.id;
        if (!runId) return;
        const button = elements['download-source-button'];
        button.disabled = true;
        try {
            const response = await fetch(`${apiBaseUrl()}/zsxq-analysis/runs/${encodeURIComponent(runId)}/source`, {
                credentials: 'include',
                headers: authHeaders(),
            });
            if (!response.ok) {
                const payload = await readJson(response);
                throw new Error(payload?.error?.message || '原始文件读取失败');
            }
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = detail.run.source_filename || `${state.kind}-analysis.json`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
        } catch (error) {
            setStatus(error.message || '原始文件读取失败。', 'error');
        } finally {
            button.disabled = false;
        }
    }

    async function loadCurrentItems() {
        const detail = state.currentRunByKind.get(state.kind);
        const runId = detail?.run?.id;
        if (!runId) return;
        const kindAtRequest = state.kind;
        const button = elements['load-items-button'];
        button.disabled = true;
        button.textContent = '正在读取…';
        try {
            const params = new URLSearchParams({ page: '1', per_page: '50', sort: 'downloads' });
            const response = await fetch(`${apiBaseUrl()}/zsxq-analysis/runs/${encodeURIComponent(runId)}/items?${params}`, {
                credentials: 'include',
                headers: authHeaders(),
            });
            const payload = await readJson(response);
            if (!response.ok || !payload.success) throw new Error(payload?.error?.message || '文件列表读取失败');
            if (kindAtRequest !== state.kind || state.currentRunByKind.get(kindAtRequest)?.run?.id !== runId) {
                button.textContent = '查看前 50 条文件';
                return;
            }
            renderTopItems(payload.data?.items || []);
            button.textContent = `已加载 ${payload.data?.items?.length || 0} 条文件`;
        } catch (error) {
            button.textContent = '文件列表读取失败';
            setStatus(error.message || '文件列表读取失败。', 'error');
        } finally {
            button.disabled = false;
        }
    }

    function renderCurrent(detail) {
        const run = detail?.run;
        if (!run || run.kind !== state.kind) {
            elements['current-analysis-title'].textContent = '还没有打开分析结果';
            elements['current-analysis-meta'].textContent = '上传文件或从历史中选择一个批次。';
            elements['analysis-metric-grid'].innerHTML = '';
            elements['analysis-keyword-cloud'].innerHTML = '<div class="keyword-empty">等待当前 Tab 的分析结果</div>';
            elements['analysis-keyword-list'].innerHTML = '';
            elements['analysis-topic-table'].innerHTML = '<div class="table-empty">暂无主题聚合</div>';
            elements['analysis-top-item-table'].innerHTML = '<div class="table-empty">暂无热度排行</div>';
            elements['analysis-run-id'].textContent = '-';
            elements['analysis-run-hash'].textContent = '-';
            elements['analysis-run-version'].textContent = '-';
            elements['download-source-button'].disabled = true;
            elements['load-items-button'].disabled = false;
            elements['load-items-button'].textContent = '查看前 50 条文件';
            clearChart('trend');
            return;
        }
        const isAudio = state.kind === 'audio';
        elements['current-analysis-title'].textContent = run.source_filename || '未命名分析批次';
        elements['current-analysis-meta'].textContent = `${formatDateTime(run.created_at)} · ${isAudio ? '音频' : 'PDF'} · ${run.status === 'completed' ? '分析完成' : run.status}`;
        elements['download-source-button'].disabled = !run.source_available;
        elements['load-items-button'].disabled = false;
        elements['load-items-button'].textContent = '查看前 50 条文件';
        elements['analysis-run-id'].textContent = run.id || '-';
        elements['analysis-run-hash'].textContent = run.source_sha256_prefix || '-';
        elements['analysis-run-version'].textContent = run.analysis_version || '-';
        renderMetrics(run);
        renderKeywords(detail.keywords || []);
        renderTopics(detail.topics || []);
        renderTopItems(detail.top_items || []);
        renderTrend(detail.timeline || [], isAudio);
        document.querySelectorAll('.history-row').forEach((row) => row.classList.toggle('is-current', row.dataset.runId === run.id));
    }

    function renderMetrics(run) {
        const isAudio = state.kind === 'audio';
        const durationBuckets = Object.fromEntries(
            (run.duration_distribution || []).map((bucket) => [bucket.key, Number(bucket.count) || 0]),
        );
        const metrics = isAudio
            ? [
                ['音频数量', formatNumber(run.item_count), `${formatNumber(run.done_count)} 已下载`],
                ['主题数', formatNumber(run.unique_topic_count), '按 topicId 聚合'],
                ['总时长', formatDuration(run.total_duration_seconds), `${formatNumber(run.duration_nonzero_count)} 条有时长`],
                ['平均时长', formatDuration(run.duration_avg_seconds), `中位 ${formatDuration(run.duration_median_seconds)}`],
                ['时长分布', `${formatNumber(durationBuckets.short)} / ${formatNumber(durationBuckets.medium)} / ${formatNumber(durationBuckets.long)}`, `短 / 中 / 长 · ${formatNumber(durationBuckets.unknown)} 条未知`],
                ['下载总数', formatNumber(run.total_download_count), `最高 ${formatNumber(run.download_count_max)}`],
                ['数据日期', formatPeriod(run.period_start, run.period_end), '来源 uploadTime'],
            ]
            : [
                ['PDF 数量', formatNumber(run.item_count), `${formatNumber(run.done_count)} 已下载`],
                ['主题数', formatNumber(run.unique_topic_count), '按 topicId 聚合'],
                ['下载总数', formatNumber(run.total_download_count), `最高 ${formatNumber(run.download_count_max)}`],
                ['平均热度', formatDecimal(run.download_count_avg), `中位 ${formatDecimal(run.download_count_median)}`],
                ['总文件大小', formatBytes(run.total_file_size_bytes), `${formatNumber(run.pending_count)} 待下载`],
                ['数据日期', formatPeriod(run.period_start, run.period_end), '来源 uploadTime'],
            ];
        elements['analysis-metric-grid'].innerHTML = '';
        metrics.forEach(([label, value, note]) => {
            const card = document.createElement('div');
            card.className = 'metric-card';
            card.innerHTML = `<span></span><strong></strong><small></small>`;
            card.querySelector('span').textContent = label;
            card.querySelector('strong').textContent = value;
            card.querySelector('small').textContent = note;
            elements['analysis-metric-grid'].appendChild(card);
        });
    }

    function renderKeywords(keywords) {
        const cloud = elements['analysis-keyword-cloud'];
        const list = elements['analysis-keyword-list'];
        cloud.innerHTML = '';
        list.innerHTML = '';
        if (!keywords.length) {
            cloud.innerHTML = '<div class="keyword-empty">文件名中暂未提取到稳定关键词</div>';
            return;
        }
        const max = Math.max(...keywords.map((item) => Number(item.item_count) || 1));
        keywords.slice(0, 35).forEach((keyword) => {
            const node = document.createElement('span');
            const ratio = (Number(keyword.item_count) || 1) / max;
            node.style.fontSize = `${12 + Math.round(ratio * 17)}px`;
            node.textContent = keyword.keyword;
            node.title = `${keyword.item_count} 个文件 · ${formatNumber(keyword.total_download_count)} 次下载`;
            cloud.appendChild(node);
        });
        keywords.slice(0, 10).forEach((keyword) => {
            const row = document.createElement('div');
            row.className = 'keyword-list-row';
            row.innerHTML = '<strong></strong><span></span>';
            row.querySelector('strong').textContent = keyword.keyword;
            row.querySelector('span').textContent = `${keyword.item_count} 文件`;
            list.appendChild(row);
        });
    }

    function renderTopics(topics) {
        const root = elements['analysis-topic-table'];
        root.innerHTML = '';
        if (!topics.length) {
            root.innerHTML = '<div class="table-empty">暂无 topicId 聚合</div>';
            return;
        }
        const table = document.createElement('table');
        table.className = 'analysis-table';
        table.innerHTML = '<thead><tr><th>主题</th><th>文件数</th><th>下载总数</th><th>代表文件</th></tr></thead><tbody></tbody>';
        const body = table.querySelector('tbody');
        topics.slice(0, 12).forEach((topic) => {
            const row = document.createElement('tr');
            row.innerHTML = '<td></td><td></td><td></td><td class="name-cell"></td>';
            row.children[0].textContent = `#${topic.rank} ${topic.topic_id}`;
            row.children[1].textContent = formatNumber(topic.item_count);
            row.children[2].textContent = formatNumber(topic.total_download_count);
            row.children[3].textContent = topic.representative_name || '-';
            body.appendChild(row);
        });
        root.appendChild(table);
    }

    function renderTopItems(items) {
        const root = elements['analysis-top-item-table'];
        root.innerHTML = '';
        if (!items.length) {
            root.innerHTML = '<div class="table-empty">暂无代表文件</div>';
            return;
        }
        const table = document.createElement('table');
        table.className = 'analysis-table';
        table.innerHTML = `<thead><tr><th>文件</th><th>下载</th><th>状态</th><th>${state.kind === 'audio' ? '时长' : '大小'}</th></tr></thead><tbody></tbody>`;
        const body = table.querySelector('tbody');
        items.slice(0, 12).forEach((item) => {
            const row = document.createElement('tr');
            row.innerHTML = '<td class="name-cell"></td><td></td><td><span class="status-pill"></span></td><td></td>';
            row.children[0].textContent = item.name || '-';
            row.children[1].textContent = formatNumber(item.download_count);
            const status = row.querySelector('.status-pill');
            status.textContent = statusLabel(item.status);
            status.classList.add(item.status || '');
            row.children[3].textContent = state.kind === 'audio' ? formatDuration(item.duration_seconds) : formatBytes(item.file_size_bytes);
            body.appendChild(row);
        });
        root.appendChild(table);
    }

    function renderTrend(timeline, isAudio) {
        elements['trend-title'].textContent = isAudio ? '上传趋势与时长' : '上传趋势与下载热度';
        elements['trend-unit-note'].textContent = isAudio ? '按日 · 小时' : '按日 · 次数';
        clearChart('trend');
        if (!window.Chart || !timeline.length) return;
        const labels = timeline.map((item) => item.date);
        const datasets = [
            { label: '文件数量', data: timeline.map((item) => item.item_count), borderColor: '#2563eb', backgroundColor: 'rgba(37,99,235,.12)', tension: .35, fill: true, yAxisID: 'y' },
            {
                label: isAudio ? '总时长（小时）' : '下载次数',
                data: timeline.map((item) => isAudio ? Math.round((item.duration_seconds || 0) / 360) / 10 : item.download_count),
                borderColor: isAudio ? '#7c3aed' : '#059669',
                backgroundColor: 'transparent',
                tension: .35,
                fill: false,
                yAxisID: 'y1',
            },
        ];
        state.charts.set('trend', new Chart(elements['analysis-trend-chart'].getContext('2d'), {
            type: 'line',
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: { legend: { labels: { color: '#475569', boxWidth: 12, font: { size: 10 } } } },
                scales: {
                    x: { grid: { display: false }, ticks: { color: '#94a3b8', font: { size: 10 }, maxTicksLimit: 8 } },
                    y: { beginAtZero: true, grid: { color: '#eef2f7' }, ticks: { color: '#94a3b8', font: { size: 10 } } },
                    y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, ticks: { color: '#94a3b8', font: { size: 10 } } },
                },
            },
        }));
    }

    function renderHistory(items, pagination) {
        elements['analysis-history-list'].innerHTML = '';
        elements['history-count'].textContent = `${formatNumber(pagination?.total || 0)} 个批次`;
        if (!items.length) {
            elements['analysis-history-list'].innerHTML = '<div class="history-empty">当前 Tab 还没有历史分析。上传第一个 JSON 开始建立可追溯记录。</div>';
        } else {
            items.forEach((item) => {
                const row = document.createElement('div');
                row.className = 'history-row';
                row.dataset.runId = item.id;
                row.innerHTML = `
                    <div class="history-primary"><strong></strong><span></span></div>
                    <div class="history-stat"><strong></strong><span>文件</span></div>
                    <div class="history-stat"><strong></strong><span>已下载</span></div>
                    <div class="history-stat"><strong></strong><span>分析时间</span></div>
                    <span class="history-open">打开 <i class="fa-solid fa-arrow-up-right-from-square" aria-hidden="true"></i></span>`;
                row.querySelector('.history-primary strong').textContent = item.source_filename || '未命名 JSON';
                row.querySelector('.history-primary span').textContent = `${formatDateTime(item.created_at)} · SHA ${item.source_sha256_prefix || '-'}`;
                row.querySelectorAll('.history-stat strong')[0].textContent = formatNumber(item.item_count);
                row.querySelectorAll('.history-stat strong')[1].textContent = formatNumber(item.done_count);
                row.querySelectorAll('.history-stat strong')[2].textContent = formatNumber(item.total_download_count);
                elements['analysis-history-list'].appendChild(row);
            });
        }
        const currentId = state.currentRunByKind.get(state.kind)?.run?.id;
        document.querySelectorAll('.history-row').forEach((row) => row.classList.toggle('is-current', row.dataset.runId === currentId));
        const currentPage = pagination?.page || 1;
        const pages = pagination?.pages || 0;
        elements['history-pagination-label'].textContent = pages ? `第 ${currentPage} / ${pages} 页` : '第 1 页';
        elements['history-prev-button'].disabled = !pagination?.has_prev;
        elements['history-next-button'].disabled = !pagination?.has_next;
    }

    function renderHistoryLoading() {
        elements['analysis-history-list'].innerHTML = '<div class="history-empty"><i class="fa-solid fa-circle-notch fa-spin" aria-hidden="true"></i> 正在读取当前 Tab 历史…</div>';
    }

    function changeHistoryPage(delta) {
        const next = state.historyPage + delta;
        const pages = state.historyPagination?.pages || 1;
        if (next < 1 || next > pages) return;
        state.historyPage = next;
        loadHistory();
    }

    function clearChart(name) {
        const chart = state.charts.get(name);
        if (chart) chart.destroy();
        state.charts.delete(name);
    }

    function setBusy(busy) {
        elements['analysis-submit-button'].disabled = busy;
        elements['analysis-submit-button'].innerHTML = busy
            ? '<i class="fa-solid fa-circle-notch fa-spin" aria-hidden="true"></i>正在分析…'
            : '<i class="fa-solid fa-wand-magic-sparkles" aria-hidden="true"></i>开始分析并入库';
    }

    function setStatus(message, tone = '') {
        elements['analysis-status'].textContent = message;
        elements['analysis-status'].className = `analysis-status${tone ? ` is-${tone}` : ''}`;
    }

    function authHeaders() {
        const token = localStorage.getItem('access_token');
        return token ? { Authorization: `Bearer ${token}`, Accept: 'application/json' } : { Accept: 'application/json' };
    }

    function apiBaseUrl() {
        return (window.API_BASE_URL || window.CONFIG?.API_BASE_URL || '/api/v1').replace(/\/$/, '');
    }

    async function readJson(response) {
        const type = response.headers.get('content-type') || '';
        if (!type.includes('application/json')) throw new Error(`接口返回异常（HTTP ${response.status}）`);
        return response.json();
    }

    function formatNumber(value) { return new Intl.NumberFormat('zh-CN').format(Number(value) || 0); }
    function formatDecimal(value) { return (Number(value) || 0).toLocaleString('zh-CN', { maximumFractionDigits: 1 }); }
    function formatBytes(value) {
        const bytes = Number(value) || 0;
        if (bytes < 1024) return `${formatNumber(bytes)} B`;
        const units = ['KB', 'MB', 'GB', 'TB'];
        let size = bytes / 1024;
        let unit = units[0];
        for (let index = 0; size >= 1024 && index < units.length - 1; index += 1) { size /= 1024; unit = units[index + 1]; }
        return `${size.toLocaleString('zh-CN', { maximumFractionDigits: 1 })} ${unit}`;
    }
    function formatDuration(value) {
        const seconds = Math.max(0, Math.round(Number(value) || 0));
        if (seconds < 60) return `${seconds} 秒`;
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes} 分钟`;
        const hours = Math.floor(minutes / 60);
        const rest = minutes % 60;
        return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`;
    }
    function formatDateTime(value) {
        if (!value) return '-';
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
    }
    function formatPeriod(start, end) {
        if (!start && !end) return '-';
        const first = start ? String(start).slice(0, 10) : '?';
        const last = end ? String(end).slice(0, 10) : '?';
        return first === last ? first : `${first} 至 ${last}`;
    }
    function statusLabel(status) { return ({ done: '已下载', pending: '待下载', failed: '失败', processing: '处理中' })[status] || status || '-'; }
})();
