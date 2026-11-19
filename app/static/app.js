"use strict";

const paths = {
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  user: '<circle cx="12" cy="8" r="3.5"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>',
  bag: '<path d="M5 7h14l1 14H4L5 7Z"/><path d="M8 8V6a4 4 0 0 1 8 0v2"/>',
  'arrow-right': '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  'arrow-up-right': '<path d="M6 18 18 6M6 6h12v12"/>',
  'check-circle': '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  truck: '<path d="M2 5h12v12H2V5Zm12 4h4l4 5v3h-8"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>',
  package: '<path d="m12 3 9 5v9l-9 5-9-5V8l9-5ZM3 8l9 5 9-5M12 13v9M7.5 5.5l9 5"/>',
  heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/>',
  x: '<path d="m6 6 12 12M6 18 18 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
  sparkles: '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/>',
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.bag}</svg>`;
const $ = selector => document.querySelector(selector);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const money = cents => (cents / 100).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const price = cents => Number.isInteger(cents / 100) ? String(cents / 100) : money(cents);
const image = product => /^\/media\/[a-f0-9]{64}\.(png|jpg|webp)$/.test(product.image) ? product.image : `/static/images/${encodeURIComponent(product.image)}.svg`;
const state = {user: null, cart: {items: [], count: 0, subtotal: 0, shipping_fee: 0, total: 0}, products: [], category: '', q: '', sort: 'recommended', page: 1, authMode: 'login', pendingAdd: null, afterAuth: null, ordersPage: 1, checkoutKey: null,checkoutStoreId:null,storeId:null};
let productRequest = 0;
let toastTimer;
let syncingStorefront = false;
let heroProductId = 1;
let heroRotationInterval = null;
let heroProducts = [];
const shopChannel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('heima-mall') : null;
const publishShopChange = resource => shopChannel?.postMessage({resource});

function mountIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach(element => { element.innerHTML = icon(element.dataset.icon); });
}

function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  clearTimeout(toastTimer);
  if (!element.matches(':popover-open')) element.showPopover();
  requestAnimationFrame(() => element.classList.add('visible'));
  toastTimer = setTimeout(() => { element.classList.remove('visible'); setTimeout(() => { if (element.matches(':popover-open')) element.hidePopover(); }, 200); }, 3200);
}

function showDialog(id) {
  const dialog = document.getElementById(id);
  if (!dialog.open) dialog.showModal();
  document.body.style.overflow = 'hidden';
}

function closeDialog(id) { document.getElementById(id).close(); }

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'HeimaMall', ...options.headers},
  });
  const data = await response.json();
  if (!response.ok) {
    let message = data.detail;
    if (Array.isArray(message)) message = '请检查输入：手机号需为 11 位，密码至少 8 位，地址至少 5 个字符。';
    const error = new Error(message || '操作失败，请稍后重试');
    error.status = response.status;
    if ((response.status === 401 || (response.status === 403 && String(message).includes('账号已停用'))) && path !== '/auth/login') {
      state.user = null;
      applyCart({items: [], count: 0, subtotal: 0, shipping_fee: 0, total: 0});
      $('#orders-list').innerHTML = '';
      for (const id of ['cart-dialog','checkout-dialog','orders-dialog','account-dialog']) closeDialog(id);
      window.mallFeatures?.reset();
    }
    throw error;
  }
  return data;
}

function friendlyError(error) { return error instanceof TypeError ? '连接失败，请检查网络后重试' : error.message; }

function updateAccount() {
  $('#account-label').textContent = state.user ? state.user.name : '登录 / 注册';
  $('#cart-count').textContent = state.cart.count;
  $('#cart-button').setAttribute('aria-label', `购物车，${state.cart.count} 件商品`);
  const canManage = state.user?.role === 'admin' && Boolean(state.user?.is_active) && Boolean(state.user?.is_super_admin || state.user?.store_id);
  $('#footer-admin-link').hidden = !canManage;
  $('#profile-admin-link').hidden = !canManage;
  window.mallFeatures?.accountChanged();
}

async function refreshCaptcha() {
  const image = $('#captcha-image');
  try {
    const captcha = await api('/auth/captcha');
    image.src = captcha.image;
    $('#auth-form [name="captcha_id"]').value = captcha.captcha_id;
    $('#auth-form [name="captcha_code"]').value = '';
  } catch (error) { image.removeAttribute('src'); image.alt = '验证码加载失败，点击重试'; }
}

function setAuthMode(mode) {
  state.authMode = mode;
  const register = mode === 'register';
  $('#auth-title').textContent = register ? '遇见你的新喜欢' : '欢迎回来';
  $('#auth-description').textContent = register ? '注册账号，从一件好物开始。' : '登录，收藏生活里的每一份喜欢。';
  $('#auth-submit').textContent = register ? '创建账号' : '登录';
  $('#name-field').hidden = !register;
  $('#name-field input').required = register;
  $('#captcha-field').hidden = register;
  $('#captcha-field [name="captcha_code"]').required = !register;
  $('#auth-form [name="password"]').autocomplete = register ? 'new-password' : 'current-password';
  $('#auth-error').textContent = '';
  document.querySelectorAll('[data-auth]').forEach(button => button.classList.toggle('active', button.dataset.auth === mode));
  if (!register) refreshCaptcha();
}

function requestLogin(productId = null, afterAuth = null) {
  state.pendingAdd = productId;
  state.afterAuth = afterAuth;
  setAuthMode('login');
  showDialog('auth-dialog');
}

function emptyState(title, description, action = '') {
  return `<div class="empty-state">${icon('bag')}<h3>${escapeHTML(title)}</h3><p>${escapeHTML(description)}</p>${action}</div>`;
}

function productCard(product) {
  return `<article class="product-card">
    <button class="product-picture" style="background:${escapeHTML(product.color)}" data-action="detail" data-id="${product.id}" aria-label="查看 ${escapeHTML(product.name)}">
      <img src="${image(product)}" alt="${escapeHTML(product.name)}" loading="lazy" width="280" height="225">
      ${product.badge ? `<span class="product-badge">${escapeHTML(product.badge)}</span>` : ''}
    </button>
    <div class="product-card-body"><button class="product-name" data-action="detail" data-id="${product.id}">${escapeHTML(product.name)}</button><p class="product-subtitle">${escapeHTML(product.subtitle)}</p><button class="product-store-link" data-feature="support" data-store="${product.store_id}">${escapeHTML(product.store_name)} · 联系客服</button>
      <div class="product-bottom"><div class="product-price"><span class="currency">¥</span>${price(product.price)}<span class="original-price">¥${price(product.original_price)}</span></div><button class="add-button" data-action="add" data-id="${product.id}" aria-label="将 ${escapeHTML(product.name)} 加入购物车" ${product.stock ? '' : 'disabled'}>${icon('plus')}</button></div>
      <div class="product-sales">${icon('sparkles')}${product.stock ? `${(product.sales / 1000).toFixed(1)}k 人已入手` : '暂时售罄'}</div>
    </div></article>`;
}

function renderPagination(id, total, page, pageSize, action) {
  const element = document.getElementById(id);
  const pages = Math.ceil(total / pageSize);
  element.hidden = pages <= 1;
  element.innerHTML = pages > 1 ? `<button data-action="${action}" data-page="${page - 1}" ${page === 1 ? 'disabled' : ''}>上一页</button><span>第 ${page} / ${pages} 页</span><button data-action="${action}" data-page="${page + 1}" ${page === pages ? 'disabled' : ''}>下一页</button>` : '';
}

async function loadProducts() {
  const requestId = ++productRequest;
  $('#product-grid').setAttribute('aria-busy', 'true');
  const parameters = new URLSearchParams({q: state.q, category: state.category, sort: state.sort, page: state.page,on_sale:state.onSale?'true':'false'});
  if(state.storeId)parameters.set('store_id',state.storeId);
  try {
    const [result,stores]=await Promise.all([api(`/products?${parameters}`),state.q?api('/stores?q='+encodeURIComponent(state.q)):Promise.resolve([])]);
    if (requestId !== productRequest) return;
    state.products = result.items;
    window.mallSearch?.renderStores(stores);
    if (!state.q && !state.category && state.sort === 'recommended' && state.page === 1) {
      const featured = result.items[0];
      $('#hero-detail').disabled = !featured;
      $('.hero-product').hidden = !featured;
      if (featured) {
        heroProductId = featured.id;
        $('.hero-product').src = image(featured);
        $('.hero-product').alt = featured.name;
        $('#hero-product-name').textContent = featured.name;
        $('#hero-product-subtitle').textContent = featured.subtitle;
        // 保存所有商品用于轮动效果
        heroProducts = result.items;
        // 启动轮动效果
        startHeroRotation();
      } else {
        $('#hero-product-name').textContent = '更多好物，敬请期待';
        $('#hero-product-subtitle').textContent = '新的喜欢正在准备中';
        stopHeroRotation();
      }
    }
    $('#product-grid').innerHTML = result.items.length ? result.items.map(productCard).join('') : emptyState('暂时没有找到好物', '试试其他关键词，或看看全部精选。', '<button class="button outline" data-action="reset-search">查看全部好物</button>');
    $('#product-total').textContent = `${result.total} 件精选好物`;
    $('#search-summary').hidden = !state.q;
    $('#search-summary').innerHTML = state.q ? `<span>“${escapeHTML(state.q)}” 的搜索结果</span><button data-action="reset-search">清除搜索 ×</button>` : '';
    renderPagination('pagination', result.total, state.page, result.page_size, 'product-page');
  } catch (error) {
    if (requestId !== productRequest) return;
    window.mallSearch?.renderStores([]);
    $('#product-grid').innerHTML = emptyState('好物暂时没有加载成功', friendlyError(error), '<button class="button outline" data-action="retry-products">重新加载</button>');
  } finally {
    if (requestId === productRequest) $('#product-grid').removeAttribute('aria-busy');
  }
}

// 轮动效果相关函数
function startHeroRotation() {
  if (heroRotationInterval) return;
  if (heroProducts.length < 2) return;

  let currentIndex = 0;
  heroRotationInterval = setInterval(() => {
    currentIndex = (currentIndex + 1) % heroProducts.length;
    const product = heroProducts[currentIndex];
    if (product) {
      heroProductId = product.id;
      $('.hero-product').src = image(product);
      $('.hero-product').alt = product.name;
      $('#hero-product-name').textContent = product.name;
      $('#hero-product-subtitle').textContent = product.subtitle;
      // 更新轮动指示器
      updateHeroPagination(currentIndex);
    }
  }, 3000); // 每3秒轮动一次
}

function stopHeroRotation() {
  if (heroRotationInterval) {
    clearInterval(heroRotationInterval);
    heroRotationInterval = null;
  }
}

function updateHeroPagination(currentIndex) {
  const pagination = $('.hero-pagination');
  if (!pagination) return;

  const spans = pagination.querySelectorAll('span');
  spans.forEach((span, index) => {
    if (index === currentIndex) {
      span.classList.add('current');
    } else {
      span.classList.remove('current');
    }
  });
}

// 轮动指示器点击处理
function handleHeroPaginationClick(event) {
  const span = event.target.closest('.hero-pagination span');
  if (!span) return;

  const index = Array.from(span.parentNode.children).indexOf(span);
  if (index >= 0 && index < heroProducts.length) {
    const product = heroProducts[index];
    if (product) {
      heroProductId = product.id;
      $('.hero-product').src = image(product);
      $('.hero-product').alt = product.name;
      $('#hero-product-name').textContent = product.name;
      $('#hero-product-subtitle').textContent = product.subtitle;
      updateHeroPagination(index);
    }
  }
}

// 轮动效果相关函数
function startHeroRotation() {
  if (heroRotationInterval) return;
  if (heroProducts.length < 2) return;

  let currentIndex = 0;
  heroRotationInterval = setInterval(() => {
    currentIndex = (currentIndex + 1) % heroProducts.length;
    const product = heroProducts[currentIndex];
    if (product) {
      heroProductId = product.id;
      $('.hero-product').src = image(product);
      $('.hero-product').alt = product.name;
      $('#hero-product-name').textContent = product.name;
      $('#hero-product-subtitle').textContent = product.subtitle;
      // 更新轮动指示器
      updateHeroPagination(currentIndex);
    }
  }, 3000); // 每3秒轮动一次
}

function stopHeroRotation() {
  if (heroRotationInterval) {
    clearInterval(heroRotationInterval);
    heroRotationInterval = null;
  }
}

function updateHeroPagination(currentIndex) {
  const pagination = $('.hero-pagination');
  if (!pagination) return;

  const spans = pagination.querySelectorAll('span');
  spans.forEach((span, index) => {
    if (index === currentIndex) {
      span.classList.add('current');
    } else {
      span.classList.remove('current');
    }
  });
}

// 轮动指示器点击处理
function handleHeroPaginationClick(event) {
  const span = event.target.closest('.hero-pagination span');
  if (!span) return;

  const index = Array.from(span.parentNode.children).indexOf(span);
  if (index >= 0 && index < heroProducts.length) {
    const product = heroProducts[index];
    if (product) {
      heroProductId = product.id;
      $('.hero-product').src = image(product);
      $('.hero-product').alt = product.name;
      $('#hero-product-name').textContent = product.name;
      $('#hero-product-subtitle').textContent = product.subtitle;
      updateHeroPagination(index);
    }
  }
}

function setCategory(category) {
  state.category = category;
  state.page = 1;
  document.querySelectorAll('[data-category]').forEach(button => button.classList.toggle('active', button.dataset.category === category));
  return loadProducts();
}

function scrollCatalog() { window.mallPages?.catalog(); $('#catalog').scrollIntoView({behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start'}); }

async function showProduct(productId) {
  return window.mallPages.product(productId);
}

function applyCart(cart) {
  const signature = items => JSON.stringify(items.map(item => [item.id,item.quantity,item.price,item.is_active,item.store_id,item.store_version,item.store_active]));
  if (signature(state.cart.items) !== signature(cart.items)) state.checkoutKey = null;
  state.cart = cart; updateAccount(); renderCart();
  if ($('#checkout-dialog').open) { window.mallFeatures?.checkoutCartChanged(); }
}

async function addToCart(productId) {
  if (!state.user) { requestLogin(productId); return; }
  try {
    applyCart(await api('/cart', {method: 'POST', body: JSON.stringify({product_id: productId, quantity: 1})}));
    toast('已加入购物车，离喜欢又近了一步');
  } catch (error) {
    if (error.status === 401) { state.user = null; applyCart({items: [], count: 0}); requestLogin(productId); }
    else throw error;
  }
}

function summaryHTML(cart) {
  return `<div class="summary-row"><span>商品金额</span><span>¥${money(cart.subtotal)}</span></div><div class="summary-row"><span>运费</span><span>${cart.shipping_fee ? `¥${money(cart.shipping_fee)}` : '免运费'}</span></div><div class="summary-row total"><span>合计</span><b>¥${money(cart.total)}</b></div>`;
}

function checkoutCart() {
  return state.cart.groups?.find(g=>g.store_id===state.checkoutStoreId) || {items:[],count:0,subtotal:0,shipping_fee:0,total:0};
}
function renderCart() {
  $('#drawer-count').textContent = state.cart.count;
  const row=product=>{
    const inactive=!product.is_active||!product.store_active;
    const availability=!product.store_active?'店铺暂停营业':!product.is_active?'商品已下架，请移除后结算':product.quantity>product.stock?`库存仅剩 ${product.stock} 件，请调整数量`:escapeHTML(product.category);
    return `<div class="cart-item"><div class="cart-item-picture" style="background:${escapeHTML(product.color)}"><img src="${image(product)}" alt="${escapeHTML(product.name)}"></div><div class="cart-item-info"><h3>${escapeHTML(product.name)}</h3><p class="muted ${inactive||product.quantity>product.stock?'cart-unavailable':''}">${availability}</p><div class="product-price"><span class="currency">¥</span>${price(product.price)}</div><div class="cart-item-actions"><div class="quantity-control"><button data-action="quantity" data-id="${product.id}" data-quantity="${Math.max(1,Math.min(product.quantity-1,product.stock))}" aria-label="减少 ${escapeHTML(product.name)} 数量" ${product.quantity===1||product.stock===0||inactive?'disabled':''}>−</button><span>${product.quantity}</span><button data-action="quantity" data-id="${product.id}" data-quantity="${product.quantity+1}" aria-label="增加 ${escapeHTML(product.name)} 数量" ${inactive||product.quantity>=Math.min(product.stock,99)?'disabled':''}>+</button></div><button class="icon-button remove-button" data-action="remove" data-id="${product.id}" aria-label="移除 ${escapeHTML(product.name)}">${icon('trash')}</button></div></div></div>`;
  };
  const groups=state.cart.groups||[];
  $('#cart-items').innerHTML=groups.length?groups.map(g=>{
    const unavailable=!g.store_active||g.items.some(p=>!p.is_active||p.quantity>p.stock);
    return `<section class="cart-store"><div class="cart-store-heading"><strong>${escapeHTML(g.store_name)}</strong><button data-feature="support" data-store="${g.store_id}">客服</button></div>${g.items.map(row).join('')}<div class="cart-store-summary"><p>${g.shipping_fee?`本店再购 ¥${money(Math.max(0,g.free_shipping_threshold-g.subtotal))} 可包邮 · 运费 ¥${money(g.shipping_fee)}`:'本店免运费'} · 合计 ¥${money(g.total)}</p><button class="button primary full" data-action="checkout" data-store="${g.store_id}" ${unavailable?'disabled':''}>${unavailable?'请先处理暂停营业、下架或缺货商品':`结算本店（${g.count} 件）`}</button></div></section>`;
  }).join(''):emptyState('购物车还空着','把喜欢的好物带回家吧。','<button class="button primary" data-close="cart-dialog">去逛逛</button>');
  $('#cart-summary').hidden=!groups.length;
  $('#cart-summary').innerHTML=groups.length?`${summaryHTML(state.cart)}<p class="modal-hint">按店铺分别结算，包邮与优惠券门槛按本店商品金额计算。</p>`:'';
}

async function openCart() {
  if (!state.user) { requestLogin(null, 'cart'); return; }
  applyCart(await api('/cart'));
  showDialog('cart-dialog');
}

function openCheckout(storeId=null) {
  if (!state.cart.items.length) return;
  const groups=state.cart.groups||[];
  const group=groups.find(g=>g.store_id===storeId)||(groups.length===1?groups[0]:null);
  if(!group){toast('请选择店铺分别结算');return;}
  if(state.checkoutStoreId!==group.store_id)state.checkoutKey=null;
  state.checkoutStoreId=group.store_id;
  $('#checkout-store-name').textContent=`${group.store_name} · 本次只结算该店铺的 ${group.count} 件商品`;
  closeDialog('cart-dialog');
  if (!state.checkoutKey) state.checkoutKey = crypto.randomUUID();
  $('#checkout-form [name="recipient"]').value ||= state.user.name;
  $('#checkout-form [name="phone"]').value ||= state.user.phone;
  $('#checkout-summary').innerHTML = summaryHTML(checkoutCart());
  $('#checkout-error').textContent = '';
  showDialog('checkout-dialog');
  window.mallFeatures?.checkoutOpened().catch(error => { $('#checkout-error').textContent = friendlyError(error); });
}

const statusLabel = {pending: '待支付', unfulfilled: '待发货', shipped: '已发货', completed: '已完成', cancelled: '已取消', expired: '已过期'};
let orderExpiryRefreshBusy = false;
let lastOrderExpiryRefresh = 0;
let orderRequest = 0;

function paymentTime(seconds) {
  return `${String(Math.floor(seconds / 60)).padStart(2,'0')}:${String(seconds % 60).padStart(2,'0')}`;
}

function orderPaymentDeadline(order) {
  if (order.is_expired) return '<p class="order-date">超过15分钟未支付，订单已自动取消。</p>';
  if (order.status !== 'pending' || !order.payment_expires_at) return '';
  const offset = order.server_time ? Date.parse(order.server_time) - Date.now() : 0;
  const seconds = Math.max(0,Math.ceil((Date.parse(order.payment_expires_at) - Date.now() - offset) / 1000));
  return `<p class="order-date" data-payment-deadline="${escapeHTML(order.payment_expires_at)}" data-clock-offset="${offset}">剩余支付时间 ${paymentTime(seconds)} · 超时自动取消</p>`;
}

function updateOrderCountdowns() {
  if (!$('#orders-dialog').open || document.hidden) return;
  let due = false;
  document.querySelectorAll('[data-payment-deadline]').forEach(element => {
    const seconds = Math.max(0,Math.ceil((Date.parse(element.dataset.paymentDeadline) - Date.now() - Number(element.dataset.clockOffset || 0)) / 1000));
    element.textContent = seconds ? `剩余支付时间 ${paymentTime(seconds)} · 超时自动取消` : '支付时间已到，正在关闭订单…';
    const button = element.closest('.order-card')?.querySelector('[data-action="pay-order"]');
    if (!seconds) { due = true; if (button) button.disabled = true; }
  });
  if (due && !orderExpiryRefreshBusy && Date.now() - lastOrderExpiryRefresh >= 5000) {
    orderExpiryRefreshBusy = true; lastOrderExpiryRefresh = Date.now();
    loadOrders().then(() => publishShopChange('products')).catch(() => {}).finally(() => { orderExpiryRefreshBusy = false; });
  }
}

function renderOrder(order) {
  const displayStatus = order.is_expired ? 'expired' : order.status === 'paid' ? order.fulfillment_status || 'unfulfilled' : order.status;
  const shipping = order.carrier ? `<div class="order-shipping">${icon('truck')}<span>${escapeHTML(order.carrier)} · ${escapeHTML(order.tracking_no)}${order.completed_at ? '<small>已确认收货</small>' : ''}</span></div>` : '';
  const actions = order.status === 'pending' ? `<div><button class="button outline" data-action="cancel-order" data-order="${escapeHTML(order.id)}">取消订单</button><button class="button primary" data-action="pay-order" data-order="${escapeHTML(order.id)}">演示支付</button></div>` : displayStatus === 'shipped' && !['pending','approved'].includes(order.refund?.status) ? `<button class="button primary" data-action="receive-order" data-order="${escapeHTML(order.id)}">确认收货</button>` : '';
  return `<article class="order-card"><div class="order-heading"><span>${escapeHTML(order.id)}</span><span class="order-status ${displayStatus}">${statusLabel[displayStatus]}</span></div>${order.items.map(item => `<div class="order-line"><img src="${image(item)}" alt="${escapeHTML(item.name)}"><span>${escapeHTML(item.name)}<small>¥${money(item.price)} × ${item.quantity}</small></span><b>¥${money(item.price * item.quantity)}</b></div>`).join('')}<p class="order-address">${escapeHTML(order.recipient)} · ${escapeHTML(order.phone)}<br>${escapeHTML(order.address)}</p><div class="order-store"><strong>${escapeHTML(order.store_name)}</strong><button data-feature="support" data-store="${order.store_id}">联系本店客服</button></div>${shipping}${window.mallFeatures?.orderExtras(order) || ''}${orderPaymentDeadline(order)}<div class="order-footer"><span class="order-total">合计<strong>¥${money(order.total)}</strong></span>${actions}</div><div class="order-date">${new Date(order.created_at).toLocaleString('zh-CN')} · ${order.shipping_fee ? `运费 ¥${money(order.shipping_fee)}` : '免运费'}</div></article>`;
}

async function loadOrders() {
  const request = ++orderRequest, page = state.ordersPage, userId = state.user?.id;
  const result = await api(`/orders?page=${page}`);
  if (request !== orderRequest || page !== state.ordersPage || userId !== state.user?.id) return;
  $('#orders-list').innerHTML = result.items.length ? result.items.map(renderOrder).join('') : emptyState('还没有订单', '喜欢的好物，正等着与你相遇。');
  renderPagination('orders-pagination', result.total, page, result.page_size, 'orders-page');
  updateOrderCountdowns();
}

async function openOrders() {
  if (!state.user) { requestLogin(null, 'orders'); return; }
  state.ordersPage = 1;
  await loadOrders();
  showDialog('orders-dialog');
}

async function handleAction(button) {
  const action = button.dataset.action;
  const id = Number(button.dataset.id);
  if (action === 'detail') await showProduct(id);
  else if (action === 'add') await addToCart(id);
  else if (action === 'quantity') applyCart(await api(`/cart/${id}`, {method: 'PATCH', body: JSON.stringify({quantity: Number(button.dataset.quantity)})}));
  else if (action === 'remove') applyCart(await api(`/cart/${id}`, {method: 'DELETE'}));
  else if (action === 'checkout') openCheckout(Number(button.dataset.store)||null);
  else if (action === 'reset-search') {
    window.mallPages?.catalog({home:true});
    state.storeId=null;$('#store-filter').value='';state.onSale=false; state.q = ''; state.sort = 'recommended'; $('#search-input').value = ''; $('#sort-select').value = state.sort; await setCategory('');
  } else if (action === 'retry-products') await loadProducts();
  else if (action === 'product-page') { state.page = Number(button.dataset.page); await loadProducts(); scrollCatalog(); }
  else if (action === 'orders-page') { state.ordersPage = Number(button.dataset.page); await loadOrders(); }
  else if (['pay-order','cancel-order','receive-order'].includes(action)) {
    const operation = {'pay-order':'pay','cancel-order':'cancel','receive-order':'receive'}[action];
    const order = await api(`/orders/${button.dataset.order}/${operation}`, {method: 'POST'});
    toast({'pay-order':'演示支付成功，未发生真实扣款','cancel-order':'订单已取消，库存已恢复','receive-order':'已确认收货，谢谢你的喜欢'}[action]);
    publishShopChange('orders');publishShopChange('products');
    await loadOrders();
    await loadProducts();
    return order;
  }
}

document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button || button.disabled) return;
  if (button.dataset.close) { closeDialog(button.dataset.close); return; }
  if (button.dataset.auth) { setAuthMode(button.dataset.auth); return; }
  if (button.hasAttribute('data-category')) { await setCategory(button.dataset.category); return; }
  if (!button.dataset.action) return;
  button.disabled = true;
  try { await handleAction(button); }
  catch (error) { toast(friendlyError(error)); }
  finally { button.disabled = false; }
});

// 轮动指示器点击处理
document.addEventListener('click', event => {
  handleHeroPaginationClick(event);
});

document.querySelectorAll('dialog').forEach(dialog => {
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rectangle = dialog.getBoundingClientRect();
    if (event.clientX < rectangle.left || event.clientX > rectangle.right || event.clientY < rectangle.top || event.clientY > rectangle.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => {
    if (!document.querySelector('dialog[open]')) document.body.style.overflow = '';
    if (dialog.id === 'auth-dialog') { state.pendingAdd = null; $('#auth-form [name="password"]').value = ''; }
  });
});

$('#auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = $('#auth-submit');
  if (button.disabled) return;
  button.disabled = true;
  $('#auth-error').textContent = '';
  const values = Object.fromEntries(new FormData(event.target));
  if (state.authMode === 'login') delete values.name;
  else { delete values.captcha_id; delete values.captcha_code; }
  try {
    state.user = await api(`/auth/${state.authMode}`, {method: 'POST', body: JSON.stringify(values)});
    publishShopChange('session');
    const pendingAdd = state.pendingAdd;
    const afterAuth = state.afterAuth;
    state.pendingAdd = null;
    state.afterAuth = null;
    updateAccount();
    closeDialog('auth-dialog');
    event.target.reset();
    toast(`欢迎你，${state.user.name}`);
    try {
      applyCart(await api('/cart'));
      if (pendingAdd) await addToCart(pendingAdd);
      else if (afterAuth === 'cart') await openCart();
      else if (afterAuth === 'orders') await openOrders();
    } catch (error) { toast(friendlyError(error)); }
  } catch (error) {
    $('#auth-error').textContent = friendlyError(error);
    if (state.authMode === 'login') refreshCaptcha();
  }
  finally { button.disabled = false; }
});

$('#captcha-image').addEventListener('click', refreshCaptcha);

$('#checkout-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.target.querySelector('[type="submit"]');
  if (button.disabled) return;
  button.disabled = true;
  $('#checkout-error').textContent = '';
  try {
    state.checkoutKey ||= crypto.randomUUID();
    const order = await api('/orders', {method: 'POST', body: JSON.stringify({...Object.fromEntries(new FormData(event.target)), ...window.mallFeatures?.checkoutPayload(), idempotency_key: state.checkoutKey})});
    publishShopChange('orders');publishShopChange('products');
    state.checkoutKey = null;
    const groups=(state.cart.groups||[]).filter(g=>g.store_id!==order.store_id);
    applyCart({groups,items:groups.flatMap(g=>g.items),count:groups.reduce((n,g)=>n+g.count,0),subtotal:groups.reduce((n,g)=>n+g.subtotal,0),shipping_fee:groups.reduce((n,g)=>n+g.shipping_fee,0),total:groups.reduce((n,g)=>n+g.total,0)});
    closeDialog('checkout-dialog');
    api('/cart').then(applyCart).catch(error=>toast(`订单已创建，购物车刷新失败：${friendlyError(error)}`));
    state.ordersPage = 1;
    $('#orders-list').innerHTML = renderOrder(order);
    $('#orders-pagination').hidden = true;
    showDialog('orders-dialog');
    toast('订单已创建，点击「演示支付」完成体验');
    loadOrders().catch(error => toast(`订单已创建，列表刷新失败：${friendlyError(error)}`));
    await loadProducts();
  } catch (error) { $('#checkout-error').textContent = friendlyError(error); }
  finally { button.disabled = false; }
});

$('#account-button').addEventListener('click', () => {
  if (!state.user) { requestLogin(); return; }
  $('#profile-name').textContent = state.user.name;
  $('#profile-phone').textContent = state.user.phone.replace(/(\d{3})\d{4}(\d{4})/, '$1 **** $2');
  showDialog('account-dialog');
});
$('#cart-button').addEventListener('click', () => openCart().catch(error => toast(friendlyError(error))));
$('#hero-detail').addEventListener('click', () => showProduct(heroProductId).catch(error => toast(friendlyError(error))));
$('#my-orders').addEventListener('click', async () => { closeDialog('account-dialog'); try { await openOrders(); } catch (error) { toast(friendlyError(error)); } });
$('#logout-button').addEventListener('click', async () => {
  try {
    await api('/auth/logout', {method: 'POST'});
    publishShopChange('session');
    state.user = null; state.checkoutKey = null; window.mallFeatures?.reset();
    applyCart({items: [], count: 0, subtotal: 0, shipping_fee: 0, total: 0});
    $('#checkout-form').reset();
    closeDialog('account-dialog'); toast('已退出登录');
  } catch (error) { toast(friendlyError(error)); }
});
$('#search-form').addEventListener('submit', event => {
  event.preventDefault(); window.mallPages?.catalog({home:true}); state.storeId=null;$('#store-filter').value='';state.q = $('#search-input').value.trim(); state.page = 1; loadProducts(); scrollCatalog();
});
$('#search-input').addEventListener('search', () => { if (!$('#search-input').value) { state.q = ''; state.page = 1; loadProducts(); } });
$('#store-filter').addEventListener('change',event=>{const id=Number(event.target.value);if(id)window.mallPages.store(id).catch(error=>toast(friendlyError(error)));else{state.storeId=null;window.mallPages.catalog({home:true});state.page=1;loadProducts();scrollCatalog();}});
$('#sort-select').addEventListener('change', event => { state.sort = event.target.value; state.page = 1; loadProducts(); });
$('#explore-button').addEventListener('click', scrollCatalog);
$('#home-collection').addEventListener('click', async () => { state.q = ''; $('#search-input').value = ''; await setCategory('家居生活'); scrollCatalog(); });
document.querySelectorAll('[data-nav]').forEach(button => button.addEventListener('click', async () => {
  window.mallPages?.catalog({home:true});state.storeId=null;$('#store-filter').value='';
  document.querySelectorAll('[data-nav]').forEach(item => item.classList.toggle('active', item === button));
  state.onSale = button.dataset.nav === 'discount';
  state.q = ''; $('#search-input').value = '';
  state.sort = button.dataset.nav === 'popular' ? 'popular' : button.dataset.nav === 'new' ? 'newest' : 'recommended';
  $('#sort-select').value = state.sort;
  await setCategory(button.dataset.nav === 'lifestyle' ? '家居生活' : '');
  if (button.dataset.nav === 'home') window.scrollTo({top: 0, behavior: 'smooth'}); else scrollCatalog();
}));

async function initialize() {
  mountIcons();
  window.mallFeatures?.loadStores().catch(()=>{});
  $('#year').textContent = new Date().getFullYear();
  const results = await Promise.allSettled([loadProducts(), api('/categories'), api('/auth/me')]);
  if (results[1].status === 'fulfilled') {
    renderCategories(results[1].value);
  }
  if (results[2].status === 'fulfilled') {
    state.user = results[2].value;
    updateAccount();
    try { applyCart(await api('/cart')); } catch (error) { toast(friendlyError(error)); }
  }
  await window.mallPages?.initialize();
}

function renderCategories(categories) {
  if (state.category && !categories.some(category => category.name === state.category)) { state.category = '';state.page = 1; }
  const all = [{name:''},...categories];
  $('#category-tabs').innerHTML = all.map(category => `<button class="category ${state.category === category.name ? 'active' : ''}" data-category="${escapeHTML(category.name)}">${escapeHTML(category.name || '全部好物')}</button>`).join('');
}

async function syncStorefront() {
  if (syncingStorefront || document.hidden) return;
  syncingStorefront = true;
  try {
    const results = await Promise.allSettled([api('/categories'),api('/auth/me')]);
    if (results[0].status === 'fulfilled') renderCategories(results[0].value);
    if (results[1].status === 'fulfilled') {
      const user = results[1].value;
      if (state.user && state.user.id !== user.id) {
        for (const id of ['cart-dialog','checkout-dialog','orders-dialog','account-dialog']) closeDialog(id);
      window.mallFeatures?.reset();
        $('#checkout-form').reset();state.checkoutKey = null;
      }
      state.user = user;updateAccount();
    }
    const tasks = [loadProducts(),window.mallFeatures?.sync(),window.mallFeatures?.loadStores()];
    if (state.user) {
      tasks.push(api('/cart').then(applyCart));
      if ($('#orders-dialog').open) tasks.push(loadOrders());
    }
    await Promise.allSettled(tasks);
  } finally { syncingStorefront = false; }
}

window.addEventListener('focus',syncStorefront);
document.addEventListener('visibilitychange',() => { if (!document.hidden) syncStorefront(); });
if (shopChannel) shopChannel.onmessage = syncStorefront;
setInterval(syncStorefront,30000);
setInterval(updateOrderCountdowns,1000);

document.addEventListener('DOMContentLoaded',initialize,{once:true});
