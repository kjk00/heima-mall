"use strict";

const $ = selector => document.querySelector(selector);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const money = cents => (cents / 100).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const date = value => value ? new Date(value).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '—';
const imageURL = value => /^\/media\/[a-f0-9]{64}\.(png|jpg|webp)$/.test(value) ? value : `/static/images/${encodeURIComponent(value)}.svg`;
const paths = {
  grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  package:'<path d="m12 3 9 5v9l-9 5-9-5V8l9-5ZM3 8l9 5 9-5M12 13v9M7 6l9 5"/>',
  receipt:'<path d="M5 3h14v18l-3-2-4 2-4-2-3 2V3ZM9 7h6M9 11h6M9 15h3"/>',
  users:'<circle cx="9" cy="8" r="3"/><path d="M3 21v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6M18 15a5 5 0 0 1 3 4v2"/>',
  store:'<path d="M3 9 5 3h14l2 6v3a3 3 0 0 1-6 0 3 3 0 0 1-6 0 3 3 0 0 1-6 0V9ZM5 15v6h14v-6M9 21v-5h6v5"/>',
  code:'<path d="m8 6-6 6 6 6m8-12 6 6-6 6M14 3l-4 18"/>',
  refresh:'<path d="M20 8a8 8 0 1 0 1 7M20 3v6h-6"/>',
  menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  x:'<path d="m6 6 12 12M6 18 18 6"/>',
  arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
  truck:'<path d="M2 5h12v12H2V5Zm12 4h4l4 5v3h-8"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>',
  upload:'<path d="M12 16V3m-5 5 5-5 5 5M3 15v6h18v-6"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v.2"/>',
  wallet:'<path d="M20 7H4a2 2 0 0 1 0-4h13v4M3 5v14a2 2 0 0 0 2 2h15V7M20 11h-6v6h6"/><circle cx="16" cy="14" r=".7"/>',
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.package}</svg>`;
const titles = {dashboard:'经营概览',products:'商品管理',orders:'订单管理',users:'用户管理'};
const labels = {headphones:'降噪耳机',backpack:'双肩包',sneakers:'休闲鞋',candle:'香氛蜡烛',speaker:'蓝牙音箱',mug:'马克杯',skincare:'洁面乳',bottle:'保温水杯',sunglasses:'太阳镜',coffee:'挂耳咖啡',lamp:'桌面台灯',yoga:'瑜伽垫'};
const freshFilter = () => ({q:'',status:'all',category:'',page:1,page_size:10,store_id:''});
const state = {user:null,setupRequired:false,canSetup:true,section:'dashboard',filters:{products:freshFilter(),orders:freshFilter(),users:freshFilter()},options:{categories:[],images:[]},products:new Map(),orders:new Map(),users:new Map(),editProduct:null,editorGeneration:0,uploadBusy:false,shippingOrder:null,confirmAction:null,confirmBusy:false};
let requestSequence = 0;
let toastTimer;
const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('heima-mall') : null;

function mountIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach(element => { element.innerHTML = icon(element.dataset.icon); });
}

function toast(message) {
  const element = $('#admin-toast');
  clearTimeout(toastTimer);
  element.textContent = message;
  if (!element.matches(':popover-open')) element.showPopover();
  toastTimer = setTimeout(() => { if (element.matches(':popover-open')) element.hidePopover(); }, 3500);
}

function showDialog(id) {
  const dialog = document.getElementById(id);
  if (!dialog.open) dialog.showModal();
  document.body.style.overflow = 'hidden';
}

function closeDialog(id) { document.getElementById(id).close(); }
function publish(resource) { channel?.postMessage({resource}); }
function friendlyError(error) { return error instanceof TypeError ? '无法连接服务，请确认商城服务已启动后重试' : error.message; }

function showAuth() {
  state.user = null;
  ++requestSequence;
  document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
  $('#admin-app').hidden = true;
  $('#admin-auth').hidden = false;
  closeSidebar();
}

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {...options,credentials:'same-origin',headers:{'Content-Type':'application/json','X-Requested-With':'HeimaMall',...options.headers}});
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('；') : data.detail;
    const error = new Error(detail || '操作失败，请重试');
    error.status = response.status;
    if (state.user && (response.status === 401 || (response.status === 403 && detail !== '需要终端管理员权限'))) {
      showAuth();
      $('#admin-auth-error').textContent = '登录状态已失效，请重新使用管理员账号登录。';
    }
    throw error;
  }
  return data;
}

async function refreshAdminCaptcha() {
  const image = $('#admin-captcha-image');
  try {
    const captcha = await api('/auth/captcha');
    image.src = captcha.image;
    $('#admin-auth-form [name="captcha_id"]').value = captcha.captcha_id;
    $('#admin-auth-form [name="captcha_code"]').value = '';
  } catch (error) { image.removeAttribute('src'); image.alt = '验证码加载失败，点击重试'; }
}

function configureAuth(setupRequired, canSetup = true) {
  state.setupRequired = setupRequired;
  state.canSetup = canSetup;
  $('#admin-auth-title').textContent = setupRequired ? '创建终端管理员账号' : '欢迎回来';
  $('#admin-auth-description').textContent = setupRequired ? '首次使用，设置可管理全部店铺的终端管理员。' : '使用管理员账号，开启今天的经营。';
  $('#admin-name-field').hidden = !setupRequired;
  $('#admin-name-field input').required = setupRequired;
  $('#admin-captcha-field').hidden = setupRequired;
  $('#admin-captcha-field [name="captcha_code"]').required = !setupRequired;
  $('#admin-auth-form [name="password"]').autocomplete = setupRequired ? 'new-password' : 'current-password';
  $('#admin-auth-submit').innerHTML = `${setupRequired ? '创建管理员并进入后台' : '登录管理中心'} ${icon('arrow')}`;
  $('#admin-auth-note').textContent = setupRequired ? '如手机号已在商城注册，请输入该账号的现有密码。' : '普通商城账号无法进入管理中心。';
  $('#admin-auth-form').hidden = setupRequired && !canSetup;
  $('#admin-auth-retry').hidden = true;
  if (setupRequired && !canSetup) $('#admin-auth-description').textContent = '请先在服务器本机打开后台，完成首次管理员初始化。';
  if (!setupRequired) refreshAdminCaptcha();
}

async function loadOptions() {
  const identity = state.user;
  const options = await api('/admin/options');
  if (state.user !== identity) return;
  state.options = options;
  $('#category-options').innerHTML = state.options.categories.map(category => `<option value="${escapeHTML(category)}">`).join('');
  $('#products-category').innerHTML = '<option value="">全部分类</option>' + state.options.categories.map(category => `<option value="${escapeHTML(category)}">${escapeHTML(category)}</option>`).join('');
  $('#products-category').value = state.filters.products.category;
  const storeOptions=(state.options.stores||[]).map(s=>`<option value="${s.id}">${escapeHTML(s.name)}${s.is_active?'':'（暂停营业）'}</option>`).join('');
  const allStores = state.user?.is_super_admin ? '<option value="">全部店铺</option>' : '';
  for(const section of ['products','orders']){
    const field=$(`#${section}-store`);field.innerHTML=allStores+storeOptions;
    state.filters[section].store_id = state.user?.is_super_admin ? state.filters[section].store_id : String(state.user.store_id);
    field.value=state.filters[section].store_id||'';
    field.disabled=!state.user?.is_super_admin;
  }
  const support=$('#admin-support-store'),selected=support.value;support.innerHTML=allStores+storeOptions;
  support.value=state.user?.is_super_admin ? selected : String(state.user.store_id);
  support.disabled=!state.user?.is_super_admin;
  const store=state.options.stores.find(s=>s.id===state.user?.store_id);
  $('#admin-profile-name').textContent=`${state.user.name} · ${state.user.is_super_admin?'终端管理员':store?.name||'店铺管理员'}`;
  document.querySelectorAll('[data-store-action="new"]').forEach(button=>{button.hidden=!state.user?.is_super_admin;});
}

