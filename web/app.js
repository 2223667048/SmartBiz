// Same-origin API; the Langflow API key is NEVER exposed to this browser script.
const conversation = document.querySelector('#conversation');
const question = document.querySelector('#question');
const statusText = document.querySelector('#status');
let sessionToken = sessionStorage.getItem('smartbiz_session_token');

async function request(path, {method = 'GET', data} = {}) {
  const headers = {};
  if (sessionToken) headers['X-Session-Token'] = sessionToken;
  if (data !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, {method, headers, body: data === undefined ? undefined : JSON.stringify(data)});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}
async function ensureSession(force = false) {
  if (sessionToken && !force) return;
  const result = await request('/api/sessions', {method: 'POST'});
  sessionToken = result.session_token;
  sessionStorage.setItem('smartbiz_session_token', sessionToken);
}
function bubble(text, type) {
  const node = document.createElement('article');
  node.className = `bubble ${type}`;
  node.textContent = text; // Prevent arbitrary model text from becoming executable HTML.
  conversation.appendChild(node);
  conversation.scrollTop = conversation.scrollHeight;
  return node;
}
function feedbackButtons(messageId) {
  const row = document.createElement('div');
  row.className = 'feedback';
  for (const [score, title] of [[1, '有帮助'], [-1, '没帮助']]) {
    const button = document.createElement('button');
    button.type = 'button'; button.textContent = title;
    button.addEventListener('click', async () => {
      try {
        await request('/api/feedback', {method: 'POST', data: {message_id: messageId, score}});
        row.textContent = '已记录反馈';
      } catch (err) { row.textContent = err.message; }
    });
    row.appendChild(button);
  }
  conversation.appendChild(row);
}

document.querySelector('#new-session').addEventListener('click', async () => {
  try {
    await ensureSession(true);
    conversation.replaceChildren();
    bubble('已开始新会话。', 'bot');
    statusText.textContent = '';
  } catch (err) { statusText.textContent = err.message; }
});

document.querySelector('#chat-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const value = question.value.trim();
  if (!value) return;
  const button = document.querySelector('#send');
  button.disabled = true; statusText.textContent = '处理中...';
  try {
    await startup; // Finish restoring saved messages before adding new ones.
    await ensureSession();
    const reply = await request('/api/chat', {method: 'POST', data: {message: value}});
    bubble(value, 'user'); bubble(reply.reply, 'bot'); feedbackButtons(reply.message_id);
    question.value = ''; statusText.textContent = '';
  } catch (err) {
    statusText.textContent = `发送失败：${err.message}`;
    if (err.message.includes('会话失效')) { sessionToken = null; sessionStorage.removeItem('smartbiz_session_token'); }
  } finally { button.disabled = false; question.focus(); }
});

document.querySelector('#lead-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = document.querySelector('#lead-send');
  const message = document.querySelector('#lead-status');
  button.disabled = true; message.textContent = '提交中...';
  try {
    await ensureSession();
    const data = Object.fromEntries(new FormData(form));
    await request('/api/leads', {method: 'POST', data: {
      name: data.name, contact: data.contact, requirement: data.requirement, consent: data.consent === 'on'
    }});
    form.reset(); message.textContent = '提交成功。';
  } catch (err) { message.textContent = `提交失败：${err.message}`; }
  finally { button.disabled = false; }
});
// Recover this tab's previous session after refreshing the page.
async function restoreHistory() {
  const tokenBefore = sessionToken;
  const messages = await request('/api/messages?limit=50');
  if (!messages.length || tokenBefore !== sessionToken) return;

  conversation.replaceChildren();
  for (const item of messages) {
    bubble(item.content, item.role === 'user' ? 'user' : 'bot');
    if (item.role === 'assistant') feedbackButtons(item.id);
  }
}

const startup = ensureSession().then(restoreHistory).catch(async (err) => {
  if (err.message.includes('会话失效')) {
    sessionToken = null;
    sessionStorage.removeItem('smartbiz_session_token');
    try { await ensureSession(); }
    catch (retryError) { statusText.textContent = `初始化失败：${retryError.message}`; }
    return;
  }
  statusText.textContent = `初始化失败：${err.message}`;
});


// Added business module: tickets are linked to the current session token.
async function refreshTickets() {
  const list = document.querySelector('#ticket-list');
  list.replaceChildren();
  try {
    await ensureSession();
    const items = await request('/api/tickets');
    if (!items.length) {
      const empty = document.createElement('li'); empty.textContent = '暂无工单'; list.appendChild(empty);
      return;
    }
    for (const item of items) {
      const li = document.createElement('li');
      li.textContent = `${item.subject} · ${item.status} · ${item.created_at}`;
      list.appendChild(li);
    }
  } catch (err) {
    const li = document.createElement('li'); li.textContent = err.message; list.appendChild(li);
  }
}
document.querySelector('#ticket-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = document.querySelector('#ticket-send');
  const output = document.querySelector('#ticket-status');
  button.disabled = true;
  try {
    await ensureSession();
    const data = Object.fromEntries(new FormData(form));
    const result = await request('/api/tickets', {method: 'POST', data});
    output.textContent = `已创建工单 ${result.ticket_id.substring(0, 8)}`;
    form.reset(); await refreshTickets();
  } catch (err) { output.textContent = err.message; }
  finally { button.disabled = false; }
});
document.querySelector('#ticket-refresh').addEventListener('click', refreshTickets);
