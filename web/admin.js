'use strict';
// No ADMIN_API_KEY or KB_SERVICE_KEY is persisted in JavaScript storage.
let csrfToken = '';
const byId = id => document.getElementById(id);
const setText = (id, value) => { byId(id).textContent = String(value); };
const fmtTime = s => s ? String(s).replace('T', ' ').slice(0, 19) : '—';

async function api(path, {method = 'GET', data} = {}) {
  const headers = {};
  if (!['GET', 'HEAD'].includes(method)) headers['X-CSRF-Token'] = csrfToken;
  if (data !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, {
    method, headers, credentials: 'same-origin', cache: 'no-store',
    body: data === undefined ? undefined : JSON.stringify(data)
  });
  if (res.status === 204) return null;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401) lockUI();
    throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`);
  }
  return body;
}
function lockUI() {
  csrfToken = '';
  byId('dashboard').classList.add('hidden');
  byId('logout').classList.add('hidden');
  byId('login-panel').classList.remove('hidden');
}
function openUI() {
  byId('login-panel').classList.add('hidden');
  byId('dashboard').classList.remove('hidden');
  byId('logout').classList.remove('hidden');
}
async function authenticate() {
  const res = await fetch('/api/admin/ui/me', {credentials: 'same-origin', cache: 'no-store'});
  if (!res.ok) { lockUI(); return; }
  const data = await res.json();
  csrfToken = data.csrf_token;
  openUI(); await refreshAll();
}

byId('login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button');
  const password = byId('password').value;
  byId('password').value = '';
  button.disabled = true;
  setText('login-status', '验证中…');
  try {
    const result = await api('/api/admin/ui/login', {method: 'POST', data: {password}});
    csrfToken = result.csrf_token;
    openUI(); setText('login-status', '');
    await refreshAll();
  } catch (e) { setText('login-status', e.message); }
  finally { button.disabled = false; }
});
byId('logout').addEventListener('click', async () => {
  try { await api('/api/admin/ui/logout', {method: 'POST'}); }
  catch (e) { setText('login-status', e.message); }
  lockUI();
});

let lastIndex = null;
async function refreshAll() {
  const [index, docs] = await Promise.all([
    api('/api/admin/kb/index'), api('/api/kb/documents')
  ]);
  lastIndex = index;
  setText('doc-count', index.document_count);
  setText('chunk-count', index.chunk_count);
  setText('index-state', index.requires_reindex ? '需要重建' : '可用');
  byId('index-state').className = index.requires_reindex ? 'attention' : 'healthy';
  setText('active-spec', index.active_spec);
  setText('stored-spec', index.stored_specs.join(', ') || '空索引');
  renderDocuments(docs);
}
function renderDocuments(items) {
  const tbody = byId('documents');
  tbody.replaceChildren();
  if (!items.length) {
    const tr = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 4; cell.textContent = '暂无文档，请先上传示例文件';
    tr.appendChild(cell); tbody.appendChild(tr);
    return;
  }
  for (const item of items) {
    const tr = document.createElement('tr');
    for (const val of [item.filename, item.chunk_count, fmtTime(item.created_at)]) {
      const td = document.createElement('td'); td.textContent = String(val); tr.appendChild(td);
    }
    const action = document.createElement('td');
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'danger subtle'; btn.textContent = '删除';
    btn.addEventListener('click', async () => {
      if (!confirm(`删除「${item.filename}」及其文本块？不可撤销。`)) return;
      btn.disabled = true;
      try {
        await api(`/api/kb/documents/${encodeURIComponent(item.id)}`, {method: 'DELETE'});
        await refreshAll(); setText('documents-status', '已删除文档及对应的索引数据。');
      } catch (e) { btn.disabled = false; setText('documents-status', e.message); }
    });
    action.appendChild(btn); tr.appendChild(action); tbody.appendChild(tr);
  }
}
byId('refresh').addEventListener('click', async () => {
  try { await refreshAll(); setText('documents-status', '文档列表已刷新'); }
  catch (e) { setText('documents-status', e.message); }
});
byId('doc-file').addEventListener('change', () => {
  const file = byId('doc-file').files[0];
  setText('selected-file', file ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB` : '从电脑选取文件');
});

