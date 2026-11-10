(() => {
    'use strict';
    const $ = id => document.getElementById(id);
    const hotspotStatuses = {pending: '待审', processed: '已采用', ignored: '忽略'};
    const postStatuses = {draft: '草稿待选', approved: '已选定', published: '已发布', rejected: '弃用'};
    const postTypes = {short_tweet: '犀利短推', thread: '长文 / 连推', card_text: '卡片文案'};
    let page = 1;
    let active = null;
    let listRequest = 0;
    let detailRequest = 0;
    let messageTimer;
    let dirtyForms = new Set();
    const platforms = {baidu: '百度热搜', douyin: '抖音', xiaohongshu: '小红书', x: 'X', reddit: 'Reddit', github: 'GitHub'};
    const categories = {finance: '财经商业', tech_ai: '科技 AI', society: '社会热点', entertainment: '娱乐', opensource: '开源'};
    const notify = message => {
        $('hot-message').textContent = message;
        clearTimeout(messageTimer);
        messageTimer = setTimeout(() => { $('hot-message').textContent = ''; }, 4500);
    };

    function element(tag, text) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function button(text, handler) {
        const node = element('button', text);
        node.type = 'button';
        node.addEventListener('click', handler);
        return node;
    }

    async function api(path, payload) {
        const token = localStorage.getItem('access_token');
        const headers = token ? {Authorization: 'Bearer ' + token} : {};
        const options = {headers};
        if (payload) {
            headers['Content-Type'] = 'application/json';
            options.method = 'PATCH';
            options.body = JSON.stringify(payload);
        }
        const response = await fetch((window.API_BASE_URL || '/api/v1') + path, options);
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.message || '请求失败（' + response.status + '）');
        return result;
    }

    function select(values, selected) {
        const node = element('select');
        for (const [key, name] of Object.entries(values)) node.add(new Option(name, key));
        node.value = selected;
        return node;
    }

    function label(text, control) {
        const node = element('label', text);
        node.append(control);
        return node;
    }

    async function copy(control) {
        try {
            await navigator.clipboard.writeText(control.value);
            notify('已复制正文');
        } catch {
            control.focus();
            control.select();
            notify('已选中正文，请使用系统复制快捷键。');
        }
    }

    function attachment(hotspot, compact = false) {
        const evidence = hotspot.evidence_image_url;
        const url = evidence
            ? (window.API_BASE_URL || '/api/v1') + evidence.slice('/api/v1'.length)
            : hotspot.cover_image_url;
        if (!url) return null;

        const figure = element('figure');
        figure.className = compact ? 'hotspot-image compact' : 'hotspot-image';
        const preview = element('img');
        preview.alt = evidence ? '证据截图' : '封面配图';
        preview.loading = 'lazy';
        preview.referrerPolicy = 'no-referrer';
        const fetchImage = () => {
            if (imageRequests.has(url)) return imageRequests.get(url);
            const token = evidence && localStorage.getItem('access_token');
            const loading = fetch(url, {
                headers: token ? {Authorization: 'Bearer ' + token} : {},
                referrerPolicy: 'no-referrer',
            }).then(response => {
                if (!response.ok) throw new Error('图片读取失败');
                return response.blob();
            }).catch(error => {
                imageRequests.delete(url);
                throw error;
            });
            if (imageRequests.size >= 60) imageRequests.delete(imageRequests.keys().next().value);
            imageRequests.set(url, loading);
            return loading;
        };
        const view = element('a');
        view.target = '_blank';
        view.rel = 'noopener noreferrer';
        view.setAttribute('aria-label', '查看原图');
        view.className = 'image-preview loading';
        const placeholder = element('span', '配图');
        placeholder.className = 'image-placeholder';
        view.append(placeholder, preview);
        preview.addEventListener('load', () => {
            view.classList.remove('loading', 'failed');
        });
        preview.addEventListener('error', () => {
            view.classList.remove('loading');
            view.classList.add('failed');
            placeholder.textContent = '图片不可用';
        });
        if (evidence) {
            const observer = new IntersectionObserver(entries => {
                if (!entries.some(entry => entry.isIntersecting)) return;
                observer.disconnect();
                imageObservers.delete(figure);
                fetchImage().then(blob => {
                    const objectUrl = URL.createObjectURL(blob);
                    if (!figure.isConnected) {
                        URL.revokeObjectURL(objectUrl);
                        return;
                    }
                    view.href = preview.src = objectUrl;
                    imagePreviews.set(figure, objectUrl);
                }).catch(() => {
                    if (figure.isConnected) {
                        view.classList.remove('loading');
                        view.classList.add('failed');
                        placeholder.textContent = '加载失败';
                        const retry = button('重试图片', () => {
                            imageRequests.delete(url);
                            const replacement = attachment(hotspot, compact);
                            figure.replaceWith(replacement);
                        });
                        figure.append(retry);
                    }
                });
            }, {rootMargin: '100px'});
            imageObservers.set(figure, observer);
            observer.observe(figure);
        } else {
            view.href = preview.src = url;
        }
        figure.append(view);
        if (compact) return figure;

        const controls = element('div');
        controls.className = 'actions';
        controls.append(button(evidence ? '复制证据图' : '复制封面图', async () => {
            try {
                if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') {
                    throw new Error('clipboard unavailable');
                }
                await navigator.clipboard.write([new ClipboardItem({'image/png': fetchImage().then(asPng)})]);
                notify('已复制图片，可直接粘贴到 X。');
            } catch {
                notify('当前浏览器或图片来源限制了复制，请下载图片。');
            }
        }));
        controls.append(button('下载图片', async () => {
            try {
                const blob = await fetchImage();
                const objectUrl = URL.createObjectURL(blob);
                const link = element('a');
                link.href = objectUrl;
                const extension = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp',
                    'image/gif': 'gif', 'image/avif': 'avif'}[blob.type] || 'image';
                link.download = hotspot.id + '.' + extension;
                link.click();
                setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);
            } catch {
                notify('无法直接下载，请点击缩略图打开原图后保存。');
            }
        }));
        figure.append(controls);
        return figure;
    }

    async function asPng(blob) {
        if (blob.type === 'image/png') return blob;
        const bitmap = await createImageBitmap(blob);
        try {
            const canvas = element('canvas');
            canvas.width = bitmap.width;
            canvas.height = bitmap.height;
            canvas.getContext('2d').drawImage(bitmap, 0, 0);
            return await new Promise((resolve, reject) => {
                canvas.toBlob(result => result ? resolve(result) : reject(new Error('PNG conversion failed')), 'image/png');
            });
        } finally {
            bitmap.close();
        }
    }

    const imageObservers = new Map();
    const imageRequests = new Map();
    const imagePreviews = new Map();

    function releasePreviews(container) {
        for (const [figure, observer] of imageObservers) {
            if (container.contains(figure)) {
                observer.disconnect();
                imageObservers.delete(figure);
            }
        }
        for (const [figure, url] of imagePreviews) {
            if (container.contains(figure)) {
                URL.revokeObjectURL(url);
                imagePreviews.delete(figure);
            }
        }
    }

    function badge(text, status) {
        const node = element('span', text);
        node.className = 'badge ' + status;
        return node;
    }

    function candidate(hotspot, post, index) {
        const card = element('article');
        card.className = 'candidate-card';
        const heading = element('div');
        heading.className = 'candidate-heading';
        const number = element('span', String(index + 1).padStart(2, '0'));
        number.className = 'candidate-number';
        let statusBadge = badge(postStatuses[post.publish_status], post.publish_status);
        heading.append(number, element('h3', postTypes[post.post_type] || post.post_type), statusBadge);
        card.append(heading);
        const image = attachment(hotspot, true);
        if (image) card.append(image);
        const preview = element('p');
        preview.className = 'candidate-preview';
        const cta = element('p');
        cta.className = 'candidate-cta';
        const tags = element('div');
        tags.className = 'tag-list';
        card.append(preview, cta, tags);

        const editor = element('form');
        editor.className = 'candidate-editor';
        editor.hidden = true;
        editor.setAttribute('aria-label', '编辑候选 ' + (index + 1));
        const fields = {};
        for (const [key, name, size] of [
            ['hook', '开头钩子', 255], ['content', '正文', 50000],
            ['call_to_action', '互动话术', 255], ['suggested_tags', '建议标签（每行一个）', 2000],
        ]) {
            const control = element(key === 'content' || key === 'suggested_tags' ? 'textarea' : 'input');
            control.name = key;
            control.maxLength = size;
            control.required = key === 'content';
            fields[key] = control;
            editor.append(label(name, control));
        }
        const status = select(postStatuses, post.publish_status);
        editor.append(label('发布状态', status));
        const save = element('button', '保存修改');
        save.type = 'submit';
        save.className = 'primary';
        const cancel = button('取消', () => {
            fillEditor();
            dirtyForms.delete(editor);
            editor.hidden = true;
            edit.setAttribute('aria-expanded', 'false');
        });
        const editorActions = element('div');
        editorActions.className = 'actions';
        editorActions.append(save, cancel);
        editor.append(editorActions);
        editor.addEventListener('input', () => dirtyForms.add(editor));
        editor.addEventListener('change', () => dirtyForms.add(editor));
        card.append(editor);

        const footer = element('div');
        footer.className = 'candidate-footer';
        const wordCount = element('span');
        wordCount.className = 'word-count';
        const edit = button('编辑文案', () => {
            editor.hidden = !editor.hidden;
            edit.setAttribute('aria-expanded', String(!editor.hidden));
            if (!editor.hidden) fields.content.focus();
        });
        edit.setAttribute('aria-expanded', 'false');
        const copyButton = button('复制正文', () => copy({value: editor.hidden ? post.content : fields.content.value,
            focus: () => { editor.hidden = false; fields.content.focus(); }, select: () => fields.content.select()}));
        copyButton.className = 'primary';
        footer.append(wordCount, edit, copyButton);
        card.append(footer);

        function render() {
            preview.textContent = post.content;
            cta.textContent = post.call_to_action || '';
            cta.hidden = !post.call_to_action;
            tags.replaceChildren(...post.suggested_tags.map(tag => element('span', tag)));
            wordCount.textContent = Array.from(post.content).length + ' 字符';
            const nextBadge = badge(postStatuses[post.publish_status], post.publish_status);
            statusBadge.replaceWith(nextBadge);
            statusBadge = nextBadge;
        }

        function fillEditor() {
            for (const [key, control] of Object.entries(fields)) {
                control.value = key === 'suggested_tags' ? post.suggested_tags.join('\n') : post[key] || '';
            }
            status.value = post.publish_status;
        }

        editor.onsubmit = async event => {
            event.preventDefault();
            save.disabled = cancel.disabled = true;
            const payload = Object.fromEntries(Object.entries(fields).map(([key, control]) => [key, control.value]));
            payload.suggested_tags = payload.suggested_tags.split('\n').map(tag => tag.trim()).filter(Boolean);
            payload.publish_status = status.value;
            const controls = [...editor.querySelectorAll('input, textarea, select')];
            controls.forEach(control => { control.disabled = true; });
            try {
                const result = await api('/hotspots/' + encodeURIComponent(hotspot.id) + '/posts/' + encodeURIComponent(post.id), payload);
                post = result.data;
                dirtyForms.delete(editor);
                render();
                editor.hidden = true;
                edit.setAttribute('aria-expanded', 'false');
                notify('文案与状态已保存');
            } catch (error) {
                notify(error.message);
            } finally {
                save.disabled = cancel.disabled = false;
                controls.forEach(control => { control.disabled = false; });
            }
        };
        render();
        fillEditor();
        return card;
    }

    async function show(id) {
        if (dirtyForms.size && !confirm('当前有未保存的修改。放弃修改并切换热点？')) return;
        dirtyForms.clear();
        const requestId = ++detailRequest;
        active = id;
        for (const item of $('hot-list').querySelectorAll('.hotspot-choice')) {
            item.setAttribute('aria-current', String(item.dataset.id === id));
        }
        const container = $('hot-detail');
        releasePreviews(container);
        container.setAttribute('aria-busy', 'true');
        const loading = element('div', '正在打开热点与候选文案…');
        loading.className = 'welcome-state';
        container.replaceChildren(loading);
        try {
            const {data} = await api('/hotspots/' + encodeURIComponent(id));
            if (requestId !== detailRequest) return;
            const hotspot = data.hotspot;
            const topic = element('section');
            topic.className = 'topic-summary';
            const meta = element('div');
            meta.className = 'topic-meta';
            meta.append(badge(platforms[hotspot.source_platform] || hotspot.source_platform, 'processed'));
            for (const text of [categories[hotspot.category] || hotspot.category,
                hotspot.rank && '榜单 #' + hotspot.rank, hotspot.heat_value,
                new Date(hotspot.scraped_at).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false})]) {
                if (text) meta.append(element('span', text));
            }
            topic.append(meta, element('h2', hotspot.title));
            const body = element('div');
            body.className = 'topic-body';
            const text = element('div');
            text.className = 'topic-copy';
            const summary = element('p', hotspot.summary || '来源暂未提供摘要，可打开原文了解详情。');
            summary.className = 'hotspot-summary';
            text.append(summary);
            if (/^https?:\/\//i.test(hotspot.raw_url || '')) {
                const link = element('a', '查看原始来源 ↗');
                link.className = 'source-link';
                link.href = hotspot.raw_url;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                text.append(link);
            }
            body.append(text);
            const image = attachment(hotspot);
            if (image) body.append(image);
            topic.append(body);
            const status = select(hotspotStatuses, hotspot.status);
            const toolbar = element('div');
            toolbar.className = 'topic-toolbar';
            const save = button('更新状态', async () => {
                save.disabled = true;
                try {
                    await api('/hotspots/' + encodeURIComponent(id), {status: status.value});
                    notify('热点状态已更新');
                    await load();
                } catch (error) {
                    notify(error.message);
                } finally {
                    save.disabled = false;
                }
            });
            toolbar.append(label('热点状态', status), save);
            topic.append(toolbar);
            const candidatesHeading = element('div');
            candidatesHeading.className = 'candidates-heading';
            candidatesHeading.append(element('h3', '候选文案 · ' + data.posts.length), element('span', '选好角度，改成你的语气'));
            container.replaceChildren(topic, candidatesHeading);
            if (!data.posts.length) {
                const empty = element('p', '这个热点还没有候选文案。');
                empty.className = 'empty-state';
                container.append(empty);
            }
            data.posts.forEach((post, index) => container.append(candidate(hotspot, post, index)));
            if (matchMedia('(max-width: 720px)').matches) container.scrollIntoView({behavior: 'smooth', block: 'start'});
        } catch (error) {
            if (requestId === detailRequest) {
                const message = element('div');
                message.className = 'welcome-state';
                message.append(element('p', error.message), button('重新加载', () => show(id)));
                container.replaceChildren(message);
            }
        } finally {
            if (requestId === detailRequest) container.setAttribute('aria-busy', 'false');
        }
    }

    async function load() {
        const requestId = ++listRequest;
        const params = new URLSearchParams(new FormData($('hot-search')));
        params.set('page', page);
        $('hot-prev').disabled = $('hot-next').disabled = true;
        $('hot-list').setAttribute('aria-busy', 'true');
        try {
            const result = await api('/hotspots?' + params);
            if (requestId !== listRequest) return;
            const choices = result.data.map(hotspot => {
                const choice = button('', () => show(hotspot.id));
                choice.className = 'hotspot-choice';
                choice.dataset.id = hotspot.id;
                choice.setAttribute('aria-current', String(active === hotspot.id));
                const top = element('span');
                top.className = 'choice-top';
                const platform = element('span', platforms[hotspot.source_platform] || hotspot.source_platform);
                platform.className = 'platform-label';
                top.append(platform, badge(hotspotStatuses[hotspot.status], hotspot.status));
                const bottom = element('span');
                bottom.className = 'choice-bottom';
                bottom.append(element('span', hotspot.rank ? '榜单 #' + hotspot.rank : categories[hotspot.category] || hotspot.category),
                    element('span', new Date(hotspot.scraped_at).toLocaleTimeString('zh-CN', {timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false})));
                choice.append(top, element('strong', hotspot.title), bottom);
                const row = element('div');
                row.className = 'hotspot-list-row';
                row.append(choice);
                const image = attachment(hotspot, true);
                if (image) row.append(image);
                return row;
            });
            releasePreviews($('hot-list'));
            $('hot-list').replaceChildren(...(choices.length ? choices : [element('p', '没有匹配的热点。')]));
            $('hot-prev').disabled = page <= 1;
            $('hot-next').disabled = !result.has_next;
            $('hot-page').textContent = '第 ' + page + ' 页';
            $('feed-count').textContent = '本页 ' + result.data.length + ' 条';
        } catch (error) {
            if (requestId === listRequest) {
                releasePreviews($('hot-list'));
                const retry = button('重新加载', load);
                $('hot-list').replaceChildren(element('p', error.message), retry);
            }
        } finally {
            if (requestId === listRequest) $('hot-list').setAttribute('aria-busy', 'false');
        }
    }

    $('hot-search').onsubmit = event => { event.preventDefault(); page = 1; load(); };
    $('refresh').onclick = () => { imageRequests.clear(); load(); };
    for (const filter of $('hot-search').querySelectorAll('select')) {
        filter.addEventListener('change', () => { page = 1; load(); });
    }
    window.addEventListener('beforeunload', event => {
        if (dirtyForms.size) {
            event.preventDefault();
            event.returnValue = '';
        }
    });
    $('hot-prev').onclick = () => { page--; load(); };
    $('hot-next').onclick = () => { page++; load(); };
    load();
})();
