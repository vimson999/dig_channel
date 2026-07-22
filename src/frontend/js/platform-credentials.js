const API_BASE = (window.CONFIG?.API_BASE_URL || '/api/v1').replace(/\/$/, '');
const PAGE_SIZE = 12;

const state = {
    options: { platforms: [], purposes: [], statuses: [] },
    filters: { platform: '', purpose: '', status: '', q: '', page: 1 },
    items: [],
    total: 0,
    pages: 1,
};

const els = {
    platformTabs: document.getElementById('platform-tabs'),
    purposeFilter: document.getElementById('purpose-filter'),
    statusFilter: document.getElementById('status-filter'),
    keywordInput: document.getElementById('keyword-input'),
    searchBtn: document.getElementById('search-btn'),
    pageMessage: document.getElementById('page-message'),
    listSummary: document.getElementById('list-summary'),
    credentialList: document.getElementById('credential-list'),
    refreshBtn: document.getElementById('refresh-btn'),
    prevPageBtn: document.getElementById('prev-page-btn'),
    nextPageBtn: document.getElementById('next-page-btn'),
    pageIndicator: document.getElementById('page-indicator'),
    form: document.getElementById('credential-form'),
    formModeBadge: document.getElementById('form-mode-badge'),
    accountIdInput: document.getElementById('account-id-input'),
    platformInput: document.getElementById('platform-input'),
    credentialTypeInput: document.getElementById('credential-type-input'),
    nameInput: document.getElementById('name-input'),
    purposeCheckboxes: document.getElementById('purpose-checkboxes'),
    statusInput: document.getElementById('status-input'),
    priorityInput: document.getElementById('priority-input'),
    ownerLabelInput: document.getElementById('owner-label-input'),
    payloadInput: document.getElementById('payload-input'),
    notesInput: document.getElementById('notes-input'),
    resetBtn: document.getElementById('reset-btn'),
    newAccountBtn: document.getElementById('new-account-btn'),
    importBiliBtn: document.getElementById('import-bili-btn'),
    importZsxqBtn: document.getElementById('import-zsxq-btn'),
};

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function showMessage(message, type = 'info') {
    if (!message) {
        els.pageMessage.classList.add('hidden');
        return;
    }
    const classes = {
        success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
        error: 'border-rose-200 bg-rose-50 text-rose-700',
        warning: 'border-amber-200 bg-amber-50 text-amber-700',
        info: 'border-sky-200 bg-sky-50 text-sky-700',
    };
    els.pageMessage.className = `mt-4 rounded-lg border px-4 py-3 text-sm ${classes[type] || classes.info}`;
    els.pageMessage.textContent = message;
    els.pageMessage.classList.remove('hidden');
}

async function requestJson(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, {
        headers: options.body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
        ...options,
    });
    const json = await response.json().catch(() => ({}));
    if (!response.ok || json.success === false) {
        throw new Error(json.message || json.error || `HTTP ${response.status}`);
    }
    return json.data || {};
}

function platformName(id) {
    return state.options.platforms.find((item) => item.id === id)?.name || id || '-';
}

function purposeName(id) {
    return state.options.purposes.find((item) => item.id === id)?.name || id || '-';
}

function statusMeta(status) {
    const map = {
        active: { text: '可用', className: 'bg-emerald-50 text-emerald-700 border-emerald-100' },
        expired: { text: '失效', className: 'bg-amber-50 text-amber-700 border-amber-100' },
        disabled: { text: '禁用', className: 'bg-slate-100 text-slate-600 border-slate-200' },
        deleted: { text: '删除', className: 'bg-rose-50 text-rose-700 border-rose-100' },
    };
    return map[status] || { text: status || '-', className: 'bg-slate-100 text-slate-600 border-slate-200' };
}

function purposeOptionsForPlatform(platform) {
    return state.options.purposes.filter((item) => !platform || item.platform === platform);
}

function credentialTypesForPlatform(platform) {
    return state.options.platforms.find((item) => item.id === platform)?.credential_types || ['cookie_header'];
}