async function enterAdmin(user) {
  ++requestSequence;
  state.user = user;
  for (const section of ['products','orders','users']) {
    state.filters[section] = freshFilter();
    state[section].clear();
    $(`#${section}-table`).innerHTML='';
  }
  for (const section of ['stores','support','coupons','refunds','discussions']) $(`#${section}-extra`).innerHTML='';
  $('#dashboard-content').innerHTML='';
  state.editProduct=null;state.shippingOrder=null;state.confirmAction=null;
  state.options={categories:[],images:[],stores:[]};
  state.setupRequired = false;
  configureAuth(false);
  $('#admin-auth').hidden = true;
  $('#admin-app').hidden = false;
  $('#admin-profile-name').textContent = user.name;
  $('#admin-avatar').textContent = [...user.name][0] || '管';
  $('#dashboard-greeting').textContent = `${user.name}，今天也为好生意加油。`;
  try { await loadOptions(); } catch (error) { toast(friendlyError(error)); }
  await navigate(titles[location.hash.slice(1)] ? location.hash.slice(1) : 'dashboard', false);
}

function closeSidebar() {
  $('#admin-sidebar').classList.remove('open');
  $('#sidebar-backdrop').hidden = true;
  $('#menu-toggle').setAttribute('aria-expanded','false');
}

async function navigate(section, updateHash = true) {
  if (!titles[section] || !state.user) return;
  state.section = section;
  if (updateHash) history.replaceState(null,'',`${location.pathname}#${section}`);
  document.querySelectorAll('[data-panel]').forEach(panel => { panel.hidden = panel.dataset.panel !== section; });
  document.querySelectorAll('[data-section]').forEach(button => {
    button.classList.toggle('active',button.dataset.section === section);
    if (button.dataset.section === section) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
  });
  $('#breadcrumb-current').textContent = titles[section];
  document.title = `${titles[section]} · 黑马商城管理中心`;
  if (state.filters[section]) {
    const filter = state.filters[section];
    const form = document.getElementById(`${section}-filter`);
    for (const name of ['q','status','category','store_id']) if (form.elements.namedItem(name)) form.elements.namedItem(name).value = filter[name];
  }
  closeSidebar();
  await loadCurrent();
}

