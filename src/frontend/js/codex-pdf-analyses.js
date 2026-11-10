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

    function disclosure(title, body, note = '', count = '', group = '') {
        return `<details class="report-disclosure"${group ? ` name="${escapeHtml(group)}"` : ''}><summary>
            <span class="disclosure-label"><strong>${escapeHtml(title)}</strong>${note ? `<small>${escapeHtml(note)}</small>` : ''}</span>
            ${count ? `<span class="count-pill">${escapeHtml(count)}</span>` : ''}
            <i class="fa-solid fa-chevron-down" aria-hidden="true"></i>
        </summary><div class="disclosure-content">${body}</div></details>`;
    }

    const sectionNumber = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 12 });
    const pdfColumns = ['PDF 文件名', '机构', '报告日候选', '上传时间', '下载', '状态'];
    const pdfCells = (source) => [source?.name, source?.institution, source?.report_date_text,
        source?.upload_time_text, source?.download_count, source?.status];

    function sectionTable(title, columns, rows) {
        return `<div class="table-wrap section-table" tabindex="0" aria-label="${escapeHtml(title)}"><table class="report-table">
            <thead><tr>${columns.map((cell) => `<th scope="col"${cell === 'PDF 文件名' ? ' class="pdf-column"' : ''}>${escapeHtml(cell)}</th>`).join('')}</tr></thead>
            <tbody>${rows.map((row) => `<tr>${row.map((cell) => typeof cell === 'number'
                ? `<td class="num">${escapeHtml(sectionNumber.format(cell))}</td>`
                : `<td>${escapeHtml(cell ?? '—')}</td>`).join('')}</tr>`).join('')}</tbody>
        </table></div>`;
    }

    function sourceTable(title, entries) {
        return sectionTable(title, [...pdfColumns, '用途', '证据说明'], entries.map((link) =>
            [...pdfCells(link.source), roleLabel(link.source_role), link.evidence_note]));
    }

    function renderBlocks(blocks, rankings) {
        const html = [];
        let cards = [];
        let cardKind = '';
        function flush() {
            if (cards.length) html.push(`<div class="section-card-grid ${cardKind}">${cards.join('')}</div>`);
            cards = [];
        }
        for (const block of blocks) {
            const title = escapeHtml(block.title);
            if (['metric', 'finding', 'limitation'].includes(block.kind)) {
                if (cardKind !== block.kind) flush();
                cardKind = block.kind;
                cards.push(block.kind === 'metric'
                    ? `<div class="metric-card"><span>${title}</span><strong>${formatNumber(block.value)} <small>${escapeHtml(block.unit)}</small></strong></div>`
                    : `<article class="section-point"><h4>${title}</h4><p>${escapeHtml(block.text)}</p></article>`);
                continue;
            }
            flush();
            if (block.kind === 'heading') html.push(`<h4 class="section-subtitle">${title}</h4>`);
            if (block.kind === 'table') {
                const table = sectionTable(block.title, block.columns, block.rows);
                html.push(block.columns.includes('PDF 文件名') || block.rows.length > 6
                    ? disclosure(block.title, table, '', String(block.rows.length))
                    : `<div class="section-table-block"><h4 class="section-subtitle">${title}</h4>${table}</div>`);
            }
            if (block.kind === 'ranking') {
                const rows = rankings.filter((row) => row.ranking_type === block.ranking_type);
                const groups = new Map();
                rows.forEach((row) => {
                    const topic = row.topic_name || '';
                    if (!groups.has(topic)) groups.set(topic, []);
                    groups.get(topic).push(row);
                });
                html.push(disclosure(block.title, [...groups].map(([topic, items]) =>
                    `${topic ? `<h4 class="section-subtitle">${escapeHtml(topic)}</h4>` : ''}
                    ${sectionTable(topic || block.title, ['排名', ...pdfColumns],
                        items.map(({ rank, source }) => [rank, ...pdfCells(source)]))}`).join(''), '', String(rows.length)));
            }
        }
        flush();
        return html.join('');
    }

    function renderSections(sections, rankings = []) {
        return sections?.map((section) => disclosure(section.title, `
            <div class="structured-section">${section.blocks?.length
                ? renderBlocks(section.blocks, rankings)
                : `<p class="section-prose">${escapeHtml(section.body_text)}</p>`}
            ${section.sources?.length ? disclosure('引用与取材', sourceTable('引用与取材', section.sources),
                '', String(section.sources.length)) : ''}</div>
        `, section.summary_text, '', 'analysis-chapters')).join('') || '';
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
                        ${disclosure([rankingLabel(type), topic].filter(Boolean).join(' · '), `<div class="evidence-list">${rows.map((row) => `
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
                            `).join('')}</div>`, '', String(rows.length))}
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
                        ${candidate.sources?.length ? disclosure('查看引用', `<div class="candidate-source-list">${candidate.sources.map((link) => `
                            <article class="candidate-source">
                                <strong>[${escapeHtml(roleLabel(link.source_role))}] ${escapeHtml(link.source?.name || '')}</strong>
                                <p>${escapeHtml(link.selection_reason)}</p>
                                <div class="source-meta"><span>${escapeHtml(link.source?.institution || '机构未提供')}</span><span>报告日 ${escapeHtml(link.source?.report_date_text || '未提供')}</span><span>下载 ${formatNumber(link.source?.download_count)}</span></div>
                            </article>
                        `).join('')}</div>`, '', String(candidate.sources.length)) : ''}
                    </article>
                `).join('')}</div>
            </section>
        `;
    }

    function renderSourceIndex(sources) {
        if (!sources?.length) return '';
        return `
            <section class="content-section">
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

    function renderOverview(detail, run) {
        const primary = detail.candidates?.find((candidate) => candidate.is_primary);
        const themes = (detail.topics || []).filter((topic) => topic.classification_scope === 'multi')
            .sort((a, b) => a.rank - b.rank).slice(0, 3);
        const max = Math.max(1, ...themes.map((theme) => theme.pdf_count));
        return `
            ${primary ? `<article class="lead-card">
                <span class="eyebrow"><i class="fa-solid fa-lightbulb" aria-hidden="true"></i> 本期主推</span>
                <h3>${escapeHtml(primary.title)}</h3>
                <p>${escapeHtml(primary.rationale)}</p>
                <button type="button" class="archive-action" data-show-tab="candidates">查看选题与引用 <span aria-hidden="true">→</span></button>
            </article>` : ''}
            <div class="overview-grid">
                <section class="overview-card"><h3>报告摘要</h3><p class="summary">${escapeHtml(run.overall_summary || '暂无摘要')}</p></section>
                ${themes.length ? `<section class="overview-card"><h3>主要研究主题</h3>
                    <p class="chart-caption">按 PDF 数量 · 标题多标签</p>
                    ${themes.map((theme) => `<div class="theme-row">
                        <span>${escapeHtml(theme.topic_name)}</span><strong>${formatNumber(theme.pdf_count)}</strong>
                        <span class="theme-track" aria-hidden="true"><span style="width:${Math.min(100, Math.max(0, theme.pdf_count / max * 100))}%"></span></span>
                    </div>`).join('')}
                </section>` : ''}
            </div>`;
    }

    function initSourceSearch(panel, sources) {
        const input = panel.querySelector('input');
        const list = panel.querySelector('.source-results');
        const info = panel.querySelector('.source-count');
        const prev = panel.querySelector('[data-source-prev]');
        const next = panel.querySelector('[data-source-next]');
        const pageSize = 10;
        let page = 0;
        function update() {
            const query = input.value.trim().toLowerCase();
            const matches = sources.filter((source) => [source.name, source.institution].some((value) =>
                String(value || '').toLowerCase().includes(query)));
            const start = page * pageSize;
            list.innerHTML = matches.length ? renderSourceIndex(matches.slice(start, start + pageSize)) :
                '<p class="empty-state">没有匹配的引用</p>';
            info.textContent = matches.length ? `${start + 1}–${Math.min(start + pageSize, matches.length)} / ${matches.length} 份` : '0 份';
            prev.disabled = page === 0;
            next.disabled = start + pageSize >= matches.length;
        }
        input.addEventListener('input', () => { page = 0; update(); });
        prev.addEventListener('click', () => { if (!prev.disabled) { page--; update(); } });
        next.addEventListener('click', () => { if (!next.disabled) { page++; update(); } });
        update();
    }

    function renderReport(detail) {
        const run = detail.run || {};
        const title = run.source_filename || 'PDF 分析报告';
        const sources = detail.sources || [];
        const tabs = [
            ['overview', '概览', () => renderOverview(detail, run)],
            ['sections', '分析正文', () => renderSections(detail.sections, detail.rankings)],
            ['data', '统计与排行', () => `
                ${detail.topics?.length ? disclosure('主题统计', renderTopics(detail.topics)) : ''}
                ${detail.targets?.length ? disclosure('标的聚合', renderTargets(detail.targets)) : ''}
                ${renderRankings(detail.rankings)}`],
            ['candidates', '选题池', () => renderCandidates(detail.candidates)],
            ['sources', '引用 PDF', () => `
                <div class="source-toolbar">
                    <label><span>搜索引用</span><input type="search" placeholder="按文件名或机构搜索"></label>
                    <div class="source-controls">
                        <button type="button" data-source-prev aria-label="上一页">‹</button>
                        <span class="source-count" role="status"></span>
                        <button type="button" data-source-next aria-label="下一页">›</button>
                    </div>
                </div><div class="source-results"></div>`],
        ];
        nodes.empty.hidden = true;
        nodes.content.hidden = false;
        nodes.content.innerHTML = `
            <header class="report-head">
                <span class="eyebrow">分析批次 · ${escapeHtml(formatDate(run.completed_at || run.created_at))}</span>
                <h2>${escapeHtml(title)}</h2>
                ${renderMetrics(run, detail)}
                ${disclosure('来源与数据说明', `<div class="report-tags">
                    ${[`导出 ${formatDate(run.source_exported_at)}`, run.contract_version, run.analysis_version,
                        `SHA-256 ${run.source_sha256 || run.source_sha256_prefix || '—'}`].filter(Boolean).map((tag) =>
                        `<span class="report-tag">${escapeHtml(tag)}</span>`).join('')}
                    </div>${run.data_quality_note ? `<p class="quality-note">${escapeHtml(run.data_quality_note)}</p>` : ''}`)}
            </header>
            <div class="report-tabs" role="tablist" aria-label="报告视图">
                ${tabs.map(([key, label]) => `<button type="button" role="tab" id="tab-${key}"
                    aria-controls="panel-${key}" aria-selected="false" tabindex="-1">${label}</button>`).join('')}
            </div>
            ${tabs.map(([key]) => `<div class="tab-panel" role="tabpanel" id="panel-${key}" aria-labelledby="tab-${key}" tabindex="0" hidden></div>`).join('')}
        `;
        const buttons = [...nodes.content.querySelectorAll('[role="tab"]')];
        const panels = [...nodes.content.querySelectorAll('[role="tabpanel"]')];
        const loaded = new Set();
        function select(index, focus = false) {
            const [key, , render] = tabs[index];
            const panel = panels[index];
            if (!loaded.has(key)) {
                panel.innerHTML = render() || '<p class="empty-state">本批次暂无此项内容</p>';
                if (key === 'sources') initSourceSearch(panel, sources);
                if (key === 'overview') panel.querySelector('[data-show-tab]')?.addEventListener('click', () => select(3, true));
                loaded.add(key);
            }
            buttons.forEach((button, i) => {
                button.setAttribute('aria-selected', String(i === index));
                button.tabIndex = i === index ? 0 : -1;
                panels[i].hidden = i !== index;
            });
            if (focus) buttons[index].focus();
        }
        buttons.forEach((button, index) => {
            button.addEventListener('click', () => select(index));
            button.addEventListener('keydown', (event) => {
                const destination = { ArrowLeft: (index + tabs.length - 1) % tabs.length,
                    ArrowRight: (index + 1) % tabs.length, Home: 0, End: tabs.length - 1 };
                if (!Object.hasOwn(destination, event.key)) return;
                event.preventDefault();
                select(destination[event.key], true);
            });
        });
        select(0);
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