function renderOptions() {
    els.platformInput.innerHTML = state.options.platforms.map((item) => (
        `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`
    )).join('');

    els.statusInput.innerHTML = state.options.statuses.map((item) => (
        `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`
    )).join('');

    els.statusFilter.innerHTML = '<option value="">非删除</option>' + state.options.statuses.map((item) => (
        `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`
    )).join('');

    els.purposeFilter.innerHTML = '<option value="">全部用途</option>' + state.options.purposes.map((item) => (
        `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`
    )).join('');

    renderPlatformTabs();
    hydrateCredentialTypeOptions();
    renderPurposeCheckboxes();
}

function renderPlatformTabs() {
    const items = [{ id: '', name: '全部' }, ...state.options.platforms];
    els.platformTabs.innerHTML = items.map((item) => {
        const active = state.filters.platform === item.id;
        return `
            <button type="button" data-platform="${escapeHtml(item.id)}"
                class="platform-tab rounded-lg border px-3 py-2 text-sm font-black transition-colors ${active ? 'border-slate-950 bg-slate-950 text-white' : 'border-line bg-white text-slate-600 hover:bg-slate-50'}">
                ${escapeHtml(item.name)}
            </button>
        `;
    }).join('');
}

function hydrateCredentialTypeOptions() {
    const platform = els.platformInput.value || state.options.platforms[0]?.id || 'zsxq';
    const current = els.credentialTypeInput.value;
    const types = credentialTypesForPlatform(platform);
    els.credentialTypeInput.innerHTML = types.map((type) => (
        `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`
    )).join('');
    if (types.includes(current)) {
        els.credentialTypeInput.value = current;
    }
}

function renderPurposeCheckboxes(selected = null) {
    const platform = els.platformInput.value || state.options.platforms[0]?.id || 'zsxq';
    const selectedSet = new Set(selected || getSelectedPurposes());
    const purposes = purposeOptionsForPlatform(platform);
    if (!purposes.length) {
        els.purposeCheckboxes.innerHTML = '<div class="rounded-lg border border-dashed border-line p-3 text-xs text-slate-400">该平台暂无预置用途</div>';
        return;
    }
    els.purposeCheckboxes.innerHTML = purposes.map((item) => `
        <label class="flex items-center gap-2 rounded-lg border border-line bg-slate-50 px-3 py-2 text-sm font-bold text-slate-700">
            <input type="checkbox" class="purpose-checkbox h-4 w-4 rounded border-slate-300" value="${escapeHtml(item.id)}" ${selectedSet.has(item.id) ? 'checked' : ''}>
            <span>${escapeHtml(item.name)}</span>
        </label>
    `).join('');
}

function getSelectedPurposes() {
    return Array.from(document.querySelectorAll('.purpose-checkbox:checked')).map((item) => item.value);
}

