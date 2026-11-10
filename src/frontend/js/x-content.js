/* Shared X operations catalog. User text is rendered with textContent only. */
(() => {
'use strict';
const $ = id => document.getElementById(id);
const sections = {
 patterns:['套路武器库','从三段式公式开始，把验证过的经验留作下一次创作的起点。'],
 cases:['外部爆款实验室','对照原文与拆解，分清高曝光、高回复和需要避开的样本。'],
 posts:['自有账号实战台','串起灵感来源、创作正文与发布后的实际表现。'],
 tactics:['评论接梗提词器','根据来访者的特征，找到自然、具体、有回应的下一句话。']
};
const labels = {id:'记录编号',name:'套路名称',category:'创作目的',time_slot:'发布时段 / 档期',hook_formula:'开头钩子',body_formula:'正文推进',cta_formula:'结尾互动',template_skeleton:'填空母版',evidence_status:'来源验证状态',avg_reply_rate:'来源预期回复率（%）',evidence_notes:'来源经验总结',tags:'标签',pattern_id:'关联套路',author_handle:'作者账号',author_name:'作者名称',author_followers:'采集时粉丝数',verified_badge:'认证标识',language:'语言',raw_text:'原帖文案',translated_text:'中文译文',media_type:'媒体类型',views:'浏览量',likes:'点赞数',replies:'回复数',reposts:'转发数',bookmarks:'书签数',reply_rate:'回复率（%）',like_rate:'点赞率（%）',follower_leverage:'粉丝撬动倍数',why_it_worked:'为什么有效 / 负向警示',reusable_hook:'可复用金句',is_negative_sample:'负向对照样本',screenshot_path:'截图证据路径',source_url:'原帖链接',published_at:'发布时间',metrics_captured_at:'指标采集时间',ref_case_ids:'参考案例',unresolved_case_ids:'来源待补全的案例',account_handle:'自有账号',title:'帖子标题',status:'帖子状态',content:'帖子正文',early_burst_notes:'冷启动表现',early_replies:'冷启动回复数',early_window_minutes:'冷启动窗口（分钟）',total_blue_v_replies:'蓝 V 回复数',core_interactors:'互动老铁名单',review_notes:'实战复盘',tactic_name:'接梗名称',trigger_condition:'触发条件',reply_logic:'接梗公式',sample_replies:'即用回复',frequent_friends:'匹配熟客',scenario_type:'场景类型',algorithm_purpose:'来源效果判断',priority:'优先级',action_type:'应对方式',created_at:'入库时间',updated_at:'更新时间',source_batch:'导入批次',is_mock:'数据来源'};
const enums = {category:{cold_start_engagement:'冷启动互动',viral_reach:'破圈曝光',viral_story:'微故事破圈',emotional_hook:'情绪钩子',pain_point_tool:'痛点工具',negative_avoid:'避坑'},evidence_status:{testing:'测试中',supported:'来源标记已验证',avoid:'负向禁用'},status:{draft_variant:'备选草稿',published:'已发布',archived:'已归档'},action_type:{reply:'回复',ignore:'忽略',block:'拉黑防范'},time_slot:{early_stall:'出早摊',morning:'早间茶馆',night_chat:'夜间复盘',anytime:'全天通用'}};
const fieldSets = {
 patterns:['name','category','time_slot','hook_formula','body_formula','cta_formula','template_skeleton','evidence_status','avg_reply_rate','evidence_notes','tags'],
 cases:['pattern_id','author_handle','author_name','author_followers','verified_badge','language','media_type','raw_text','translated_text','views','likes','replies','reposts','bookmarks','why_it_worked','reusable_hook','is_negative_sample','source_url','screenshot_path','published_at','metrics_captured_at'],
 posts:['title','account_handle','pattern_id','ref_case_ids','unresolved_case_ids','status','time_slot','content','early_burst_notes','early_replies','early_window_minutes','total_blue_v_replies','core_interactors','views','likes','replies','reposts','bookmarks','review_notes','source_url','screenshot_path','published_at','metrics_captured_at'],
 tactics:['tactic_name','scenario_type','trigger_condition','reply_logic','sample_replies','frequent_friends','algorithm_purpose','priority','action_type']
};
const arrays = new Set(['tags','ref_case_ids','unresolved_case_ids','core_interactors','sample_replies','frequent_friends']);
const numbers = new Set(['avg_reply_rate','author_followers','views','likes','replies','reposts','bookmarks','early_replies','early_window_minutes','total_blue_v_replies','priority']);
const longText = new Set(['hook_formula','body_formula','cta_formula','template_skeleton','evidence_notes','raw_text','translated_text','why_it_worked','reusable_hook','content','early_burst_notes','review_notes','trigger_condition','reply_logic','algorithm_purpose']);
const required = {patterns:'name',cases:'raw_text',posts:'content',tactics:'tactic_name'};
let kind = sections[location.hash.slice(1)] ? location.hash.slice(1) : 'patterns';
let page=1, summary=null, rows=[], sequence=0, editing=null, editingKind=null, timer, busy=false;
function el(tag,cls,text){const n=document.createElement(tag); if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
function button(text,fn,cls=''){const b=el('button',cls,text);b.type='button';b.onclick=fn;return b;}
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,3500);}
async function api(path,options={}){
 const token=localStorage.getItem('access_token');
 const response=await fetch(`${window.API_BASE_URL||'/api/v1'}/x-content${path}`,{...options,headers:{...(token?{Authorization:`Bearer ${token}`} : {}),...options.headers}});
 let data;try{data=await response.json();}catch{throw Error('服务暂时不可用，请稍后重试。');}
 if(!response.ok||!data.success)throw Error(data.error||data.message||'操作失败');return data.data;
}
async function copy(text){try{await navigator.clipboard.writeText(text||'');toast('已复制');}catch{toast('复制受浏览器限制，请在详情中选中文本复制。');}}
function display(key,value){
 if(value==null||value==='')return '未记录';
 if(key==='is_mock')return value?'示例数据':'来源数据';
 if(key==='is_negative_sample')return value?'负向对照':'正向学习';
 if(key.endsWith('_at'))return new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+'（北京时间）';
 if(enums[key]?.[value])return enums[key][value];
 if(Array.isArray(value))return value.join('\n')||'未记录';
 return typeof value==='number'?value.toLocaleString('zh-CN'):String(value);
}
function title(row){return row.name||row.tactic_name||row.title||(kind==='cases'?`@${row.author_handle||'未知作者'}`:row.content?.slice(0,32))||row.id;}
function mainText(row){return row.template_skeleton||row.raw_text||row.content||row.sample_replies?.join('\n\n')||'';}
async function loadSummary(){
 summary=await api('/summary');
 $('overview').replaceChildren(...Object.entries(sections).map(([k,v])=>{const d=el('div','metric');d.append(el('span','',v[0]),el('strong','',summary.counts[k]));return d;}));
 $('source-notice').textContent=summary.mock_count?`当前包含 ${summary.mock_count} 条示例，卡片带有「MOCK」标记。真实数据导入时可一并清理。`:'当前展示已保存的来源数据 · 未提供的指标留空，比例按原始数值计算';
 $('clear-mock').hidden=!summary.can_edit||!summary.mock_count;
 $('import-open').hidden=!summary.can_edit;$('create').hidden=!summary.can_edit;
}
function setup(){
 $('tabs').replaceChildren(...Object.entries(sections).map(([k,v])=>{const b=button(v[0],()=>{location.hash=k;},k===kind?'active':'');b.setAttribute('aria-current',k===kind?'page':'false');return b;}));
 $('section-name').textContent=sections[kind][0];$('section-description').textContent=sections[kind][1];
 const f=$('filter');f.replaceChildren(new Option('全部',''));
 let opts=kind==='patterns'?enums.evidence_status:kind==='cases'?{positive:'正向学习',negative:'负向对照'}:kind==='posts'?enums.status:enums.action_type;
 for(const [v,t] of Object.entries(opts))f.add(new Option(t,v));
 $('slot').parentElement.hidden=kind!=='patterns';
 $('sort').replaceChildren(new Option('最近更新','updated_at'));
 if(['cases','posts'].includes(kind)){ $('sort').add(new Option('回复率最高','reply_rate'));$('sort').add(new Option('浏览量最高','views')); }
 if(kind==='cases')$('sort').add(new Option('粉丝撬动倍数最高','follower_leverage'));
 if(kind==='tactics')$('sort').add(new Option('优先级最高','priority'));
}
function card(row){
 const c=el('article','card'+(row.is_negative_sample?' negative':''));const tags=el('div','badges');
 tags.append(el('span','badge',row.id));if(row.is_mock)tags.append(el('span','badge warn','MOCK 示例'));
 if(row.is_negative_sample)tags.append(el('span','badge warn','负向对照'));
 for(const f of ['evidence_status','status','action_type'])if(row[f])tags.append(el('span','badge teal',display(f,row[f])));
 c.append(tags,el('h3','',title(row)));
 if(kind==='patterns'){
  for(const [f,label] of [['hook_formula','钩子'],['body_formula','正文'],['cta_formula','互动']]){const p=el('p','formula excerpt');p.append(el('strong','',label),document.createTextNode(row[f]||'待补充'));c.append(p);}
  c.append(el('span','badge',display('time_slot',row.time_slot)));
 } else if(kind==='tactics'){
  c.append(el('p','excerpt',row.trigger_condition||'尚未记录触发条件'),el('p','formula excerpt',row.sample_replies?.[0]||row.reply_logic||''));
 } else {
  c.append(el('p','excerpt',row.raw_text||row.content));
  const nums=el('div','numbers');
  for(const [key,label] of [['views','浏览'],['reply_rate','回复率'],kind==='cases'?['follower_leverage','撬动倍数']:['total_blue_v_replies','蓝 V 回复']]){const d=el('div');d.append(el('strong','',row[key]==null?'—':display(key,row[key])+(key==='reply_rate'?'%':key==='follower_leverage'?'×':'')),el('span','',label));nums.append(d);}c.append(nums);
  c.append(el('p','formula excerpt',row.why_it_worked||row.early_burst_notes||'等待补充实战记录'));
  if(row.unresolved_case_ids?.length)c.append(el('span','badge warn',`${row.unresolved_case_ids.length} 个参考案例待补全`));
 }
 const actions=el('div','actions');actions.append(button('查看详情',()=>showDetail(row)),button(kind==='patterns'?'复制母版':kind==='tactics'?'复制首条回复':'复制文案',()=>copy(kind==='tactics'?row.sample_replies?.[0]:mainText(row))));
 if(kind==='patterns'&&summary?.can_edit&&row.evidence_status!=='avoid')actions.append(button('用于新草稿',()=>openEditor({pattern_id:row.id,content:row.template_skeleton||'',status:'draft_variant',time_slot:row.time_slot},'posts')));
 c.append(actions);return c;
}
async function load(){
 const active=++sequence;$('cards').setAttribute('aria-busy','true');$('result').textContent='正在读取…';$('prev').disabled=true;$('next').disabled=true;
 const params=new URLSearchParams({page,per_page:24,q:$('search').value,sort:$('sort').value});
 const filterKey={patterns:'evidence_status',cases:'polarity',posts:'status',tactics:'action_type'}[kind];if($('filter').value)params.set(filterKey,$('filter').value);
 if(kind==='patterns'&&$('slot').value)params.set('time_slot',$('slot').value);
 try{
  const data=await api(`/${kind}?${params}`);if(active!==sequence)return;
  rows=data.items;$('result').textContent=`共 ${data.total} 条记录`;
  $('cards').replaceChildren(...(rows.length?rows.map(card):[el('div','empty','没有符合条件的记录。试试其他关键词，或导入第一份数据。')]));
  $('page').textContent=`${page} / ${Math.max(1,Math.ceil(data.total/24))}`;$('prev').disabled=page===1;$('next').disabled=page*24>=data.total;
 }catch(e){if(active!==sequence)return;const box=el('div','empty');box.append(el('p','',e.message),button('重新加载',load));$('cards').replaceChildren(box);$('result').textContent='读取失败';}
 finally{if(active===sequence)$('cards').setAttribute('aria-busy','false');}
}
function showDetail(row,recordKind=kind){
 $('detail-title').textContent=row.name||row.tactic_name||row.title||row.author_handle||row.id;
 const body=$('detail-body');body.replaceChildren();
 for(const key of ['id',...fieldSets[recordKind],...(recordKind==='cases'?['reply_rate','like_rate','follower_leverage']:recordKind==='posts'?['reply_rate']:[]),'is_mock','created_at','updated_at']){
  const value=row[key];if(value==null||value===''||(Array.isArray(value)&&!value.length))continue;
  const section=el('section');section.append(el('h3','',labels[key]));
  if(key==='sample_replies')for(const reply of value){const d=el('div','reply');d.append(el('p','',reply),button('复制',()=>copy(reply)));section.append(d);}
  else if(key==='pattern_id'||key==='ref_case_ids'){
   const targetKind=key==='pattern_id'?'patterns':'cases';
   for(const id of Array.isArray(value)?value:[value]){const name=summary[targetKind]?.find(r=>r.id===id)?.name;section.append(button(name?`${id} · ${name}`:id,async()=>{try{showDetail(await api(`/${targetKind}/${encodeURIComponent(id)}`),targetKind);}catch(e){toast(e.message);}},'link-button'));}
  }else if(key==='source_url'){
   try{const url=new URL(value);if(!['http:','https:'].includes(url.protocol))throw Error();const a=el('a','link-button','查看 X 原帖 ↗');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';section.append(a);}catch{section.append(el('p','',value));}
  }else section.append(el('p','',display(key,value)));
  body.append(section);
 }
 const actions=$('detail-actions');actions.replaceChildren(button('复制全文',()=>copy(mainText(row))));if(summary.can_edit)actions.append(button('编辑记录',()=>{ $('detail').close();openEditor(row,recordKind);},'primary'));
 if(!$('detail').open)$('detail').showModal();
}
function openEditor(row={},recordKind=kind){
 editing=row.id||null;editingKind=recordKind;$('edit-error').textContent='';$('editor-title').textContent=(editing?'编辑 · ':'新建 · ')+sections[recordKind][0];$('fields').replaceChildren();
 for(const key of ['id',...fieldSets[recordKind]]){
  const label=el('label',longText.has(key)||arrays.has(key)?'wide':'',labels[key]);let input;
  if(key==='pattern_id'||key==='ref_case_ids'){
   input=el('select');if(key==='ref_case_ids')input.multiple=true;else input.add(new Option('不关联套路',''));
   for(const option of summary[key==='pattern_id'?'patterns':'cases']){const o=new Option(option.name,option.id);o.selected=Array.isArray(row[key])?row[key].includes(option.id):row[key]===option.id;input.add(o);}
  }else if(enums[key]&&key!=='time_slot'){
   input=el('select');for(const [value,text] of Object.entries(enums[key]))input.add(new Option(text,value));
   input.value=row[key]||({evidence_status:'testing',status:'draft_variant',action_type:'reply',category:'cold_start_engagement'}[key]);
  }else if(key==='is_negative_sample'){
   input=el('select');input.add(new Option('正向学习','false'));input.add(new Option('负向对照','true'));input.value=String(row[key]||false);
  }else if(arrays.has(key)){
   input=el('div','array-editor');input.dataset.field=key;
   const items=el('div','array-items');
   const add=value=>{const entry=el('div','reply');const field=el('textarea','entry-input');field.value=value;field.setAttribute('aria-label',labels[key]+'条目');entry.append(field,button('移除',()=>entry.remove()));items.append(entry);};
   for(const value of row[key]||[])add(value);
   input.append(items,button('＋ 添加条目',()=>add('')));
  }else if(longText.has(key)){
   input=el('textarea');input.value=row[key]||'';
  }else{
   input=el('input');input.type=numbers.has(key)?'number':key.endsWith('_at')?'datetime-local':'text';
   if(numbers.has(key)){input.min=key==='early_window_minutes'?'1':'0';input.step=key==='avg_reply_rate'?'0.01':'1';}
   if(key.endsWith('_at')&&row[key])input.value=new Date(new Date(row[key]).getTime()+8*3600000).toISOString().slice(0,19);
   else input.value=row[key]??(key==='account_handle'?'vimson999':key==='early_window_minutes'?7:'');
  }
  if(input.tagName!=='DIV')input.name=key;
  if(key.endsWith('_at'))input.step='1';
  input.required=key===required[recordKind];if(key==='id'){input.readOnly=!!editing;input.placeholder='留空自动生成';}
  label.append(input);if(arrays.has(key))label.append(el('small','',key==='ref_case_ids'?'按住 Command / Ctrl 可多选':'每个条目独立保存，可保留段落换行'));
  if(key.endsWith('_at'))label.append(el('small','','以北京时间填写'));
  $('fields').append(label);
 }
 $('editor').showModal();
}
$('edit-form').onsubmit=async e=>{
 e.preventDefault();if(busy)return;busy=true;$('save').disabled=true;const payload={};
 for(const input of $('fields').querySelectorAll('[name]')){
  const key=input.name;let value=input.value;
  if(key==='id'&&!value)continue;
  if(key==='ref_case_ids')value=Array.from(input.selectedOptions).map(o=>o.value);
  else if(arrays.has(key))value=value?value.split('\n').map(s=>s.trim()).filter(Boolean):[];
  else if(key==='is_negative_sample')value=value==='true';
  else if(numbers.has(key))value=value===''?null:Number(value);
  else if(key.endsWith('_at'))value=value?value+'+08:00':null;
  else if(key==='pattern_id')value=value||null;
  payload[key]=value;
 }
 for(const group of $('fields').querySelectorAll('.array-editor'))payload[group.dataset.field]=Array.from(group.querySelectorAll('.entry-input')).map(i=>i.value).filter(v=>v.trim());
 try{await api(`/${editingKind}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});$('editor').close();await loadSummary();await load();toast('记录已保存');}
 catch(e){$('edit-error').textContent=e.message;}finally{busy=false;$('save').disabled=false;}
};
for(const b of document.querySelectorAll('[data-close]'))b.onclick=()=>{if(!busy)$(b.dataset.close).close();};
for(const d of document.querySelectorAll('dialog'))d.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
$('create').onclick=()=>openEditor();$('import-open').onclick=()=>$('import-dialog').showModal();
$('import-submit').onclick=async()=>{
 if(busy)return;const file=$('import-file').files[0];if(!file||file.size>5*1024*1024){$('import-message').textContent='请选择不超过 5 MiB 的 JSON 文件。';return;}
 busy=true;$('import-submit').disabled=true;$('import-message').textContent='正在校验并导入…';
 const form=new FormData();form.append('file',file);form.append('replace_mock',String($('replace-mock').checked));
 try{const r=await api('/import',{method:'POST',body:form});$('import-message').textContent=`导入完成：新增 ${r.new}，更新 ${r.updated}，清理示例 ${r.deleted_mock}。`;await loadSummary();page=1;await load();}catch(e){$('import-message').textContent=e.message;}finally{busy=false;$('import-submit').disabled=false;}
};
$('clear-mock').onclick=async()=>{if(!confirm('仅删除带 MOCK 标记的示例记录，继续吗？'))return;try{const r=await api('/mock',{method:'DELETE'});toast(`已清理 ${r.deleted} 条示例`);await loadSummary();page=1;await load();}catch(e){toast(e.message);}};
$('search').oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>{page=1;load();},250);};
for(const id of ['filter','sort','slot'])$(id).onchange=()=>{page=1;load();};
$('reset').onclick=()=>{$('search').value='';$('slot').value='';page=1;setup();load();};
$('prev').onclick=()=>{if(page>1){page--;load();}};$('next').onclick=()=>{page++;load();};
window.addEventListener('hashchange',()=>{const value=location.hash.slice(1);if(sections[value]){kind=value;page=1;$('search').value='';$('slot').value='';setup();load();}});
setup();loadSummary().then(load).catch(e=>{$('source-notice').textContent=e.message;const box=el('div','empty');box.append(el('p','',e.message),button('重试',()=>location.reload()));$('cards').replaceChildren(box);$('cards').setAttribute('aria-busy','false');});
})();