function orderState(order) { return order.is_expired ? 'expired' : order.status === 'paid' ? order.fulfillment_status || 'unfulfilled' : order.status; }
function orderStatus(order) {
  const status = orderState(order);
  const names = {pending:'待支付',unfulfilled:'待发货',shipped:'已发货',completed:'已完成',cancelled:'已取消',expired:'已过期'};
  return `<span class="status ${status}">${names[status] || '已支付'}</span>`;
}

function emptyRow(columns, message, retry = false) {
  return `<tr><td colspan="${columns}" class="empty-cell">${icon('package')}<div>${escapeHTML(message)}</div>${retry ? '<button class="button secondary" data-action="refresh">重新加载</button>' : ''}</td></tr>`;
}

function pagination(section, result) {
  const pages = Math.max(1,Math.ceil(result.total / result.page_size));
  document.getElementById(`${section}-pagination`).innerHTML = `<span>共 ${result.total} 条记录 · 每页 ${result.page_size} 条</span><div class="pagination-buttons"><button data-action="page" data-section-name="${section}" data-page="${result.page - 1}" ${result.page === 1 ? 'disabled' : ''}>上一页</button><span>${result.page} / ${pages}</span><button data-action="page" data-section-name="${section}" data-page="${result.page + 1}" ${result.page >= pages ? 'disabled' : ''}>下一页</button></div>`;
  document.getElementById(`${section}-total`).textContent = result.total;
}

function productRows(products) {
  return products.map(product => `<tr><td><div class="product-cell"><img class="table-thumb" src="${imageURL(product.image)}" style="background:${escapeHTML(product.color)}" alt="${escapeHTML(product.name)}"><div><div class="cell-title">${escapeHTML(product.name)}</div><span class="cell-subtitle">#${product.id} · ${escapeHTML(product.subtitle)}</span></div></div></td><td>${escapeHTML(product.category)}<small class="cell-subtitle">${escapeHTML(product.store_name)}</small></td><td><span class="money">¥${money(product.price)}<span class="muted-price">¥${money(product.original_price)}</span></span></td><td><span class="stock ${product.stock <= 10 ? 'low' : ''}">${product.stock}</span></td><td><span class="status ${product.is_active ? 'active' : 'inactive'}">${product.is_active ? '已上架' : '已下架'}</span></td><td><div class="table-actions"><button class="text-button" data-action="edit-product" data-id="${product.id}">编辑</button><button class="text-button ${product.is_active ? 'danger' : ''}" data-action="product-status" data-id="${product.id}">${product.is_active ? '下架' : '上架'}</button></div></td></tr>`).join('');
}

function orderRows(orders, compact = false) {
  return orders.map(order => `<tr><td><div class="cell-title">${escapeHTML(order.id)}</div><div class="cell-subtitle">${escapeHTML(order.store_name)}</div><span class="cell-subtitle">${escapeHTML(order.items[0]?.name || '')}${order.items.length > 1 ? ` 等 ${order.items.length} 件商品` : ''}</span></td>${compact ? '' : `<td><div>${escapeHTML(order.recipient)}</div><span class="cell-subtitle">${escapeHTML(order.phone)}</span></td>`}<td><span class="money">¥${money(order.total)}</span></td><td>${orderStatus(order)}</td>${compact ? '' : `<td>${date(order.created_at)}</td>`}<td><div class="table-actions"><button class="text-button" data-action="view-order" data-order="${escapeHTML(order.id)}">详情</button>${!compact && orderState(order) === 'unfulfilled' ? `<button class="text-button" data-action="ship-order" data-order="${escapeHTML(order.id)}">发货</button>` : ''}${!compact && order.status === 'pending' ? `<button class="text-button danger" data-action="cancel-order" data-order="${escapeHTML(order.id)}">关闭</button>` : ''}</div></td></tr>`).join('');
}

function userRows(users) {
  return users.map(user => `<tr><td><div class="product-cell"><span class="avatar">${escapeHTML([...user.name][0] || '用')}</span><div><div class="cell-title">${escapeHTML(user.name)}</div><span class="cell-subtitle">${escapeHTML(user.phone)}</span></div></div></td><td><span class="status ${user.role === 'admin' ? 'admin' : ''}">${user.role === 'admin' ? (user.is_super_admin ? '终端管理员' : '店铺管理员') : '商城用户'}</span></td><td>${user.order_count}</td><td><span class="money">¥${money(user.spent)}</span></td><td><span class="status ${user.is_active ? 'active' : 'inactive'}">${user.is_active ? '已启用' : '已停用'}</span></td><td>${date(user.created_at)}</td><td><div class="table-actions"><button class="text-button" data-action="user-orders" data-id="${user.id}">订单</button>${user.role === 'admin' || !state.user?.is_super_admin ? '' : `<button class="text-button ${user.is_active ? 'danger' : ''}" data-action="user-status" data-id="${user.id}">${user.is_active ? '停用' : '启用'}</button>`}</div></td></tr>`).join('');
}

