'use strict';
// Lesson 7: never reuse UI data across users or tenants, including late HTTP responses.
let token = '';
let actor = null;
let authEpoch = 0;
let pendingRequests = new Set();
const el = id => document.getElementById(id);
const say = (id, value) => { el(id).textContent = String(value); };

function clearPrivateUI() {
  for (const id of ['documents', 'jobs', 'users', 'sources', 'audit-summary']) el(id).replaceChildren();
  for (const id of ['answer', 'upload-status', 'ask-status', 'users-status', 'identity', 'job-metrics']) say(id, '');
  for (const id of ['upload-form', 'ask-form', 'user-form']) el(id).reset();
  el('admin-panel').hidden = true;
  el('users-card').hidden = true;
  el('audit-card').hidden = true;
  el('workspace').hidden = true;
  say('identity', '未登录');
}
function resetIdentity() {
  // Invalidate every outstanding response before resetting DOM.
  authEpoch++;
  for (const ctl of pendingRequests) ctl.abort();
  pendingRequests.clear();
  token = '';
  actor = null;
  clearPrivateUI();
  el('password').value = '';
  el('login-section').hidden = false;
}
function guard(epoch) {
  if (epoch !== authEpoch || !token || !actor) throw new DOMException('用户已切换', 'AbortError');
}
function isAbort(err) { return err && err.name === 'AbortError'; }
async function api(path, opts = {}, epoch = authEpoch) {
  const controller = new AbortController();
  pendingRequests.add(controller);
  const bearer = token; // snapshot credential; never accidentally use next user's token
  const headers = bearer ? { 'Authorization': `Bearer ${bearer}` } : {};
  const config = {method: opts.method || 'GET', headers, cache:'no-store', signal: controller.signal};
  if (opts.form) config.body = opts.form;
  else if (opts.data !== undefined) {
    headers['Content-Type'] = 'application/json';
    config.body = JSON.stringify(opts.data);
  }
  try {
    const response = await fetch(path, config);
    if (authEpoch !== epoch || controller.signal.aborted) throw new DOMException('用户已切换','AbortError');
    if (response.status === 204) return null;
    const body = await response.json().catch(() => ({}));
    if (authEpoch !== epoch || controller.signal.aborted) throw new DOMException('用户已切换','AbortError');
    if (!response.ok) {
      if(response.status===401 && bearer && authEpoch===epoch){
        resetIdentity();
        say('login-status','账号已失效或被停用，请联系管理员。');
      }
      const retry=response.headers.get('Retry-After');
      throw new Error((body.detail || `HTTP ${response.status}`)+(retry?`（${retry} 秒后可重试）`:''));
    }
    return body;
  } finally {
    pendingRequests.delete(controller);
  }
}
function item(root,title,details,actions = []) {
  const box=document.createElement('div');box.className='item';
  const head=document.createElement('strong');head.textContent=title;box.appendChild(head);
  const info=document.createElement('p');info.className='meta';info.textContent=details;box.appendChild(info);
  for(const a of actions){
    const b=document.createElement('button');b.type='button';b.textContent=a.label;
    if(a.danger)b.className='danger';
    b.onclick=async()=>{b.disabled=true;try{await a.fn()}catch(e){if(!isAbort(e))alert(e.message)}finally{b.disabled=false}};
    box.appendChild(b);
  }
  root.appendChild(box);
}
function admin() { return actor && actor.role === 'admin'; }
async function refresh() {
  const epoch = authEpoch;
  guard(epoch);
  const [docs,jobs,users,metrics] = await Promise.all([
    api('/api/v2/documents',{},epoch),
    api('/api/v2/jobs',{},epoch),
    admin() ? api('/api/v2/users',{},epoch) : Promise.resolve([]),
    admin() ? api('/api/v2/jobs/metrics',{},epoch) : Promise.resolve(null)
  ]);
  guard(epoch);
  // Render only when all responses for the SAME logged-in account succeeded.
  el('documents').replaceChildren();el('jobs').replaceChildren();el('users').replaceChildren();
  docs.forEach(d=>item(el('documents'),d.filename,`${d.visibility} · ${d.status} · ${d.chunk_count} 块 · ${d.embedding_spec||'未索引'}`,admin()?[{label:'删除',danger:true,fn:async()=>{if(confirm('确定删除？')){await api(`/api/v2/documents/${encodeURIComponent(d.id)}`,{method:'DELETE'});await refresh();}}},...(d.status==='failed'?[{label:'重试',fn:async()=>{await api(`/api/v2/documents/${encodeURIComponent(d.id)}/retry`,{method:'POST'});await refresh();}}]:[]),...(d.status==='ready'?[{label:'重新索引',fn:async()=>{await api(`/api/v2/documents/${encodeURIComponent(d.id)}/reindex`,{method:'POST'});await refresh();}}]:[])]:[]));
  if(metrics){
    const labels=Object.entries(metrics.counts).map(([k,v])=>`${k}: ${v}`).join(' · ');
    say('job-metrics', `索引任务：${labels||'暂无'} · 最久排队 ${metrics.oldest_queued_seconds} 秒 · 租约过期 ${metrics.stale_leases}`);
  }
  jobs.forEach(j=>item(el('jobs'),j.id,`${j.state} · 尝试 ${j.attempts}/${j.max_attempts} · ${j.error_message||'—'}`));
  if(admin())users.forEach(u=>item(el('users'),u.email,`${u.role} · ${u.active?'启用':'停用'}`,
     u.active && u.id!==actor.id ? [{label:'停用账号',danger:true,fn:async()=>{
       if(confirm(`确认停用 ${u.email}？该用户将立即失去访问权限。`)){
         await api(`/api/v2/users/${encodeURIComponent(u.id)}/disable`,{method:'POST'});
         await refresh();
       }
     }}]:[]));
}
el('login-form').addEventListener('submit',async ev=>{
  ev.preventDefault();
  const credentials={email:el('email').value,password:el('password').value};
  // Clear last account's data BEFORE processing the next login.
  resetIdentity();
  const epoch=authEpoch;
  say('login-status','验证中...');
  try {
    const res=await api('/api/v2/auth/login',{method:'POST',data:credentials},epoch);
    if(epoch!==authEpoch)return;
    token=res.access_token;
    actor=await api('/api/v2/auth/me',{},epoch);
    guard(epoch);
    el('password').value='';
    say('identity',`${actor.email} / ${actor.role}`);
    el('login-section').hidden=true;
    el('workspace').hidden=false;
    el('admin-panel').hidden=!admin();el('users-card').hidden=!admin();el('audit-card').hidden=!admin();
    await refresh();guard(epoch);
    say('login-status','');
  } catch(e) {
    if(epoch===authEpoch && !isAbort(e)) {resetIdentity();say('login-status',e.message);}
  }
});
el('logout').addEventListener('click',()=>{
  const oldToken=token;
  resetIdentity();
  say('login-status','已退出登录');
  // Logout request uses captured token; never read the new user's global token.
  if(oldToken)fetch('/api/v2/auth/logout',{method:'POST',headers:{'Authorization':`Bearer ${oldToken}`},cache:'no-store'}).catch(()=>{});
});
el('refresh').onclick=()=>refresh().catch(e=>{if(!isAbort(e))alert(e.message)});
el('audit-refresh').onclick=async()=>{
  const epoch=authEpoch;
  try{
    guard(epoch);
    const summary=await api('/api/v2/audit/summary',{},epoch);
    guard(epoch);
    el('audit-summary').replaceChildren();
    for(const event of summary.events)item(el('audit-summary'),event.action,`${event.outcome} · ${event.count} 次`);
    if(!summary.events.length)say('audit-summary','过去 24 小时没有事件');
  }catch(e){if(!isAbort(e)&&epoch===authEpoch)alert(e.message)}
};
el('upload-form').addEventListener('submit',async ev=>{
  ev.preventDefault();const epoch=authEpoch;guard(epoch);
  const f=el('file').files[0];if(!f)return;
  const form=new FormData();form.append('file',f);form.append('visibility',el('visibility').value);
  say('upload-status','正在传输文件...');
  try{const r=await api('/api/v2/documents',{method:'POST',form},epoch);guard(epoch);
    say('upload-status',`已入队，任务：${r.job_id}。点击刷新查看执行进度。`);await refresh();
  }catch(e){if(epoch===authEpoch&&!isAbort(e))say('upload-status',e.message)}
});
el('ask-form').addEventListener('submit',async ev=>{
  ev.preventDefault();const epoch=authEpoch;guard(epoch);
  say('ask-status','检索中...');
  try{const r=await api('/api/v2/ask',{method:'POST',data:{question:el('question').value,limit:3}},epoch);guard(epoch);
    say('answer',r.answer);el('sources').replaceChildren();
    r.sources.forEach(s=>item(el('sources'),`${s.citation} ${s.filename} ${s.page?'第'+s.page+'页':''}`,`${s.excerpt}（相似度 ${s.similarity}）`));
    say('ask-status',r.evidence_status);
  }catch(e){if(epoch===authEpoch&&!isAbort(e))say('ask-status',e.message)}
});
el('user-form').addEventListener('submit',async ev=>{
  ev.preventDefault();const epoch=authEpoch;guard(epoch);say('users-status','创建中...');
  try{await api('/api/v2/users',{method:'POST',data:{email:el('user-email').value,password:el('user-password').value,role:el('user-role').value}},epoch);guard(epoch);
    el('user-password').value='';say('users-status','创建成功');await refresh();
  }catch(e){if(epoch===authEpoch&&!isAbort(e))say('users-status',e.message)}
});
// bfcache history restoration can revive an already rendered document.
window.addEventListener('pageshow', ev=>{if(ev.persisted)resetIdentity();});
resetIdentity();
