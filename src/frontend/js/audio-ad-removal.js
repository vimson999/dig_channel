(() => {
    'use strict';

    const el = id => document.getElementById(id);
    const origin = location.port === '5001' ? '' :
        String(window.API_BASE_URL || '').replace(/\/api\/v1\/?$/, '');
    const base = origin + '/api/audio-ad-removal';
    const player = el('player');
    const samplePlayer = el('sample-player');
    const timeline = el('timeline');
    let job, duration, selection, previewEnd, sampleUrl;
    let ranges = [];
    let busy = false;
    let batch = [];
    let active;
    let batchMode = false;

    const format = seconds => Number(seconds).toFixed(2);
    const notice = message => { el('status').textContent = message; };
    function updateActions() {
        el('upload').disabled = busy || !el('audio-file').files.length;
        el('detect').disabled = busy || !job;
        el('export').disabled = busy || (batchMode
            ? !batch.length || batch.some(item => !item.detected)
            : !ranges.some(range => range.confirmed));
        for (const id of ['audio-file', 'sample', 'threshold', 'reset-sample', 'add']) {
            el(id).disabled = busy;
        }
        el('editor').inert = busy;
        el('batch-files').inert = busy;
    }

    async function request(path, options) {
        const response = await fetch(base + path, {credentials: 'include', ...options});
        if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            throw new Error(body.error?.message || body.message ||
                (typeof body.error === 'string' ? body.error : `请求失败（${response.status}）`));
        }
        return response;
    }

    async function run(message, action) {
        if (busy) return;
        busy = true;
        updateActions();
        notice(message);
        try { await action(); }
        catch (error) { notice(error.message); }
        finally {
            busy = false;
            updateActions();
        }
    }

    function loadSample() {
        if (sampleUrl) URL.revokeObjectURL(sampleUrl);
        const file = el('sample').files[0];
        sampleUrl = file ? URL.createObjectURL(file) : null;
        el('sample-label').textContent = file ? file.name : '默认样本';
        el('sample-status').textContent = '正在加载样本…';
        el('reset-sample').hidden = !file;
        samplePlayer.src = sampleUrl || base + '/default-sample';
    }

    samplePlayer.addEventListener('loadedmetadata', () => {
        el('sample-status').textContent = `可试听 · ${format(samplePlayer.duration)} 秒`;
    });
    samplePlayer.addEventListener('error', () => {
        el('sample-status').textContent = '样本加载失败，请刷新重试或选择其他样本。';
    });
    el('sample').onchange = loadSample;
    el('reset-sample').onclick = () => {
        el('sample').value = '';
        loadSample();
    };

    function button(label, style, action) {
        const control = document.createElement('button');
        control.type = 'button';
        control.className = 'rounded-md px-2.5 py-1.5 text-xs font-semibold ' + style;
        control.textContent = label;
        control.onclick = action;
        return control;
    }

    function paint() {
        timeline.replaceChildren();
        const list = el('ranges');
        list.replaceChildren();
        if (!ranges.length) list.textContent = '暂无标记';
        ranges.sort((a, b) => a.start - b.start);
        for (const range of ranges) {
            const band = document.createElement('span');
            band.className = 'pointer-events-none absolute inset-y-0 ' +
                (range.confirmed ? 'bg-emerald-400' : 'bg-amber-400');
            band.style.left = `${100 * range.start / duration}%`;
            band.style.width = `${100 * (range.end - range.start) / duration}%`;
            timeline.append(band);

            const card = document.createElement('div');
            card.className = 'space-y-2 rounded-lg border px-3 py-2 ' +
                (range.confirmed ? 'border-emerald-200 bg-emerald-50' : 'border-slate-200');
            const heading = document.createElement('p');
            heading.className = 'font-medium text-slate-700';
            heading.textContent = `${format(range.start)}–${format(range.end)} 秒 · ` +
                (range.score == null ? '手动标记' : `匹配度 ${format(range.score)}`);
            const controls = document.createElement('div');
            controls.className = 'flex flex-wrap items-center gap-2';
            for (const key of ['start', 'end']) {
                const input = document.createElement('input');
                input.type = 'number';
                input.min = '0';
                input.max = String(duration);
                input.step = '0.01';
                input.value = format(range[key]);
                input.setAttribute('aria-label', key === 'start' ? '开始秒数' : '结束秒数');
                input.className = 'w-24 rounded-md border border-slate-200 bg-white px-2 py-1 text-sm';
                input.onchange = () => {
                    const start = key === 'start' ? input.valueAsNumber : range.start;
                    const end = key === 'end' ? input.valueAsNumber : range.end;
                    if (!(start >= 0 && end <= duration && end - start >= 0.05)) {
                        input.value = format(range[key]);
                        return notice('请输入有效的起止时间。');
                    }
                    Object.assign(range, {start, end, confirmed: false});
                    paint();
                };
                controls.append(input);
            }
            controls.append(
                button('试听', 'bg-slate-100 text-slate-700', () => {
                    previewEnd = range.end;
                    player.currentTime = range.start;
                    player.play().catch(() => notice('无法播放这段音频。'));
                }),
                button(range.confirmed ? '取消确认' : '确认剪除', 'bg-indigo-50 text-indigo-700', () => {
                    range.confirmed = !range.confirmed;
                    paint();
                }),
                button('删除', 'text-slate-500 hover:bg-slate-100', () => {
                    ranges.splice(ranges.indexOf(range), 1);
                    paint();
                })
            );
            card.append(heading, controls);
            list.append(card);
        }
        renderBatch();
        updateActions();
    }

    player.ontimeupdate = () => {
        if (previewEnd != null && player.currentTime >= previewEnd) {
            player.pause();
            previewEnd = null;
        }
    };
    player.onpause = () => { previewEnd = null; };
    el('audio-file').onchange = updateActions;
    function renderBatch() {
        const list = el('batch-files');
        list.replaceChildren();
        if (!batchMode) return;
        const hint = document.createElement('p');
        hint.className = 'text-xs text-slate-600';
        hint.textContent = `共 ${batch.length} 个文件 · 点击文件名查看该文件的广告区间`;
        list.append(hint);
        for (const item of batch) {
            const count = item.ranges?.filter(range => range.confirmed).length || 0;
            const status = item.error || (item.detected
                ? (count ? `待剪除 ${count} 段` : '不剪切')
                : '等待识别');
            const selected = item === active;
            const control = button(`${selected ? '正在查看 · ' : ''}${item.file.name} · ${status}`,
                'block w-full break-all border text-left ' + (selected
                    ? 'border-indigo-400 bg-indigo-50 text-indigo-800'
                    : 'border-transparent bg-slate-100 text-slate-700 hover:bg-slate-200'),
                () => { showFile(item); });
            control.disabled = !item.job;
            control.setAttribute('aria-pressed', String(item === active));
            list.append(control);
        }
    }

    function showFile(item) {
        active = item;
        const label = el('active-audio');
        label.textContent = `当前查看 ${batch.indexOf(item) + 1}/${batch.length}：${item.file.name}`;
        label.hidden = !batchMode;
        ({job, duration, ranges} = item);
        previewEnd = null;
        player.src = origin + item.audio_url;
        el('editor').hidden = false;
        el('audio-placeholder').hidden = true;
        el('start').value = el('end').value = '';
        paint();
    }

    async function detectFile(item) {
        const body = new FormData();
        const sample = el('sample').files[0];
        if (sample) body.append('sample', sample);
        body.append('threshold', el('threshold').value);
        const data = await (await request(`/${item.job}/detect`, {method: 'POST', body})).json();
        for (const match of data.matches) {
            if (!item.ranges.some(range => Math.abs(range.start - match.start) < 0.05 &&
                Math.abs(range.end - match.end) < 0.05)) {
                item.ranges.push({...match, confirmed: batchMode});
            }
        }
        item.detected = true;
        item.error = '';
        return data.matches.length;
    }

    el('upload').onclick = () => {
        const files = Array.from(el('audio-file').files);
        if (files.length > 30) return notice('每批最多选择 30 个音频。');
        if (files.length > 1 && !el('threshold').reportValidity()) return;
        run('正在准备音频…', async () => {
            job = active = null;
            el('active-audio').hidden = true;
            player.removeAttribute('src');
            player.load();
            ranges = [];
            el('editor').hidden = true;
            el('audio-placeholder').hidden = false;
            batchMode = files.length > 1;
            el('export').textContent = batchMode ? '剪除所选区间并下载 ZIP' : '导出 MP3';
            batch = files.map(file => ({file, ranges: []}));
            renderBatch();
            for (const [index, item] of batch.entries()) {
                notice(`正在处理 ${index + 1}/${batch.length}：${item.file.name}`);
                try {
                    const body = new FormData();
                    body.append('audio', item.file);
                    Object.assign(item, await (await request('/upload', {method: 'POST', body})).json());
                    if (batchMode) await detectFile(item);
                } catch (error) { item.error = error.message; }
                renderBatch();
            }
            const first = batch.find(item => item.job);
            if (first) showFile(first);
            const failed = batch.filter(item => item.error).length;
            notice(failed ? (batchMode
                ? `${failed} 个文件处理失败，请查看列表。识别失败可选中文件重试，上传失败请重新选择文件。`
                : batch[0].error)
                : batchMode ? '全部音频已识别。点左侧文件名逐个核对；下载 ZIP 会处理整批文件。'
                : `音频已就绪，时长 ${format(duration)} 秒。`);
        });
    };

    el('detect').onclick = () => {
        if (!el('threshold').reportValidity()) return;
        run('正在查找广告…', async () => {
            const count = await detectFile(active);
            paint();
            notice(`找到 ${count} 个候选区间。`);
        });
    };

    el('add').onclick = () => {
        const start = el('start').valueAsNumber;
        const end = el('end').valueAsNumber;
        if (!(start >= 0 && end <= duration && end - start >= 0.05)) return notice('请输入有效的起止时间。');
        ranges.push({start, end, confirmed: false});
        paint();
    };

    function position(event) {
        const rect = timeline.getBoundingClientRect();
        const time = (event.clientX - rect.left) / rect.width * duration;
        return Math.max(0, Math.min(duration, time));
    }
    timeline.onpointerdown = event => {
        selection = position(event);
        timeline.setPointerCapture(event.pointerId);
    };
    timeline.onpointerup = event => {
        if (selection == null) return;
        const end = position(event);
        el('start').value = format(Math.min(selection, end));
        el('end').value = format(Math.floor(Math.max(selection, end) * 100) / 100);
        selection = null;
    };
    timeline.onpointercancel = () => { selection = null; };

    el('export').onclick = () => run(batchMode ? '正在剪切并打包 ZIP…' : '正在生成去广告的 MP3…', async () => {
        const selected = list => list.filter(range => range.confirmed).map(({start, end}) => ({start, end}));
        const payload = batchMode ? {
            files: batch.map(item => ({job: item.job, name: item.file.name, ranges: selected(item.ranges)})),
        } : {ranges: selected(ranges)};
        const response = await request(batchMode ? '/export-zip' : `/${job}/export`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement('a');
        link.href = url;
        link.download = batchMode ? '批量去广告.zip' : '讲课音频-已去广告.mp3';
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
        notice(batchMode ? 'ZIP 已生成。' : 'MP3 已生成。');
    });

    updateActions();
    loadSample();
})();