function renderDashboard(data) {
  const summary = data.summary;
  const maxOrders = Math.max(1,...data.trend.map(day => day.orders));
  const cards = [
    {label:'已支付总额',value:`¥${money(summary.revenue)}`,icon:'wallet',note:'仅统计演示支付成功的订单',className:'revenue'},
    {label:'累计订单',value:summary.orders,icon:'receipt',note:`今日新增 <strong>${summary.today_orders}</strong> 笔`},
    {label:state.user?.is_super_admin?'商城用户':'本店顾客',value:summary.customers,icon:'users',note:state.user?.is_super_admin?'已注册的客户账号':'在本店下单的客户账号'},
    {label:'在售商品',value:summary.active_products,icon:'package',note:`商品库共 <strong>${summary.products}</strong> 件好物`},
  ];
  state.orders = new Map(data.recent_orders.map(order => [order.id,order]));
  state.products = new Map(data.low_stock_products.map(product => [product.id,product]));
  $('#shipment-badge').hidden = !summary.awaiting_shipment;
  $('#shipment-badge').textContent = summary.awaiting_shipment;
  $('#dashboard-content').innerHTML = `<div class="stats-grid">${cards.map(card => `<article class="stat-card"><div class="stat-label">${card.label}<span>${icon(card.icon)}</span></div><div class="stat-value ${card.className || ''}">${card.value}</div><div class="stat-note">${card.note}</div></article>`).join('')}</div><div class="dashboard-grid"><section class="panel"><div class="panel-heading"><div><h2>近 7 天下单趋势</h2><p>看见每天的经营进展</p></div><span>最近 7 天</span></div><div class="chart" role="img" aria-label="近7天每日订单数：${data.trend.map(day => `${day.day} ${day.orders}笔`).join('，')}">${data.trend.map(day => `<div class="chart-column" title="${day.day}：${day.orders} 笔订单，已支付 ¥${money(day.revenue)}"><span class="chart-value">${day.orders}</span><div class="chart-bar" style="height:${Math.max(3,day.orders / maxOrders * 112)}px"></div><span class="chart-label">${day.day.slice(5).replace('-','/')}</span></div>`).join('')}</div><div class="chart-caption">近 7 天下单的已支付金额：¥${money(data.trend.reduce((sum,day) => sum + day.revenue,0))} · 支付仅用于演示</div></section><section class="panel"><div class="panel-heading"><div><h2>今天的待办</h2><p>把下一步安排得明明白白</p></div></div><div class="tasks"><button class="task-row" data-action="filter-link" data-target="orders" data-status="unfulfilled"><div>${icon('truck')}待发货订单</div><strong>${summary.awaiting_shipment}</strong></button><button class="task-row" data-action="filter-link" data-target="orders" data-status="pending"><div>${icon('receipt')}待支付订单</div><strong>${summary.pending_orders}</strong></button><button class="task-row" data-action="filter-link" data-target="products" data-status="low_stock"><div>${icon('package')}库存不足</div><strong>${summary.low_stock}</strong></button></div></section></div><div class="dashboard-bottom"><section class="panel"><div class="panel-heading"><h2>最近订单</h2><button data-action="filter-link" data-target="orders" data-status="all">查看全部 ↗</button></div><div class="table-scroll"><table class="recent-orders"><thead><tr><th>订单 / 商品</th><th>金额</th><th>状态</th><th class="right">操作</th></tr></thead><tbody>${data.recent_orders.length ? orderRows(data.recent_orders,true) : emptyRow(4,'还没有订单，去商城体验一次下单吧。')}</tbody></table></div></section><section class="panel"><div class="panel-heading"><h2>库存提醒</h2><span>可售库存 ≤10</span></div><div class="low-stock-list">${data.low_stock_products.length ? data.low_stock_products.map(product => `<div class="low-stock-item"><img class="table-thumb" src="${imageURL(product.image)}" style="background:${escapeHTML(product.color)}" alt="${escapeHTML(product.name)}"><div><div class="cell-title">${escapeHTML(product.name)}</div><span class="cell-subtitle">剩余 <strong>${product.stock}</strong> 件</span></div><button data-action="edit-product" data-id="${product.id}">补货</button></div>`).join('') : '<p class="dashboard-empty">库存充足，好物正在等着被喜欢。</p>'}</div></section></div>`;
}

