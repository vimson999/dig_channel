const linkedState = { page: 1, taskTimer: null };
const linkedEls = {
    listView: document.getElementById('list-view'), detailView: document.getElementById('detail-view'),
    detailCard: document.getElementById('detail-card'), list: document.getElementById('document-list'),
    stats: document.getElementById('stats'), pagination: document.getElementById('pagination'),
    taskPanel: document.getElementById('task-panel'), backfill: document.getElementById('backfill-btn'),
    refresh: document.getElementById('refresh-btn'), provider: document.getElementById('provider-filter'),
    status: document.getElementById('status-filter'), keyword: document.getElementById('keyword-input'),
    search: document.getElementById('search-btn'), title: document.getElementById('page-title'),
    subtitle: document.getElementById('page-subtitle'),
};

document.addEventListener('DOMContentLoaded', () => {
    linkedEls.refresh.addEventListener('click', loadCurrentView);
    linkedEls.search.addEventListener('click', () => { linkedState.page = 1; loadList(); });
    linkedEls.keyword.addEventListener('keydown', event => { if (event.key === 'Enter') { linkedState.page = 1; loadList(); } });
    linkedEls.provider.addEventListener('change', () => { linkedState.page = 1; loadList(); });
    linkedEls.status.addEventListener('change', () => { linkedState.page = 1; loadList(); });
    linkedEls.backfill.addEventListener('click', startBackfill);
    loadCurrentView();
});

function apiRoot() { return String(window.API_BASE_URL || window.CONFIG?.API_BASE_URL || '/api/v1').replace(/\/$/, ''); }
function documentId() { return new URLSearchParams(location.search).get('id') || ''; }
function esc(value) { const node = document.createElement('div'); node.textContent = String(value ?? ''); return node.innerHTML; }
function providerLabel(value) { return ({ feishu:'飞书', zsxq:'知识星球 item', wechat:'微信文章', generic_web:'普通网页' })[value] || value || '未知来源'; }
function statusLabel(value) { return ({ registered:'仅登记', pending:'待抓取', fetching:'抓取中', fetched:'已抓取', failed:'失败', empty:'空正文' })[value] || value || '未知'; }
function fmt(value) { if (!value) return '-'; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12:false }); }

async function request(path, options = {}) {
    const response = await fetch(`${apiRoot()}${path}`, { headers:{ 'Content-Type':'application/json' }, ...options });
    let payload = {};
    try { payload = await response.json(); } catch {}
    if (!response.ok || payload.success === false) throw new Error(payload.message || `请求失败 HTTP ${response.status}`);
    return payload.data || payload;
}

function loadCurrentView() { return documentId() ? loadDetail(documentId()) : loadList(); }

async function loadList() {
    linkedEls.listView.classList.remove('hidden'); linkedEls.detailView.classList.add('hidden');
    const query = new URLSearchParams({ page:String(linkedState.page), per_page:'30' });
    if (linkedEls.provider.value) query.set('provider', linkedEls.provider.value);
    if (linkedEls.status.value) query.set('status', linkedEls.status.value);
    if (linkedEls.keyword.value.trim()) query.set('q', linkedEls.keyword.value.trim());
    linkedEls.list.innerHTML = '<div class="empty">正在读取关联内容…</div>';
    try {
        const data = await request(`/external-documents?${query}`);
        renderStats(data);
        linkedEls.list.innerHTML = data.items?.length ? data.items.map(renderRow).join('') : '<div class="empty">没有匹配的关联内容。</div>';
        renderPagination(data);
    } catch (error) { linkedEls.list.innerHTML = `<div class="empty">${esc(error.message)}</div>`; }
}

