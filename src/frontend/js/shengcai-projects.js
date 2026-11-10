/* Persisted Shengcai list cards. Remote text is always assigned through textContent. */
(() => {
    const grid = document.getElementById('catalog-grid');
    if (!grid) return;
    const search = document.getElementById('catalog-search');
    const sort = document.getElementById('catalog-sort');
    const publishedWithin = document.getElementById('catalog-published-within');
    const pageSize = document.getElementById('catalog-page-size');
    const filters = {platform: document.getElementById('filter-platform'), monetize: document.getElementById('filter-monetize'), crowd: document.getElementById('filter-crowd')};
    const count = document.getElementById('result-count');
    const previous = document.getElementById('prev-page');
    const next = document.getElementById('next-page');
    const dialog = document.getElementById('import-dialog');
    const fileInput = document.getElementById('import-file');
    const importStatus = document.getElementById('import-status');
    const importButton = document.getElementById('confirm-import');
    const closeButton = document.getElementById('close-import');
    let page = 1;
    let controller = null;
    let timer = null;
    let importing = false;
    let facetsReady = false;
    const base = () => `${window.API_BASE_URL || '/api/v1'}/shengcai-projects`;
    function node(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    }
    function headers() {
        const token = localStorage.getItem('access_token');
        return token ? {Authorization: `Bearer ${token}`} : {};
    }
    async function request(path, options = {}) {
        const response = await fetch(base() + path, {...options, headers: headers()});
        let result;
        try { result = await response.json(); }
        catch { throw new Error('服务暂时不可用，请稍后重试。'); }
        if (!response.ok || !result.success) throw new Error(result.error || result.message || '请求失败');
        return result.data;
    }
    function format(value) { return typeof value === 'number' ? value.toLocaleString('zh-CN') : '—'; }
    function date(value) {
        if (!value) return '—';
        return new Date(value).toLocaleDateString('zh-CN', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'});
    }
    function coverUrl(value) {
        try {
            const url = new URL(value);
            return url.protocol === 'https:' && url.hostname === 'search01.shengcaiyoushu.com' ? url.href : null;
        } catch { return null; }
    }
    function card(item) {
        const article = node('article', 'project-card');
        const detailUrl = `https://scys.com/money-ideas/detail/${encodeURIComponent(String(item.id))}`;
        function detailLink(text, className) {
            const link = node('a', className, text);
            link.href = detailUrl; link.target = '_blank'; link.rel = 'noopener noreferrer';
            link.setAttribute('aria-label', `查看原文：${item.name}（新标签页打开）`);
            return link;
        }
        const cover = node('div', 'project-cover');
        cover.append(node('span', 'project-cover-fallback', item.name.slice(0, 6)));
        const imageUrl = coverUrl(item.miniCoverImage || item.coverImage);
        if (imageUrl) {
            const image = node('img'); image.src = imageUrl; image.alt = ''; image.loading = 'lazy'; image.referrerPolicy = 'no-referrer';
            image.addEventListener('error', () => image.remove(), {once: true}); cover.append(image);
        }
        const platforms = item.platformMenus || [];
        if (platforms.length) cover.append(node('span', 'platform-tag', platforms.map(t => t.name).join(' · ')));
        const content = node('div', 'project-content');
        const title = node('h2'); title.append(detailLink(item.name, 'project-title-link'));
        content.append(title, node('p', 'project-summary', item.summary || '来源暂未提供项目简介'));
        if (item.highlightText) content.append(node('p', 'project-highlights', item.highlightText.split(/[|｜]/).filter(Boolean).join(' · ')));
        const tags = node('div', 'project-tags');
        for (const tag of item.monetizeMenus || []) tags.append(node('span', '', tag.name));
        for (const tag of item.crowdMenus || []) tags.append(node('span', 'crowd-tag', tag.name));
        content.append(tags);
        const numbers = node('div', 'project-numbers');
        const income = item.incomeMin != null && item.incomeMax != null ? `${format(item.incomeMin)}–${format(item.incomeMax)}` : '未提供';
        for (const [label, value] of [['来源收益区间', income], ['预计成本', format(item.estimatedCost)], ['每日时间', item.dailyMinutes != null ? `${format(item.dailyMinutes)} 分钟` : '—']]) {
            const cell = node('div'); cell.append(node('span', '', label), node('strong', '', value)); numbers.append(cell);
        }
        content.append(numbers);
        const footer = node('div', 'project-footer');
        footer.append(node('span', '', `案例 ${format(item.caseCount)} · 资源 ${format(item.resourceCount)}`), node('span', '', `${date(item.gmtPublish ? item.gmtPublish * 1000 : null)} · ♡ ${format(item.likeCount)}`));
        footer.append(detailLink('查看原文 ↗', 'project-source-link'));
        article.append(cover, content, footer); return article;
    }
    function fillFacets(data) {
        for (const [key, field, label] of [['platform', 'platforms', '全部平台'], ['monetize', 'monetizations', '全部方式'], ['crowd', 'crowds', '全部人群']]) {
            const select = filters[key]; const selected = select.value;
            select.replaceChildren(new Option(label, ''));
            for (const tag of data.facets[field]) select.add(new Option(`${tag.name} (${tag.count})`, tag.name));
            if (Array.from(select.options).some(o => o.value === selected)) select.value = selected;
        }
        facetsReady = true;
    }
    async function load() {
        if (controller) controller.abort();
        const active = new AbortController(); controller = active;
        previous.disabled = true; next.disabled = true;
        grid.setAttribute('aria-busy', 'true'); count.textContent = '正在读取项目…';
        if (!grid.children.length) grid.append(node('div', 'catalog-message', '正在加载已保存的项目列表…'));
        const params = new URLSearchParams({name: search.value.trim(), published_within: publishedWithin.value, sort: sort.value, secondary_sort: 'newest', page: String(page), per_page: pageSize.value});
        for (const [key, element] of Object.entries(filters)) if (element.value) params.set(key, element.value);
        try {
            const data = await request(`?${params}`, {signal: active.signal});
            if (active !== controller) return;
            if (!facetsReady) fillFacets(data);
            document.getElementById('metric-total').textContent = format(data.catalog_total);
            document.getElementById('metric-platforms').textContent = format(data.stats.platforms);
            document.getElementById('metric-monetizations').textContent = format(data.stats.monetizations);
            document.getElementById('metric-imported').textContent = date(data.last_imported_at);
            count.textContent = `共 ${format(data.total)} 个项目${data.total !== data.catalog_total ? ` · 全库 ${format(data.catalog_total)} 个` : ''}`;
            if (!data.items.length) {
                const empty = node('div', 'catalog-message', data.catalog_total ? '没有符合条件的项目，试试其他关键词或重置筛选。' : '项目库还没有数据，点击「导入列表」保存第一份 JSON。');
                grid.replaceChildren(empty);
            } else grid.replaceChildren(...data.items.map(card));
            previous.disabled = page <= 1; next.disabled = !data.has_next;
            document.getElementById('page-label').textContent = `${page} / ${Math.max(1, Math.ceil(data.total / data.per_page))}`;
        } catch (error) {
            if (error.name === 'AbortError' || active !== controller) return;
            count.textContent = '读取失败';
            const message = node('div', 'catalog-message'); message.append(node('p', '', error.message));
            const retry = node('button', 'catalog-button', '重新加载'); retry.type = 'button'; retry.addEventListener('click', load); message.append(retry); grid.replaceChildren(message);
        } finally { if (active === controller) grid.setAttribute('aria-busy', 'false'); }
    }
    function changed() { page = 1; load(); }
    search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(changed, 300); });
    sort.addEventListener('change', changed);
    publishedWithin.addEventListener('change', changed);
    pageSize.addEventListener('change', changed);
    for (const element of Object.values(filters)) element.addEventListener('change', changed);
    document.getElementById('clear-filters').addEventListener('click', () => {
        clearTimeout(timer); search.value = ''; sort.value = 'income_max'; publishedWithin.value = '30'; pageSize.value = '48';
        for (const element of Object.values(filters)) element.value = '';
        changed();
    });
    previous.addEventListener('click', () => { if (page > 1) { page -= 1; load(); } });
    next.addEventListener('click', () => { page += 1; load(); });
    document.getElementById('open-import').addEventListener('click', () => dialog.showModal());
    closeButton.addEventListener('click', () => { if (!importing) dialog.close(); });
    dialog.addEventListener('cancel', event => { if (importing) event.preventDefault(); });
    importButton.addEventListener('click', async () => {
        if (importing) return;
        const file = fileInput.files[0];
        if (!file || file.size > 5 * 1024 * 1024) { importStatus.textContent = file ? '文件不能超过 5 MiB。' : '请先选择 JSON 文件。'; return; }
        importing = true; importButton.disabled = true; closeButton.disabled = true; fileInput.disabled = true;
        importStatus.textContent = '正在校验并保存到数据库，请勿关闭页面…';
        try {
            const form = new FormData(); form.append('file', file);
            const result = await request('/import', {method: 'POST', body: form});
            importStatus.textContent = `导入完成：新增 ${result.new}，更新 ${result.updated}，重复 ${result.unchanged}。`;
            facetsReady = false; page = 1; await load();
        } catch (error) { importStatus.textContent = error.message; }
        finally { importing = false; importButton.disabled = false; closeButton.disabled = false; fileInput.disabled = false; }
    });
    load();
})();