async function loadCurrent() {
  if (!state.user) return;
  if(window.adminFeatures?.handles(state.section)) { ++requestSequence; await window.adminFeatures.load();return; }
  const sequence = ++requestSequence;
  const section = state.section;
  $('#admin-refresh').disabled = true;
  const columns = {products:6,orders:6,users:7};
  if (section === 'dashboard') $('#dashboard-content').innerHTML = '<div class="panel dashboard-empty">正在加载经营数据…</div>';
  else document.getElementById(`${section}-table`).innerHTML = emptyRow(columns[section],'正在加载…');
  try {
    const parameters = section === 'dashboard' ? '' : '?' + new URLSearchParams(state.filters[section]);
    const result = await api(`/admin/${section}${parameters}`);
    if (sequence !== requestSequence || !state.user) return;
    if (section === 'dashboard') renderDashboard(result);
    else {
      const lastPage = Math.max(1,Math.ceil(result.total / result.page_size));
      if (result.page > lastPage) { state.filters[section].page = lastPage; await loadCurrent(); return; }
      state[section] = new Map(result.items.map(item => [item.id,item]));
      const renderer = {products:productRows,orders:orderRows,users:userRows}[section];
      document.getElementById(`${section}-table`).innerHTML = result.items.length ? renderer(result.items) : emptyRow(columns[section],'暂时没有符合条件的记录');
      pagination(section,result);
    }
    $('#last-refreshed').textContent = `更新于 ${new Date().toLocaleTimeString('zh-CN',{hour12:false})}`;
  } catch (error) {
    if (sequence !== requestSequence || !state.user) return;
    if (section === 'dashboard') $('#dashboard-content').innerHTML = `<div class="panel dashboard-empty">${escapeHTML(friendlyError(error))}<br><button class="button secondary" data-action="refresh">重新加载</button></div>`;
    else document.getElementById(`${section}-table`).innerHTML = emptyRow(columns[section],friendlyError(error),true);
  } finally { if (sequence === requestSequence) $('#admin-refresh').disabled = false; }
}

function selectProductImage(value) {
  $('#product-image-value').value = value;
  $('#product-preview-image').src = imageURL(value);
}

async function openProductEditor(productId = null) {
  await loadOptions();
  const product = productId ? await api(`/admin/products/${productId}`) : {name:'',subtitle:'',category:state.options.categories[0] || '家居生活',price:6900,original_price:9900,stock:50,image:'mug',badge:'',color:'#eae5ed',description:'',detail_html:'',params:[],is_active:1,store_id:state.options.stores.find(s=>s.is_active)?.id||1};
  state.editProduct = product;
  ++state.editorGeneration;
  state.uploadBusy = false;
  $('#product-save').disabled = false;
  $('#product-upload').disabled = false;
  $('#product-upload').value = '';
  $('#product-upload-note').textContent = 'PNG、JPEG 或 WebP，最大 2 MB。';
  $('#product-form').reset();
  $('#product-editor-title').textContent = productId ? '编辑商品' : '新增商品';
  $('#product-form-error').textContent = '';
  for (const name of ['name','subtitle','category','stock','badge','color','description','detail_html']) $('#product-form').elements.namedItem(name).value = product[name] || '';
  $('#product-form').elements.namedItem('params_raw').value = (product.params || []).map(it => `${it.name}:${it.value}`).join('\n');
  for (const name of ['price','original_price']) $('#product-form').elements.namedItem(name).value = (product[name] / 100).toFixed(2);
  $('#product-form [name="is_active"]').checked = !!product.is_active;
  $('#product-store-select').innerHTML=state.options.stores.map(s=>`<option value="${s.id}">${escapeHTML(s.name)}${s.is_active?'':'（暂停营业）'}</option>`).join('');
  $('#product-store-select').value=product.store_id;
  $('#product-image-select').innerHTML = state.options.images.map(item => `<option value="${escapeHTML(item.image)}">${escapeHTML(labels[item.image] || item.label)}</option>`).join('');
  if (product.image.startsWith('/media/')) $('#product-image-select').add(new Option('已上传的主图',product.image));
  $('#product-image-select').value = product.image;
  selectProductImage(product.image);
  $('#product-image-preview').style.background = product.color;
  showDialog('product-editor');
}

function decimalToCents(value) {
  if (!/^\d+(?:\.\d{1,2})?$/.test(value)) throw new Error('价格最多保留两位小数，请使用普通数字格式');
  const [whole,fraction = ''] = value.split('.');
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2,'0'));
  if (!Number.isSafeInteger(cents) || cents < 1 || cents > 1_000_000_000) throw new Error('价格应在 ¥0.01 到 ¥10,000,000 之间');
  return cents;
}

function parseParams(raw) {
  const params = [];
  for (const line of raw.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const separator = trimmed.search(/[:：]/);
    if (separator < 1) throw new Error('商品参数每行需为「参数名:参数值」格式');
    const name = trimmed.slice(0, separator).trim();
    const value = trimmed.slice(separator + 1).trim();
    if (!name || !value) throw new Error('商品参数名和参数值都不能为空');
    params.push({name, value});
  }
  if (params.length > 30) throw new Error('商品参数最多 30 项');
  return params;
}