function renderStats(data) {
    const providers = data.provider_counts || {}, statuses = data.status_counts || {};
    linkedEls.stats.innerHTML = [
        ['全部内容', data.total || 0], ['飞书', providers.feishu || 0],
        ['星球 / 微信', (providers.zsxq || 0) + (providers.wechat || 0)], ['已抓取', statuses.fetched || 0],
    ].map(([label,value]) => `<div class="stat"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join('');
}

function renderRow(doc) {
    const title = doc.title || doc.canonical_url || '未命名内容';
    return `<article class="document-row"><div><div class="document-meta"><span class="provider">${esc(providerLabel(doc.provider))}</span><span class="status status-${esc(doc.fetch_status)}">${esc(statusLabel(doc.fetch_status))}</span><span>${esc(doc.content_chars || 0)} 字</span><span>更新 ${esc(fmt(doc.updated_at))}</span></div><h2><a href="linked-content-detail.html?id=${encodeURIComponent(doc.id)}">${esc(title)}</a></h2>${doc.fetch_error ? `<div class="error-box">${esc(doc.fetch_error)}</div>` : ''}</div><a class="button row-action" href="linked-content-detail.html?id=${encodeURIComponent(doc.id)}">查看更多 <i class="fas fa-arrow-right"></i></a></article>`;
}

function renderPagination(data) {
    linkedEls.pagination.innerHTML = '';
    if ((data.pages || 1) <= 1) return;
    const previous = document.createElement('button'), next = document.createElement('button');
    previous.className = next.className = 'button'; previous.textContent = '上一页'; next.textContent = '下一页';
    previous.disabled = data.page <= 1; next.disabled = data.page >= data.pages;
    previous.onclick = () => { linkedState.page--; loadList(); }; next.onclick = () => { linkedState.page++; loadList(); };
    linkedEls.pagination.append(previous, Object.assign(document.createElement('span'), { textContent:`第 ${data.page} / ${data.pages} 页` }), next);
}

async function loadDetail(id) {
    linkedEls.listView.classList.add('hidden'); linkedEls.detailView.classList.remove('hidden'); linkedEls.backfill.classList.add('hidden');
    linkedEls.detailCard.innerHTML = '<div class="empty">正在读取完整内容…</div>';
    try {
        const data = await request(`/external-documents/${encodeURIComponent(id)}`);
        const doc = data.document || data;
        linkedEls.title.textContent = doc.title || '关联内容详情'; linkedEls.subtitle.textContent = `${providerLabel(doc.provider)} · ${statusLabel(doc.fetch_status)}`;
        linkedEls.detailCard.innerHTML = renderDetail(doc, data.references || []);
        linkedEls.detailCard.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => runDocumentAction(button, doc.id)));
    } catch (error) { linkedEls.detailCard.innerHTML = `<div class="empty">${esc(error.message)}</div>`; }
}

function renderDetail(doc, references) {
    const mayFetch = ['feishu','zsxq','wechat'].includes(doc.provider);
    const fetched = doc.fetch_status === 'fetched';
    return `<div class="detail-hero"><span class="provider">${esc(providerLabel(doc.provider))}</span><h2>${esc(doc.title || '未命名内容')}</h2><div class="document-meta"><span class="status status-${esc(doc.fetch_status)}">${esc(statusLabel(doc.fetch_status))}</span>${doc.author_name ? `<span>作者 ${esc(doc.author_name)}</span>` : ''}<span>${esc(doc.content_chars || 0)} 字</span><span>抓取 ${esc(fmt(doc.fetched_at))}</span></div><p><a class="original-url" href="${esc(doc.canonical_url)}" target="_blank" rel="noopener noreferrer">${esc(doc.canonical_url)}</a></p><div class="detail-actions">${mayFetch ? `<button class="button button-primary" data-action="fetch"><i class="fas fa-cloud-arrow-down"></i> ${fetched ? '重新抓取' : '抓取正文'}</button>` : ''}<button class="button" data-action="summarize" ${fetched ? '' : 'disabled'}><i class="fas fa-wand-magic-sparkles"></i> AI 总结</button><button class="button" data-action="tts" ${(fetched || doc.summary_text) ? '' : 'disabled'}><i class="fas fa-volume-high"></i> 生成语音</button>${doc.tts_status === 'completed' ? `<a class="button" href="${apiRoot()}/external-documents/${encodeURIComponent(doc.id)}/tts/audio"><i class="fas fa-download"></i> 下载音频</a>` : ''}</div>${doc.fetch_error ? `<div class="error-box">${esc(doc.fetch_error)}</div>` : ''}</div>${doc.summary_text ? `<section class="detail-section"><h3>AI 总结</h3><div class="summary-body">${esc(doc.summary_text)}</div></section>` : ''}<section class="detail-section"><h3>完整正文</h3><div class="content-body">${doc.content_text ? esc(doc.content_text) : '尚无正文。普通网页只登记原始地址，不会自动抓取。'}</div></section><section class="detail-section"><h3>被哪些 item 引用（${references.length}）</h3>${references.length ? references.map(renderReference).join('') : '<div class="document-meta">暂无引用记录。</div>'}</section>`;
}

function renderReference(ref) { return `<div class="reference"><strong>${esc(ref.topic_title || `item ${ref.topic_id}`)}</strong><div class="reference-meta"><span>${esc(ref.group_name || ref.group_id)}</span><span>${esc(ref.tab_label || ref.tab_id)}</span><span>item ${esc(ref.topic_id)}</span></div></div>`; }

async function runDocumentAction(button, id) {
    const action = button.dataset.action; const original = button.innerHTML; button.disabled = true; button.textContent = '处理中…';
    try { await request(`/external-documents/${encodeURIComponent(id)}/${action}`, { method:'POST', body:JSON.stringify({ force:action === 'fetch' }) }); await loadDetail(id); }
    catch (error) { alert(error.message); button.disabled = false; button.innerHTML = original; }
}

async function startBackfill() {
    linkedEls.backfill.disabled = true;
    try { const data = await request('/external-documents/backfill/tasks', { method:'POST', body:JSON.stringify({ providers:['feishu','zsxq','wechat'], scan:true, fetch:true }) }); pollTask(data.task); }
    catch (error) { alert(error.message); linkedEls.backfill.disabled = false; }
}

async function pollTask(task) {
    clearTimeout(linkedState.taskTimer); linkedEls.taskPanel.classList.remove('hidden');
    linkedEls.taskPanel.innerHTML = `<strong>后台补抓：${esc(task.current_step || task.status)}</strong> · ${esc(task.progress || 0)}%<div class="task-progress"><span style="width:${Number(task.progress || 0)}%"></span></div>`;
    if (['completed','failed'].includes(task.status)) { linkedEls.backfill.disabled = false; await loadList(); return; }
    linkedState.taskTimer = setTimeout(async () => { try { const data = await request(`/external-documents/backfill/tasks/${encodeURIComponent(task.id)}`); pollTask(data.task); } catch { linkedState.taskTimer = setTimeout(() => pollTask(task), 3000); } }, 2000);
}
