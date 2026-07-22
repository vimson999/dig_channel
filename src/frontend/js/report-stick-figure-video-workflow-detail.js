class ReportStickFigureVideoWorkflowDetailPage {
    constructor() {
        const params = new URLSearchParams(location.search);
        this.workflowJobId = params.get('workflow_job_id') || '';
        this.materialJobId = params.get('material_job_id') || '';
        this.material = null;
        this.storyboard = null;
        this.assets = null;
        this.assetsError = '';
        this.visualScheme = {key:'stick_figure',name:'白板火柴人'};
        this.timer = null;
        this.quickNavObserver = null;
        this.rawText = '';
        this.els = {
            title: document.getElementById('detail-title'),
            subtitle: document.getElementById('detail-subtitle'),
            refresh: document.getElementById('refresh-btn'),
            root: document.getElementById('detail-root'),
            notice: document.getElementById('notice'),
            materialJson: document.getElementById('material-json'),
            modal: document.getElementById('raw-modal'),
            rawMeta: document.getElementById('raw-meta'),
            rawContent: document.getElementById('raw-content'),
            copyRaw: document.getElementById('copy-raw'),
            quickNav: document.getElementById('quick-nav'),
        };
        this.bind();
        this.load();
    }

    apiRoot() { const value=window.API_BASE_URL||window.CONFIG?.API_BASE_URL||''; return value ? value.replace(/\/api\/v1\/?$/,'').replace(/\/$/,'') : `${location.protocol}//${location.hostname}:5001`; }
    headers(json=false) { const token=localStorage.getItem('access_token')||localStorage.getItem('token')||''; return {...(json?{'Content-Type':'application/json'}:{}),...(token?{Authorization:`Bearer ${token}`}:{})}; }
    async request(path, options={}) { const response=await fetch(`${this.apiRoot()}${path}`,{...options,headers:{...this.headers(Boolean(options.body)),...(options.headers||{})}}); const data=await response.json().catch(()=>({})); if(!response.ok||data.success===false)throw new Error(data.message||`HTTP ${response.status}`); return data; }

    bind() {
        this.els.refresh.addEventListener('click',()=>this.load(true));
        this.els.root.addEventListener('click',async event=>{
            const storyboard=event.target.closest('[data-generate-storyboard]');
            if(storyboard){await this.generateStoryboard(storyboard);return;}
            const raw=event.target.closest('[data-view-raw]');
            if(raw){await this.showRaw(raw);return;}
            const batch=event.target.closest('[data-asset-batch]');
            if(batch){await this.submitAssets(batch.dataset.assetBatch,batch);return;}
            const retry=event.target.closest('[data-asset-retry]');
            if(retry){await this.retryAsset(retry.dataset.assetRetry,retry.dataset.assetId,retry);}
            const video=event.target.closest('[data-video-submit]');
            if(video){await this.submitVideo(video);}
        });
        this.els.root.addEventListener('change',event=>{
            if(event.target.matches('[data-video-subtitles]'))this.toggleSubtitleFields();
            if(event.target.matches('[data-video-watermark]'))this.toggleWatermarkFields();
        });
        this.els.modal.addEventListener('click',event=>{if(event.target===this.els.modal||event.target.closest('[data-close-raw]'))this.closeRaw();});
        this.els.copyRaw.addEventListener('click',()=>navigator.clipboard.writeText(this.rawText));
        this.els.quickNav?.addEventListener('click',event=>{
            const item=event.target.closest('[data-quick-nav]');
            if(item){
                const target=document.getElementById(item.dataset.quickNav);
                if(target){
                    target.scrollIntoView({behavior:'smooth',block:'start'});
                    this.setActiveQuickNav(item.dataset.quickNav);
                }
                return;
            }
            if(event.target.closest('[data-scroll-top]'))window.scrollTo({top:0,behavior:'smooth'});
        });
    }

    async load(manual=false) {
        clearTimeout(this.timer);
        if(!this.materialJobId&&!this.workflowJobId){this.els.root.innerHTML='<div class="rounded-lg border border-amber-200 bg-amber-50 p-6">URL 缺少 workflow_job_id。</div>';return;}
        if(manual)this.setNotice('正在刷新。','info');
        try {
            if(this.workflowJobId){const workflow=await this.request(`/api/report-stick-figure-video-workflows/jobs/${encodeURIComponent(this.workflowJobId)}`);this.materialJobId=workflow.data?.material_job_id||'';this.visualScheme=workflow.data?.visual_scheme||this.visualScheme;}
            if(!this.materialJobId)throw new Error('编排任务尚未关联基础文案任务');
            const materialResult=await this.request(`/api/report-script-material-workflows/jobs/${encodeURIComponent(this.materialJobId)}`);
            this.material=materialResult.data;
            const boardResult=await this.request(`/api/report-visual-plans/sources/${encodeURIComponent(this.materialJobId)}/latest?visual_scheme_key=${encodeURIComponent(this.visualScheme.key||'stick_figure')}`);
            this.storyboard=boardResult.data||null;
            if(this.storyboard?.status==='succeeded'&&this.workflowJobId)await this.loadAssets(true);
            this.render();
            this.schedulePolling();
            if(manual)this.setNotice('已刷新。','success');
        } catch(error) { this.setNotice(`加载失败：${error.message}`,'error'); }
    }

    async loadAssets(silent=false,statusOnly=false) {
        if(!this.workflowJobId)return;
        try {
            const storyboardId=this.assets?.storyboard_job_id||'';
            const query=statusOnly&&storyboardId?`?view=status&storyboard_job_id=${encodeURIComponent(storyboardId)}`:'';
            const result=await this.request(`/api/report-stick-figure-video-workflows/jobs/${encodeURIComponent(this.workflowJobId)}/scene-assets${query}`);
            if(statusOnly&&this.assets&&this.canMergeAssetStatus(result.data))this.mergeAssetStatus(result.data);
            else if(statusOnly)return this.loadAssets(silent,false);
            else this.assets=result.data;
            this.assetsError='';
        } catch(error) {
            this.assets=null;
            this.assetsError=error.message;
            if(!silent)this.setNotice(`素材读取失败：${error.message}`,'error');
        }
    }

    schedulePolling() {
        clearTimeout(this.timer);
        const boardActive=this.storyboard&&['queued','running'].includes(this.storyboard.status);
        const summary=this.assets?.summary||{};
        const assetActive=Number(summary.images_active||0)>0||Number(summary.tts_active||0)>0;
        const alignmentActive=['pending','processing'].includes(this.assets?.audio_strategy?.status);
        const videoActive=['queued','processing'].includes(this.assets?.video?.status);
        if(boardActive)this.timer=setTimeout(()=>this.pollStoryboard(),4000);
        else if(assetActive||alignmentActive||videoActive)this.timer=setTimeout(()=>this.pollAssets(),10000);
    }

    async pollStoryboard() {
        if(!this.storyboard?.job_id)return;
        try {
            const result=await this.request(`/api/report-visual-plans/jobs/${encodeURIComponent(this.storyboard.job_id)}`);
            this.storyboard=result.data;
            if(this.storyboard.status==='succeeded')await this.loadAssets(true);
            this.render();
            this.schedulePolling();
        } catch(error) { this.setNotice(`刷新分镜失败：${error.message}`,'error'); }
    }

    async pollAssets() {
        await this.loadAssets(true,true);
        this.render();
        this.schedulePolling();
    }

    canMergeAssetStatus(next) {
        const currentIds=new Set((this.assets?.items||[]).map(item=>String(item.id)));
        const nextItems=next?.items||[];
        return currentIds.size===nextItems.length&&nextItems.every(item=>currentIds.has(String(item.id)));
    }

    mergeAssetStatus(next) {
        const byId=new Map((next.items||[]).map(item=>[String(item.id),item]));
        const items=(this.assets.items||[]).map(item=>{
            const state=byId.get(String(item.id));
            if(!state)return item;
            return {...item,...state,image:{...item.image,...state.image},tts:{...item.tts,...state.tts},timeline:{...item.timeline,...state.timeline}};
        });
        this.assets={...this.assets,...next,items};
    }

    async generateStoryboard(button) {
        const force=button.dataset.force==='1';
        if(force&&!window.confirm('重新生成后会创建一套新的分镜方案。现有图片、语音和视频不会复用，需要按新分镜重新生成。确定继续吗？'))return;
        const old=button.innerHTML; button.disabled=true; button.innerHTML='<i class="fas fa-spinner fa-spin mr-1"></i>投递中';
        try {
            const result=await this.request('/api/report-visual-plans/jobs',{method:'POST',body:JSON.stringify({material_job_id:this.materialJobId,force,visual_scheme_key:this.visualScheme.key||'stick_figure'})});
            this.storyboard={job_id:result.job_id,status:result.status||'queued',progress:0};
            this.assets=null; this.assetsError=''; this.render();
            this.setNotice(force?'新的分镜方案已投递；完成后请按新分镜重新生成图片和语音。':'分镜任务已投递。','success');
            this.schedulePolling();
        } catch(error) { this.setNotice(`投递失败：${error.message}`,'error'); }
        finally { button.disabled=false; button.innerHTML=old; }
    }

    async submitAssets(kind,button) {
        const isRetry=kind.endsWith('-failed');
        const assetKind=kind.startsWith('image')?'images':'tts';
        const audioMode=kind==='tts-whole'?'whole_audio':kind.startsWith('tts-scene')?'scene_tts':'auto';
        const old=button.innerHTML; button.disabled=true; button.innerHTML='<i class="fas fa-spinner fa-spin mr-1"></i>投递中';
        try {
            const result=await this.request(`/api/report-stick-figure-video-workflows/jobs/${encodeURIComponent(this.workflowJobId)}/scene-assets/${assetKind}`,{method:'POST',body:JSON.stringify({retry_failed:isRetry,audio_mode:audioMode,force:isRetry})});
            this.setNotice(result.message||'素材任务已投递。','success');
            await this.loadAssets(true); this.render(); this.schedulePolling();
        } catch(error) { this.setNotice(`投递失败：${error.message}`,'error'); }
        finally { button.disabled=false; button.innerHTML=old; }
    }

    async retryAsset(kind,assetId,button) {
        const old=button.innerHTML; button.disabled=true; button.innerHTML='投递中';
        try {
            const result=await this.request(`/api/report-stick-figure-video-workflows/jobs/${encodeURIComponent(this.workflowJobId)}/scene-assets/${encodeURIComponent(assetId)}/${kind}/retry`,{method:'POST',body:'{}'});
            this.setNotice(result.message||'已重新投递。','success');
            await this.loadAssets(true); this.render(); this.schedulePolling();
        } catch(error) { this.setNotice(`重试失败：${error.message}`,'error'); }
        finally { button.disabled=false; button.innerHTML=old; }
    }

    async submitVideo(button) {
        const old=button.innerHTML; button.disabled=true; button.innerHTML='<i class="fas fa-spinner fa-spin mr-1"></i>投递中';
        try {
            const force=button.dataset.videoForce==='1';
            const renderOptions=this.readVideoOptions();
            const audioMode=this.els.root.querySelector('[data-video-audio-mode]')?.value||'auto';
            const result=await this.request(`/api/report-stick-figure-video-workflows/jobs/${encodeURIComponent(this.workflowJobId)}/video`,{method:'POST',body:JSON.stringify({force,audio_mode:audioMode,render_options:renderOptions})});
            this.setNotice(result.message||'视频合成任务已投递。','success');
            await this.loadAssets(true); this.render(); this.schedulePolling();
        } catch(error) { this.setNotice(`视频投递失败：${error.message}`,'error'); }
        finally { button.disabled=false; button.innerHTML=old; }
    }

    render() {
        const material=this.material||{};
        const structured=material.structured_result||{};
        const title=structured.title||material.title||material.pdf?.topic_title||material.pdf?.filename||'未命名研报';
        const schemeName=this.visualScheme?.name||this.storyboard?.visual_scheme?.name||'白板火柴人';
        const script=material.final_script_for_tts||'';
        const board=this.storyboard;
        const segments=Array.isArray(board?.segments)?board.segments:[];
        const hasTitleScene=segments.some(item=>Number(item.sequence)===0);
        const summary=this.assets?.summary||{};
        const imageDone=Number(summary.total||0)>0&&Number(summary.images_completed||0)===Number(summary.total||0);
        const ttsDone=Boolean(summary.scene_tts_ready);
        const audioReady=Boolean(summary.audio_ready);
        const audioStrategy=this.assets?.audio_strategy||{};
        const audioDetail=audioStrategy.ready
            ? `整篇音频已对齐 ${audioStrategy.aligned_scene_count||0}/${summary.body_total||0}`
            : ttsDone?`分段 TTS ${summary.tts_completed||0}/${summary.body_total||0}`
            : audioStrategy.available?`${audioStrategy.status||'available'} · 可复用整篇 WAV`:`分段 TTS ${summary.tts_completed||0}/${summary.body_total||0}`;
        const video=this.assets?.video||{};
        const videoDone=video.status==='completed';
        this.els.title.textContent=title;
        this.els.subtitle.textContent=`${schemeName} · 基础文案 ${material.status||'-'} · ${material.job_id||this.materialJobId}`;
        this.els.materialJson.href=this.absolute(`/api/report-script-material-workflows/jobs/${encodeURIComponent(this.materialJobId)}`);
        this.els.root.innerHTML=`
            <section id="workflow-overview" data-nav-section class="scroll-mt-24 grid grid-cols-2 gap-2 md:grid-cols-5">
                ${this.stage('1. 基础文案',material.status==='succeeded','PDF、Gem、DS')}
                ${this.stage('2. 标题与正文分镜',board?.status==='succeeded',board?`${board.segment_count||0} 镜头`:'未开始')}
                ${this.stage('3. 生成图片',imageDone,summary.total?`${summary.images_completed||0}/${summary.total}`:'未开始')}
                ${this.stage('4. 语音与时间轴',audioReady,audioDetail)}
                ${this.stage('5. 合成视频',videoDone,videoDone?`${Number(video.duration_seconds||0).toFixed(1)} 秒`:(['queued','processing'].includes(video.status)?`${video.progress||0}%`:(audioReady&&imageDone?'素材已就绪':'待音画素材')))}
            </section>
            <section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"><div class="flex items-start justify-between gap-3"><div><div class="flex items-center gap-2"><div class="text-xs font-black uppercase tracking-wider text-slate-400">研报标题</div><span class="rounded-full bg-violet-50 px-2 py-1 text-[11px] font-black text-violet-700">${this.escape(schemeName)}</span></div><h2 class="mt-2 text-xl font-black leading-8">${this.escape(title)}</h2><p class="mt-1 text-xs text-slate-500">${this.escape(material.pdf?.filename||'')}</p></div>${material.input_pdf_url?`<a href="${this.absolute(material.input_pdf_url)}" target="_blank" class="rounded-md border px-3 py-2 text-xs font-bold text-slate-700">查看 PDF</a>`:''}</div></section>
            <section id="report-script" data-nav-section class="scroll-mt-24 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"><div class="flex items-center justify-between"><h2 class="font-black">基础口播文案</h2><span class="text-xs font-bold text-slate-400">${script.length} 字</span></div><div class="mt-3 rounded-md border border-amber-100 bg-amber-50 p-3"><div class="text-xs font-black text-amber-800">标题（必须保留）</div><div class="mt-1 font-bold text-amber-950">${this.escape(title)}</div></div><pre class="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-slate-950 p-4 text-sm leading-7 text-slate-100">${this.escape(script||'等待基础文案生成。')}</pre></section>
            ${this.withNavSection(this.storyboardSection(board,segments,hasTitleScene,title),'storyboard-plan')}
            ${this.withNavSection(this.assetSection(imageDone,audioReady),'scene-assets')}
            ${this.withNavSection(this.videoSection(imageDone&&audioReady,video),'video-render')}
            <section id="future-pipeline" data-nav-section class="scroll-mt-24 grid gap-3 md:grid-cols-2">${this.futureCard('生成封面','沿用同一父编排，后续增加横竖封面资产。','image')}${this.futureCard('社媒发布','视频完成后接入 B站、抖音、视频号等投递阶段。','share-nodes')}</section>`;
        this.bindAudioPreviews();
        this.setupQuickNavigation();
    }

    withNavSection(html,id) {
        return String(html||'').replace('<section ',`<section id="${id}" data-nav-section style="scroll-margin-top:6rem" `);
    }

    setupQuickNavigation() {
        if(!this.els.quickNav)return;
        if(this.quickNavObserver)this.quickNavObserver.disconnect();
        const buttons=Array.from(this.els.quickNav.querySelectorAll('[data-quick-nav]'));
        const sections=buttons.map(button=>document.getElementById(button.dataset.quickNav)).filter(Boolean);
        buttons.forEach(button=>{button.disabled=!document.getElementById(button.dataset.quickNav);button.classList.toggle('opacity-40',button.disabled);});
        if(!sections.length)return;
        this.setActiveQuickNav(sections[0].id);
        this.quickNavObserver=new IntersectionObserver(entries=>{
            const visible=entries.filter(entry=>entry.isIntersecting).sort((a,b)=>Math.abs(a.boundingClientRect.top-96)-Math.abs(b.boundingClientRect.top-96));
            if(visible[0])this.setActiveQuickNav(visible[0].target.id);
        },{rootMargin:'-96px 0px -62% 0px',threshold:[0,0.05,0.2]});
        sections.forEach(section=>this.quickNavObserver.observe(section));
    }

    setActiveQuickNav(id) {
        this.els.quickNav?.querySelectorAll('[data-quick-nav]').forEach(button=>{
            const active=button.dataset.quickNav===id;
            button.classList.toggle('bg-indigo-50',active);
            button.classList.toggle('text-indigo-700',active);
            button.classList.toggle('text-slate-600',!active);
            if(active)button.setAttribute('aria-current','location');
            else button.removeAttribute('aria-current');
        });
    }

    storyboardSection(board,segments,hasTitleScene,title) {
        const succeeded=board?.status==='succeeded';
        const failed=board?.status==='failed';
        const actions=`${succeeded?`<button data-view-raw class="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-800">Gem 原文</button>`:''}${succeeded||failed?`<button data-generate-storyboard data-force="1" class="rounded-md bg-indigo-700 px-3 py-2 text-xs font-bold text-white"><i class="fas fa-rotate mr-1"></i>重新生成分镜方案</button>`:''}${!board?`<button data-generate-storyboard class="rounded-md bg-indigo-700 px-3 py-2 text-xs font-bold text-white">生成分镜</button>`:''}`;
        return `<section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"><div class="flex flex-wrap items-center justify-between gap-2"><div><h2 class="font-black">标题与正文分镜</h2><p class="mt-1 text-xs text-slate-500">标题镜头独立保存；正文逐字覆盖原始口播文案。</p></div><div class="flex flex-wrap gap-2">${actions}</div></div>${board&&['queued','running'].includes(board.status)?`<div class="mt-4 rounded-md bg-indigo-50 p-3 text-xs font-bold text-indigo-700"><i class="fas fa-spinner fa-spin mr-2"></i>${this.escape(board.step||board.status)} · ${board.progress||0}%</div>`:''}${failed?`<div class="mt-4 rounded-md bg-red-50 p-3 text-xs text-red-700">${this.escape(board.error||'分镜失败')}</div>`:''}${succeeded?`<div class="mt-4 flex items-center justify-between text-xs"><span class="font-black">${segments.length} 个镜头</span><span class="font-bold text-emerald-700">正文覆盖${board.coverage_complete?'完整':'待检查'}</span></div><div class="mt-3 max-h-[36rem] space-y-2 overflow-auto">${!hasTitleScene?this.legacyTitleCard(title):''}${segments.map(item=>this.sceneCard(item)).join('')}</div>`:'<div class="mt-4 rounded-md border border-dashed p-6 text-center text-sm text-slate-500">基础文案完成后可生成分镜。</div>'}</section>`;
    }

    assetSection(imageDone,audioReady) {
        if(this.assetsError){
            const needsTitleScene=this.assetsError.includes('缺少标题镜头提示词');
            return `<section class="rounded-xl border border-amber-200 bg-amber-50 p-5"><div class="flex flex-wrap items-start justify-between gap-3"><div><h2 class="font-black text-amber-900">逐镜头音画素材</h2><p class="mt-2 text-sm text-amber-800">${this.escape(this.assetsError)}</p></div>${needsTitleScene?'<button data-generate-storyboard data-force="1" class="rounded-md bg-indigo-700 px-4 py-2 text-xs font-bold text-white shadow-sm"><i class="fas fa-clapperboard mr-1"></i>重新生成含标题分镜</button>':''}</div>${needsTitleScene?'<p class="mt-3 text-xs text-amber-700">新分镜完成后，本区域会自动出现“批量生成图片”和“批量生成语音”入口。</p>':''}</section>`;
        }
        if(!this.assets)return `<section class="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-500">新版标题与正文分镜完成后，这里会出现逐镜头图片和语音任务。</section>`;
        const summary=this.assets.summary||{};
        const hasImageFailed=Number(summary.images_failed||0)>0;
        const hasTtsFailed=Number(summary.tts_failed||0)>0;
        const audio=this.assets.audio_strategy||{};
        const alignmentActive=['pending','processing'].includes(audio.status);
        const imageStyle=this.assets.image_style?.description||`${this.visualScheme?.name||'所选'}视觉方案、中文标签、16:9`;
        const wholeAudioPanel=audio.available
            ? `<div class="mt-4 rounded-lg border ${audio.ready?'border-emerald-200 bg-emerald-50':'border-blue-200 bg-blue-50'} p-4"><div class="flex flex-wrap items-center justify-between gap-3"><div><div class="text-sm font-black ${audio.ready?'text-emerald-900':'text-blue-900'}">发现研报转语音的整篇 WAV</div><div class="mt-1 text-xs ${audio.ready?'text-emerald-700':'text-blue-700'}">${audio.ready?`已对齐 ${audio.aligned_scene_count||0} 个正文镜头 · 平均置信度 ${(Number(audio.average_confidence||0)*100).toFixed(1)}%`:`${Number(audio.audio_seconds||0).toFixed(1)} 秒 · 不会重复生成分段语音`}</div>${audio.error?`<div class="mt-2 text-xs text-red-700">${this.escape(audio.error)}</div>`:''}</div><button data-asset-batch="tts-whole" ${alignmentActive?'disabled':''} class="rounded-md bg-blue-700 px-3 py-2 text-xs font-bold text-white disabled:opacity-40">${alignmentActive?'<i class="fas fa-spinner fa-spin mr-1"></i>正在对齐':audio.ready?'重新对齐整篇语音':'复用整篇语音并对齐'}</button></div></div>`
            : '<div class="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4 text-xs text-slate-600">没有发现同一研报可复用的完整 WAV，可使用逐镜头 TTS。</div>';
        return `<section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"><div class="flex flex-wrap items-start justify-between gap-3"><div><h2 class="font-black">逐镜头音画素材</h2><p class="mt-1 text-xs text-slate-500">图片固定按 000、001… 保存；音频可选择复用整篇 WAV 时间轴，或逐镜头生成 TTS。</p><p class="mt-1 text-[11px] text-slate-400">生图风格：${this.escape(imageStyle)}</p></div><div class="flex flex-wrap gap-2"><button data-asset-batch="images" class="rounded-md bg-indigo-700 px-3 py-2 text-xs font-bold text-white">批量生成图片</button>${hasImageFailed?'<button data-asset-batch="images-failed" class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs font-bold text-red-700">续跑失败图片</button>':''}<button data-asset-batch="tts-scene" ${imageDone?'':'disabled'} class="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-bold text-emerald-700 disabled:opacity-40">生成分段 TTS</button>${hasTtsFailed?'<button data-asset-batch="tts-scene-failed" class="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs font-bold text-red-700">续跑失败语音</button>':''}</div></div>${wholeAudioPanel}<div class="mt-4 grid grid-cols-2 gap-2 text-xs md:grid-cols-6">${this.metric('总镜头',summary.total)}${this.metric('正文镜头',summary.body_total)}${this.metric('图片完成',summary.images_completed)}${this.metric('分段语音',`${summary.tts_completed||0}/${summary.body_total||0}`)}${this.metric('当前音频',audio.ready?'整篇 WAV':summary.scene_tts_ready?'分段 TTS':'未就绪')}${this.metric('可合成',imageDone&&audioReady?'是':'否')}</div><div class="mt-4 space-y-3">${(this.assets.items||[]).map(item=>this.assetCard(item)).join('')}</div></section>`;
    }

    videoSection(ready,video) {
        video=video||{};
        const options=this.normalizedVideoOptions(video.render_options);
        const audio=this.assets?.audio_strategy||{};
        const summary=this.assets?.summary||{};
        const selectedAudioMode=video.audio_mode||(audio.ready?'whole_audio':summary.scene_tts_ready?'scene_tts':'auto');
        const active=['queued','processing'].includes(video.status);
        const failed=video.status==='failed';
        const completed=video.status==='completed'&&video.url;
        const action=completed
            ? '<button data-video-submit data-video-force="1" class="rounded-md border border-indigo-200 bg-indigo-50 px-4 py-2 text-xs font-bold text-indigo-700">重新合成</button>'
            : `<button data-video-submit ${failed?'data-video-force="1"':''} ${ready&&!active?'':'disabled'} class="rounded-md bg-violet-700 px-4 py-2 text-xs font-bold text-white disabled:cursor-not-allowed disabled:opacity-40">${failed?'失败续跑':active?'合成中':'合成视频'}</button>`;
        return `<section class="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"><div class="flex flex-wrap items-start justify-between gap-3"><div><h2 class="font-black">视频合成</h2><p class="mt-1 text-xs text-slate-500">正片从 001 正文镜头开始，000 仅作标题/封面。整篇 WAV 模式按对齐时间轴切换图片；分段 TTS 模式按每段 WAV 时长切换。</p></div>${action}</div>${this.videoOptionsPanel(options,active,selectedAudioMode,audio,summary)}${active?`<div class="mt-4"><div class="flex justify-between text-xs font-bold text-violet-700"><span>${this.escape(video.step||'render_video')}</span><span>${video.progress||0}%</span></div><div class="mt-2 h-2 overflow-hidden rounded-full bg-violet-100"><div class="h-full bg-violet-600" style="width:${Math.max(0,Math.min(100,Number(video.progress||0)))}%"></div></div></div>`:''}${failed?`<div class="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700">${this.escape(video.error||'视频合成失败，可点击失败续跑。')}</div>`:''}${completed?`<div class="mt-4"><video controls preload="metadata" src="${this.absolute(video.url)}" class="aspect-video w-full rounded-lg bg-black"></video><div class="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-500"><span>${video.audio_mode==='whole_audio'?'整篇 WAV 时间轴':'逐镜头 TTS'}</span><span>${Number(video.duration_seconds||0).toFixed(1)} 秒</span><span>${video.scene_count||0} 个正文镜头</span><span>${video.width||0}×${video.height||0}</span><span>${this.formatBytes(video.bytes)}</span><span>${Number(options.speed||1).toFixed(2).replace(/0+$/,'').replace(/\.$/,'')} 倍速</span><span>${options.subtitles_enabled?`字幕 ${video.subtitle_entry_count||0} 条`:'无字幕'}</span><span>${options.watermark_enabled?`右上角水印：${this.escape(options.watermark_text)}`:'无水印'}</span>${video.subtitle_url?`<a href="${this.absolute(video.subtitle_url)}" class="font-bold text-indigo-700 hover:underline">下载 ASS 字幕</a>`:''}</div></div>`:(!active&&!failed?`<div class="mt-4 rounded-md border border-dashed p-6 text-center text-sm text-slate-500">${ready?'图片和至少一种音频方案已就绪，可开始合成。':'等待图片及整篇音频时间轴或分段 TTS 完成。'}</div>`:'')}</section>`;
    }

    normalizedVideoOptions(value) {
        const raw=value&&typeof value==='object'?value:{};
        return {
            speed:Number(raw.speed||1),
            subtitles_enabled:Boolean(raw.subtitles_enabled),
            subtitle_font_name:raw.subtitle_font_name||'Hiragino Sans GB',
            subtitle_font_size:Number(raw.subtitle_font_size||42),
            subtitle_font_color:raw.subtitle_font_color||'#FFFFFF',
            subtitle_outline_color:raw.subtitle_outline_color||'#000000',
            subtitle_outline:Number(raw.subtitle_outline??3),
            subtitle_position:raw.subtitle_position||'bottom',
            watermark_enabled:Boolean(raw.watermark_enabled),
            watermark_text:raw.watermark_text||'Shioa',
            watermark_font_size:Number(raw.watermark_font_size||28),
            watermark_color:raw.watermark_color||'#111827',
            watermark_opacity:Number(raw.watermark_opacity||45),
        };
    }

    videoOptionsPanel(options,disabled,audioMode,audio,summary) {
        const speeds=[0.5,0.75,1,1.25,1.5,2];
        const fonts=['Hiragino Sans GB','PingFang SC','Arial Unicode MS'];
        const positions=[['bottom','底部'],['middle','居中'],['top','顶部']];
        const fieldDisabled=disabled||!options.subtitles_enabled;
        const watermarkDisabled=disabled||!options.watermark_enabled;
        const wholeDisabled=!audio?.ready; const sceneDisabled=!summary?.scene_tts_ready;
        return `<div class="mt-4 rounded-lg border border-violet-100 bg-violet-50/60 p-4"><div class="grid gap-3 md:grid-cols-3 lg:grid-cols-5"><label class="text-xs font-bold text-slate-700"><span class="mb-1 block">音频方案</span><select data-video-audio-mode ${disabled?'disabled':''} class="w-full rounded-md border border-slate-300 bg-white px-3 py-2 disabled:opacity-50"><option value="auto" ${audioMode==='auto'?'selected':''}>自动选择</option><option value="whole_audio" ${audioMode==='whole_audio'?'selected':''} ${wholeDisabled?'disabled':''}>复用整篇 WAV</option><option value="scene_tts" ${audioMode==='scene_tts'?'selected':''} ${sceneDisabled?'disabled':''}>逐镜头 TTS</option></select></label><label class="text-xs font-bold text-slate-700"><span class="mb-1 block">播放倍速</span><select data-video-speed ${disabled?'disabled':''} class="w-full rounded-md border border-slate-300 bg-white px-3 py-2 disabled:opacity-50">${speeds.map(value=>`<option value="${value}" ${Math.abs(value-options.speed)<0.001?'selected':''}>${value}×</option>`).join('')}</select></label><label class="flex items-center gap-2 rounded-md border border-violet-200 bg-white px-3 py-2 text-xs font-bold text-violet-800"><input data-video-subtitles type="checkbox" ${options.subtitles_enabled?'checked':''} ${disabled?'disabled':''} class="h-4 w-4 rounded border-slate-300"><span>生成并烧录字幕</span></label><div class="md:col-span-3 lg:col-span-2 flex items-center text-[11px] leading-5 text-slate-500">自动模式优先整篇 WAV；不可用时使用分段 TTS。字幕始终取分镜原文。</div></div><div class="mt-3 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6"><label class="text-xs font-bold text-slate-700"><span class="mb-1 block">字体</span><select data-video-subtitle-font data-subtitle-field ${fieldDisabled?'disabled':''} class="w-full rounded-md border border-slate-300 bg-white px-2 py-2 disabled:opacity-50">${fonts.map(value=>`<option value="${value}" ${value===options.subtitle_font_name?'selected':''}>${value}</option>`).join('')}</select></label><label class="text-xs font-bold text-slate-700"><span class="mb-1 block">字号</span><input data-video-subtitle-size data-subtitle-field type="number" min="24" max="72" value="${options.subtitle_font_size}" ${fieldDisabled?'disabled':''} class="w-full rounded-md border border-slate-300 bg-white px-2 py-2 disabled:opacity-50"></label><label class="text-xs font-bold text-slate-700"><span class="mb-1 block">文字颜色</span><input data-video-subtitle-color data-subtitle-field type="color" value="${this.escape(options.subtitle_font_color)}" ${fieldDisabled?'disabled':''} class="h-9 w-full rounded-md border border-slate-300 bg-white p-1 disabled:opacity-50"></label><label class="text-xs font-bold text-slate-700"><span class="mb-1 block">描边颜色</span><input data-video-subtitle-outline-color data-subtitle-field type="color" value="${this.escape(options.subtitle_outline_color)}" ${fieldDisabled?'disabled':''} class="h-9 w-full rounded-md border border-slate-300 bg-white p-1 disabled:opacity-50"></label><label class="text-xs font-bold text-slate-700"><span class="mb-1 block">描边宽度</span><input data-video-subtitle-outline data-subtitle-field type="number" min="0" max="8" value="${options.subtitle_outline}" ${fieldDisabled?'disabled':''} class="w-full rounded-md border border-slate-300 bg-white px-2 py-2 disabled:opacity-50"></label><label class="text-xs font-bold text-slate-700"><span class="mb-1 block">字幕位置</span><select data-video-subtitle-position data-subtitle-field ${fieldDisabled?'disabled':''} class="w-full rounded-md border border-slate-300 bg-white px-2 py-2 disabled:opacity-50">${positions.map(([value,label])=>`<option value="${value}" ${value===options.subtitle_position?'selected':''}>${label}</option>`).join('')}</select></label></div><div class="mt-4 border-t border-violet-100 pt-4"><div class="grid gap-3 md:grid-cols-2 lg:grid-cols-5"><label class="flex items-center gap-2 rounded-md border border-violet-200 bg-white px-3 py-2 text-xs font-bold text-violet-800"><input data-video-watermark type="checkbox" ${options.watermark_enabled?'checked':''} ${disabled?'disabled':''} class="h-4 w-4 rounded border-slate-300"><span>右上角品牌水印</span></label><label class="text-xs font-bold text-slate-700"><span class="mb-1 block">水印文案</span><input data-video-watermark-text data-watermark-field type="text" maxlength="40" value="${this.escape(options.watermark_text)}" ${watermarkDisabled?'disabled':''} class="w-full rounded-md border border-slate-300 bg-white px-2 py-2 disabled:opacity-50"></label><label class="text-xs font-bold text-slate-700"><span class="mb-1 block">字号</span><input data-video-watermark-size data-watermark-field type="number" min="18" max="56" value="${options.watermark_font_size}" ${watermarkDisabled?'disabled':''} class="w-full rounded-md border border-slate-300 bg-white px-2 py-2 disabled:opacity-50"></label><label class="text-xs font-bold text-slate-700"><span class="mb-1 block">颜色</span><input data-video-watermark-color data-watermark-field type="color" value="${this.escape(options.watermark_color)}" ${watermarkDisabled?'disabled':''} class="h-9 w-full rounded-md border border-slate-300 bg-white p-1 disabled:opacity-50"></label><label class="text-xs font-bold text-slate-700"><span class="mb-1 block">不透明度 ${options.watermark_opacity}%</span><input data-video-watermark-opacity data-watermark-field type="range" min="10" max="100" step="5" value="${options.watermark_opacity}" ${watermarkDisabled?'disabled':''} class="h-9 w-full disabled:opacity-50"></label></div><p class="mt-2 text-[11px] text-slate-500">水印固定在右上角并贯穿整片；建议使用品牌名或账号名，保持 35%～50% 不透明度。</p></div></div>`;
    }

    toggleSubtitleFields() {
        const enabled=Boolean(this.els.root.querySelector('[data-video-subtitles]')?.checked);
        this.els.root.querySelectorAll('[data-subtitle-field]').forEach(field=>{field.disabled=!enabled;});
    }

    toggleWatermarkFields() {
        const enabled=Boolean(this.els.root.querySelector('[data-video-watermark]')?.checked);
        this.els.root.querySelectorAll('[data-watermark-field]').forEach(field=>{field.disabled=!enabled;});
    }

    readVideoOptions() {
        const root=this.els.root;
        return {
            speed:Number(root.querySelector('[data-video-speed]')?.value||1),
            subtitles_enabled:Boolean(root.querySelector('[data-video-subtitles]')?.checked),
            subtitle_font_name:root.querySelector('[data-video-subtitle-font]')?.value||'Hiragino Sans GB',
            subtitle_font_size:Number(root.querySelector('[data-video-subtitle-size]')?.value||42),
            subtitle_font_color:root.querySelector('[data-video-subtitle-color]')?.value||'#FFFFFF',
            subtitle_outline_color:root.querySelector('[data-video-subtitle-outline-color]')?.value||'#000000',
            subtitle_outline:Number(root.querySelector('[data-video-subtitle-outline]')?.value??3),
            subtitle_position:root.querySelector('[data-video-subtitle-position]')?.value||'bottom',
            watermark_enabled:Boolean(root.querySelector('[data-video-watermark]')?.checked),
            watermark_text:root.querySelector('[data-video-watermark-text]')?.value||'Shioa',
            watermark_font_size:Number(root.querySelector('[data-video-watermark-size]')?.value||28),
            watermark_color:root.querySelector('[data-video-watermark-color]')?.value||'#111827',
            watermark_opacity:Number(root.querySelector('[data-video-watermark-opacity]')?.value||45),
        };
    }

    assetCard(item) {
        const image=item.image||{};
        const styleName=this.assets?.image_style?.scheme_name||this.visualScheme?.name||'所选视觉方案';
        const tts=item.tts||{};
        const timeline=item.timeline||{};
        const isBody=Number(item.sequence)>=1;
        const hasTimeline=isBody&&timeline.source==='whole_audio'&&Number(timeline.end_seconds)>Number(timeline.start_seconds);
        const imageActive=['queued','processing'].includes(image.status);
        const ttsActive=['queued','processing'].includes(tts.status);
        const audioBadge=hasTimeline?this.statusBadge('completed',100):this.statusBadge(tts.status,tts.progress);
        const audioBody=hasTimeline
            ? `<div class="mt-2 rounded-md border border-blue-100 bg-blue-50 p-3"><div class="text-xs font-black text-blue-900">${Number(timeline.start_seconds).toFixed(2)}s → ${Number(timeline.end_seconds).toFixed(2)}s</div><div class="mt-1 text-[11px] text-blue-700">整篇 WAV 连续时间轴 · 匹配置信度 ${(Number(timeline.confidence||0)*100).toFixed(1)}%</div></div>${tts.url?`<details class="mt-2"><summary class="cursor-pointer text-[11px] font-bold text-slate-500">另有分段 TTS（备用）</summary><audio data-scene-audio controls preload="metadata" src="${this.absolute(tts.url)}" class="mt-2 w-full"></audio><div data-audio-error class="mt-2 hidden break-words text-[11px] text-red-600"></div></details>`:''}`
            : (tts.url
                ? `<audio data-scene-audio controls preload="metadata" src="${this.absolute(tts.url)}" class="mt-3 w-full"></audio><div data-audio-error class="mt-2 hidden break-words text-[11px] text-red-600"></div><a href="${this.absolute(tts.url)}" target="_blank" class="mt-1 inline-block text-[11px] font-bold text-emerald-700 hover:underline">打开 WAV</a>`
                : `<div class="mt-2 flex h-16 items-center justify-center rounded-md border border-dashed bg-white text-xs text-slate-400">${ttsActive?'<i class="fas fa-spinner fa-spin mr-2"></i>生成中':isBody?'尚未生成分段 TTS':'标题镜头不参与正片音频'}</div>`);
        return `<article class="grid gap-3 rounded-lg border border-slate-200 p-4 lg:grid-cols-[minmax(0,1fr)_260px_280px]"><div class="min-w-0"><div class="text-xs font-black text-slate-900">${Number(item.sequence)===0?'000 · 标题镜头':`${String(item.sequence).padStart(3,'0')} · ${this.escape(item.scene_title||'分镜')}`}</div><div class="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-600">${this.escape(item.script_text||'')}</div><div class="mt-2 rounded-md bg-indigo-50 p-3 text-xs leading-5 text-indigo-900"><span class="font-black">Gem 原始画面提示词：</span>${this.escape(item.visual_prompt||'')}</div><div class="mt-2 text-[11px] font-bold text-emerald-700"><i class="fas fa-wand-magic-sparkles mr-1"></i>生成时绑定${this.escape(styleName)}图片契约与中文标签约束</div></div><div class="rounded-md border border-slate-200 bg-slate-50 p-3"><div class="flex items-center justify-between"><span class="text-xs font-black">图片</span>${this.statusBadge(image.status,image.progress)}</div>${image.url?`<img src="${this.absolute(image.url)}" alt="分镜 ${item.sequence}" class="mt-2 aspect-video w-full rounded-md bg-white object-contain">`:`<div class="mt-2 flex aspect-video items-center justify-center rounded-md border border-dashed bg-white text-xs text-slate-400">${imageActive?'<i class="fas fa-spinner fa-spin mr-2"></i>生成中':'尚未生成'}</div>`}<div class="mt-2 text-[11px] text-slate-500">尝试 ${image.attempt||0} 次${image.width?` · ${image.width}×${image.height}`:''}</div>${image.error?`<div class="mt-2 break-words text-[11px] text-red-600">${this.escape(image.error)}</div>`:''}<button data-asset-retry="image" data-asset-id="${this.escape(item.id)}" ${imageActive?'disabled':''} class="mt-2 w-full rounded-md border bg-white px-2 py-1.5 text-xs font-bold text-indigo-700 disabled:opacity-40">${image.status==='completed'?'重新生成':'生成 / 重试'}</button></div><div class="rounded-md border border-slate-200 bg-slate-50 p-3"><div class="flex items-center justify-between"><span class="text-xs font-black">${hasTimeline?'整篇 WAV 时间轴':'分段 TTS'}</span>${audioBadge}</div>${audioBody}<div class="mt-2 text-[11px] text-slate-500">分段 TTS 尝试 ${tts.attempt||0} 次${tts.seconds?` · ${Number(tts.seconds).toFixed(1)} 秒`:''}</div>${tts.error?`<div class="mt-2 break-words text-[11px] text-red-600">${this.escape(tts.error)}</div>`:''}${isBody?`<button data-asset-retry="tts" data-asset-id="${this.escape(item.id)}" ${ttsActive?'disabled':''} class="mt-2 w-full rounded-md border bg-white px-2 py-1.5 text-xs font-bold text-emerald-700 disabled:opacity-40">${hasTimeline?'另生成分段 TTS':tts.status==='completed'?'重新生成':'生成 / 重试'}</button>`:''}</div></article>`;
    }

    bindAudioPreviews() {
        this.els.root.querySelectorAll('[data-scene-audio]').forEach(audio=>{
            const errorBox=audio.parentElement?.querySelector('[data-audio-error]');
            const showError=()=>{
                if(!errorBox)return;
                const messages={1:'音频加载被中止',2:'音频网络加载失败',3:'浏览器无法解码该音频',4:'浏览器不支持该音频格式'};
                errorBox.textContent=messages[audio.error?.code]||'音频预览加载失败，请点击“打开 WAV”检查文件。';
                errorBox.classList.remove('hidden');
            };
            audio.addEventListener('loadedmetadata',()=>errorBox?.classList.add('hidden'));
            audio.addEventListener('error',showError);
            audio.load();
            if(audio.error)showError();
        });
    }

    metric(label,value){return `<div class="rounded-md bg-slate-50 p-3"><div class="text-slate-400">${label}</div><div class="mt-1 font-black text-slate-800">${value??0}</div></div>`;}
    formatBytes(value){const size=Number(value||0);if(!size)return '-';if(size>=1024*1024)return `${(size/1024/1024).toFixed(1)} MB`;if(size>=1024)return `${(size/1024).toFixed(1)} KB`;return `${size} B`;}
    statusBadge(status,progress){const text={pending:'未开始',queued:'排队中',processing:'处理中',completed:'已完成',failed:'失败',blocked:'验证阻塞'}[status]||status||'未开始';const cls=status==='completed'?'bg-emerald-100 text-emerald-700':['queued','processing'].includes(status)?'bg-indigo-100 text-indigo-700':['failed','blocked'].includes(status)?'bg-red-100 text-red-700':'bg-slate-200 text-slate-600';return `<span class="rounded-full px-2 py-1 text-[10px] font-black ${cls}">${text}${['queued','processing'].includes(status)?` · ${progress||0}%`:''}</span>`;}
    stage(name,done,detail){return `<div class="rounded-lg border ${done?'border-emerald-200 bg-emerald-50':'border-slate-200 bg-white'} p-3"><div class="text-xs font-black ${done?'text-emerald-800':'text-slate-700'}">${name}</div><div class="mt-1 text-[11px] ${done?'text-emerald-700':'text-slate-400'}">${this.escape(detail)}</div></div>`;}
    legacyTitleCard(title){return `<article class="rounded-lg border border-amber-200 bg-amber-50 p-4"><div class="text-xs font-black text-amber-900">0. 标题镜头</div><div class="mt-2 font-bold text-amber-950">${this.escape(title)}</div><div class="mt-2 text-xs text-amber-700">这条旧分镜没有单独保存标题提示词；请先点击“重新生成含标题分镜”。</div></article>`;}
    sceneCard(item){const isTitle=Number(item.sequence)===0;return `<article class="rounded-lg border ${isTitle?'border-amber-200 bg-amber-50':'border-slate-200 bg-white'} p-4"><div class="text-xs font-black ${isTitle?'text-amber-900':'text-slate-900'}">${Number(item.sequence)||0}. ${this.escape(item.scene_title||'分镜')}</div><div class="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-600">${this.escape(item.script_text||'')}</div><div class="mt-2 rounded-md bg-white/80 p-3 text-xs leading-5 text-indigo-800"><span class="font-black">画面提示词：</span>${this.escape(item.visual_prompt||'')}</div></article>`;}
    futureCard(title,text,icon){return `<div class="rounded-xl border border-dashed border-slate-300 bg-white p-5"><i class="fas fa-${icon} text-slate-300"></i><h3 class="mt-3 font-black text-slate-700">${title}</h3><p class="mt-2 text-xs leading-5 text-slate-500">${text}</p><span class="mt-3 inline-block rounded-full bg-slate-100 px-2 py-1 text-[11px] font-bold text-slate-500">后续阶段</span></div>`;}

    async showRaw(button){const old=button.innerHTML;button.disabled=true;button.innerHTML='读取中';try{const result=await this.request(`/api/report-visual-plans/jobs/${encodeURIComponent(this.storyboard.job_id)}/gemini-raw`);this.rawText=String(result.data?.raw_text||'');this.els.rawContent.textContent=this.rawText;this.els.rawMeta.textContent=result.data?.gemini_url||'';this.els.modal.classList.remove('hidden');document.body.classList.add('overflow-hidden');}catch(error){this.setNotice(`读取失败：${error.message}`,'error');}finally{button.disabled=false;button.innerHTML=old;}}
    closeRaw(){this.els.modal.classList.add('hidden');document.body.classList.remove('overflow-hidden');}
    setNotice(text,type){this.els.notice.textContent=text;this.els.notice.className=`mb-4 rounded-lg border px-4 py-3 text-sm ${type==='error'?'border-red-200 bg-red-50 text-red-700':type==='success'?'border-emerald-200 bg-emerald-50 text-emerald-700':'border-indigo-200 bg-indigo-50 text-indigo-700'}`;this.els.notice.classList.toggle('hidden',!text);}
    absolute(path){return /^https?:\/\//.test(path||'')?path:`${this.apiRoot()}${path||''}`;}
    escape(value){return String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));}
}

document.addEventListener('DOMContentLoaded',()=>{window.reportStickFigureVideoWorkflowDetailPage=new ReportStickFigureVideoWorkflowDetailPage();});