function askConfirmation(title, description, label, action) {
  if (state.confirmBusy) throw new Error('正在处理上一项操作，请稍候');
  state.confirmAction = action;
  $('#confirm-title').textContent = title;
  $('#confirm-description').textContent = description;
  $('#confirm-submit').textContent = label;
  $('#confirm-error').textContent = '';
  showDialog('confirm-dialog');
}

async function showOrder(orderId) {
  const order = await api(`/admin/orders/${orderId}`);
  $('#order-detail-content').innerHTML = `<div class="order-summary"><span>${escapeHTML(order.id)}</span>${orderStatus(order)}</div><div class="order-items">${order.items.map(item => `<div class="order-item"><img class="table-thumb" src="${imageURL(item.image)}" alt="${escapeHTML(item.name)}"><div><strong>${escapeHTML(item.name)}</strong><p>¥${money(item.price)} × ${item.quantity}</p></div><span class="money">¥${money(item.price * item.quantity)}</span></div>`).join('')}</div><div class="order-info-block"><h3>所属店铺</h3><p>${escapeHTML(order.store_name)}</p></div><div class="order-info-block"><h3>收货信息</h3><p>${escapeHTML(order.recipient)} · ${escapeHTML(order.phone)}<br>${escapeHTML(order.address)}</p></div><div class="order-info-block"><h3>下单用户</h3><p>${escapeHTML(order.customer.name)} · ${escapeHTML(order.customer.phone)}<br>下单时间：${date(order.created_at)}</p></div>${order.fulfillment_status !== 'unfulfilled' ? `<div class="order-info-block"><h3>物流信息</h3><p>${escapeHTML(order.carrier)} · ${escapeHTML(order.tracking_no)}<br>发货时间：${date(order.shipped_at)}${order.completed_at ? `<br>收货时间：${date(order.completed_at)}` : ''}</p></div>` : ''}<div class="order-breakdown"><div><span>商品金额</span><span>¥${money(order.subtotal)}</span></div><div><span>运费</span><span>${order.shipping_fee ? `¥${money(order.shipping_fee)}` : '免运费'}</span></div><div><span>订单合计</span><strong>¥${money(order.total)}</strong></div></div><div class="dialog-footer">${orderState(order) === 'unfulfilled' ? `<button class="button primary" data-action="ship-order" data-order="${escapeHTML(order.id)}">${icon('truck')}安排发货</button>` : ''}${order.status === 'pending' ? `<button class="button secondary" data-action="cancel-order" data-order="${escapeHTML(order.id)}">关闭未支付订单</button>` : ''}<button class="button secondary" data-close="order-detail-dialog">关闭</button></div>`;
  window.adminFeatures?.orderOpened(order);
  showDialog('order-detail-dialog');
}

async function openShipment(orderId) {
  const order = await api(`/admin/orders/${orderId}`);
  if (orderState(order) !== 'unfulfilled') throw new Error('订单当前无法发货，请刷新订单状态');
  state.shippingOrder = orderId;
  $('#shipment-form').reset();
  $('#shipment-form [name="carrier"]').value = '顺丰速运';
  $('#shipment-order').textContent = `${orderId} · ${order.recipient} · ¥${money(order.total)}`;
  $('#shipment-error').textContent = '';
  closeDialog('order-detail-dialog');
  showDialog('shipment-dialog');
}

async function handleAction(button) {
  const action = button.dataset.action;
  const id = Number(button.dataset.id);
  if (action === 'refresh') await loadCurrent();
  else if (action === 'new-product') await openProductEditor();
  else if (action === 'edit-product') await openProductEditor(id);
  else if (action === 'view-order') await showOrder(button.dataset.order);
  else if (action === 'ship-order') await openShipment(button.dataset.order);
  else if (action === 'page') { state.filters[button.dataset.sectionName].page = Number(button.dataset.page); await loadCurrent(); }
  else if (action === 'filter-link') {
    const target = button.dataset.target;
    state.filters[target] = {...freshFilter(),status:button.dataset.status};
    await navigate(target);
  } else if (action === 'user-orders') {
    const user = state.users.get(id);
    state.filters.orders = {...freshFilter(),q:user.phone};
    await navigate('orders');
  } else if (action === 'product-status') {
    const product = state.products.get(id);
    askConfirmation(`${product.is_active ? '下架' : '上架'}这件商品？`,product.is_active ? `「${product.name}」将从商城隐藏，购物车中的该商品也无法继续下单。` : `「${product.name}」将重新出现在商城中。`,product.is_active ? '确认下架' : '确认上架',async () => {
      await api(`/admin/products/${id}/status`,{method:'PATCH',body:JSON.stringify({is_active:!product.is_active,version:product.version})});
      publish('products');
      toast(product.is_active ? '商品已下架，商城同步更新' : '商品已上架，商城同步更新');
      await loadCurrent();
    });
  } else if (action === 'user-status') {
    const user = state.users.get(id);
    askConfirmation(`${user.is_active ? '停用' : '启用'}用户账号？`,user.is_active ? `停用「${user.name}」后，该账号会退出登录，暂时无法登录和下单。` : `「${user.name}」可以重新登录并使用商城。`,user.is_active ? '确认停用' : '确认启用',async () => {
      await api(`/admin/users/${id}/status`,{method:'PATCH',body:JSON.stringify({is_active:!user.is_active})});
      publish('users');toast(user.is_active ? '用户已停用' : '用户已启用');await loadCurrent();
    });
  } else if (action === 'cancel-order') {
    const orderId = button.dataset.order;
    askConfirmation('关闭这笔未支付订单？',`订单 ${orderId} 将被取消，占用的商品库存会自动恢复。`,'确认关闭',async () => {
      await api(`/admin/orders/${orderId}/cancel`,{method:'POST'});
      closeDialog('order-detail-dialog');publish('orders');publish('products');toast('订单已关闭，库存已恢复');await loadCurrent();
    });
  }
}

