(() => {
    'use strict';
    const base = `${(window.API_BASE_URL || window.CONFIG?.API_BASE_URL || '/api/v1').replace(/\/$/, '')}/book-commerce`;
    const state = {tab: 'accounts', page: 1, total: 0, size: 20, preview: null, previewFile: null, previewKey: null, categoryOptions: [], categoryIds: new Set()};
    const $ = id => document.getElementById(id);
    const decisionLabel = value => ({pending:'待处理', saved:'已收藏', dismissed:'已忽略', to_research:'待研究'}[value] || value || '待处理');
    const qualityLabels = {exact:'精确值', rounded:'约数', lower_bound:'下限值', unknown:'未标注', missing_previous_day:'缺少前一日数据', missing:'缺失', invalid:'数据无效', regressed:'累计值回落', no_baseline:'缺少基准数据', different_series:'数据来源发生变化', imprecise_metric:'数值精度不足', missing_metric:'指标缺失', counter_decrease:'累计值回落', irregular_window:'统计间隔不规则'};
    const reasonLabels = {unknown_scope:'销量口径不明确', insufficient_metrics:'数据不足', no_sales_growth:'销量未增长', shares_below_threshold:'转发增长未达到门槛', account_mismatch:'账号不一致', window_mismatch:'统计时间不匹配', unverified_link:'商品关联未确认', new_video:'视频发布时间较新'};
    const scopeLabels = {account_listing:'账号橱窗', platform_listing:'平台商品', unknown:'未区分'};
    const kindLabels = {account:'账号概况', showcase:'商品动销', video:'视频互动'};
    const sourceLabels = {wechat_channels_screen_benchmark:'视频号页面采集', wechat_channels:'视频号采集'};
    const fieldLabels = {
        account_name:'对标账号', category:'所属赛道', location:'账号地区', bio:'账号简介', is_ai_content:'是否标注 AI 内容',
        business_date:'业务日期', observed_at:'采集时间', received_at:'接收时间', computed_at:'计算时间', start_date:'开始日期', end_date:'结束日期',
        total_posts:'作品总数', showcase_rating:'橱窗评分', buyers_count:'跟买人数', total_sales:'橱窗累计销量', repeat_customers:'回头客数', total_products:'在售商品数',
        cumulative_sales:'累计销量', sales_delta:'销量增长', shares_delta:'转发增长', likes:'累计点赞', delta_likes:'新增点赞', shares:'累计转发', delta_shares:'新增转发', comments:'累计评论', views:'播放量',
        listing_title:'商品名称', video_title:'视频标题', title:'名称', author:'作者', publisher:'出版社', price:'价格',
        metric_scope:'销量口径', kind:'数据类型', source:'数据来源', decision:'当前标记', note:'备注', current_status:'候选状态',
        window_start:'统计开始时间', window_end:'统计结束时间', previous:'上次观测', current:'本次观测', current_values:'当前观测值', deltas:'日度变化', value:'观测值', quality:'数据质量',
        delta:'变化量', eligible:'可用于计算', reason:'数据说明', reason_codes:'入选说明', scope:'销量范围', message:'说明', competing_video_count:'同期其他视频数',
        shares_threshold:'转发增长门槛', source_priority:'数据来源优先级', version:'版本号', status:'处理状态', error_code:'错误代码', created_at:'创建时间', updated_at:'更新时间', signed_delta:'带方向变化量',
        created_observations:'新增观测', duplicate_observations:'重复观测', account_count:'监测账号', listing_count:'橱窗商品', video_count:'对标视频', latest_observed_date:'最近数据日期'
    };
    const hiddenDetailFields = new Set(['id','run_id','generation_id','target_key','series_key','slot_key','batch_id','content_hash','selected_observation_id','previous_observation_id','selected_series','calculation_version','rule_version','evidence_run_id','owner_user_id','account_id','video_id','listing_id','account_listing_id','recommendation_id','decision_version']);
    const node = (tag, value, className) => {
        const element = document.createElement(tag);
        if (value != null) element.textContent = String(value);
        if (className) element.className = className;
        return element;
    };
    async function api(path, options = {}) {
        const token = localStorage.getItem('access_token');
        const headers = new Headers(options.headers || {});
        headers.set('Accept', 'application/json');
        if (token) headers.set('Authorization', `Bearer ${token}`);
        if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
        const response = await fetch(base + path, {...options, headers, credentials: 'include'});
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.success) throw new Error(result.message || `请求失败（${response.status}）`);
        return result.data;
    }
    function message(text) { $('notice').textContent = text || ''; }
    function button(label, action) { const el = node('button', label); el.type = 'button'; el.addEventListener('click', action); return el; }
    function entry(title, description, actions = []) {
        const row = node('article', null, 'entry'), body = node('div', null, 'entry-body'), tools = node('div', null, 'entry-actions');
        body.append(node('h3', title || '未命名'));
        for (const line of description) body.append(node('p', line));
        for (const action of actions) tools.append(action);
        row.append(body);
        if (actions.length) row.append(tools);
        return row;
    }
    function formatCount(value) {
        if (value === null || value === undefined || value === '') return '—';
        const text = String(value);
        if (/^-?\d+$/.test(text)) {
            try { return BigInt(text).toLocaleString('zh-CN'); } catch (_) { return text; }
        }
        const number = Number(value);
        return Number.isFinite(number) ? new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2}).format(number) : text;
    }
    function formatTimestamp(value) {
        if (!value) return '—';
        const date = new Date(value);
        if (!Number.isFinite(date.getTime())) return String(value);
        const parts = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23'}).formatToParts(date);
        const part = type => parts.find(item => item.type === type)?.value || '';
        return `${part('year')}-${part('month')}-${part('day')} ${part('hour')}:${part('minute')}`;
    }
    function formatDate(value) {
        if (!value) return '—';
        const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})$/);
        return match ? `${match[1]}年${Number(match[2])}月${Number(match[3])}日` : formatTimestamp(value);
    }
    function fieldLabel(key) {
        return fieldLabels[key] || String(key).replaceAll('_', ' ');
    }
    function qualityLabel(value) {
        if (value === true) return '可用';
        if (value === false) return '不可用';
        return qualityLabels[value] || reasonLabels[value] || (value ? String(value).replaceAll('_', ' ') : '未标注');
    }
    function valueLabel(key, value) {
        if (value === null || value === undefined || value === '') return '未提供';
        if (typeof value === 'boolean') return value ? '是' : '否';
        if (key === 'business_date' || key.endsWith('_date')) return formatDate(value);
        if (key.endsWith('_at') || key === 'computed_at') return formatTimestamp(value);
        if (key === 'decision') return decisionLabel(value);
        if (key === 'metric_scope' || key === 'scope') return scopeLabels[value] || String(value);
        if (key === 'kind') return kindLabels[value] || String(value);
        if (key === 'quality' || key === 'reason') return qualityLabel(value);
        if (key === 'status') return ({pending:'待处理', running:'计算中', completed:'已完成', failed:'失败', candidate:'候选', accepted:'已接收', committed:'已完成', rejected:'已拒绝'}[value] || String(value));
        if (typeof value === 'number' || (typeof value === 'string' && /^-?\d+(\.\d+)?$/.test(value))) return formatCount(value);
        return String(value);
    }
    function sourceLabel(seriesKey) {
        const source = String(seriesKey || '').split(':')[0];
        return sourceLabels[source] || '导入数据';
    }
    function categoryText(categories) {
        return (categories || []).map(category => category.path || category.name).filter(Boolean).join(' · ');
    }
    function openDetail(title, content) {
        $('detail-title').textContent = title;
        $('detail-body').replaceChildren(content);
        $('detail').showModal();
    }
    function appendDetailItems(parent, data, depth = 0, parentKey = '') {
        if (!data || typeof data !== 'object') return;
        const items = Object.entries(data).filter(([key]) => !hiddenDetailFields.has(key));
        const grid = node('div', null, 'detail-grid');
        let added = 0;
        for (const [key, value] of items) {
            if (value && typeof value === 'object' && !Array.isArray(value)) {
                const section = node('section', null, 'detail-section');
                section.append(node('h3', fieldLabel(key)));
                appendDetailItems(section, value, depth + 1, key);
                parent.append(section);
                continue;
            }
            if (Array.isArray(value)) {
                const item = node('div', null, 'detail-item');
                item.append(node('span', fieldLabel(key), 'detail-label'), node('span', value.map(entry => reasonLabels[entry] || qualityLabels[entry] || String(entry)).join('、') || '—', 'detail-value'));
                grid.append(item); added++; continue;
            }
            if (key === 'quality' || key === 'reason' || key === 'eligible') {
                const item = node('div', null, 'detail-item');
                item.append(node('span', fieldLabel(key), 'detail-label'), node('span', valueLabel(key, value), 'detail-value'));
                grid.append(item); added++; continue;
            }
            const item = node('div', null, 'detail-item');
            item.append(node('span', fieldLabel(key), 'detail-label'), node('span', parentKey === 'quality' ? qualityLabel(value) : valueLabel(key, value), 'detail-value'));
            grid.append(item); added++;
        }
        if (added) parent.append(grid);
        if (!items.length && depth === 0) parent.append(node('p', '暂无可展示的详情', 'empty'));
    }
    function detail(title, data) {
        const body = node('div');
        appendDetailItems(body, data);
        openDetail(title, body);
    }
    function showContext(data) {
        let text = data.run_id ? `已计算 ${formatDate(data.start_date)} 至 ${formatDate(data.end_date)} · 更新于 ${formatTimestamp(data.computed_at)}` : '尚未生成分析结果';
        if (data.is_stale) text += ' · 数据有更新，分析结果待重算';
        if (data.date_computed === false) text += ' · 所选日期尚未计算';
        $('context').textContent = text;
    }
    async function summary() {
        const data = await api('/dashboard');
        const items = [['监测账号', data.account_count], ['橱窗商品', data.listing_count], ['对标视频', data.video_count], ['最近数据日期', data.latest_observed_date || '—']];
        $('summary').replaceChildren(...items.map(([label, value]) => {
            const el = node('div', null, `metric${label === '最近数据日期' ? ' metric-date' : ''}`); el.append(node('span', label), node('strong', label === '最近数据日期' ? formatDate(value) : formatCount(value))); return el;
        }));
        if (!$('business-date').value && data.latest_observed_date) $('business-date').value = data.latest_observed_date;
        showContext(data);
    }
    async function history(resource, id, title) {
        try {
            const result = await api(`/${resource}/${encodeURIComponent(id)}/history?page_size=20`);
            const body = $('detail-body');
            body.replaceChildren(node('p', `最近显示 ${result.items.length} 条，共 ${formatCount(result.total)} 条；时间按北京时间展示。`, 'history-intro'));
            for (const item of result.items) {
                const article = node('article', null, 'history-card');
                const head = node('div', null, 'history-card-head'), heading = node('div');
                heading.append(node('h3', formatDate(item.business_date)), node('p', `采集时间 ${formatTimestamp(item.observed_at)}`));
                const kind = kindLabels[item.kind] || '观测记录';
                const scope = item.metric_scope ? ` · ${scopeLabels[item.metric_scope] || '未区分'}` : '';
                head.append(heading, node('span', `${kind}${scope} · ${sourceLabel(item.series_key)}`, 'history-source'));
                const values = node('div', null, 'history-values');
                for (const [key, value] of Object.entries(item.values || {})) {
                    const card = node('div', null, 'value-card'), row = node('div', null, 'value-row');
                    card.append(node('span', fieldLabel(key), 'value-label'));
                    row.append(node('span', valueLabel(key, value), 'value-number'), node('span', qualityLabel(item.quality?.[key]), `quality-chip${item.quality?.[key] === 'unknown' ? ' quality-muted' : ''}`));
                    card.append(row); values.append(card);
                }
                article.append(head, values); body.append(article);
            }
            if (!result.items.length) body.append(node('p', '暂无历史数据', 'empty'));
            $('detail-title').textContent = title;
            $('detail').showModal();
        } catch (error) { message(error.message); }
    }
    function candidateDetail(row) {
        const evidence = row.evidence || {};
        const body = node('div');
        body.append(node('h3', row.video_title || '视频'), node('p', `${row.account_name || '—'} · ${row.listing_title || '—'} · ${formatDate(row.business_date)}`));
        for (const [label, data] of [['商品累计销量', evidence.sales], ['视频累计转发', evidence.shares]]) {
            const section = node('section', null, 'detail-section');
            section.append(node('h3', label));
            const previous = data?.previous || {}, current = data?.current || {};
            const grid = node('div', null, 'detail-grid');
            for (const [labelText, point] of [['上次观测', previous], ['本次观测', current]]) {
                const card = node('div', null, 'detail-item');
                card.append(node('span', labelText, 'detail-label'), node('span', `${formatCount(point.value)} · ${formatTimestamp(point.observed_at)}`, 'detail-value'), node('span', qualityLabel(point.quality), 'quality-chip'));
                grid.append(card);
            }
            section.append(grid); body.append(section);
        }
        body.append(node('p', `同期还有 ${formatCount(evidence.competing_video_count)} 条视频满足条件。`, 'detail-note'));
        body.append(node('p', '以上是同期变化线索，不能证明这些销量由该视频带来。', 'detail-note'));
        openDetail('推荐依据', body);
    }
    async function updateDecision(row, status) {
        try {
            const note = prompt('可填写备注（留空表示无备注）', row.note || '');
            if (note === null) return;
            const id = row.recommendation_id || row.id;
            await api(`/recommendations/${encodeURIComponent(id)}/decision`, {method: 'PUT', body: JSON.stringify({decision: status, note: note || null, expected_version: row.decision_version ?? row.version ?? 0})});
            message('标记已保存'); await load();
        } catch (error) { message(error.message); }
    }
    function decisionActions(row) {
        return [button('收藏', () => updateDecision(row, 'saved')), button('待研究', () => updateDecision(row, 'to_research')),
                button('忽略', () => updateDecision(row, 'dismissed')), button('重置', () => updateDecision(row, 'pending'))];
    }
    async function list() {
        const date = $('business-date').value;
        const paths = {accounts: '/accounts', products: '/rankings/products', videos: '/rankings/videos', candidates: '/recommendations', decisions: '/decisions'};
        const params = new URLSearchParams({page: state.page, page_size: state.size});
        for (const categoryId of state.categoryIds) params.append('category_id', categoryId);
        if (date && ['products', 'videos', 'candidates'].includes(state.tab)) params.set('date', date);
        if (state.tab === 'products') params.set('scope', $('scope').value);
        if (state.tab === 'candidates') params.set('decision', $('decision-filter').value);
        const result = await api(`${paths[state.tab]}?${params}`);
        if ('run_id' in result) showContext(result);
        state.total = result.total;
        const titles = {accounts:'账号概览', products:'商品动销榜', videos:'视频互动榜', candidates:'推荐候选', decisions:'我的标记'};
        $('results-title').textContent = titles[state.tab] || '浏览结果';
        $('result-count').textContent = `共 ${formatCount(result.total)} 条`;
        const rows = result.items.map(row => {
            if (state.tab === 'accounts') {
                const snap = row.latest_snapshot, values = snap?.values || {};
                return entry(row.name, [`赛道：${categoryText(row.categories) || '未分类'} · ${row.location || '地区未记录'}`,
                    `${snap?.business_date ? `${formatDate(snap.business_date)}数据 · ` : ''}橱窗销量 ${formatCount(values.total_sales)} · 评分 ${valueLabel('showcase_rating', values.showcase_rating)} · 跟买 ${formatCount(values.buyers_count)}`],
                    [button('查看历史', () => history('accounts', row.id, row.name + ' · 历史记录'))]);
            }
            if (state.tab === 'products' || state.tab === 'videos') {
                const isProduct = state.tab === 'products';
                const delta = isProduct ? row.sales_delta : row.shares_delta;
                const countLabel = delta !== null && delta !== undefined && Number(delta) > 0 ? `+${formatCount(delta)}` : formatCount(delta);
                const trackText = categoryText(row.categories);
                const description = [`${row.account_name || (isProduct ? '平台商品范围' : '账号未记录')} · ${formatDate(row.business_date)}${trackText ? ` · 赛道：${trackText}` : ''}`,
                    `${isProduct ? '销量增长' : '新增转发'} ${countLabel} · 统计区间 ${formatTimestamp(row.window_start)} 至 ${formatTimestamp(row.window_end)}`];
                return entry(isProduct ? row.listing_title : row.video_title, description,
                    [button('查看依据', () => detail('增长依据', row)), ...(isProduct && row.account_listing_id ? [button('查看历史', () => history('account-listings', row.account_listing_id, '商品历史记录'))] : !isProduct && row.video_id ? [button('查看历史', () => history('videos', row.video_id, '视频历史记录'))] : [])]);
            }
            if (state.tab === 'candidates') return entry(row.video_title, [`${row.account_name || '—'} · ${row.listing_title || '—'} · ${formatDate(row.business_date)} · 赛道：${categoryText(row.categories) || '未分类'}`, `商品销量增长 +${formatCount(row.sales_delta)} · 视频转发增长 +${formatCount(row.shares_delta)}`, '同期增长是关联线索，不能证明成交归因。'], [button('查看依据', () => candidateDetail(row)), ...decisionActions(row)]);
            return entry(row.video_title, [`${row.account_name || '—'} · ${row.listing_title || '—'} · ${formatDate(row.business_date)} · 赛道：${categoryText(row.categories) || '未分类'}`, `${row.current_status} · 标记：${decisionLabel(row.decision)}${row.note ? ' · ' + row.note : ''}`], [button('查看记录', () => detail('标记详情', row)), ...(row.recommendation_id ? decisionActions(row) : [])]);
        });
        const content = $('content');
        if (rows.length) { const grid = node('div', null, 'entries-grid'); grid.append(...rows); content.replaceChildren(grid); }
        else content.replaceChildren(node('p', '当前筛选条件下暂无数据。可以调整赛道或日期后再查看。', 'empty'));
        $('pagination').hidden = false;
        $('page').textContent = `第 ${state.page} 页 · 共 ${state.total} 条`;
        $('prev').disabled = state.page <= 1;
        $('next').disabled = state.page * state.size >= state.total;
    }

    async function loadCategories() {
        const result = await api('/filters');
        state.categoryOptions = result.categories || [];
        const validIds = new Set(state.categoryOptions.map(category => category.id));
        state.categoryIds = new Set([...state.categoryIds].filter(id => validIds.has(id)));
        renderCategoryOptions();
        renderCategorySelection();
    }
    function renderCategoryOptions() {
        const query = $('category-search').value.trim().toLocaleLowerCase();
        const categories = state.categoryOptions.filter(category => !query || `${category.name} ${category.path}`.toLocaleLowerCase().includes(query));
        const options = $('category-options');
        if (!categories.length) {
            options.replaceChildren(node('p', '没有找到匹配的赛道', 'empty'));
            return;
        }
        const rows = categories.map(category => {
            const label = node('label', null, 'category-option');
            label.style.paddingLeft = `${7 + Math.min(category.depth || 0, 5) * 13}px`;
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox'; checkbox.checked = state.categoryIds.has(category.id);
            checkbox.dataset.categoryId = category.id;
            const copy = node('span', null, 'category-option-copy');
            copy.append(node('span', category.name, 'category-option-name'));
            if (category.parent_id) {
                const parentPath = category.path.slice(0, -(category.name.length + 3));
                copy.append(node('span', parentPath, 'category-option-path'));
            }
            label.append(checkbox, copy, node('span', `${formatCount(category.account_count)} 个账号`, 'category-option-count'));
            checkbox.addEventListener('change', () => {
                if (checkbox.checked) state.categoryIds.add(category.id);
                else state.categoryIds.delete(category.id);
                state.page = 1;
                renderCategorySelection();
                load();
            });
            return label;
        });
        options.replaceChildren(...rows);
    }
    function renderCategorySelection() {
        const selected = state.categoryOptions.filter(category => state.categoryIds.has(category.id));
        $('category-summary').textContent = selected.length ? `已选 ${selected.length} 个赛道` : '全部赛道';
        $('category-selection-count').textContent = selected.length ? `已选择 ${selected.length} 项 · 命中任一项` : '未选择赛道';
        const chips = selected.map(category => {
            const chip = node('span', null, 'selected-category');
            chip.title = category.path;
            chip.append(node('span', category.path));
            const remove = button('×', () => {
                state.categoryIds.delete(category.id);
                const checkbox = $('category-options').querySelector(`[data-category-id="${CSS.escape(category.id)}"]`);
                if (checkbox) checkbox.checked = false;
                state.page = 1;
                renderCategorySelection();
                load();
            });
            remove.setAttribute('aria-label', `移除赛道：${category.path}`);
            chip.append(remove);
            return chip;
        });
        $('selected-categories').replaceChildren(...chips);
    }
    function setCategoryMenu(open) {
        $('category-menu').hidden = !open;
        $('category-toggle').setAttribute('aria-expanded', String(open));
        if (open) $('category-search').focus();
    }
    function imports() {
        $('pagination').hidden = true;
        const box = node('div', null, 'form-card');
        box.append(node('h2', '导入数据'), node('p', '选择数据文件后先预览。文件有冲突或不合格记录时，整批不会写入。'));
        const file = document.createElement('input'); file.type = 'file'; file.accept = '.json,application/json';
        file.addEventListener('change', () => {state.preview = null; state.previewFile = null; state.previewKey = null; confirm.disabled = true;});
        const output = node('p', '尚未预览');
        const preview = button('预览文件', async () => {
            if (!file.files[0]) { message('请选择文件'); return; }
            try { const body = new FormData(); body.set('file', file.files[0]); const result = await api('/observations/preview', {method:'POST', body}); state.preview = result; state.previewFile = file.files[0]; state.previewKey = crypto.randomUUID(); output.textContent = `新增观测 ${result.created_observations} 条，重复 ${result.duplicate_observations} 条。`; confirm.disabled = false; message('预览完成'); }
            catch (error) { state.preview = null; confirm.disabled = true; output.textContent = error.message; message('预览失败'); }
        });
        const confirm = button('确认导入', async () => {
            if (!state.preview || file.files[0] !== state.previewFile) return;
            confirm.disabled = true;
            try { const body = new FormData(); body.set('file', state.previewFile); body.set('payload_hash', state.preview.payload_hash); body.set('expected_revision', state.preview.input_revision); body.set('idempotency_key', state.previewKey); const result = await api('/observations/commit', {method:'POST', body}); output.textContent = `导入完成：新增 ${result.created_observations} 条，重复 ${result.duplicate_observations} 条。`; state.preview = null; message('导入完成，请计算需要查看的日期'); await summary(); await loadCategories(); }
            catch (error) { message(error.message); confirm.disabled = false; }
        });
        confirm.disabled = true;
        box.append(file, node('p', ''), preview, document.createTextNode(' '), confirm, output, node('h2', '计算增长与候选'), node('p', '选择连续日期范围，最长 31 天；计算结果用于榜单和候选池。'));
        const start = document.createElement('input'); start.type = 'date'; start.value = $('business-date').value;
        const end = document.createElement('input'); end.type = 'date'; end.value = $('business-date').value;
        let calculationKey = null, calculationRange = '';
        const run = button('开始计算', async () => {
            try {
                run.disabled = true;
                const range = `${start.value}:${end.value}`;
                if (range !== calculationRange) { calculationRange = range; calculationKey = crypto.randomUUID(); }
                message('正在提交计算');
                let job = await api('/analysis-runs', {method:'POST', headers: {'Idempotency-Key': calculationKey}, body: JSON.stringify({start_date:start.value, end_date:end.value})});
                for (let attempts = 0; ['pending', 'running'].includes(job.status) && attempts < 90; attempts++) {
                    message(`计算${job.status === 'pending' ? '排队中' : '进行中'}，请稍候`);
                    await new Promise(resolve => setTimeout(resolve, 2000));
                    job = await api(`/analysis-runs/${encodeURIComponent(job.id)}`);
                }
                if (job.status === 'completed') { message('计算完成'); calculationRange = ''; await summary(); }
                else if (job.status === 'failed') { calculationRange = ''; throw new Error(job.error_code === 'stale_input' ? '计算期间数据发生变化，请重新计算' : '计算失败，请查看运行状态'); }
                else message('计算仍在进行，可稍后刷新查看');
            } catch (error) { message(error.message); } finally { run.disabled = false; }
        });
        box.append(node('label', '开始日期'), start, node('label', '结束日期'), end, node('p', ''), run);
        const rulesBox = node('section');
        rulesBox.append(node('h2', '候选规则'), node('p', '商品销量必须增长；视频新增转发需严格大于下列阈值。数据不足时不会自动推荐。'));
        const threshold = document.createElement('input'); threshold.type = 'number'; threshold.min = '0'; threshold.max = '1000000000'; threshold.step = '1'; threshold.disabled = true;
        const priority = document.createElement('input'); priority.type = 'text'; priority.maxLength = 2000; priority.placeholder = '多个来源使用英文逗号分隔'; priority.disabled = true;
        let rulesVersion = null;
        const saveRules = button('保存规则', async () => {
            const value = Number(threshold.value);
            if (!Number.isSafeInteger(value) || value < 0 || value > 1000000000) {message('请输入有效的转发阈值'); return;}
            try {
                saveRules.disabled = true;
                const sources = priority.value.split(',').map(x => x.trim()).filter(Boolean);
                const updated = await api('/rules', {method:'PATCH', body: JSON.stringify({expected_version:rulesVersion, shares_threshold:value, source_priority:sources})});
                rulesVersion = updated.version;
                message('规则已保存，数据需要重新计算'); await summary();
            } catch (error) { message(error.message); } finally { saveRules.disabled = false; }
        });
        saveRules.disabled = true;
        rulesBox.append(node('label', '新增转发阈值'), threshold, node('label', '来源优先级（可留空）'), priority, node('p', ''), saveRules);
        const batchBox = node('section');
        batchBox.append(node('h2', '最近导入记录'), node('p', '正在读取记录…'));
        box.append(rulesBox, batchBox);
        $('content').replaceChildren(box);
        api('/rules').then(rules => {rulesVersion = rules.version; threshold.value = String(rules.shares_threshold); priority.value = rules.source_priority.join(', '); threshold.disabled = false; priority.disabled = false; saveRules.disabled = false;}).catch(error => {rulesBox.append(node('p', error.message));});
        api('/batches?page_size=20').then(result => {
            batchBox.replaceChildren(node('h2', '最近导入记录'));
            if (!result.items.length) batchBox.append(node('p', '暂无导入记录'));
            for (const batch of result.items) {
                const status = {committed:'已完成', rejected:'已拒绝', failed:'失败', accepted:'处理中'}[batch.status] || batch.status;
                const counts = batch.counts || {};
                batchBox.append(entry(`${status} · ${sourceLabels[batch.source] || '导入数据'}`, [`接收时间：${formatTimestamp(batch.received_at)}`, `新增：${formatCount(counts.created_observations)} · 重复：${formatCount(counts.duplicate_observations)}${batch.error_summary?.message ? ' · ' + batch.error_summary.message : ''}`]));
            }
        }).catch(error => {batchBox.append(node('p', error.message));});
    }
    async function load() {
        const isImportTab = state.tab === 'imports';
        $('filters-panel').hidden = isImportTab;
        $('results-heading').hidden = isImportTab;
        try { if (isImportTab) imports(); else await list(); }
        catch (error) { $('content').replaceChildren(node('p', error.message, 'empty')); $('pagination').hidden = true; }
    }
    $('tabs').addEventListener('click', event => {
        const target = event.target.closest('button[data-tab]'); if (!target) return;
        state.tab = target.dataset.tab; state.page = 1;
        $('scope-label').hidden = state.tab !== 'products';
        $('decision-label').hidden = state.tab !== 'candidates';
        for (const el of $('tabs').querySelectorAll('button')) el.setAttribute('aria-selected', String(el === target));
        message(''); load();
    });
    $('load').addEventListener('click', () => {state.page = 1; load();});
    $('category-toggle').addEventListener('click', () => setCategoryMenu($('category-menu').hidden));
    $('category-search').addEventListener('input', renderCategoryOptions);
    $('category-clear').addEventListener('click', () => {
        state.categoryIds.clear();
        $('category-options').querySelectorAll('input[type="checkbox"]').forEach(checkbox => {checkbox.checked = false;});
        state.page = 1;
        renderCategorySelection();
        load();
    });
    document.addEventListener('click', event => {
        if (!$('category-filter').contains(event.target)) setCategoryMenu(false);
    });
    $('refresh').addEventListener('click', async () => {try {await summary(); await loadCategories(); await load();} catch(error) {message(error.message);}});
    $('prev').addEventListener('click', () => {if (state.page > 1) {state.page--; load();}});
    $('next').addEventListener('click', () => {if (state.page * state.size < state.total) {state.page++; load();}});
    $('close-detail').addEventListener('click', () => $('detail').close());
    (async () => {
        try {
            await window.pageAccessGuard;
            let user = null;
            try { user = JSON.parse(localStorage.getItem('user_info') || 'null'); } catch (_) { /* Invalid cached identity grants no admin controls. */ }
            if (user?.is_admin === true) document.querySelector('[data-tab="imports"]').hidden = false;
            await summary(); await loadCategories(); await load();
        } catch(error) {message(error.message);}
    })();
})();
