"use strict";
(() => {
  Object.assign(titles,{support:'店铺客服',coupons:'优惠券管理',refunds:'退款审核',discussions:'评论与评价'});
  const x={pages:{support:1,coupons:1,refunds:1,discussions:1},items:new Map(),kind:'comments',sequence:0,conversation:null,messages:[],chatBusy:false,chatKey:null,identity:null};
  const e=escapeHTML;
  const request=(path,method,body)=>api(path,{method,...(body?{body:JSON.stringify(body)}:{})});
  const handles=section=>['support','coupons','refunds','discussions'].includes(section);
  const couponTerms=c=>c.kind==='amount'?`减 ¥${money(c.value)}`:`${c.value/10} 折${c.max_discount?`，最多减 ¥${money(c.max_discount)}`:''}`;
  const stages={awaiting:'等待处理',packing:'打包中',dispatched:'已发货',transit:'运输中',delivered:'已到货'};
  function pager(result){const pages=Math.ceil(result.total/result.page_size);return pages>1?`<div class="table-pagination"><button class="button secondary" data-extra="page" data-page="${result.page-1}" ${result.page<=1?'disabled':''}>上一页</button><span>${result.page} / ${pages} · 共 ${result.total} 条</span><button class="button secondary" data-extra="page" data-page="${result.page+1}" ${result.page>=pages?'disabled':''}>下一页</button></div>`:`<p class="muted">共 ${result.total} 条记录</p>`;}
  async function load(){
    if(!state.user)return;const section=state.section,sequence=++x.sequence;
    const target=$(`#${section}-extra`);if(!target)return;
    $('#admin-refresh').disabled=true;
    try {
      const result=await api(`/admin/${section}?page=${x.pages[section]}${section==='discussions'?'&kind='+x.kind:''}${section==='support'&&$('#admin-support-store').value?'&store_id='+$('#admin-support-store').value:''}`);
      if(sequence!==x.sequence||section!==state.section||!state.user)return;
      x.items=new Map(result.items.map(i=>[i.id,i]));
      if(section==='coupons')target.innerHTML=result.items.map(c=>`<article class="feature-card"><div class="feature-toolbar"><h3>${e(c.name)}</h3><span class="status ${c.is_active?'active':'inactive'}">${c.is_active?'已启用':'已停用'}</span></div><p>${e(c.code)} · ${e(couponTerms(c))} · 满 ¥${money(c.threshold)} 可用</p><p class="muted">已领取 ${c.claimed_count} / ${c.claim_limit} · ${date(c.starts_at)} 至 ${date(c.expires_at)}</p><div class="feature-actions"><button class="button secondary" data-extra="edit-coupon" data-id="${c.id}">编辑</button><button class="button secondary" data-extra="toggle-coupon" data-id="${c.id}">${c.is_active?'停用':'启用'}</button></div></article>`).join('');
      if(section==='refunds')target.innerHTML=result.items.map(r=>`<article class="feature-card"><div class="feature-toolbar"><h3>${e(r.order_id)}</h3><span class="status ${r.status==='pending'?'pending':r.status==='approved'?'completed':'cancelled'}">${{pending:'待审核',approved:'已退款',rejected:'已拒绝'}[r.status]}</span></div><p>${e(r.name)} · 退款金额 ¥${money(r.amount)} · ${date(r.created_at)}</p><p class="extra-content-text">原因：${e(r.reason)}</p>${r.note?`<p>审核说明：${e(r.note)}</p>`:''}<div class="feature-actions"><button class="button secondary" data-action="view-order" data-order="${e(r.order_id)}">查看订单</button>${r.status==='pending'?`<button class="button primary" data-extra="review-refund" data-id="${r.id}">审核退款</button>`:''}</div></article>`).join('');
      if(section==='discussions')target.innerHTML=result.items.map(r=>`<article class="feature-card"><div class="feature-toolbar"><h3>${e(r.product_name)}</h3><span class="status ${r.is_visible?'active':'inactive'}">${r.is_visible?'公开显示':'已隐藏'}</span></div><p class="muted">${e(r.name)} · ${date(r.created_at)}${x.kind==='reviews'?` · ${r.rating} 分 · 已购买`:''}</p><p class="extra-content-text">${e(r.content)}</p><button class="button secondary" data-extra="visibility" data-id="${r.id}">${r.is_visible?'隐藏内容':'恢复显示'}</button></article>`).join('');
      if(section==='support')target.innerHTML=result.items.map(c=>`<article class="feature-card"><div class="feature-toolbar"><h3>${e(c.name)}</h3><span class="status ${c.unread?'pending':'completed'}">${c.unread?`${c.unread} 条未读`:'已读'}</span></div><p class="muted">${e(c.store_name)} · ${e(c.phone)}</p><p class="extra-content-text">${e(c.last_message)}</p><button class="button primary" data-extra="conversation" data-id="${c.id}">进入会话</button></article>`).join('');
      if(!result.items.length)target.innerHTML='<p class="feature-empty">暂时没有记录。</p>';
      target.insertAdjacentHTML('beforeend',pager(result));
      $('#last-refreshed').textContent=`更新于 ${new Date().toLocaleTimeString('zh-CN')}`;
    }catch(err){if(sequence===x.sequence)target.innerHTML=`<p class="error">${e(friendlyError(err))}</p><button class="button secondary" data-extra="reload">重新加载</button>`;}
    finally{if(sequence===x.sequence)$('#admin-refresh').disabled=false;}
  }
  function couponBody(c){return Object.fromEntries(['code','name','kind','value','threshold','max_discount','claim_limit','starts_at','expires_at','is_active','store_id'].map(k=>[k,k==='is_active'?!!c[k]:c[k]]));}
  function editCoupon(id=null){
    const c=id?x.items.get(id):{code:'',name:'',kind:'amount',value:1000,threshold:9900,max_discount:0,claim_limit:200,starts_at:new Date().toISOString().slice(0,19)+'Z',expires_at:new Date(Date.now()+30*86400000).toISOString().slice(0,19)+'Z',is_active:1};
    const input=(name,label,value,type='text',props='')=>`<label>${label}<input name="${name}" type="${type}" value="${e(value)}" required ${type==='datetime-local'?'step="1"':''} ${props}></label>`;
    const storeOptions=(state.user.is_super_admin?`<option value="" ${!c.store_id?'selected':''}>全平台通用</option>`:'')+state.options.stores.map(s=>`<option value="${s.id}" ${s.id===c.store_id?'selected':''}>${e(s.name)}</option>`).join('');
    $('#extra-title').textContent=id?'编辑优惠券':'新增优惠券';
    $('#extra-content').innerHTML=`<form id="coupon-form" data-id="${id||''}">${input('code','券码（字母、数字、下划线）',c.code,'text','maxlength="30" pattern="[a-zA-Z0-9_-]+"')}${input('name','优惠券名称',c.name,'text','maxlength="60"')}<label>适用店铺<select name="store_id">${storeOptions}</select></label><label>优惠类型<select name="kind"><option value="amount" ${c.kind==='amount'?'selected':''}>满减券</option><option value="rate" ${c.kind==='rate'?'selected':''}>折扣券</option></select></label><p class="field-note" id="coupon-value-hint"></p><div class="form-grid">${input('value','优惠数值',c.kind==='rate'?c.value:c.value/100,'number','min="0.01" step="0.01"')}${input('threshold','商品金额门槛（元）',c.threshold/100,'number','min="0" step="0.01"')}${input('max_discount','最多减免（元，0 为不限）',c.max_discount/100,'number','min="0" step="0.01"')}${input('claim_limit','领取数量上限',c.claim_limit,'number','min="1" max="1000000" step="1"')}</div><div class="form-grid">${input('starts_at','开始时间（本地时间）',localTime(c.starts_at),'datetime-local')}${input('expires_at','结束时间（本地时间）',localTime(c.expires_at),'datetime-local')}</div><label class="checkbox-label"><input name="is_active" type="checkbox" ${c.is_active?'checked':''}>启用优惠券</label>${c.claimed_count?'<p class="field-note">已有用户领取，优惠规则不可修改；可调整名称、上限和启用状态。</p>':''}<p class="error" role="alert"></p><button class="button primary" type="submit">保存优惠券</button></form>`;
    couponHint();showDialog('extra-dialog');
  }
  function couponCents(value){return /^0(?:\.0{1,2})?$/.test(value)?0:decimalToCents(value);}
  function localTime(utc){const d=new Date(utc);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,19);}
  function couponHint(){const form=$('#coupon-form');const rate=form.elements.kind.value==='rate';$('#coupon-value-hint').textContent=rate?'折扣填付款百分比：90 表示 9 折，85 表示 8.5 折。':'满减金额以元填写，例如 10 表示减 10 元。';form.elements.value.min=rate?1:.01;form.elements.value.max=rate?99:10000000;form.elements.value.step=rate?1:.01;}
  function orderOpened(o){
    const blocked=['pending','approved'].includes(o.refund?.status);
    const s=o.delivery_stage;
    const allowed=o.status==='paid'&&!blocked?o.fulfillment_status==='unfulfilled'?(s==='awaiting'?['packing']:[]):s==='dispatched'?['transit','delivered']:s==='transit'?['delivered']:[]:[];
    const buttons=allowed.map(stage=>`<button class="button secondary" data-extra="tracking" data-order="${e(o.id)}" data-stage="${stage}">更新为${stages[stage]}</button>`).join('');
    $('#order-detail-content').insertAdjacentHTML('beforeend',`<div class="order-info-block"><h3>订单配送进度 · ${stages[s]||'等待处理'}</h3>${o.tracking_events.map(t=>`<p>${date(t.created_at)} · ${e(t.note)}</p>`).join('')}<div class="feature-actions">${buttons}</div>${o.discount?`<p>优惠券抵扣 ¥${money(o.discount)}</p>`:''}${o.refund?`<p class="refund-state">${{pending:'退款审核中',approved:'已退款',rejected:'退款已拒绝'}[o.refund.status]} · ¥${money(o.refund.amount)}<br>${e(o.refund.note)}</p>`:''}</div>`);
    if(blocked)$('#order-detail-content [data-action="ship-order"]')?.remove();
  }
  function renderChat(scroll=true){
    $('#admin-chat-thread').innerHTML=x.messages.map(m=>`<article class="chat-message ${m.sender_side==='staff'?'mine':'theirs'}"><small>${m.sender_side==='staff'?'店铺客服':'顾客'} · ${date(m.created_at)}</small><p>${e(m.content)}</p></article>`).join('');
    $('#admin-chat-earlier').hidden=x.messages.length<50;
    if(scroll)$('#admin-chat-thread').scrollTop=$('#admin-chat-thread').scrollHeight;
  }
  async function chat(earlier=false){
    if(x.chatBusy||!x.conversation||!state.user)return;x.chatBusy=true;
    const id=x.conversation,identity=state.user.id;
    try {
      const cursor=earlier?x.messages[0]?.id||0:x.messages.at(-1)?.id||0;
      const data=await api(`/admin/support/${id}/messages?page_size=50&${earlier?'before_id':'after_id'}=${cursor}`);
      if(id!==x.conversation||identity!==state.user?.id||!$('#admin-chat-dialog').open)return;
      x.messages=[...new Map((earlier?[...data.items,...x.messages]:[...x.messages,...data.items]).map(m=>[m.id,m])).values()].sort((a,b)=>a.id-b.id);
      if(data.items.length)renderChat(!earlier);
      const last=x.messages.at(-1)?.id;if(last)await request(`/admin/support/${id}/read`,'POST',{last_message_id:last});
    }finally{x.chatBusy=false;}
  }
  document.addEventListener('click',async event=>{
    const b=event.target.closest('[data-extra]');if(!b||b.disabled)return;const action=b.dataset.extra;b.disabled=true;
    try {
      if(action==='reload')await load();
      if(action==='page'){x.pages[state.section]=Number(b.dataset.page);await load();}
      if(action==='new-coupon')editCoupon();
      if(action==='edit-coupon')editCoupon(Number(b.dataset.id));
      if(action==='toggle-coupon'){const c=x.items.get(Number(b.dataset.id));await request(`/admin/coupons/${c.id}`,'PUT',{...couponBody(c),is_active:!c.is_active});await load();publish('coupons');}
      if(action==='visibility'){const r=x.items.get(Number(b.dataset.id));await request(`/admin/discussions/${r.id}?kind=${x.kind}`,'PATCH',{is_visible:!r.is_visible});await load();publish('discussions');}
      if(action==='tracking'){
        $('#extra-title').textContent=`更新为${stages[b.dataset.stage]}`;
        $('#extra-content').innerHTML=`<form id="tracking-form" data-order="${e(b.dataset.order)}" data-stage="${b.dataset.stage}"><label>进度说明<textarea name="note" maxlength="200" rows="3" placeholder="可填写打包或物流处理说明"></textarea></label><p class="error" role="alert"></p><button class="button primary" type="submit">确认更新</button></form>`;showDialog('extra-dialog');
      }
      if(action==='review-refund'){
        const r=x.items.get(Number(b.dataset.id));$('#extra-title').textContent='审核退款';
        $('#extra-content').innerHTML=`<p>${e(r.order_id)} · 退款金额 ¥${money(r.amount)}</p><p class="extra-content-text">${e(r.reason)}</p><form id="refund-decision-form" data-id="${r.id}"><label>审核结果<select name="decision"><option value="approve">同意退款</option><option value="reject">拒绝退款</option></select></label><label>审核说明<textarea name="note" maxlength="500" rows="3" placeholder="拒绝时必须填写原因"></textarea></label>${r.fulfillment_status==='unfulfilled'?'<p class="field-note">订单尚未发货，同意退款会自动恢复库存。</p>':'<label class="checkbox-label"><input name="restock" type="checkbox">退货已收到并可重新销售，恢复库存</label>'}<p class="field-note">此操作更新演示订单，不涉及真实资金转账。</p><p class="error" role="alert"></p><button class="button primary" type="submit">提交审核结果</button></form>`;showDialog('extra-dialog');
      }
      if(action==='conversation'){
        const c=x.items.get(Number(b.dataset.id));x.identity=state.user.id;x.conversation=c.id;x.messages=[];x.chatKey=null;$('#admin-chat-form').reset();$('#admin-chat-form .error').textContent='';$('#admin-chat-title').textContent=`${c.store_name} · 与 ${c.name} 的会话`;$('#admin-chat-thread').innerHTML='';showDialog('admin-chat-dialog');await chat();await load();
      }
    }catch(err){toast(friendlyError(err));}finally{b.disabled=false;}
  });
  document.addEventListener('change',event=>{if(event.target.id==='discussion-kind'){x.kind=event.target.value;x.pages.discussions=1;load();}if(event.target.closest('#coupon-form')&&event.target.name==='kind')couponHint();});
  document.addEventListener('submit',async event=>{
    const form=event.target;if(!['coupon-form','refund-decision-form','tracking-form','admin-chat-form'].includes(form.id))return;
    event.preventDefault();const b=form.querySelector('[type="submit"]');if(b.disabled)return;b.disabled=true;const error=form.querySelector('.error');error.textContent='';
    const v=Object.fromEntries(new FormData(form));
    try {
      if(form.id==='coupon-form'){
        const rate=v.kind==='rate';const payload={...v,store_id:Number(v.store_id)||null,value:rate?Number(v.value):decimalToCents(v.value),threshold:couponCents(v.threshold),max_discount:couponCents(v.max_discount),claim_limit:Number(v.claim_limit),starts_at:new Date(v.starts_at).toISOString().slice(0,19)+'Z',expires_at:new Date(v.expires_at).toISOString().slice(0,19)+'Z',is_active:form.elements.is_active.checked};
        await request(`/admin/coupons${form.dataset.id?'/'+form.dataset.id:''}`,form.dataset.id?'PUT':'POST',payload);closeDialog('extra-dialog');await load();publish('coupons');toast('优惠券已保存');
      }
      if(form.id==='refund-decision-form'){
        await request(`/admin/refunds/${form.dataset.id}/decision`,'POST',{approve:v.decision==='approve',note:v.note,restock:!!form.elements.restock?.checked});closeDialog('extra-dialog');await load();publish('orders');publish('products');toast('退款审核已完成');
      }
      if(form.id==='tracking-form'){await request(`/admin/orders/${form.dataset.order}/tracking`,'POST',{stage:form.dataset.stage,note:v.note});closeDialog('extra-dialog');await showOrder(form.dataset.order);await loadCurrent();publish('orders');toast('订单进度已更新');}
      if(form.id==='admin-chat-form'){x.chatKey||=crypto.randomUUID();await request(`/admin/support/${x.conversation}/messages`,'POST',{content:v.content,client_message_id:x.chatKey});x.chatKey=null;form.reset();await chat();if(state.section==='support')await load();publish('support');}
    }catch(err){error.textContent=friendlyError(err);}finally{b.disabled=false;}
  });
  $('#admin-support-store').addEventListener('change',()=>{x.pages.support=1;load();});
  $('#admin-chat-form textarea').addEventListener('input',()=>{x.chatKey=null;});
  $('#admin-chat-earlier').addEventListener('click',()=>chat(true).catch(err=>toast(friendlyError(err))));
  setInterval(async()=>{if(document.hidden)return;
    if(x.identity!==state.user?.id){x.identity=state.user?.id;x.messages=[];x.conversation=null;if($('#admin-chat-dialog').open)closeDialog('admin-chat-dialog');}
    if(!state.user)return;
    try{if($('#admin-chat-dialog').open)await chat();const result=await api('/admin/support?page_size=50');const count=result.unread_total;$('#admin-support-badge').hidden=!count;$('#admin-support-badge').textContent=count;if(state.section==='support')await load();}catch{}
  },5000);
  window.adminFeatures={handles,load,orderOpened};
})();
