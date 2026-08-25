(() => {
    'use strict';

    const API_BASE = (window.CONFIG?.API_BASE_URL || '/api/v1').replace(/\/$/, '');
    const PAGE_SIZE = 20;
    const state = { page: 1, pages: 1, total: 0, items: [], tenantId: '', status: '' };
    const els = {
        pageMessage: document.getElementById('page-message'),
        oneTimeSecretPanel: document.getElementById('one-time-secret-panel'),
        issuedAppId: document.getElementById('issued-app-id'),
        issuedSecret: document.getElementById('issued-secret'),
        dismissSecretBtn: document.getElementById('dismiss-secret-btn'),
        tenantFilter: document.getElementById('tenant-filter'),
        statusFilter: document.getElementById('status-filter'),
        searchBtn: document.getElementById('search-btn'),
        resetFilterBtn: document.getElementById('reset-filter-btn'),
        form: document.getElementById('client-form'),
        tenantIdInput: document.getElementById('tenant-id-input'),
        nameInput: document.getElementById('name-input'),
        appIdInput: document.getElementById('app-id-input'),
        expiresAtInput: document.getElementById('expires-at-input'),
        createBtn: document.getElementById('create-btn'),
        clearFormBtn: document.getElementById('clear-form-btn'),
        newClientBtn: document.getElementById('new-client-btn'),
        clientList: document.getElementById('client-list'),
        listSummary: document.getElementById('list-summary'),
        refreshBtn: document.getElementById('refresh-btn'),
        prevPageBtn: document.getElementById('prev-page-btn'),
        nextPageBtn: document.getElementById('next-page-btn'),
        pageIndicator: document.getElementById('page-indicator'),
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
            els.pageMessage.textContent = '';
            return;
        }
        const classes = {
            success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
            error: 'border-rose-200 bg-rose-50 text-rose-800',
            warning: 'border-amber-200 bg-amber-50 text-amber-900',
            info: 'border-sky-200 bg-sky-50 text-sky-800',
        };
        els.pageMessage.className = `mt-4 rounded-xl border px-4 py-3 text-sm ${classes[type] || classes.info}`;
        els.pageMessage.textContent = message;
        els.pageMessage.classList.remove('hidden');
    }

    async function requestJson(path, options = {}) {
        const headers = new Headers(options.headers || {});
        if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
        const response = await fetch(`${API_BASE}${path}`, {
            credentials: 'same-origin',
            ...options,
            headers,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.success === false) {
            throw new Error(payload.error || payload.message || `HTTP ${response.status}`);
        }
        return payload;
    }

    function statusMeta(status) {
        const map = {
            active: { label: '可用', className: 'border-emerald-100 bg-emerald-50 text-emerald-700' },
            revoked: { label: '已吊销', className: 'border-rose-100 bg-rose-50 text-rose-700' },
            expired: { label: '已过期', className: 'border-amber-100 bg-amber-50 text-amber-700' },
            deleted: { label: '已删除', className: 'border-slate-200 bg-slate-100 text-slate-600' },
        };
        return map[status] || { label: status || '-', className: 'border-slate-200 bg-slate-100 text-slate-600' };
    }

    function formatDate(value) {
        if (!value) return '—';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return new Intl.DateTimeFormat('zh-CN', {
            dateStyle: 'medium', timeStyle: 'short', hour12: false,
        }).format(date);
    }

    function copyFallback(text) {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        const copied = document.execCommand('copy');
        textarea.remove();
        if (!copied) throw new Error('浏览器不支持自动复制，请手动复制。');
    }

    async function copyText(text, label) {
        if (!text) return;
        try {
            if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
            else copyFallback(text);
            showMessage(`${label}已复制到剪贴板。`, 'success');
        } catch (error) {
            showMessage(`${label}复制失败：${error.message}`, 'error');
        }
    }

    function dismissSecret() {
        els.issuedAppId.textContent = '';
        els.issuedSecret.textContent = '';
        els.oneTimeSecretPanel.classList.add('hidden');
    }

    function showIssuedCredentials(client, credentials) {
        const appId = credentials?.app_id || client?.app_id || '';
        const secret = credentials?.secret || '';
        if (!appId || !secret) {
            showMessage('凭据创建成功，但服务端没有返回一次性 Secret。请不要假定旧 Secret 可用。', 'warning');
            return;
        }
        // Intentionally keep the secret only in the one-time DOM panel. It is
        // never placed in state, localStorage, URL parameters, or console logs.
        els.issuedAppId.textContent = appId;
        els.issuedSecret.textContent = secret;
        els.oneTimeSecretPanel.classList.remove('hidden');
        els.oneTimeSecretPanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function renderPagination() {
        els.listSummary.textContent = `共 ${state.total} 个调用方，当前第 ${state.page}/${state.pages} 页。`;
        els.pageIndicator.textContent = `第 ${state.page} / ${state.pages} 页`;
        els.prevPageBtn.disabled = state.page <= 1;
        els.nextPageBtn.disabled = state.page >= state.pages;
        for (const button of [els.prevPageBtn, els.nextPageBtn]) {
            button.classList.toggle('cursor-not-allowed', button.disabled);
            button.classList.toggle('opacity-50', button.disabled);
        }
    }

    function renderList() {
        renderPagination();
        if (!state.items.length) {
            els.clientList.innerHTML = `
                <div class="col-span-full rounded-xl border border-dashed border-line bg-slate-50 p-10 text-center">
                    <i class="fas fa-key text-2xl text-slate-300"></i>
                    <p class="mt-3 text-sm font-bold text-slate-600">没有符合条件的调用方</p>
                    <p class="mt-1 text-xs text-slate-400">创建后会在这里显示，但 Secret 不会再次出现在列表中。</p>
                </div>`;
            return;
        }
        els.clientList.innerHTML = state.items.map((item) => {
            const meta = statusMeta(item.status);
            const rotateLabel = item.status === 'active' ? '轮换 Secret' : '生成新 Secret 并启用';
            const lastUsed = item.last_used_at
                ? `${formatDate(item.last_used_at)}${item.last_used_ip ? ` · ${item.last_used_ip}` : ''}`
                : '尚未使用';
            const scopes = (Array.isArray(item.scopes) ? item.scopes : []).map((scope) => (
                `<code class="rounded bg-slate-100 px-1.5 py-1 text-[11px] text-slate-600">${escapeHtml(scope)}</code>`
            )).join(' ');
            return `
                <article class="rounded-xl border border-line bg-white p-4 shadow-sm">
                    <div class="flex items-start justify-between gap-3">
                        <div class="min-w-0">
                            <h3 class="truncate text-sm font-black text-slate-950" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</h3>
                            <p class="mt-1 truncate text-xs text-slate-500">租户：${escapeHtml(item.tenant_id)}</p>
                        </div>
                        <span class="shrink-0 rounded-full border px-2 py-1 text-[11px] font-black ${meta.className}">${meta.label}</span>
                    </div>
                    <div class="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3">
                        <div class="flex items-center justify-between gap-3">
                            <span class="text-[11px] font-black uppercase tracking-wide text-slate-500">AppID</span>
                            <button type="button" class="copy-app-id rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] font-bold text-slate-700 hover:bg-slate-100" data-app-id="${escapeHtml(item.app_id)}">复制</button>
                        </div>
                        <code class="mt-2 block break-all text-xs text-slate-800">${escapeHtml(item.app_id)}</code>
                    </div>
                    <dl class="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
                        <div><dt class="text-slate-400">授权</dt><dd class="mt-1 flex flex-wrap gap-1">${scopes || '—'}</dd></div>
                        <div><dt class="text-slate-400">过期时间</dt><dd class="mt-1 font-semibold text-slate-700">${escapeHtml(item.expires_at ? formatDate(item.expires_at) : '永不过期')}</dd></div>
                        <div class="col-span-2"><dt class="text-slate-400">最近使用</dt><dd class="mt-1 break-all font-semibold text-slate-700">${escapeHtml(lastUsed)}</dd></div>
                    </dl>
                    <div class="mt-4 flex flex-wrap gap-2 border-t border-slate-100 pt-4">
                        ${item.status !== 'deleted' ? `<button type="button" class="rotate-client rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-xs font-bold text-sky-700 hover:bg-sky-100" data-client-id="${escapeHtml(item.id)}" data-client-name="${escapeHtml(item.name)}"><i class="fas fa-rotate mr-1"></i>${rotateLabel}</button>` : ''}
                        ${item.status === 'active' ? `<button type="button" class="revoke-client rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold text-rose-700 hover:bg-rose-100" data-client-id="${escapeHtml(item.id)}" data-client-name="${escapeHtml(item.name)}"><i class="fas fa-ban mr-1"></i>吊销</button>` : ''}
                    </div>
                </article>`;
        }).join('');
    }

    async function loadClients({ quiet = false } = {}) {
        if (!quiet) {
            els.clientList.innerHTML = '<div class="col-span-full p-10 text-center text-sm font-bold text-slate-500"><i class="fas fa-spinner fa-spin mr-2"></i>加载调用方…</div>';
        }
        const params = new URLSearchParams({ page: String(state.page), per_page: String(PAGE_SIZE) });
        if (state.tenantId) params.set('tenant_id', state.tenantId);
        if (state.status) params.set('status', state.status);
        try {
            const result = await requestJson(`/zsxq/history-api-clients?${params.toString()}`);
            const data = result.data || {};
            state.items = Array.isArray(data.items) ? data.items : [];
            state.total = Number(data.total || 0);
            state.page = Number(data.page || state.page || 1);
            state.pages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
            renderList();
        } catch (error) {
            state.items = [];
            state.total = 0;
            state.pages = 1;
            renderList();
            showMessage(`读取调用方失败：${error.message}`, 'error');
        }
    }

    function resetForm({ focus = false } = {}) {
        els.form.reset();
        if (focus) els.tenantIdInput.focus();
    }

    function toIsoOrNull(value) {
        if (!value) return null;
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) throw new Error('过期时间无效');
        return date.toISOString();
    }

    async function createClient(event) {
        event.preventDefault();
        const tenantId = els.tenantIdInput.value.trim();
        const name = els.nameInput.value.trim();
        if (!tenantId || !name) {
            showMessage('请填写租户 ID 和调用方名称。', 'warning');
            return;
        }
        let expiresAt;
        try {
            expiresAt = toIsoOrNull(els.expiresAtInput.value);
        } catch (error) {
            showMessage(error.message, 'warning');
            return;
        }
        const body = {
            tenant_id: tenantId,
            name,
            app_id: els.appIdInput.value.trim() || undefined,
            expires_at: expiresAt || undefined,
            scopes: ['zsxq.history.read'],
        };
        const original = els.createBtn.innerHTML;
        els.createBtn.disabled = true;
        els.createBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>创建中';
        try {
            const result = await requestJson('/zsxq/history-api-clients', { method: 'POST', body: JSON.stringify(body) });
            showIssuedCredentials(result.client, result.credentials);
            showMessage('调用方已创建。请先安全保存一次性 Secret，再交给对应租户。', 'success');
            resetForm();
            state.page = 1;
            await loadClients({ quiet: true });
        } catch (error) {
            showMessage(`创建失败：${error.message}`, 'error');
        } finally {
            els.createBtn.disabled = false;
            els.createBtn.innerHTML = original;
        }
    }

    async function rotateClient(clientId, name) {
        if (!window.confirm(`确定要为“${name}”轮换 Secret 吗？旧 Secret 将立即失效。`)) return;
        try {
            const result = await requestJson(`/zsxq/history-api-clients/${encodeURIComponent(clientId)}/rotate`, { method: 'POST' });
            showIssuedCredentials(result.client, result.credentials);
            showMessage('Secret 已轮换，旧 Secret 已立即失效。', 'success');
            await loadClients({ quiet: true });
        } catch (error) {
            showMessage(`轮换失败：${error.message}`, 'error');
        }
    }

    async function revokeClient(clientId, name) {
        if (!window.confirm(`确定要吊销“${name}”吗？该 AppID 的签名请求会立即被拒绝。`)) return;
        try {
            await requestJson(`/zsxq/history-api-clients/${encodeURIComponent(clientId)}/revoke`, { method: 'POST' });
            showMessage('调用方已吊销。', 'success');
            await loadClients({ quiet: true });
        } catch (error) {
            showMessage(`吊销失败：${error.message}`, 'error');
        }
    }

    function bindEvents() {
        els.searchBtn.addEventListener('click', () => {
            state.tenantId = els.tenantFilter.value.trim();
            state.status = els.statusFilter.value;
            state.page = 1;
            loadClients();
        });
        els.tenantFilter.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') els.searchBtn.click();
        });
        els.resetFilterBtn.addEventListener('click', () => {
            els.tenantFilter.value = '';
            els.statusFilter.value = '';
            state.tenantId = '';
            state.status = '';
            state.page = 1;
            loadClients();
        });
        els.form.addEventListener('submit', createClient);
        els.clearFormBtn.addEventListener('click', () => resetForm({ focus: true }));
        els.newClientBtn.addEventListener('click', () => {
            resetForm({ focus: true });
            els.form.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
        els.refreshBtn.addEventListener('click', () => loadClients());
        els.prevPageBtn.addEventListener('click', () => {
            if (state.page > 1) {
                state.page -= 1;
                loadClients();
            }
        });
        els.nextPageBtn.addEventListener('click', () => {
            if (state.page < state.pages) {
                state.page += 1;
                loadClients();
            }
        });
        els.dismissSecretBtn.addEventListener('click', dismissSecret);
        document.querySelectorAll('.copy-issued-value').forEach((button) => {
            button.addEventListener('click', () => {
                const target = button.dataset.copyTarget;
                const text = target === 'issued-secret'
                    ? els.issuedSecret.textContent
                    : els.issuedAppId.textContent;
                copyText(text, target === 'issued-secret' ? 'Secret' : 'AppID');
            });
        });
        els.clientList.addEventListener('click', (event) => {
            const copyButton = event.target.closest('.copy-app-id');
            if (copyButton) {
                copyText(copyButton.dataset.appId || '', 'AppID');
                return;
            }
            const rotateButton = event.target.closest('.rotate-client');
            if (rotateButton) {
                rotateClient(rotateButton.dataset.clientId, rotateButton.dataset.clientName);
                return;
            }
            const revokeButton = event.target.closest('.revoke-client');
            if (revokeButton) revokeClient(revokeButton.dataset.clientId, revokeButton.dataset.clientName);
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        bindEvents();
        dismissSecret();
        loadClients();
    });
})();
