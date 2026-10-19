/* Offline capture import page. All captured text is rendered as text, never HTML. */
(() => {
    const fileInput = document.getElementById('json-import-file');
    if (!fileInput) return;
    const preview = document.getElementById('json-import-preview');
    const commit = document.getElementById('json-import-commit');
    const report = document.getElementById('json-import-report');
    const historyButton = document.getElementById('json-import-history');
    const phase = document.getElementById('json-import-phase');
    const hint = document.getElementById('json-import-hint');
    const drop = document.getElementById('json-import-drop');
    let digest = null;
    let selectedFile = null;
    let busy = false;
    const base = () => `${window.API_BASE_URL || '/api/v1'}/zsxq/json-import`;
    const formatTime = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false}) : '—';
    function node(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    }
    function setPhase(text, kind = '') { phase.textContent = text; phase.className = `status-pill ${kind}`; }
    async function call(path, options = {}) {
        const headers = new Headers(options.headers || {});
        const token = localStorage.getItem('access_token');
        if (token) headers.set('Authorization', `Bearer ${token}`);
        const response = await fetch(base() + path, {...options, headers});
        let body;
        try { body = await response.json(); }
        catch { throw new Error('服务暂时不可用，请稍后重试。'); }
        if (!response.ok || !body.success) throw new Error(body.error || body.message || '请求失败');
        return body.data;
    }
    function showResult(r, saved) {
        report.replaceChildren();
        if (saved) report.append(node('div', 'result-success', '✓ 导入完成，数据已保存到本地知识库。'));
        const group = node('div', 'result-group');
        const name = node('div'); name.append(node('h3', '', r.group_name), node('p', '', `星球 ID ${r.group_id}`));
        const total = node('div', 'result-total');
        total.append(node('strong', '', `${r.unique_topics} 篇独立帖子`), node('div', '', `对应 ${r.tab_records} 条标签页记录`));
        group.append(name, total); report.append(group);
        const stats = node('div', 'stat-grid');
        for (const [label, count] of [['新增',r.new_records],['更新',r.updated_records],['重复',r.unchanged_records],['旧快照保护',r.protected_records]]) {
            const tile = node('div', 'stat-tile'); tile.append(node('span', '', label), node('strong', '', String(count ?? 0))); stats.append(tile);
        }
        report.append(stats);
        const range = node('div', 'result-range', '文件中的帖子范围 · 北京时间');
        range.append(node('strong', '', `${formatTime(r.oldest_topic_time)} — ${formatTime(r.latest_topic_time)}`)); report.append(range);
        for (const warning of r.warnings || []) report.append(node('p', 'result-note', warning));
        report.append(node('p', 'result-note', `已跳过 ${r.skipped_responses_or_topics || 0} 条无关响应或其他星球帖子。`));
        setPhase(saved ? '已入库' : '待确认', saved ? 'done' : 'ready');
        hint.textContent = saved ? '可在下方记录中核对本次导入' : '确认后，将按上述结果更新数据库';
    }
    function fileChanged() {
        digest = null; selectedFile = null; commit.disabled = true;
        const file = fileInput.files[0];
        document.getElementById('json-import-filename').textContent = file ? file.name : '点击选择 JSON 文件';
        document.getElementById('json-import-filesize').textContent = file ? `${(file.size / 1024 / 1024).toFixed(2)} MiB · 点击可重新选择` : '或将文件拖到这里 · 最大 12 MiB';
        report.replaceChildren(node('div', 'preview-empty', file ? '文件已选择，点击「解析并预览」查看导入统计。' : '选择文件后预览导入统计。'));
        setPhase(file ? '等待预览' : '等待文件'); hint.textContent = '预览不会修改数据库';
    }
    fileInput.addEventListener('change', fileChanged);
    for (const eventName of ['dragenter', 'dragover']) drop.addEventListener(eventName, event => {
        event.preventDefault(); if (!busy) drop.classList.add('dragging');
    });
    drop.addEventListener('dragleave', () => drop.classList.remove('dragging'));
    drop.addEventListener('drop', event => {
        event.preventDefault(); drop.classList.remove('dragging');
        if (busy) return;
        const files = event.dataTransfer.files;
        if (files.length !== 1) { report.replaceChildren(node('div', 'result-error', '请一次选择一个 JSON 文件。')); return; }
        fileInput.files = files; fileChanged();
    });
    async function submit(action) {
        if (busy) return;
        const file = fileInput.files[0];
        if (!file || file.size > 12 * 1024 * 1024) {
            report.replaceChildren(node('div', 'result-error', file ? '文件不能超过 12 MiB。' : '请先选择 JSON 文件。')); return;
        }
        if (action === 'commit' && (!digest || selectedFile !== file)) return;
        busy = true; preview.disabled = true; commit.disabled = true; fileInput.disabled = true;
        preview.textContent = action === 'preview' ? '正在解析…' : '解析并预览';
        commit.textContent = action === 'commit' ? '正在入库…' : '确认入库';
        if (action === 'preview') report.replaceChildren(node('div', 'preview-empty', '正在解析文件并与本地数据库对照…'));
        setPhase(action === 'preview' ? '解析中' : '入库中', 'ready');
        hint.textContent = action === 'preview' ? '正在与本地数据库对照' : '正在保存，请勿关闭页面';
        try {
            const form = new FormData(); form.append('file', file);
            if (action === 'commit') form.append('file_sha256', digest);
            const r = await call(`/${action}`, {method: 'POST', body: form});
            showResult(r, action === 'commit');
            if (action === 'preview') { digest = r.file_sha256; selectedFile = file; }
            else { digest = null; selectedFile = null; await loadHistory(); }
        } catch (error) {
            digest = null; selectedFile = null; report.replaceChildren(node('div', 'result-error', error.message));
            setPhase('操作失败'); hint.textContent = '请检查文件或重新预览后重试';
        } finally {
            busy = false; preview.disabled = false; fileInput.disabled = false; commit.disabled = !digest;
            preview.textContent = '解析并预览'; commit.textContent = '确认入库';
        }
    }
    async function loadHistory() {
        const element = document.getElementById('json-import-history-list');
        historyButton.disabled = true;
        try {
            const rows = await call('/history');
            if (!rows.length) { element.replaceChildren(node('p', 'history-empty', '还没有导入记录。完成首次入库后，会在这里留下记录。')); return; }
            const wrap = node('div', 'history-table-wrap');
            const table = node('table', 'history-table'); const head = node('thead'); const headers = node('tr');
            for (const label of ['星球 / 批次', '入库时间', '独立帖子', '新增', '更新', '重复', '状态']) headers.append(node('th', '', label));
            head.append(headers); const body = node('tbody');
            for (const r of rows) {
                const tr = node('tr'); const group = node('td');
                group.append(node('strong', '', r.group_name), node('small', '', `批次 ${r.run_id}`));
                tr.append(group, node('td', '', formatTime(r.imported_at)), node('td', '', String(r.unique_topics ?? 0)), node('td', '', `+${r.new_records ?? 0}`), node('td', '', String(r.updated_records ?? 0)), node('td', '', String(r.unchanged_records ?? 0)));
                const status = node('td'); status.append(node('span', 'history-status', r.status === 'completed' ? '已完成' : r.status)); tr.append(status); body.append(tr);
            }
            table.append(head, body); wrap.append(table); element.replaceChildren(wrap);
        } catch (error) { element.replaceChildren(node('p', 'result-error', error.message)); }
        finally { historyButton.disabled = false; }
    }
    preview.addEventListener('click', () => submit('preview'));
    commit.addEventListener('click', () => submit('commit'));
    historyButton.addEventListener('click', loadHistory);
    loadHistory();
})();