document.addEventListener('click',async event => {
  const button = event.target.closest('button');
  if (!button || button.disabled) return;
  if (button.dataset.close) { closeDialog(button.dataset.close); return; }
  if (button.dataset.section) { await navigate(button.dataset.section); return; }
  if (!button.dataset.action) return;
  button.disabled = true;
  try { await handleAction(button); } catch (error) { toast(friendlyError(error)); }
  finally { button.disabled = false; }
});

$('#admin-auth-form').addEventListener('submit',async event => {
  event.preventDefault();
  const button = $('#admin-auth-submit');
  if (button.disabled) return;
  button.disabled = true;
  $('#admin-auth-error').textContent = '';
  const payload = Object.fromEntries(new FormData(event.target));
  if (!state.setupRequired) delete payload.name;
  else { delete payload.captcha_id; delete payload.captcha_code; }
  try {
    const user = await api(state.setupRequired ? '/admin/setup' : '/admin/auth/login',{method:'POST',body:JSON.stringify(payload)});
    event.target.reset();publish('session');await enterAdmin(user);toast('欢迎进入管理中心');
  } catch (error) {
    $('#admin-auth-error').textContent = friendlyError(error);
    if (error.status === 409 && state.setupRequired) configureAuth(false);
    else if (!state.setupRequired) refreshAdminCaptcha();
  } finally { button.disabled = false; }
});

$('#admin-captcha-image').addEventListener('click', refreshAdminCaptcha);

$('#product-form').addEventListener('submit',async event => {
  event.preventDefault();
  const button = $('#product-save');
  if (button.disabled || state.uploadBusy) return;
  button.disabled = true;
  $('#product-form-error').textContent = '';
  try {
    const values = Object.fromEntries(new FormData(event.target));
    const payload = {...values,price:decimalToCents(values.price),original_price:decimalToCents(values.original_price),stock:Number(values.stock),store_id:Number(values.store_id),is_active:$('#product-form [name="is_active"]').checked};
    payload.params = parseParams(values.params_raw || '');
    delete payload.params_raw;
    payload.detail_html = values.detail_html || '';
    if (payload.original_price < payload.price) throw new Error('划线价不能低于售价');
    if (!Number.isInteger(payload.stock)) throw new Error('库存必须为整数');
    const productId = state.editProduct.id;
    if (productId) payload.version = state.editProduct.version;
    await api(productId ? `/admin/products/${productId}` : '/admin/products',{method:productId ? 'PUT' : 'POST',body:JSON.stringify(payload)});
    closeDialog('product-editor');publish('products');toast('商品已保存，商城同步更新');
    if (state.section === 'products') state.filters.products.page = 1;
    const updates = await Promise.allSettled([loadOptions(),loadCurrent()]);
    if (updates[0].status === 'rejected') toast('商品已保存，分类刷新失败，请稍后刷新页面');
  } catch (error) { $('#product-form-error').textContent = friendlyError(error); }
  finally { button.disabled = false; }
});

$('#product-image-select').addEventListener('change',event => selectProductImage(event.target.value));
$('#product-color').addEventListener('input',event => { $('#product-image-preview').style.background = event.target.value; });
$('#product-upload').addEventListener('change',async event => {
  const file = event.target.files[0];
  if (!file || state.uploadBusy) return;
  if (file.size > 2 * 1024 * 1024) { toast('图片不能超过 2 MB'); event.target.value = ''; return; }
  if (!['image/png','image/jpeg','image/webp'].includes(file.type)) { toast('请选择 PNG、JPEG 或 WebP 图片'); event.target.value = ''; return; }
  const generation = state.editorGeneration;
  state.uploadBusy = true;$('#product-save').disabled = true;event.target.disabled = true;
  $('#product-upload-note').textContent = '正在上传图片…';
  try {
    const encoded = await new Promise((resolve,reject) => { const reader = new FileReader();reader.onload = () => resolve(String(reader.result).split(',')[1]);reader.onerror = () => reject(new Error('图片读取失败'));reader.readAsDataURL(file); });
    const result = await api('/admin/images',{method:'POST',body:JSON.stringify({filename:file.name,data_base64:encoded})});
    if (generation !== state.editorGeneration) return;
    $('#product-image-select').add(new Option('已上传的主图',result.image));
    $('#product-image-select').value = result.image;selectProductImage(result.image);
    $('#product-upload-note').textContent = '图片上传成功，保存商品后会展示在商城。';
  } catch (error) { if (generation === state.editorGeneration) { $('#product-upload-note').textContent = friendlyError(error);toast(friendlyError(error)); } }
  finally { if (generation === state.editorGeneration) { state.uploadBusy = false;$('#product-save').disabled = false;event.target.disabled = false;event.target.value = ''; } }
});