function renderList() {
    els.listSummary.textContent = `共 ${state.total} 个凭据，当前第 ${state.filters.page}/${state.pages || 1} 页。`;
    els.pageIndicator.textContent = `第 ${state.filters.page} / ${state.pages || 1} 页`;
    els.prevPageBtn.disabled = state.filters.page <= 1;
    els.nextPageBtn.disabled = state.filters.page >= state.pages;
    for (const btn of [els.prevPageBtn, els.nextPageBtn]) {
        btn.classList.toggle('opacity-50', btn.disabled);
        btn.classList.toggle('cursor-not-allowed', btn.disabled);
    }

    if (!state.items.length) {
        els.credentialList.innerHTML = '<div class="rounded-xl border border-dashed border-line p-8 text-center text-sm text-slate-400 2xl:col-span-2">暂无凭据</div>';
        return;
    }

    els.credentialList.innerHTML = state.items.map((item) => {
        const meta = statusMeta(item.status);
        const purposes = (item.purposes || []).map((id) => purposeName(id)).join(' / ') || '-';
        return `
            <article class="credential-card rounded-xl border border-line bg-white p-4 shadow-sm" data-id="${escapeHtml(item.id)}">
                <div class="flex items-start justify-between gap-4">
                    <div class="min-w-0">
                        <div class="flex flex-wrap items-center gap-2">
                            <span class="rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-black text-slate-500">${escapeHtml(platformName(item.platform))}</span>
                            <span class="rounded-full border px-2 py-1 text-[11px] font-black ${meta.className}">${escapeHtml(meta.text)}</span>
                        </div>
                        <h4 class="mt-2 truncate text-base font-black text-slate-950">${escapeHtml(item.name)}</h4>
                        <div class="mt-1 break-all font-mono text-xs text-slate-500">${escapeHtml(item.id)}</div>
                    </div>
                    <div class="flex shrink-0 gap-1">
                        <button data-action="copy-id" class="h-9 w-9 rounded-lg border border-line text-slate-500 hover:bg-slate-50" title="复制 ID"><i class="fas fa-copy"></i></button>
                        <button data-action="edit" class="h-9 w-9 rounded-lg border border-line text-slate-500 hover:bg-slate-50" title="编辑"><i class="fas fa-pen"></i></button>
                    </div>
                </div>
                <dl class="mt-4 grid grid-cols-1 gap-2 text-sm">
                    <div class="rounded-lg bg-slate-50 px-3 py-2">
                        <dt class="text-xs font-black text-slate-400">用途</dt>
                        <dd class="mt-1 font-bold text-slate-700">${escapeHtml(purposes)}</dd>
                    </div>
                    <div class="rounded-lg bg-slate-50 px-3 py-2">
                        <dt class="text-xs font-black text-slate-400">脱敏摘要</dt>
                        <dd class="mt-1 break-all font-mono text-xs text-slate-700">${escapeHtml(item.masked_summary || '-')}</dd>
                    </div>
                    <div class="grid grid-cols-2 gap-2">
                        <div class="rounded-lg bg-slate-50 px-3 py-2">
                            <dt class="text-xs font-black text-slate-400">优先级</dt>
                            <dd class="mt-1 font-bold text-slate-700">${escapeHtml(item.priority ?? '-')}</dd>
                        </div>
                        <div class="rounded-lg bg-slate-50 px-3 py-2">
                            <dt class="text-xs font-black text-slate-400">账号</dt>
                            <dd class="mt-1 truncate font-bold text-slate-700">${escapeHtml(item.owner_label || '-')}</dd>
                        </div>
                    </div>
                    <div class="rounded-lg bg-slate-50 px-3 py-2">
                        <dt class="text-xs font-black text-slate-400">校验</dt>
                        <dd class="mt-1 text-xs text-slate-600">${escapeHtml(item.last_validated_at || '未校验')}${item.last_error ? ` · ${escapeHtml(item.last_error)}` : ''}</dd>
                    </div>
                </dl>
                <div class="mt-4 flex flex-wrap gap-2">
                    <button data-action="validate" class="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-black text-emerald-700 hover:bg-emerald-100">
                        <i class="fas fa-shield-halved mr-1"></i>${['douyin', 'wechat_channels'].includes(item.platform) ? '登录校验' : '结构校验'}
                    </button>
                    <button data-action="disable" class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-black text-amber-700 hover:bg-amber-100">
                        <i class="fas fa-ban mr-1"></i>${item.status === 'disabled' ? '启用' : '禁用'}
                    </button>
                    <button data-action="delete" class="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-black text-rose-700 hover:bg-rose-100">
                        <i class="fas fa-trash mr-1"></i>删除
                    </button>
                </div>
            </article>
        `;
    }).join('');
}

async function loadOptions() {
    const data = await requestJson('/platform-credentials/options');
    state.options = data;
    renderOptions();
}

async function loadCredentials() {
    const params = new URLSearchParams({
        page: String(state.filters.page),
        per_page: String(PAGE_SIZE),
    });
    for (const key of ['platform', 'purpose', 'status', 'q']) {
        if (state.filters[key]) params.set(key, state.filters[key]);
    }
    const data = await requestJson(`/platform-credentials?${params.toString()}`);
    state.items = data.items || [];
    state.total = data.total || 0;
    state.pages = data.pages || 1;
    state.filters.page = data.page || state.filters.page;
    renderList();
}

