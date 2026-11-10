(() => {
    'use strict';

    const PAGE_SIZE = 20;
    const state = {
        page: 1,
        pages: 1,
        selectedRunId: null,
        items: [],
        requestSerial: 0,
    };
    const nodes = {
        status: document.getElementById('archive-status'),
        history: document.getElementById('history-list'),
        historyCount: document.getElementById('history-count'),
        pageLabel: document.getElementById('history-page-label'),
        prev: document.getElementById('history-prev'),
        next: document.getElementById('history-next'),
        empty: document.getElementById('report-empty'),
        content: document.getElementById('report-content'),
    };

    const apiRoot = () => (window.API_BASE_URL || (
        ['localhost', '127.0.0.1'].includes(window.location.hostname)
            ? 'http://localhost:5001/api/v1'
            : '/api/v1'
    )).replace(/\/$/, '');

    const authHeaders = () => {
        const token = localStorage.getItem('access_token');
        return token ? { Authorization: `Bearer ${token}`, Accept: 'application/json' } : { Accept: 'application/json' };
    };

    const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[character]));

    const formatNumber = (value) => {
        const number = Number(value);
        return Number.isFinite(number) ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(number) : '—';
    };

    const formatDate = (value) => {
        if (!value) return '未提供';
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
        }).format(date);
    };

    const status = (message = '', isError = false) => {
        nodes.status.textContent = message;
        nodes.status.classList.toggle('is-error', Boolean(isError));
    };

    async function requestJson(url) {
        const response = await fetch(url, {
            method: 'GET',
            credentials: 'include',
            headers: authHeaders(),
            cache: 'no-store',
        });
        let payload = null;
        try { payload = await response.json(); } catch (_) { /* handled below */ }
        if (!response.ok || !payload?.success) {
            throw new Error(payload?.error?.message || `读取失败（HTTP ${response.status}）`);
        }
        return payload.data || {};
    }

    function renderHistory(items, pagination) {
        state.items = items;
        state.pages = pagination?.pages || 1;
        nodes.historyCount.textContent = formatNumber(pagination?.total || 0);
        nodes.pageLabel.textContent = `第 ${state.page} / ${state.pages} 页`;
        nodes.prev.disabled = !pagination?.has_prev;
        nodes.next.disabled = !pagination?.has_next;

        if (!items.length) {
            nodes.history.innerHTML = '<div class="empty-state">还没有保存的 Codex PDF 分析。</div>';
            return;
        }

        nodes.history.innerHTML = items.map((run) => `
            <button type="button" class="history-item ${run.id === state.selectedRunId ? 'is-active' : ''}" data-run-id="${escapeHtml(run.id)}">
                <span class="history-item-title" title="${escapeHtml(run.source_filename)}">${escapeHtml(run.source_filename)}</span>
                <span class="history-item-date">分析于 ${escapeHtml(formatDate(run.completed_at || run.created_at))}</span>
                <span class="history-item-meta">
                    <span>${formatNumber(run.source_record_count)} 条 PDF</span>
                    <span>${formatNumber(run.source_total_download_count)} 次下载</span>
                    <span>${escapeHtml(run.analysis_version)}</span>
                </span>
            </button>
        `).join('');
    }

    function markActiveHistoryItem() {
        nodes.history.querySelectorAll('[data-run-id]').forEach((button) => {
            button.classList.toggle('is-active', button.dataset.runId === state.selectedRunId);
        });
    }

    async function loadHistory({ openFirst = false } = {}) {
        status('正在读取分析历史…');
        nodes.history.innerHTML = '<div class="empty-state">正在读取历史…</div>';
        try {
            const params = new URLSearchParams({ page: String(state.page), per_page: String(PAGE_SIZE) });
            const data = await requestJson(`${apiRoot()}/codex-pdf-analyses/?${params}`);
            renderHistory(data.items || [], data.pagination || null);
            status('');
            if (openFirst) {
                const requestedId = new URLSearchParams(window.location.search).get('run_id');
                const selected = requestedId || state.items[0]?.id;
                if (selected) await loadDetail(selected);
            }
        } catch (error) {
            nodes.history.innerHTML = `<div class="empty-state">${escapeHtml(error.message || '历史读取失败')}</div>`;
            status(error.message || '分析历史读取失败。', true);
        }
    }

    function roleLabel(value) {
        return ({
            primary_report: '主报告',
            mechanism_support: '机制补充',
            counterpoint: '反向 / 分歧',
            background: '背景资料',
            data_validation: '数据验证',
        })[value] || value || '证据来源';
    }

    function trackLabel(value) {
        return ({
            china_personal: '中国宏观 × 个人命运',
            global_cross_asset: '全球趋势 × 跨资产',
            company_industry: '公司财报 × 产业链',
            commodities: '大宗商品 × 宏观产业',
        })[value] || value || '选题';
    }

    function rankingLabel(value) {
        return ({
            single_pdf_top: '单篇下载排行',
            topic_single_pdf: '分领域单篇排行',
            special_topic_pdf: '重点专题 PDF',
            commodity_recent_3d: '近三天商品新上传',
            commodity_today: '当日商品新上传',
            commodity_download_gain: '商品同文件下载增量',
            target_pdf: '标的相关 PDF',
            candidate_source: '候选选题 PDF',
        })[value] || value || 'PDF 排名';
    }

    function renderMetrics(run, detail) {
        const metrics = [
            ['输入 PDF 条目', formatNumber(run.source_record_count)],
            ['引用 PDF', formatNumber(detail.sources?.length || 0)],
            ['累计下载', formatNumber(run.source_total_download_count)],
            ['选题候选', formatNumber(detail.candidates?.length || 0)],
        ];
        return `<div class="metric-grid">${metrics.map(([label, value]) => `
            <div class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
        `).join('')}</div>`;
    }

    function renderSections(sections) {
        if (!sections?.length) return '';
        return `
            <section class="content-section">
                <h3><i class="fa-solid fa-list-check" aria-hidden="true"></i>分析模块</h3>
                ${sections.map((section) => `
                    <article class="content-section">
                        <h3><i class="fa-regular fa-file-lines" aria-hidden="true"></i>${escapeHtml(section.title)}</h3>
                        ${section.evidence_state ? `<div class="report-tags"><span class="report-tag">证据状态：${escapeHtml(section.evidence_state)}</span></div>` : ''}
                        ${section.summary_text ? `<p class="section-summary">${escapeHtml(section.summary_text)}</p>` : ''}
                        <pre class="section-body">${escapeHtml(section.body_text)}</pre>
                        ${section.sources?.length ? `<div class="section-sources">${section.sources.map((link) => `
                            <span class="source-chip" title="${escapeHtml(link.evidence_note || '')}"><i class="fa-solid fa-paperclip" aria-hidden="true"></i>${escapeHtml(roleLabel(link.source_role))} · ${escapeHtml(link.source?.name || '')}</span>
                        `).join('')}</div>` : ''}
                    </article>
                `).join('')}
            </section>
        `;
    }

    function renderTopics(topics) {
        if (!topics?.length) return '';
        const rows = topics.map((topic) => `
            <tr>
                <td>${escapeHtml(topic.rank)}</td>
                <td><strong>${escapeHtml(topic.topic_name)}</strong></td>
                <td>${topic.classification_scope === 'multi' ? '多标签' : '唯一主主题'}</td>
                <td class="num">${formatNumber(topic.pdf_count)}</td>
                <td class="num">${formatNumber(topic.total_download_count)}</td>
                <td class="num">${formatNumber(topic.average_download_count)}</td>
                <td class="num">${formatNumber(topic.institution_count)}</td>
                <td>${escapeHtml(topic.keywords_text || '—')}</td>
            </tr>
        `).join('');
        return `
            <section class="content-section">
                <h3><i class="fa-solid fa-chart-column" aria-hidden="true"></i>主题统计</h3>
                <div class="table-wrap"><table class="report-table"><thead><tr>
                    <th>排名</th><th>主题</th><th>口径</th><th>PDF 数</th><th>累计下载</th><th>篇均下载</th><th>机构数</th><th>关键词</th>
                </tr></thead><tbody>${rows}</tbody></table></div>
            </section>
        `;
    }

    function renderRankings(rankings) {
        if (!rankings?.length) return '';
        const groups = new Map();
        rankings.forEach((row) => {
            const key = `${row.ranking_type}|||${row.topic_name || ''}`;
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(row);
        });
        return `
            <section class="content-section">
                <h3><i class="fa-solid fa-ranking-star" aria-hidden="true"></i>PDF 排行与专题清单</h3>
                ${[...groups.entries()].map(([key, rows]) => {
                    const [type, topic] = key.split('|||');
                    return `
                        <div class="content-section">
                            <h3>${escapeHtml(rankingLabel(type))}${topic ? ` · ${escapeHtml(topic)}` : ''}</h3>
                            <div class="evidence-list">${rows.map((row) => `
                                <article class="evidence-card">
                                    <strong><span class="muted">#${escapeHtml(row.rank)}</span> ${escapeHtml(row.source?.name || '')}</strong>
                                    <div class="source-meta">
                                        <span>${escapeHtml(row.source?.institution || '机构未提供')}</span>
                                        <span>报告日 ${escapeHtml(row.source?.report_date_text || '未提供')}</span>
                                        <span>上传 ${escapeHtml(row.source?.upload_time_text || '未提供')}</span>
                                        <span>下载 ${formatNumber(row.source?.download_count)}</span>
                                        <span>${escapeHtml(row.source?.status || '状态未提供')}</span>
                                    </div>
                                </article>
                            `).join('')}</div>
                        </div>
                    `;
                }).join('')}
            </section>
        `;
    }

    function renderTargets(targets) {
        if (!targets?.length) return '';
        return `
            <section class="content-section">
                <h3><i class="fa-solid fa-arrow-trend-up" aria-hidden="true"></i>标的聚合</h3>
                <div class="table-wrap"><table class="report-table"><thead><tr>
                    <th>代码</th><th>标的</th><th>主题</th><th>PDF 数</th><th>累计下载</th><th>篇均下载</th><th>机构</th><th>说明</th>
                </tr></thead><tbody>${targets.map((row) => `
                    <tr><td>${escapeHtml(row.ticker)}</td><td><strong>${escapeHtml(row.display_name)}</strong></td>
                    <td>${escapeHtml(row.main_topic || '—')}</td><td class="num">${formatNumber(row.pdf_count)}</td>
                    <td class="num">${formatNumber(row.total_download_count)}</td><td class="num">${formatNumber(row.average_download_count)}</td>
                    <td>${escapeHtml(row.institutions_text || '—')}</td><td>${escapeHtml(row.aggregation_note || '—')}</td></tr>
                `).join('')}</tbody></table></div>
            </section>
        `;
    }

    function renderCandidates(candidates) {
        if (!candidates?.length) return '';
        return `
            <section class="content-section">
                <h3><i class="fa-solid fa-lightbulb" aria-hidden="true"></i>四轨选题候选</h3>
                <div class="candidate-grid">${candidates.map((candidate) => `
                    <article class="candidate-card ${candidate.is_primary ? 'is-primary' : ''}">
                        <div class="candidate-title-row"><h4>${escapeHtml(candidate.title)}</h4>${candidate.is_primary ? '<span class="primary-badge">本期主推</span>' : ''}</div>
                        <div class="candidate-track">${escapeHtml(trackLabel(candidate.track_code))} · 优先级 ${escapeHtml(candidate.priority)}</div>
                        <p class="candidate-copy"><strong>母问题：</strong>${escapeHtml(candidate.parent_question)}</p>
                        ${candidate.audience ? `<p class="candidate-copy"><strong>目标受众：</strong>${escapeHtml(candidate.audience)}</p>` : ''}
                        ${candidate.new_evidence ? `<p class="candidate-copy"><strong>新增证据：</strong>${escapeHtml(candidate.new_evidence)}</p>` : ''}
                        <p class="candidate-copy"><strong>推荐理由：</strong>${escapeHtml(candidate.rationale)}</p>
                        ${candidate.uncertainty_text ? `<p class="candidate-copy"><strong>不确定性：</strong>${escapeHtml(candidate.uncertainty_text)}</p>` : ''}
                        <div class="candidate-source-list">${(candidate.sources || []).map((link) => `
                            <article class="candidate-source">
                                <strong>[${escapeHtml(roleLabel(link.source_role))}] ${escapeHtml(link.source?.name || '')}</strong>
                                <p>${escapeHtml(link.selection_reason)}</p>
                                <div class="source-meta"><span>${escapeHtml(link.source?.institution || '机构未提供')}</span><span>报告日 ${escapeHtml(link.source?.report_date_text || '未提供')}</span><span>下载 ${formatNumber(link.source?.download_count)}</span></div>
                            </article>
                        `).join('')}</div>
                    </article>
                `).join('')}</div>
            </section>
        `;
    }

    function renderSourceIndex(sources) {
        if (!sources?.length) return '';
        return `
            <section class="content-section">
                <h3><i class="fa-solid fa-file-pdf" aria-hidden="true"></i>引用 PDF 目录</h3>
                <div class="evidence-list">${sources.map((source) => `
                    <article class="evidence-card">
                        <strong>${escapeHtml(source.name)}</strong>
                        <div class="source-meta"><span>${escapeHtml(source.institution || '机构未提供')}</span>
                            <span>报告日 ${escapeHtml(source.report_date_text || '未提供')}</span>
                            <span>上传 ${escapeHtml(source.upload_time_text || '未提供')}</span>
                            <span>下载 ${formatNumber(source.download_count)}</span>
                            <span>${escapeHtml(source.status || '状态未提供')}</span></div>
                    </article>
                `).join('')}</div>
            </section>
        `;
    }

    function renderReport(detail) {
        const run = detail.run || {};
        const title = run.source_filename || 'PDF 分析报告';
        const tags = [
            `导出时间 ${formatDate(run.source_exported_at)}`,
            `生成时间 ${formatDate(run.completed_at || run.created_at)}`,
            `合同 ${run.contract_version || '—'}`,
            `分析版本 ${run.analysis_version || '—'}`,
            `SHA-256 ${run.source_sha256 || run.source_sha256_prefix || '—'}`,
        ];
        nodes.empty.hidden = true;
        nodes.content.hidden = false;
        nodes.content.innerHTML = `
            <header class="report-head">
                <div class="report-head-top"><div><span class="eyebrow">Codex LLM 分析 · ${escapeHtml(run.status || 'completed')}</span><h2>${escapeHtml(title)}</h2></div></div>
                <div class="report-tags">${tags.map((tag) => `<span class="report-tag">${escapeHtml(tag)}</span>`).join('')}</div>
                <p class="summary">${escapeHtml(run.overall_summary || '')}</p>
            </header>
            ${renderMetrics(run, detail)}
            ${run.data_quality_note ? `<section class="content-section"><h3><i class="fa-solid fa-shield-halved" aria-hidden="true"></i>数据质量提示</h3><div class="quality-note">${escapeHtml(run.data_quality_note)}</div></section>` : ''}
            ${renderTopics(detail.topics)}
            ${renderRankings(detail.rankings)}
            ${renderTargets(detail.targets)}
            ${renderCandidates(detail.candidates)}
            ${renderSections(detail.sections)}
            ${renderSourceIndex(detail.sources)}
        `;
        markActiveHistoryItem();
        document.title = `${title} - Codex PDF 分析归档`;
    }

    async function loadDetail(runId) {
        const serial = ++state.requestSerial;
        state.selectedRunId = runId;
        markActiveHistoryItem();
        nodes.empty.hidden = false;
        nodes.content.hidden = true;
        nodes.empty.innerHTML = '<div class="empty-icon"><i class="fa-solid fa-spinner fa-spin"></i></div><h2>正在读取报告</h2><p>正在加载结构化分析结果与 PDF 引用。</p>';
        status('正在打开分析报告…');
        try {
            const detail = await requestJson(`${apiRoot()}/codex-pdf-analyses/${encodeURIComponent(runId)}`);
            if (serial !== state.requestSerial) return;
            renderReport(detail);
            status('');
            const url = new URL(window.location.href);
            url.searchParams.set('run_id', runId);
            window.history.replaceState({}, '', url);
        } catch (error) {
            if (serial !== state.requestSerial) return;
            nodes.empty.innerHTML = `<div class="empty-icon"><i class="fa-solid fa-triangle-exclamation"></i></div><h2>报告读取失败</h2><p>${escapeHtml(error.message || '请稍后重试')}</p>`;
            status(error.message || '分析详情读取失败。', true);
        }
    }

    nodes.history.addEventListener('click', (event) => {
        const button = event.target.closest('[data-run-id]');
        if (button) loadDetail(button.dataset.runId);
    });
    nodes.prev.addEventListener('click', async () => {
        if (state.page <= 1) return;
        state.page -= 1;
        await loadHistory();
    });
    nodes.next.addEventListener('click', async () => {
        if (state.page >= state.pages) return;
        state.page += 1;
        await loadHistory();
    });

    loadHistory({ openFirst: true });
})();