$('#shipment-form').addEventListener('submit',async event => {
  event.preventDefault();
  const button = $('#shipment-save');
  if (button.disabled) return;
  button.disabled = true;$('#shipment-error').textContent = '';
  const orderId = state.shippingOrder;
  try {
    await api(`/admin/orders/${orderId}/ship`,{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(event.target)))});
    closeDialog('shipment-dialog');publish('orders');toast('订单已发货，用户订单页已同步物流信息');await loadCurrent();
  } catch (error) { $('#shipment-error').textContent = friendlyError(error); }
  finally { button.disabled = false; }
});

$('#confirm-submit').addEventListener('click',async () => {
  if (state.confirmBusy || !state.confirmAction) return;
  state.confirmBusy = true;$('#confirm-submit').disabled = true;$('#confirm-error').textContent = '';
  try { await state.confirmAction();closeDialog('confirm-dialog'); }
  catch (error) { $('#confirm-error').textContent = friendlyError(error); }
  finally { state.confirmBusy = false;$('#confirm-submit').disabled = false; }
});

for (const section of ['products','orders','users']) {
  const form = document.getElementById(`${section}-filter`);
  const applyFilter = async () => { state.filters[section] = {...state.filters[section],...Object.fromEntries(new FormData(form)),page:1};await loadCurrent(); };
  form.addEventListener('submit',event => { event.preventDefault();applyFilter(); });
  form.querySelectorAll('select').forEach(select => select.addEventListener('change',applyFilter));
  form.querySelector('input[type=search]').addEventListener('search',applyFilter);
}

document.querySelectorAll('dialog').forEach(dialog => {
  dialog.addEventListener('click',event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  dialog.addEventListener('close',() => {
    if (!document.querySelector('dialog[open]')) document.body.style.overflow = '';
    if (dialog.id === 'product-editor') ++state.editorGeneration;
  });
});

$('#menu-toggle').addEventListener('click',() => {
  const open = $('#admin-sidebar').classList.toggle('open');
  $('#sidebar-backdrop').hidden = !open;$('#menu-toggle').setAttribute('aria-expanded',String(open));
});
$('#sidebar-backdrop').addEventListener('click',closeSidebar);
$('#admin-refresh').addEventListener('click',async () => { try { await loadOptions();await loadCurrent(); } catch (error) { toast(friendlyError(error)); } });
$('#admin-logout').addEventListener('click',async () => {
  const button = $('#admin-logout');
  if (button.disabled) return;
  button.disabled = true;
  try { await api('/auth/logout',{method:'POST'});showAuth();configureAuth(false);publish('session');toast('已退出后台'); }
  catch (error) { toast(friendlyError(error)); }
  finally { button.disabled = false; }
});
window.addEventListener('hashchange',() => { if (state.user) navigate(location.hash.slice(1),false); });
window.addEventListener('focus',() => { if (state.user && !document.querySelector('dialog[open]')) loadCurrent(); });
if (channel) channel.onmessage = async event => {
  if (!state.user) return;
  if (event.data?.resource === 'session' || event.data?.resource === 'users') {
    try { state.user = await api('/admin/auth/me'); } catch { return; }
  }
  if (!document.querySelector('dialog[open]')) loadCurrent();
};
setInterval(() => { if (state.user && !document.hidden && !document.querySelector('dialog[open]')) loadCurrent(); },30000);

async function initialize() {
  $('#admin-auth-retry').disabled = true;
  try {
    const setup = await api('/admin/setup');
    configureAuth(setup.setup_required,setup.can_setup);
    try { await enterAdmin(await api('/admin/auth/me')); }
    catch (error) { if (![401,403].includes(error.status)) throw error; }
  } catch (error) {
    $('#admin-auth-description').textContent = error.status === 404 ? '后台接口尚未加载，请先重启商城服务。' : friendlyError(error);
    $('#admin-auth-form').hidden = true;$('#admin-auth-retry').hidden = false;
  } finally { $('#admin-auth-retry').disabled = false; }
}

$('#admin-auth-retry').addEventListener('click',initialize);
mountIcons();
$('#admin-year').textContent = new Date().getFullYear();
document.addEventListener('DOMContentLoaded',initialize,{once:true});