function resetForm() {
    els.form.reset();
    els.accountIdInput.value = '';
    els.formModeBadge.textContent = '新增';
    els.formModeBadge.className = 'rounded-full bg-slate-100 px-2 py-1 text-[11px] font-black text-slate-500';
    els.platformInput.value = state.filters.platform || state.options.platforms[0]?.id || 'zsxq';
    els.statusInput.value = 'active';
    els.priorityInput.value = '100';
    els.payloadInput.placeholder = '粘贴 Cookie 请求头、浏览器导出 JSON，或平台凭据 JSON';
    hydrateCredentialTypeOptions();
    renderPurposeCheckboxes([]);
}

function fillForm(item) {
    els.accountIdInput.value = item.id;
    els.formModeBadge.textContent = '编辑';
    els.formModeBadge.className = 'rounded-full bg-blue-50 px-2 py-1 text-[11px] font-black text-blue-700';
    els.platformInput.value = item.platform;
    hydrateCredentialTypeOptions();
    els.credentialTypeInput.value = item.credential_type || credentialTypesForPlatform(item.platform)[0];
    els.nameInput.value = item.name || '';
    els.statusInput.value = item.status === 'deleted' ? 'disabled' : item.status || 'active';
    els.priorityInput.value = String(item.priority ?? 100);
    els.ownerLabelInput.value = item.owner_label || '';
    els.payloadInput.value = '';
    els.payloadInput.placeholder = '已加密保存。留空表示不替换密文；粘贴新内容则替换。';
    els.notesInput.value = item.notes || '';
    renderPurposeCheckboxes(item.purposes || []);
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function payloadFromForm() {
    const payload = {
        platform: els.platformInput.value,
        credential_type: els.credentialTypeInput.value,
        name: els.nameInput.value.trim(),
        purposes: getSelectedPurposes(),
        status: els.statusInput.value,
        priority: Number(els.priorityInput.value || 100),
        owner_label: els.ownerLabelInput.value.trim(),
        notes: els.notesInput.value.trim(),
    };
    const secret = els.payloadInput.value.trim();
    if (secret) {
        payload.payload = secret;
    }
    return payload;
}

async function saveForm(event) {
    event.preventDefault();
    const accountId = els.accountIdInput.value;
    const payload = payloadFromForm();
    if (!accountId && !payload.payload) {
        showMessage('新增凭据需要填写 Cookie / Token / JSON。', 'warning');
        return;
    }
    if (!payload.purposes.length) {
        showMessage('至少选择一个用途授权。', 'warning');
        return;
    }
    try {
        if (accountId) {
            await requestJson(`/platform-credentials/${encodeURIComponent(accountId)}`, {
                method: 'PUT',
                body: JSON.stringify(payload),
            });
            showMessage('凭据已更新。', 'success');
        } else {
            await requestJson('/platform-credentials', {
                method: 'POST',
                body: JSON.stringify(payload),
            });
            showMessage('凭据已保存。', 'success');
        }
        resetForm();
        await loadCredentials();
    } catch (error) {
        showMessage(error.message, 'error');
    }
}

async function updateStatus(item, status) {
    const payload = {
        platform: item.platform,
        credential_type: item.credential_type,
        name: item.name,
        purposes: item.purposes || [],
        status,
        priority: item.priority ?? 100,
        owner_label: item.owner_label || '',
        notes: item.notes || '',
    };
    await requestJson(`/platform-credentials/${encodeURIComponent(item.id)}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
    });
}

async function handleListClick(event) {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const card = event.target.closest('.credential-card');
    const item = state.items.find((row) => row.id === card?.dataset.id);
    if (!item) return;

    const action = button.dataset.action;
    try {
        if (action === 'copy-id') {
            await navigator.clipboard.writeText(item.id);
            showMessage('已复制凭据 ID。', 'success');
            return;
        }
        if (action === 'edit') {
            fillForm(item);
            return;
        }
        if (action === 'validate') {
            const data = await requestJson(`/platform-credentials/${encodeURIComponent(item.id)}/validate`, {
                method: 'POST',
                body: JSON.stringify({ live: ['douyin', 'wechat_channels'].includes(item.platform) }),
            });
            showMessage(data.message || data.data?.message || '校验完成。', data.valid === false ? 'warning' : 'success');
            await loadCredentials();
            return;
        }
        if (action === 'disable') {
            await updateStatus(item, item.status === 'disabled' ? 'active' : 'disabled');
            showMessage(item.status === 'disabled' ? '凭据已启用。' : '凭据已禁用。', 'success');
            await loadCredentials();
            return;
        }
        if (action === 'delete') {
            if (!confirm(`删除凭据「${item.name}」？`)) return;
            await requestJson(`/platform-credentials/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
            showMessage('凭据已删除。', 'success');
            await loadCredentials();
        }
    } catch (error) {
        showMessage(error.message, 'error');
    }
}

