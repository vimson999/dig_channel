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

    const format = seconds => Number(seconds).toFixed(2);
    const notice = message => { el('status').textContent = message; };
    function updateActions() {
        el('upload').disabled = busy || !el('audio-file').files.length;
        el('detect').disabled = busy || !job;
        el('export').disabled = busy || !ranges.some(range => range.confirmed);
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
                    ranges = ranges.filter(item => item !== range);
                    paint();
                })
            );
            card.append(heading, controls);
            list.append(card);
        }
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
    el('upload').onclick = () => run('正在上传并准备音频…', async () => {
        const body = new FormData();
        body.append('audio', el('audio-file').files[0]);
        const data = await (await request('/upload', {method: 'POST', body})).json();
        ({job, duration} = data);
        ranges = [];
        player.src = origin + data.audio_url;
        el('editor').hidden = false;
        el('audio-placeholder').hidden = true;
        el('start').value = el('end').value = '';
        paint();
        notice(`音频已就绪，时长 ${format(duration)} 秒。`);
    });

    el('detect').onclick = () => {
        if (!el('threshold').reportValidity()) return;
        run('正在查找广告…', async () => {
            const body = new FormData();
            const file = el('sample').files[0];
            if (file) body.append('sample', file);
            body.append('threshold', el('threshold').value);
            const data = await (await request(`/${job}/detect`, {method: 'POST', body})).json();
            for (const match of data.matches) {
                if (!ranges.some(range => Math.abs(range.start - match.start) < 0.05 &&
                    Math.abs(range.end - match.end) < 0.05)) ranges.push(match);
            }
            paint();
            notice(`找到 ${data.matches.length} 个候选区间。`);
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

    el('export').onclick = () => run('正在生成去广告的 MP3…', async () => {
        const selected = ranges.filter(range => range.confirmed).map(({start, end}) => ({start, end}));
        const response = await request(`/${job}/export`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ranges: selected}),
        });
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement('a');
        link.href = url;
        link.download = '讲课音频-已去广告.mp3';
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
        notice('MP3 已生成。');
    });

    updateActions();
    loadSample();
})();
