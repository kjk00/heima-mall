"use strict";
(() => {
  titles.stores='店铺管理';
  const base=window.adminFeatures;
  const s={page:1,q:'',status:'all',sequence:0,edit:null};
  const e=escapeHTML;
  const request=(path,method,body)=>api(path,{method,body:JSON.stringify(body)});
  function cents(value){return /^0(?:\.0{1,2})?$/.test(value)?0:decimalToCents(value);}
  async function load(){
    const seq=++s.sequence;$('#admin-refresh').disabled=true;
    try {
      const data=await api('/admin/stores?'+new URLSearchParams({page:s.page,q:s.q,status:s.status}));
      if(seq!==s.sequence||state.section!=='stores'||!state.user)return;
      const pages=Math.max(1,Math.ceil(data.total/data.page_size));if(s.page>pages){s.page=pages;await load();return;}
      $('#stores-extra').innerHTML=data.items.map(store=>`<article class="feature-card store-admin-card"><div class="feature-toolbar"><h3>${e(store.name)}</h3><span class="status ${store.is_active?'active':'inactive'}">${store.is_active?'营业中':'暂停营业'}</span></div><p class="extra-content-text">${e(store.description||'暂无介绍')}</p><p class="muted">客服电话：${e(store.phone||'未填写')} · 本店满 ¥${money(store.free_shipping_threshold)} 包邮 · 运费 ¥${money(store.shipping_fee)}</p><div class="store-statistics"><span>商品 <b>${store.products}</b>（上架 ${store.active_products}）</span><span>订单 <b>${store.orders}</b></span><span>净实付 <b>¥${money(store.revenue)}</b></span></div><div class="feature-actions"><button class="button primary" data-store-action="edit" data-id="${store.id}">编辑店铺</button><button class="button secondary" data-store-action="status" data-id="${store.id}">${store.is_active?'暂停营业':'恢复营业'}</button><button class="button secondary" data-store-action="products" data-id="${store.id}">店铺商品</button><button class="button secondary" data-store-action="orders" data-id="${store.id}">店铺订单</button><button class="button secondary" data-store-action="support" data-id="${store.id}">客服会话</button></div></article>`).join('')||'<p class="feature-empty">没有符合条件的店铺。</p>';
      $('#stores-extra').insertAdjacentHTML('beforeend',`<div class="table-pagination"><span>共 ${data.total} 家店铺 · ${data.page} / ${pages}</span><button class="button secondary" data-store-action="page" data-page="${data.page-1}" ${data.page<=1?'disabled':''}>上一页</button><button class="button secondary" data-store-action="page" data-page="${data.page+1}" ${data.page>=pages?'disabled':''}>下一页</button></div>`);
      $('#last-refreshed').textContent=`更新于 ${new Date().toLocaleTimeString('zh-CN')}`;
    }catch(err){if(seq===s.sequence&&state.section==='stores')$('#stores-extra').innerHTML=`<p class="error">${e(friendlyError(err))}</p><button class="button secondary" data-store-action="refresh">重新加载</button>`;}
    finally{if(seq===s.sequence)$('#admin-refresh').disabled=false;}
  }
  async function edit(id=null){
    s.edit=id?await api(`/admin/stores/${id}`):null;
    const store=s.edit||{name:'',description:'',phone:'',shipping_fee:1000,free_shipping_threshold:19900,is_active:1};
    $('#extra-title').textContent=id?'编辑店铺':'新增店铺';
    $('#extra-content').innerHTML=`<form id="store-form"><label>店铺名称<input name="name" required maxlength="60" value="${e(store.name)}" placeholder="请输入店铺名称"></label><label>店铺介绍<textarea name="description" maxlength="500" rows="3" placeholder="介绍本店主营商品">${e(store.description)}</textarea></label><label>客服电话<input name="phone" type="tel" maxlength="30" value="${e(store.phone)}" placeholder="可选，手机号或固定电话"></label><div class="form-grid"><label>基础运费（元）<input name="shipping_fee" type="number" required min="0" max="10000000" step="0.01" value="${(store.shipping_fee/100).toFixed(2)}"></label><label>包邮门槛（元）<input name="free_shipping_threshold" type="number" required min="0" max="10000000" step="0.01" value="${(store.free_shipping_threshold/100).toFixed(2)}"></label></div><p class="field-note">门槛为 0 时本店始终包邮；运费和门槛只影响新订单。</p><label class="checkbox-label"><input name="is_active" type="checkbox" ${store.is_active?'checked':''}>营业中</label><p class="field-note">暂停营业后，商品从商城隐藏并停止新下单。历史订单与售后仍可处理。</p><p class="error" role="alert"></p><button class="button primary" type="submit">保存店铺</button></form>`;
    showDialog('extra-dialog');
  }
  document.addEventListener('click',async event=>{
    const b=event.target.closest('[data-store-action]');if(!b||b.disabled)return;b.disabled=true;
    try {
      const id=Number(b.dataset.id),action=b.dataset.storeAction;
      if(action==='new')await edit();
      if(action==='edit')await edit(id);
      if(action==='refresh')await load();
      if(action==='page'){s.page=Number(b.dataset.page);await load();}
      if(action==='products'||action==='orders'){state.filters[action]={...freshFilter(),store_id:id};await navigate(action);}
      if(action==='support'){await loadOptions();$('#admin-support-store').value=id;await navigate('support');}
      if(action==='status'){
        const store=await api(`/admin/stores/${id}`);
        askConfirmation(`${store.is_active?'暂停':'恢复'}「${store.name}」营业？`,store.is_active?'本店商品会从商城隐藏，停止新下单；历史订单与售后保留。':'本店已上架商品会重新显示在商城。',store.is_active?'暂停营业':'恢复营业',async()=>{
          await request(`/admin/stores/${id}/status`,'PATCH',{is_active:!store.is_active,version:store.version});
          await loadOptions();await loadCurrent();publish('stores');publish('products');toast('店铺营业状态已更新');
        });
      }
    }catch(err){toast(friendlyError(err));}finally{b.disabled=false;}
  });
  document.addEventListener('submit',async event=>{
    if(event.target.id!=='store-form')return;event.preventDefault();
    const form=event.target,b=form.querySelector('[type="submit"]');if(b.disabled)return;b.disabled=true;
    const error=form.querySelector('.error');error.textContent='';
    try {
      const values=Object.fromEntries(new FormData(form));const payload={...values,shipping_fee:cents(values.shipping_fee),free_shipping_threshold:cents(values.free_shipping_threshold),is_active:form.elements.is_active.checked};
      if(s.edit)payload.version=s.edit.version;
      await request(s.edit?`/admin/stores/${s.edit.id}`:'/admin/stores',s.edit?'PUT':'POST',payload);
      closeDialog('extra-dialog');await loadOptions();await loadCurrent();publish('stores');publish('products');toast('店铺已保存');
    }catch(err){error.textContent=friendlyError(err);}finally{b.disabled=false;}
  });
  const filter=$('#stores-filter');
  const apply=()=>{const values=Object.fromEntries(new FormData(filter));s.q=values.q.trim();s.status=values.status;s.page=1;load();};
  filter.addEventListener('submit',event=>{event.preventDefault();apply();});
  filter.elements.status.addEventListener('change',apply);
  filter.elements.q.addEventListener('search',apply);
  window.adminFeatures={...base,handles:section=>section==='stores'||base.handles(section),load:()=>state.section==='stores'?load():base.load()};
})();