// XHR progress = network bytes only; indexing status remains indeterminate.
function uploadWithProgress(file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/kb/documents');
    xhr.withCredentials = true;
    xhr.setRequestHeader('X-CSRF-Token', csrfToken);
    const bar = byId('upload-bar');
    xhr.upload.onprogress = event => {
      if (!event.lengthComputable) return;
      const percent = Math.min(100, Math.round(event.loaded / event.total * 100));
      bar.style.width = `${percent}%`;
      bar.parentElement.setAttribute('aria-valuenow', String(percent));
      setText('upload-status', `网络传输 ${percent}%…`);
    };
    xhr.upload.onload = () => setText('upload-status', '文件传输完成，正在提取文本并生成向量…（尚无索引进度百分比）');
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch (_) { /* no JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error('网络中断，请检查服务日志'));
    xhr.timeout = 0;
    const data = new FormData(); data.append('file', file);
    xhr.send(data);
  });
}
byId('upload-form').addEventListener('submit', async event => {
  event.preventDefault();
  const file = byId('doc-file').files[0];
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) { setText('upload-status', '文件超过 2 MiB 限制'); return; }
  const button = byId('upload-button'); button.disabled = true;
  byId('upload-bar').style.width = '0%';
  try {
    const result = await uploadWithProgress(file);
    setText('upload-status', `已完成：${result.filename}，生成 ${result.chunks} 个文本块。`);
    event.currentTarget.reset(); setText('selected-file', '从电脑选取文件');
    await refreshAll();
  } catch (e) { setText('upload-status', `上传或索引失败：${e.message}`); }
  finally { button.disabled = false; }
});

byId('probe').addEventListener('click', async event => {
  const button = event.currentTarget; button.disabled = true;
  setText('reindex-status', '测试 Embeddings 接口中… 云端模型可能产生少量调用费用。');
  try {
    const result = await api('/api/admin/kb/embedding-test', {
      method: 'POST', data: {text: '测试企业知识库语义向量服务是否可用'}
    });
    setText('reindex-status', `连接正常：${result.cloud_mode ? '云端模型' : '本地回退算法'}；维度 ${result.dimension}；索引未修改。`);
  } catch (e) { setText('reindex-status', `连接失败：${e.message}`); }
  finally { button.disabled = false; }
});

byId('reindex').addEventListener('click', async event => {
  if (!lastIndex) return;
  const count = lastIndex.chunk_count;
  const answer = prompt(`即将重新向量化 ${count} 个文本块，可能产生云端 API 费用。请输入 REINDEX 确认：`);
  if (answer !== 'REINDEX') return;
  const button = event.currentTarget; button.disabled = true;
  setText('reindex-status', '正在调用 Embeddings 并构建新索引… 期间请不要关闭页面或修改文档。');
  try {
    const result = await api('/api/admin/kb/reindex', {method: 'POST', data: {confirm: 'REINDEX'}});
    setText('reindex-status', `索引重建成功：${result.chunks_reindexed} 个文本块。`);
    await refreshAll();
  } catch (e) { setText('reindex-status', `重建失败：${e.message}。原索引应仍保持不变。`); }
  finally { button.disabled = false; }
});
function renderSources(items) {
  const list = byId('sources'); list.replaceChildren();
  for (const item of items) {
    const li = document.createElement('li');
    const title = document.createElement('strong');
    title.textContent = `${item.citation} ${item.filename}${item.page ? ' · 第 ' + item.page + ' 页' : ''}`;
    const meta = document.createElement('small'); meta.textContent = `文本块 #${item.position + 1} · 相似度 ${item.similarity}`;
    const excerpt = document.createElement('p'); excerpt.textContent = item.excerpt;
    li.append(title, meta, excerpt); list.appendChild(li);
  }
  if (!items.length) { const li = document.createElement('li'); li.textContent = '没有达到阈值的检索片段'; list.appendChild(li); }
}
async function testQuestion(generate) {
  const question = byId('question').value.trim();
  if (!question) { setText('ask-status', '请输入问题'); return; }
  const limit = Number(byId('top-k').value);
  const button = byId(generate ? 'ask-button' : 'search-button');
  button.disabled = true; setText('ask-status', generate ? '正在检索并生成回答…' : '正在执行向量检索…');
  try {
    const result = generate
      ? await api('/api/admin/ui/kb/ask', {method: 'POST', data: {question, limit}})
      : await api(`/api/admin/ui/kb/search?q=${encodeURIComponent(question)}&limit=${limit}`);
    byId('result').hidden = false;
    setText('answer', generate ? result.answer : '仅检索模式：未调用大模型。');
    setText('evidence-tag', generate ? `证据状态：${result.evidence_status}（不等于逐句事实核验）` : '仅显示相似度检索结果');
    renderSources(result.sources || []);
    setText('ask-status', `已检索 ${result.sources?.length || 0} 条来源`);
  } catch (e) { setText('ask-status', `请求失败：${e.message}`); }
  finally { button.disabled = false; }
}
byId('search-button').addEventListener('click', () => testQuestion(false));
byId('ask-form').addEventListener('submit', e => { e.preventDefault(); testQuestion(true); });
authenticate().catch(e => { lockUI(); setText('login-status', `初始化失败：${e.message}`); });
