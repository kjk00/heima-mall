"use strict";
(() => {
  const f = {view:'',page:1,addresses:[],coupons:[],addressId:null,productId:null,discussion:'comments',discussionPage:1,identity:null,chat:[],chatBusy:false,chatKey:null,refundKey:null,refundOrder:null,stores:[],threads:[],storeId:null,storeActive:true,chatEpoch:0};
  const e=escapeHTML;
  const when=v=>new Date(v).toLocaleString('zh-CN');
  const stages={awaiting:'等待处理',packing:'打包中',dispatched:'已发货',transit:'运输中',delivered:'已到货'};
  const refundLabels={pending:'退款审核中',approved:'已退款',rejected:'退款申请被拒绝'};
  const request=(path,method,payload)=>api(path,{method,...(payload?{body:JSON.stringify(payload)}:{})});
  function pager(result,action) {
    const pages=Math.ceil(result.total/result.page_size);
    return pages>1?`<div class="pagination"><button class="button outline" data-feature-action="${action}" data-page="${result.page-1}" ${result.page<=1?'disabled':''}>上一页</button><span>${result.page} / ${pages}</span><button class="button outline" data-feature-action="${action}" data-page="${result.page+1}" ${result.page>=pages?'disabled':''}>下一页</button></div>`:'';
  }
  function couponTerms(c) { return c.kind==='amount'?`减 ¥${money(c.value)}`:`${c.value/10} 折${c.max_discount?`，最多减 ¥${money(c.max_discount)}`:''}`; }
  function validCoupon(c) { const t=Date.now();return c.is_active&&Date.parse(c.starts_at)<=t&&t<Date.parse(c.expires_at); }
  function discount(c) { if(!c) return 0;return Math.min(checkoutCart().subtotal,c.max_discount||checkoutCart().subtotal,c.kind==='amount'?c.value:Math.floor((checkoutCart().subtotal*(100-c.value)+50)/100)); }
  async function open(view,storeId=null) {
    if(!state.user){requestLogin();return;}
    if($('#account-dialog').open)closeDialog('account-dialog');
    if(view==='support') {await openSupport(storeId);return;}
    f.view=view;f.page=1;
    $('#feature-title').textContent={addresses:'地址管理',coupons:'我的优惠券',history:'浏览历史',settings:'账号设置'}[view];
    $('#feature-content').innerHTML='<p class="muted">正在加载…</p>';showDialog('feature-dialog');await loadView();
  }
  async function loadView() {
    const view=f.view;
    if(view==='addresses') {
      const rows=await api('/addresses');if(f.view!==view)return;f.addresses=rows;
      $('#feature-content').innerHTML=`<div class="feature-toolbar"><span class="muted">最多保存 50 个收货地址</span><button class="button primary" data-feature-action="new-address">新增地址</button></div>${rows.length?rows.map(a=>`<article class="feature-card"><h3>${e(a.recipient)} <small>${e(a.label)} ${a.is_default?'· 默认':''}</small></h3><p>${e(a.phone)}<br>${e(a.address)}</p><div class="feature-actions"><button class="button outline" data-feature-action="edit-address" data-id="${a.id}">编辑</button><button class="button outline" data-feature-action="delete-address" data-id="${a.id}">删除</button></div></article>`).join(''):'<p class="feature-empty">还没有地址，先添加一个吧。</p>'}`;
    } else if(view==='coupons') {
      const rows=await api('/coupons');if(f.view!==view)return;f.coupons=rows;
      $('#feature-content').innerHTML=rows.map(c=>{const valid=validCoupon(c),claimed=!!c.claimed_at;
        const label=c.used_order_id?'已使用':!valid?'已失效 / 未开始':claimed?'已领取':c.claimed_count>=c.claim_limit?'已领完':'立即领取';
        const store=f.stores.find(s=>s.id===c.store_id);
        return `<article class="coupon-card"><div class="coupon-value">${e(couponTerms(c))}</div><h3>${e(c.name)}</h3><p>商品满 ¥${money(c.threshold)} 可用 · ${c.store_id?e(store?.name||'指定店铺')+'专用券':'全平台券'} · ${e(c.code)}</p><p class="muted">有效期至 ${when(c.expires_at)}</p><button class="button primary" data-feature-action="claim" data-id="${c.id}" ${claimed||!valid||c.claimed_count>=c.claim_limit?'disabled':''}>${label}</button></article>`;}).join('')||'<p class="feature-empty">暂时没有优惠券</p>';
    } else if(view==='history') {
      const result=await api(`/history?page=${f.page}`);if(f.view!==view)return;
      $('#feature-content').innerHTML=`<div class="feature-toolbar"><span class="muted">最近浏览 ${result.total} 件商品</span><button class="button outline" data-feature-action="clear-history" ${result.total?'':'disabled'}>清空历史</button></div>${result.items.map(p=>`<article class="history-item"><img src="${image(p)}" alt="${e(p.name)}"><div><h3>${e(p.name)}</h3><p>¥${money(p.price)} · ${when(p.viewed_at)}</p></div><button class="button outline" data-feature-action="history-product" data-id="${p.id}" ${p.is_active?'':'disabled'}>${p.is_active?'查看':'已下架'}</button></article>`).join('')||'<p class="feature-empty">浏览商品详情后会记录在这里。</p>'}${pager(result,'history-page')}`;
    } else if(view==='settings') {
      const s=await api('/settings');if(f.view!==view)return;
      $('#feature-content').innerHTML=`<form id="settings-form"><label>昵称<input name="name" required maxlength="30" value="${e(s.name)}"></label><label>手机号<input value="${e(s.phone)}" readonly></label><label class="feature-check"><input name="notify_support" type="checkbox" ${s.notify_support?'checked':''}>显示客服未读消息提醒</label><p class="form-error" role="alert"></p><button class="button primary" type="submit">保存设置</button></form><hr><h3>修改密码</h3><form id="password-form"><label>原密码<input name="old_password" type="password" required minlength="8" maxlength="128" autocomplete="current-password"></label><label>新密码<input name="new_password" type="password" required minlength="8" maxlength="128" autocomplete="new-password"></label><p class="muted">修改后，其他设备会退出登录。</p><p class="form-error" role="alert"></p><button class="button outline" type="submit">修改密码</button></form>`;
    }
  }
  function editAddress(id=null) {
    f.addressId=id;$('#address-form').reset();$('#address-form .form-error').textContent='';
    $('#address-title').textContent=id?'编辑地址':'新增地址';
    if(id){const a=f.addresses.find(x=>x.id===id);for(const k of ['recipient','phone','address','label'])$('#address-form').elements[k].value=a[k];$('#address-form').elements.is_default.checked=!!a.is_default;}
    showDialog('address-editor');
  }
  async function checkoutOpened() {
    const identity=state.user?.id,storeId=state.checkoutStoreId;
    const [addresses,coupons]=await Promise.all([api('/addresses'),api('/coupons')]);
    if(identity!==state.user?.id||storeId!==state.checkoutStoreId||!$('#checkout-dialog').open)return;
    f.addresses=addresses;f.coupons=coupons;
    const selected=$('#checkout-address').value;
    $('#checkout-address').innerHTML='<option value="">手动填写地址</option>'+addresses.map(a=>`<option value="${a.id}">${e(a.recipient)} · ${e(a.label||a.address)}${a.is_default?'（默认）':''}</option>`).join('');
    if(addresses.some(a=>String(a.id)===selected))$('#checkout-address').value=selected;
    else {const def=addresses.find(a=>a.is_default);if(def){$('#checkout-address').value=def.id;fillAddress();}}
    checkoutCartChanged();
  }
  function checkoutCartChanged() {
    const cart=checkoutCart();
    const eligible=f.coupons.filter(c=>c.claimed_at&&!c.used_order_id&&validCoupon(c)&&cart.subtotal>=c.threshold&&(!c.store_id||c.store_id===state.checkoutStoreId));
    const selected=$('#checkout-coupon').value;
    $('#checkout-coupon').innerHTML='<option value="">不使用优惠券</option>'+eligible.map(c=>`<option value="${c.id}">${e(c.name)} · ${c.store_id?'本店券':'全平台券'} · ${e(couponTerms(c))}</option>`).join('');
    if(eligible.some(c=>String(c.id)===selected))$('#checkout-coupon').value=selected;
    $('#checkout-form [type="submit"]').disabled=!cart.items.length||!cart.store_active||cart.items.some(p=>!p.is_active||p.quantity>p.stock);
    if(!cart.items.length)$('#checkout-error').textContent='本店购物车已变化，请重新选择店铺结算';
    else if(!cart.store_active)$('#checkout-error').textContent='店铺已暂停营业';
    checkoutSummary();
  }

  function fillAddress() {
    const a=f.addresses.find(a=>String(a.id)===$('#checkout-address').value);
    if(a){for(const k of ['recipient','phone','address'])$('#checkout-form').elements[k].value=a[k];$('#checkout-save-address').checked=false;}
    state.checkoutKey=null;
  }
  function checkoutSummary() {
    const c=f.coupons.find(c=>String(c.id)===$('#checkout-coupon').value);
    const d=c&&validCoupon(c)&&checkoutCart().subtotal>=c.threshold?discount(c):0;
    $('#checkout-summary').innerHTML=summaryHTML({...checkoutCart(),total:checkoutCart().total-d})+(d?`<p class="discount-note">优惠券已抵扣 ¥${money(d)}</p>`:'');
  }
  function checkoutPayload() {return {store_id:state.checkoutStoreId,address_id:Number($('#checkout-address').value)||null,coupon_id:Number($('#checkout-coupon').value)||null,save_address:$('#checkout-save-address').checked};}
  async function productOpened(p) {
    f.productId=p.id;f.discussion='comments';f.discussionPage=1;
    $('#product-detail').insertAdjacentHTML('beforeend',`<section class="product-discussions"><div class="discussion-tabs"><button class="active" data-discussion="comments">公共评论区</button><button data-discussion="reviews">购买评价</button></div><div id="discussion-summary" class="muted"></div><div id="discussion-list" aria-live="polite"></div><div id="discussion-pagination"></div><form id="discussion-form"><label id="discussion-rating" hidden>商品评分<select name="rating"><option value="5">★★★★★ 5 分</option><option value="4">★★★★☆ 4 分</option><option value="3">★★★☆☆ 3 分</option><option value="2">★★☆☆☆ 2 分</option><option value="1">★☆☆☆☆ 1 分</option></select></label><label id="discussion-label">参与商品讨论<textarea name="content" required maxlength="1000" rows="3" placeholder="分享你的想法，或向大家提问"></textarea></label><p class="form-error" role="alert"></p><button class="button primary" type="submit">发布评论</button><p class="modal-hint" id="discussion-hint">登录后即可评论，所有人都能查看公开讨论。</p></form></section>`);
    if(state.user)request('/history','POST',{product_id:p.id}).catch(()=>{});
    await loadDiscussions();
  }
  async function loadDiscussions() {
    const id=f.productId,kind=f.discussion,page=f.discussionPage;
    if(!id||!$('#discussion-list'))return;
    const result=await api(`/products/${id}/${kind}?page=${page}`);
    if(id!==f.productId||kind!==f.discussion||page!==f.discussionPage||!$('#discussion-list'))return;
    const reviews=kind==='reviews';
    $('#discussion-summary').textContent=reviews?`${result.total} 条购买评价 · 平均 ${Number(result.average_rating).toFixed(1)} 分`:`${result.total} 条公开评论`;
    $('#discussion-list').innerHTML=result.items.map(r=>`<article class="discussion-item"><div><strong>${e(r.name)}</strong>${reviews?`<span class="review-stars">${'★'.repeat(r.rating)}${'☆'.repeat(5-r.rating)} · 已购买</span>`:''}<time>${when(r.created_at)}</time></div><p>${e(r.content)}</p></article>`).join('')||`<p class="feature-empty">还没有${reviews?'购买评价':'评论'}，期待你的分享。</p>`;
    $('#discussion-pagination').innerHTML=pager(result,'discussion-page');
    $('#discussion-rating').hidden=!reviews;
    $('#discussion-form [type="submit"]').textContent=reviews?'保存购买评价':'发布评论';
    $('#discussion-hint').textContent=reviews?'购买并支付过此商品后可以评价；再次提交会更新你的评价。':'登录后即可评论，所有人都能查看公开讨论。';
  }
  function orderExtras(o) {
    const openRefund=['pending','approved'].includes(o.refund?.status);
    return `${o.status==='paid'?`<div class="order-progress">${Object.entries(stages).map(([k,v])=>`<span class="${Object.keys(stages).indexOf(k)<=Object.keys(stages).indexOf(o.delivery_stage)?'reached':''}">${v}</span>`).join('')}</div>`:''}${o.discount?`<p class="discount-note">优惠券抵扣 ¥${money(o.discount)}</p>`:''}${o.tracking_events?.length?`<details class="tracking-details"><summary>查看订单进度</summary>${o.tracking_events.map(t=>`<p><time>${when(t.created_at)}</time> · ${e(t.note)}</p>`).join('')}</details>`:''}${o.refund?`<p class="refund-state">${refundLabels[o.refund.status]} · ¥${money(o.refund.amount)}${o.refund.note?`<br>${e(o.refund.note)}`:''}</p>`:''}${o.status==='paid'&&!openRefund?`<div class="feature-actions"><button class="button outline" data-feature-action="refund" data-order="${e(o.id)}">申请退款</button>${o.items.map(i=>`<button class="button outline" data-feature-action="review-product" data-id="${i.product_id}">评价 ${e(i.name)}</button>`).join('')}</div>`:''}`;
  }
  async function loadStores() {
    const rows=await api('/stores');f.stores=rows;
    const selected=state.storeId?String(state.storeId):$('#store-filter').value;
    $('#store-filter').innerHTML='<option value="">全部店铺</option>'+rows.map(s=>`<option value="${s.id}">${e(s.name)}</option>`).join('');
    if(rows.some(s=>String(s.id)===selected))$('#store-filter').value=selected;
    else if(state.storeId){state.storeId=null;state.page=1;await loadProducts();}
  }
  async function supportOverview() {
    if(!state.user)return null;
    const identity=state.user.id;const data=await api('/support/conversations');
    if(identity!==state.user?.id)return null;f.threads=data.items;
    $('#support-unread').hidden=!data.notify_support||!data.unread;$('#support-unread').textContent=data.unread;
    return data;
  }
  async function openSupport(storeId) {
    const identity=state.user.id;
    await Promise.all([loadStores(),supportOverview()]);if(identity!==state.user?.id)return;
    const stores=new Map(f.stores.map(s=>[s.id,{...s,is_active:1}]));
    for(const t of f.threads)if(!stores.has(t.store_id))stores.set(t.store_id,{id:t.store_id,name:t.store_name,is_active:t.store_active});
    $('#support-store').innerHTML=[...stores.values()].map(s=>`<option value="${s.id}">${e(s.name)}${s.is_active?'':'（暂停营业）'}${f.threads.find(t=>t.store_id===s.id)?.unread?' · 有未读消息':''}</option>`).join('');
    const chosen=storeId||f.threads[0]?.store_id||f.stores.find(s=>s.products)?.id||f.stores[0]?.id;
    if(!stores.has(chosen))throw new Error('店铺不存在或已停止营业');
    $('#support-store').value=chosen;showDialog('support-dialog');await selectSupport(chosen);
  }
  async function selectSupport(storeId) {
    f.storeId=Number(storeId);++f.chatEpoch;f.chatBusy=false;f.chat=[];f.chatKey=null;
    $('#support-form').reset();$('#support-form .form-error').textContent='';
    $('#support-thread').innerHTML='<p class="feature-empty">正在加载本店会话…</p>';$('#support-earlier').hidden=true;
    await loadChat();
  }
  function renderChat(scroll=true) {
    $('#support-thread').innerHTML=f.chat.map(m=>`<article class="chat-message ${m.sender_side==='customer'?'mine':'theirs'}"><small>${m.sender_side==='customer'?'我':'本店客服'} · ${when(m.created_at)}</small><p>${e(m.content)}</p></article>`).join('')||'<p class="feature-empty">发送第一条消息，咨询这家店铺。</p>';
    $('#support-earlier').hidden=f.chat.length<50;
    if(scroll)$('#support-thread').scrollTop=$('#support-thread').scrollHeight;
  }
  async function loadChat(earlier=false) {
    if(f.chatBusy||!state.user)return;
    if(!$('#support-dialog').open||!f.storeId){await supportOverview();return;}
    f.chatBusy=true;const identity=state.user.id,storeId=f.storeId,epoch=f.chatEpoch;
    try {
      const cursor=earlier?(f.chat[0]?.id||0):(f.chat.at(-1)?.id||0);
      const data=await api(`/support/messages?store_id=${storeId}&page_size=50&${earlier?'before_id':'after_id'}=${cursor}`);
      if(state.user?.id!==identity||epoch!==f.chatEpoch||!$('#support-dialog').open)return;
      f.storeActive=!!data.store_active;
      $('#support-title').textContent=`${data.store_name} · 客服`;
      $('#support-store-status').textContent=f.storeActive?'工作人员会在本店会话中回复。':'店铺暂停营业，仍可查看历史回复。';
      $('#support-form [type="submit"]').disabled=!f.storeActive;
      const merged=earlier?[...data.items,...f.chat]:[...f.chat,...data.items];
      f.chat=[...new Map(merged.map(m=>[m.id,m])).values()].sort((a,b)=>a.id-b.id);
      if(data.items.length||!f.chat.length)renderChat(!earlier);
      const last=f.chat.at(-1)?.id;
      if(last)await request('/support/read','POST',{last_message_id:last,store_id:storeId});
      await supportOverview();
    } finally {if(epoch===f.chatEpoch)f.chatBusy=false;}
  }

  function reset() {
    window.mallSearch?.hide();
    ++f.chatEpoch;f.chatBusy=false;f.storeId=null;f.chat=[];f.chatKey=null;f.threads=[];f.addresses=[];f.coupons=[];f.view='';f.identity=null;
    for(const id of ['feature-dialog','address-editor','support-dialog','refund-dialog'])if($(`#${id}`).open)closeDialog(id);
    $('#feature-content').innerHTML='';$('#support-thread').innerHTML='';$('#support-form').reset();$('#support-unread').hidden=true;
    $('#checkout-address').innerHTML='<option value="">手动填写地址</option>';$('#checkout-coupon').innerHTML='<option value="">不使用优惠券</option>';$('#checkout-save-address').checked=false;
  }
  function accountChanged() {if(f.identity!==state.user?.id){reset();f.identity=state.user?.id;} }
  async function sync() {
    if(document.hidden)return;
    const tasks=[];
    if(state.user)tasks.push(loadChat());
    if(window.mallPages?.isProduct())tasks.push(loadDiscussions());
    if(state.user&&$('#feature-dialog').open&&f.view!=='settings')tasks.push(loadView());
    await Promise.allSettled(tasks);
  }
  document.addEventListener('click',async event=>{
    const b=event.target.closest('button');if(!b||b.disabled)return;
    const action=b.dataset.featureAction;
    if(!action&&!b.dataset.feature&&!b.dataset.discussion)return;
    b.disabled=true;
    try {
      if(b.dataset.feature)await open(b.dataset.feature,Number(b.dataset.store)||null);
      else if(b.dataset.discussion){f.discussion=b.dataset.discussion;f.discussionPage=1;document.querySelectorAll('[data-discussion]').forEach(x=>x.classList.toggle('active',x===b));$('#discussion-form .form-error').textContent='';await loadDiscussions();}
      else if(action==='new-address')editAddress();
      else if(action==='edit-address')editAddress(Number(b.dataset.id));
      else if(action==='delete-address'){await request(`/addresses/${b.dataset.id}`,'DELETE');await loadView();}
      else if(action==='claim'){await request(`/coupons/${b.dataset.id}/claim`,'POST');toast('优惠券已领取');await loadView();}
      else if(action==='clear-history'){await request('/history','DELETE');f.page=1;await loadView();}
      else if(action==='history-page'){f.page=Number(b.dataset.page);await loadView();}
      else if(action==='discussion-page'){f.discussionPage=Number(b.dataset.page);await loadDiscussions();}
      else if(action==='history-product'){closeDialog('feature-dialog');await showProduct(Number(b.dataset.id));}
      else if(action==='review-product'){closeDialog('orders-dialog');if(!await showProduct(Number(b.dataset.id)))return;f.discussion='reviews';document.querySelectorAll('[data-discussion]').forEach(x=>x.classList.toggle('active',x.dataset.discussion==='reviews'));await loadDiscussions();$('#discussion-form')?.scrollIntoView({block:'nearest'});}
      else if(action==='refund'){
        const o=await api(`/orders/${b.dataset.order}`);f.refundOrder=o.id;f.refundKey=crypto.randomUUID();$('#refund-form').reset();$('#refund-form .form-error').textContent='';$('#refund-summary').textContent=`订单 ${o.id} · 申请退款 ¥${money(o.total)}`;showDialog('refund-dialog');
      }
    } catch(err){toast(friendlyError(err));}finally{b.disabled=false;}
  });
  document.addEventListener('submit',async event=>{
    const form=event.target;
    if(!['address-form','settings-form','password-form','discussion-form','support-form','refund-form'].includes(form.id))return;
    event.preventDefault();const b=form.querySelector('[type="submit"]');if(b.disabled)return;b.disabled=true;
    const error=form.querySelector('.form-error');error.textContent='';const values=Object.fromEntries(new FormData(form));
    try {
      if(form.id==='address-form'){values.is_default=form.elements.is_default.checked;await request(`/addresses${f.addressId?'/'+f.addressId:''}`,f.addressId?'PUT':'POST',values);closeDialog('address-editor');await loadView();toast('地址已保存');}
      if(form.id==='settings-form'){values.notify_support=form.elements.notify_support.checked;const s=await request('/settings','PUT',values);state.user={...state.user,...s};updateAccount();$('#profile-name').textContent=s.name;publishShopChange('session');toast('设置已保存');}
      if(form.id==='password-form'){const r=await request('/settings/password','POST',values);form.reset();publishShopChange('session');toast(r.message);}
      if(form.id==='discussion-form'){
        if(!state.user){requestLogin();return;}
        const payload=f.discussion==='reviews'?{rating:Number(values.rating),content:values.content}:{content:values.content};
        await request(`/products/${f.productId}/${f.discussion}`,f.discussion==='reviews'?'PUT':'POST',payload);form.elements.content.value='';f.discussionPage=1;await loadDiscussions();publishShopChange('discussions');toast('内容已保存');
      }
      if(form.id==='support-form'){
        f.chatKey||=crypto.randomUUID();await request('/support/messages','POST',{content:values.content,client_message_id:f.chatKey,store_id:f.storeId});f.chatKey=null;form.reset();await loadChat();publishShopChange('support');
      }
      if(form.id==='refund-form'){await request(`/orders/${f.refundOrder}/refund`,'POST',{reason:values.reason,idempotency_key:f.refundKey});closeDialog('refund-dialog');await loadOrders();publishShopChange('orders');toast('退款申请已提交，请等待店铺审核');}
    }catch(err){error.textContent=friendlyError(err);}finally{b.disabled=false;}
  });
  $('#support-store').addEventListener('change',event=>selectSupport(event.target.value).catch(err=>toast(friendlyError(err))));
  $('#checkout-address').addEventListener('change',fillAddress);
  $('#checkout-coupon').addEventListener('change',()=>{state.checkoutKey=null;checkoutSummary();});
  for(const k of ['recipient','phone','address'])$('#checkout-form').elements[k].addEventListener('input',()=>{$('#checkout-address').value='';state.checkoutKey=null;});
  $('#support-form textarea').addEventListener('input',()=>{f.chatKey=null;});
  $('#support-earlier').addEventListener('click',()=>loadChat(true).catch(err=>toast(friendlyError(err))));
  setInterval(()=>{if(!document.hidden&&state.user)loadChat().catch(()=>{});},5000);
  window.mallFeatures={productOpened,checkoutOpened,checkoutSummary,checkoutCartChanged,checkoutPayload,orderExtras,reset,accountChanged,sync,loadStores};
})();
