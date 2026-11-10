(() => {
    const listRoot = document.getElementById('analysis-list');
    const reader = document.getElementById('analysis-reader');
    const search = document.getElementById('analysis-search');
    if (!listRoot || !reader || !search) return;

    let items = [];
    let selectedId = '';
    let loadSequence = 0;

    const apiBase = () => `${window.API_BASE_URL || '/api/v1'}/voyage-analysis`;
    const authHeaders = () => {
        const token = localStorage.getItem('access_token');
        return token ? {Authorization: `Bearer ${token}`} : {};
    };
    async function request(path = '') {
        const response = await fetch(`${apiBase()}${path}`, {
            headers: authHeaders(),
            credentials: 'include',
        });
        const result = await response.json().catch(() => null);
        if (!response.ok || !result?.success) {
            const error = result?.error;
            throw new Error(typeof error === 'string' ? error : error?.message || '读取失败，请稍后重试。');
        }
        return result.data;
    }
    const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]);

    function inlineMarkdown(value) {
        const pattern = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|\*\*([^*]+)\*\*|`([^`]+)`/g;
        let output = '';
        let cursor = 0;
        for (const match of value.matchAll(pattern)) {
            output += escapeHtml(value.slice(cursor, match.index));
            if (match[1] !== undefined) {
                let safeUrl = '';
                try {
                    const url = new URL(match[2]);
                    if (url.protocol === 'https:' || url.protocol === 'http:') safeUrl = url.href;
                } catch { /* Omit malformed links and keep the label as text. */ }
                output += safeUrl
                    ? `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(match[1])}</a>`
                    : escapeHtml(match[1]);
            } else if (match[3] !== undefined) {
                output += `<strong>${escapeHtml(match[3])}</strong>`;
            } else {
                output += `<code>${escapeHtml(match[4])}</code>`;
            }
            cursor = match.index + match[0].length;
        }
        return output + escapeHtml(value.slice(cursor));
    }

    function plainMarkdown(value) {
        return value.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/[*`_]/g, '').trim();
    }

    function renderMarkdown(markdown) {
        const output = [];
        const headings = [];
        let paragraph = [];
        let listType = '';
        let sectionNumber = 0;
        const flushParagraph = () => {
            if (!paragraph.length) return;
            output.push(`<p>${paragraph.map(inlineMarkdown).join('<br>')}</p>`);
            paragraph = [];
        };
        const closeList = () => {
            if (!listType) return;
            output.push(`</${listType}>`);
            listType = '';
        };
        const closeBlocks = () => { flushParagraph(); closeList(); };

        for (const rawLine of markdown.split(/\r?\n/)) {
            const line = rawLine.trim();
            if (!line || /^分析日期：/.test(line) || /^状态：/.test(line)) {
                closeBlocks();
                continue;
            }
            const heading = /^(#{1,4})\s+(.+)$/.exec(line);
            if (heading) {
                closeBlocks();
                const level = heading[1].length;
                if (level === 1) continue;
                const title = heading[2].replace(/\s+#+$/, '').trim();
                if (level === 2) {
                    sectionNumber += 1;
                    const id = `analysis-section-${sectionNumber}`;
                    headings.push({id, title: plainMarkdown(title)});
                    output.push(`<h2 id="${id}">${inlineMarkdown(title)}</h2>`);
                } else {
                    output.push(`<h${level}>${inlineMarkdown(title)}</h${level}>`);
                }
                continue;
            }
            if (/^---+$/.test(line)) {
                closeBlocks(); output.push('<hr>'); continue;
            }
            const bullet = /^[-*+]\s+(.+)$/.exec(line);
            const ordered = /^\d+[.)]\s+(.+)$/.exec(line);
            if (bullet || ordered) {
                flushParagraph();
                const nextType = bullet ? 'ul' : 'ol';
                if (listType && listType !== nextType) closeList();
                if (!listType) { output.push(`<${nextType}>`); listType = nextType; }
                output.push(`<li>${inlineMarkdown((bullet || ordered)[1])}</li>`);
                continue;
            }
            closeList();
            paragraph.push(line);
        }
        closeBlocks();
        return {html: output.join(''), headings};
    }

    function renderList() {
        const query = search.value.trim().toLocaleLowerCase();
        const visible = items.filter(item => `${item.title} ${item.summary}`.toLocaleLowerCase().includes(query));
        document.getElementById('visible-total').textContent = String(visible.length);
        listRoot.replaceChildren();
        if (!visible.length) {
            const empty = document.createElement('p');
            empty.className = 'voyage-empty';
            empty.textContent = items.length ? '没有匹配的项目，试试其他关键词。' : '当前还没有已保存的项目分析。';
            listRoot.append(empty);
            return;
        }
        for (const item of visible) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = `voyage-list-item${item.id === selectedId ? ' is-active' : ''}`;
            button.setAttribute('aria-current', item.id === selectedId ? 'true' : 'false');
            button.addEventListener('click', () => selectItem(item.id, true));

            const top = document.createElement('span'); top.className = 'voyage-item-top';
            const title = document.createElement('span'); title.className = 'voyage-item-title'; title.textContent = item.title;
            const date = document.createElement('span'); date.className = 'voyage-item-date'; date.textContent = item.analysis_date || '';
            top.append(title, date);
            button.append(top);
            if (item.summary) {
                const summary = document.createElement('span'); summary.className = 'voyage-item-summary'; summary.textContent = item.summary;
                button.append(summary);
            }
            if (item.status) {
                const status = document.createElement('span'); status.className = 'voyage-item-status'; status.textContent = item.status;
                button.append(status);
            }
            listRoot.append(button);
        }
    }

    function renderDetail(item) {
        const rendered = renderMarkdown(item.content || '');
        const toc = rendered.headings.length > 1
            ? `<nav class="voyage-toc" aria-label="本文目录">${rendered.headings.map(section => `<a href="#${section.id}">${escapeHtml(section.title)}</a>`).join('')}</nav>`
            : '';
        const meta = [item.analysis_date, item.status].filter(Boolean).map(escapeHtml);
        reader.innerHTML = `
            <header class="voyage-reader-head">
                <div class="voyage-reader-overline"><span>航海项目</span>${meta.map(value => `<span>${value}</span>`).join('')}</div>
                <h2 class="voyage-reader-title">${escapeHtml(item.title)}</h2>
                ${item.summary ? `<p class="voyage-reader-summary">${escapeHtml(item.summary)}</p>` : ''}
                ${toc}
            </header>
            <div class="voyage-document">${rendered.html || '<p>这份分析暂时没有正文。</p>'}</div>`;
        reader.setAttribute('aria-busy', 'false');
    }

    async function selectItem(id, updateHash = false) {
        if (!items.some(item => item.id === id)) return;
        selectedId = id;
        renderList();
        const sequence = ++loadSequence;
        reader.setAttribute('aria-busy', 'true');
        reader.innerHTML = '<div class="voyage-reader-message">正在读取这份项目分析…</div>';
        if (updateHash) history.replaceState(null, '', `#${encodeURIComponent(id)}`);
        try {
            const detail = await request(`/${encodeURIComponent(id)}`);
            if (sequence !== loadSequence) return;
            renderDetail(detail);
        } catch (error) {
            if (sequence !== loadSequence) return;
            reader.innerHTML = `<div class="voyage-reader-message"><p class="voyage-error">${escapeHtml(error.message)}</p></div>`;
            reader.setAttribute('aria-busy', 'false');
        }
    }

    async function start() {
        try {
            const data = await request();
            items = Array.isArray(data.items) ? data.items : [];
            document.getElementById('analysis-total').textContent = String(data.total ?? items.length);
            document.getElementById('analysis-updated').textContent = items.length ? '来自数据库的持久化档案' : '暂无已写入数据库的档案';
            listRoot.setAttribute('aria-busy', 'false');
            renderList();
            if (items.length) {
                let hashId = '';
                try { hashId = decodeURIComponent(location.hash.slice(1)); } catch { /* Ignore malformed hashes. */ }
                const firstId = items.some(item => item.id === hashId) ? hashId : items[0].id;
                await selectItem(firstId);
            } else {
                reader.innerHTML = '<div class="voyage-reader-message">当前数据库还没有项目分析记录。将 Markdown 更新到索引后，运行导入脚本即可写入。</div>';
                reader.setAttribute('aria-busy', 'false');
            }
        } catch (error) {
            listRoot.setAttribute('aria-busy', 'false');
            document.getElementById('analysis-updated').textContent = '读取失败';
            listRoot.innerHTML = `<p class="voyage-error">${escapeHtml(error.message)}</p>`;
            reader.innerHTML = '<div class="voyage-reader-message">档案暂时无法读取，请检查登录状态或稍后重试。</div>';
            reader.setAttribute('aria-busy', 'false');
        }
    }

    search.addEventListener('input', renderList);
    document.addEventListener('keydown', event => {
        if (event.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
            event.preventDefault(); search.focus();
        }
    });
    start();
})();
