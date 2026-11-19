"use strict";
const $ = selector => document.querySelector(selector);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
let operations = [];
let selected;

function selectOperation(index) {
  selected = operations[index];
  document.querySelectorAll('[data-operation]').forEach(button => button.classList.toggle('active', Number(button.dataset.operation) === index));
  $('#api-title').textContent = selected.spec.summary;
  $('#api-operation').innerHTML = `<span>${selected.method}</span>${escapeHTML(selected.path)}`;
  $('#api-form').hidden = false;
  const parameters = (selected.spec.parameters || []).filter(parameter => parameter.in !== 'header');
  $('#api-parameters').innerHTML = `<div class="api-parameter-grid">${parameters.map(parameter => `<label>${escapeHTML(parameter.name)} <span>(${parameter.in}${parameter.required ? '，必填' : ''})</span><input name="${escapeHTML(parameter.name)}" data-location="${parameter.in}" ${parameter.required ? 'required' : ''} value="${escapeHTML(parameter.schema?.default ?? '')}" placeholder="${parameter.name === 'product_id' ? '1' : parameter.name === 'order_id' ? '从订单响应复制订单编号' : parameter.name}"></label>`).join('')}</div>`;
  const request = selected.spec.requestBody?.content?.['application/json'];
  $('#api-body-label').hidden = !request;
  $('#api-body').value = request ? JSON.stringify(request.example || {}, null, 2) : '';
  if (['checkout','request_refund','support_send','admin_support_send'].includes(selected.spec.operationId)) {
    const data = JSON.parse($('#api-body').value);
    if('idempotency_key' in data)data.idempotency_key=crypto.randomUUID();
    if('client_message_id' in data)data.client_message_id=crypto.randomUUID();
    $('#api-body').value = JSON.stringify(data, null, 2);
  }
  $('#api-response').textContent = '发送请求后，响应会显示在这里。';
  $('#api-status').textContent = '';
}

$('#api-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = $('#api-send');
  if (button.disabled) return;
  button.disabled = true;
  try {
    let path = selected.path;
    const query = new URLSearchParams();
    document.querySelectorAll('#api-parameters input').forEach(input => {
      if (input.dataset.location === 'path') path = path.replace(`{${input.name}}`, encodeURIComponent(input.value));
      else if (input.value) query.set(input.name, input.value);
    });
    if (query.size) path += `?${query}`;
    const options = {method: selected.method, credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'HeimaMall'}};
    if (selected.spec.requestBody) options.body = JSON.stringify(JSON.parse($('#api-body').value));
    const started = performance.now();
    const response = await fetch(path, options);
    const data = await response.json();
    $('#api-status').textContent = `HTTP ${response.status} · ${Math.round(performance.now() - started)}ms`;
    $('#api-response').textContent = JSON.stringify(data, null, 2);
  } catch (error) { $('#api-response').textContent = `请求失败：${error.message}`; }
  finally { button.disabled = false; }
});

document.addEventListener('click', event => {
  const button = event.target.closest('[data-operation]');
  if (button) selectOperation(Number(button.dataset.operation));
});

async function initialize() {
  try {
    const response = await fetch('/openapi.json');
    if (!response.ok) throw new Error('接口文档加载失败');
    const document = await response.json();
    const groups = new Map();
    Object.entries(document.paths).forEach(([path, methods]) => Object.entries(methods).forEach(([method, spec]) => {
      if (!['get','post','put','patch','delete'].includes(method)) return;
      const index = operations.length;
      operations.push({path, method: method.toUpperCase(), spec});
      const tag = spec.tags?.[0] || '其他';
      if (!groups.has(tag)) groups.set(tag, []);
      groups.get(tag).push(`<button class="api-route" data-operation="${index}"><span class="api-method ${method.toUpperCase()}">${method.toUpperCase()}</span><span>${escapeHTML(spec.summary)}</span></button>`);
    }));
    $('#api-routes').innerHTML = [...groups].map(([tag, items]) => `<h2 class="api-group-title">${escapeHTML(tag)}</h2>${items.join('')}`).join('');
    if (operations.length) selectOperation(0);
  } catch (error) { $('#api-routes').textContent = error.message; }
}
initialize();