async function importCredential(kind) {
    const endpoint = kind === 'bilibili'
        ? '/platform-credentials/import/bilibili-system-config'
        : '/platform-credentials/import/zsxq-auto-publish-runtime';
    try {
        const data = await requestJson(endpoint, { method: 'POST' });
        showMessage(data.message || '导入完成。', 'success');
        state.filters.platform = kind === 'bilibili' ? 'bilibili' : 'zsxq';
        state.filters.page = 1;
        renderPlatformTabs();
        await loadCredentials();
    } catch (error) {
        showMessage(error.message, 'error');
    }
}

function bindEvents() {
    els.platformTabs.addEventListener('click', (event) => {
        const button = event.target.closest('.platform-tab');
        if (!button) return;
        state.filters.platform = button.dataset.platform || '';
        state.filters.purpose = '';
        state.filters.page = 1;
        els.purposeFilter.value = '';
        renderPlatformTabs();
        loadCredentials().catch((error) => showMessage(error.message, 'error'));
    });

    els.purposeFilter.addEventListener('change', () => {
        state.filters.purpose = els.purposeFilter.value;
        state.filters.page = 1;
        loadCredentials().catch((error) => showMessage(error.message, 'error'));
    });
    els.statusFilter.addEventListener('change', () => {
        state.filters.status = els.statusFilter.value;
        state.filters.page = 1;
        loadCredentials().catch((error) => showMessage(error.message, 'error'));
    });
    els.searchBtn.addEventListener('click', () => {
        state.filters.q = els.keywordInput.value.trim();
        state.filters.page = 1;
        loadCredentials().catch((error) => showMessage(error.message, 'error'));
    });
    els.keywordInput.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            state.filters.q = els.keywordInput.value.trim();
            state.filters.page = 1;
            loadCredentials().catch((error) => showMessage(error.message, 'error'));
        }
    });

    els.prevPageBtn.addEventListener('click', () => {
        if (state.filters.page <= 1) return;
        state.filters.page -= 1;
        loadCredentials().catch((error) => showMessage(error.message, 'error'));
    });
    els.nextPageBtn.addEventListener('click', () => {
        if (state.filters.page >= state.pages) return;
        state.filters.page += 1;
        loadCredentials().catch((error) => showMessage(error.message, 'error'));
    });

    els.platformInput.addEventListener('change', () => {
        hydrateCredentialTypeOptions();
        renderPurposeCheckboxes([]);
    });
    els.form.addEventListener('submit', saveForm);
    els.resetBtn.addEventListener('click', resetForm);
    els.newAccountBtn.addEventListener('click', resetForm);
    els.refreshBtn.addEventListener('click', () => loadCredentials().catch((error) => showMessage(error.message, 'error')));
    els.credentialList.addEventListener('click', handleListClick);
    els.importBiliBtn.addEventListener('click', () => importCredential('bilibili'));
    els.importZsxqBtn.addEventListener('click', () => importCredential('zsxq'));
}

async function init() {
    bindEvents();
    try {
        await loadOptions();
        resetForm();
        await loadCredentials();
    } catch (error) {
        showMessage(error.message, 'error');
    }
}

init();
