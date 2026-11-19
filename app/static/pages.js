"use strict";
(() => {
  const page={view:'home',sequence:0,productId:null,store:null};
  const e=escapeHTML;
  const defaultTitle=document.title;
  const defaultCatalogTitle=$('#catalog-title').innerHTML;
  function filters(){return {storeId:state.storeId,q:state.q,category:state.category,sort:state.sort,page:state.page,onSale:!!state.onSale};}
  function remember(){history.replaceState({...history.state,mallFilters:filters(),mallScroll:window.scrollY},'',location.href);}
  function changeUrl(path,updateUrl){
    if(!updateUrl)return;
    if(location.pathname.replace(/\/$/,'')===path.replace(/\/$/,''))return;
    remember();
    history.pushState({mallFrom:location.pathname,mallFilters:filters()},'',path);
  }
  function syncFilters(){
    $('#search-input').value=state.q;$('#store-filter').value=state.storeId||'';$('#sort-select').value=state.sort;
    document.querySelectorAll('[data-category]').forEach(b=>b.classList.toggle('active',b.dataset.category===state.category));
  }
  function mode(view){
    page.view=view;
    $('#product-page').hidden=view!=='product';
    $('#catalog').hidden=view==='product';
    $('.hero').hidden=view!=='home';$('.benefits').hidden=view!=='home';
    $('#storefront-store-banner').hidden=view!=='store';
    $('.collection-banner').hidden=view==='store';
    if(view!=='home')document.querySelectorAll('[data-nav]').forEach(b=>b.classList.remove('active'));
  }
  function productHTML(p){
    const delivery=p.store_shipping_fee===0||p.free_shipping_threshold===0?'本店免运费':`本店商品满 ¥${money(p.free_shipping_threshold)} 包邮，不足收取 ¥${money(p.store_shipping_fee)} 运费`;
    const paramsHTML=(p.params&&p.params.length)?`<section class="product-params-section"><h2>商品参数</h2><table class="params-table"><tbody>${p.params.map(it=>`<tr><th>${e(it.name)}</th><td>${e(it.value)}</td></tr>`).join('')}</tbody></table></section>`:'';
    const detailHTML=p.detail_html?`<section class="product-detail-html-section"><h2>详情介绍</h2><div class="detail-html">${p.detail_html}</div></section>`:'';
    return `<div class="detail-layout"><div class="detail-picture" style="background:${e(p.color)}"><img src="${image(p)}" alt="${e(p.name)}"></div><div class="detail-info"><div class="detail-category">${e(p.category)}${p.badge?` · ${e(p.badge)}`:''}</div><h1>${e(p.name)}</h1><p class="detail-subtitle">${e(p.subtitle)}</p><div class="product-price"><span class="currency">¥</span>${price(p.price)}${p.original_price>p.price?`<span class="original-price">¥${price(p.original_price)}</span>`:''}</div><dl class="detail-metrics"><div><dt>商品编号</dt><dd>${p.id}</dd></div><div><dt>可售库存</dt><dd>${p.stock} 件</dd></div><div><dt>商品销量</dt><dd>${p.sales} 件</dd></div></dl><p class="detail-delivery">${icon('truck')}<span>${delivery}</span></p><button class="button primary full" data-action="add" data-id="${p.id}" ${p.stock?'':'disabled'}>${icon('plus')} ${p.stock?'加入购物车':'暂时售罄'}</button><p class="modal-hint">演示商品，支付不会产生真实扣款。</p></div></div><section class="product-description-section"><h2>商品信息</h2><p>${e(p.description)}</p></section>${paramsHTML}${detailHTML}<section class="product-store-card"><div class="detail-store-heading"><span class="suggestion-store-icon">店</span><div><h2>${e(p.store_name)}</h2><p>${e(p.store_description||'欢迎浏览本店好物')}</p>${p.store_phone?`<small>客服电话：${e(p.store_phone)}</small>`:''}</div></div><div class="feature-actions"><button class="button primary" data-search-store="${p.store_id}">进入店铺 ${icon('arrow-right')}</button><button class="button outline" data-feature="support" data-store="${p.store_id}">联系本店客服</button></div></section>`;
  }
  async function product(id,{updateUrl=true}={}){
    if(!Number.isSafeInteger(id)||id<1)throw new Error('商品编号不正确');
    const sequence=++page.sequence;
    ++productRequest;$('#product-grid').removeAttribute('aria-busy');
    changeUrl(`/products/${id}`,updateUrl);page.productId=id;mode('product');
    document.title='商品详情 · 黑马商城';
    $('#product-detail').innerHTML='<div class="loading-state">正在加载商品信息…</div>';
    $('#product-detail').setAttribute('aria-busy','true');window.mallSearch?.hide();
    for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();
    window.scrollTo({top:0,behavior:'instant'});
    try {
      const p=await api(`/products/${id}`);if(sequence!==page.sequence)return;
      $('#product-detail').innerHTML=productHTML(p);document.title=`${p.name} · 黑马商城`;
      $('#product-detail').removeAttribute('aria-busy');
      try{await window.mallFeatures?.productOpened(p);}
      catch(err){if(sequence===page.sequence&&$('#discussion-list'))$('#discussion-list').innerHTML=`<p class="form-error">评论暂时无法加载：${e(friendlyError(err))}</p>`;}
      return sequence===page.sequence?p:null;
    }catch(err){
      if(sequence!==page.sequence)return;
      $('#product-detail').innerHTML=emptyState('商品暂时无法查看',friendlyError(err),'<button class="button primary" data-page-action="retry-product">重新加载</button><button class="button outline" data-page-action="browse">返回商品列表</button>');
      throw err;
    }finally{if(sequence===page.sequence)$('#product-detail').removeAttribute('aria-busy');}
  }
  function storeBanner(s){
    const shipping=s.shipping_fee===0||s.free_shipping_threshold===0?'本店免运费':`满 ¥${money(s.free_shipping_threshold)} 包邮 · 运费 ¥${money(s.shipping_fee)}`;
    return `<div class="store-page-heading"><span class="suggestion-store-icon">店</span><div><span class="eyebrow small">WELCOME TO OUR STORE</span><h1>${e(s.name)}</h1><p>${e(s.description||'欢迎浏览本店好物')}</p><small>${s.products} 件在售商品 · ${shipping}${s.phone?` · 客服电话 ${e(s.phone)}`:''}</small></div></div><div class="feature-actions"><button class="button primary" data-feature="support" data-store="${s.id}">联系本店客服</button><button class="button outline" data-page-action="all-products">浏览全部店铺</button></div>`;
  }
  async function store(id,{updateUrl=true,restoreFilters=false}={}){
    if(!Number.isSafeInteger(id)||id<1)throw new Error('店铺编号不正确');
    const sequence=++page.sequence;
    changeUrl(`/stores/${id}`,updateUrl);++productRequest;
    state.storeId=id;
    if(!restoreFilters){state.q='';state.category='';state.sort='recommended';state.page=1;state.onSale=false;}
    syncFilters();page.store=null;
    page.productId=null;mode('store');window.mallSearch?.hide();
    for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();
    $('#storefront-store-banner').innerHTML='<p class="loading-state">正在进入店铺…</p>';
    $('#product-grid').innerHTML='<p class="loading-state">正在加载店内商品…</p>';
    $('#product-grid').removeAttribute('aria-busy');$('#pagination').hidden=true;$('#product-total').textContent='';
    $('#search-summary').hidden=true;window.mallSearch?.renderStores([]);
    $('#catalog-title').innerHTML='店内商品';document.title='店铺 · 黑马商城';
    try {
      const s=await api(`/stores/${id}`);if(sequence!==page.sequence)return;
      page.store=s;$('#storefront-store-banner').innerHTML=storeBanner(s);document.title=`${s.name} · 黑马商城`;
      await loadProducts();if(sequence!==page.sequence)return;
      remember();$('#catalog').scrollIntoView({block:'start',behavior:'instant'});
    }catch(err){
      if(sequence!==page.sequence)return;
      page.store=null;$('#storefront-store-banner').innerHTML=emptyState('店铺暂时无法查看',friendlyError(err),'<button class="button outline" data-page-action="all-products">返回商城</button>');
      $('#product-grid').innerHTML='';$('#pagination').hidden=true;$('#product-total').textContent='';
      throw err;
    }
  }
  function catalog({home=false,updateUrl=true}={}){
    const inStore=!home&&!!state.storeId;
    if(home||page.view!==(inStore?'store':'home')){
      ++page.sequence;++productRequest;$('#product-grid').removeAttribute('aria-busy');
    }
    changeUrl(inStore?`/stores/${state.storeId}`:'/',updateUrl);
    page.productId=null;
    if(home){state.storeId=null;page.store=null;}
    mode(inStore?'store':'home');
    if(inStore&&page.store?.id===state.storeId){$('#storefront-store-banner').innerHTML=storeBanner(page.store);document.title=`${page.store.name} · 黑马商城`;$('#catalog-title').innerHTML='店内商品';}
    else if(!inStore){document.title=defaultTitle;$('#catalog-title').innerHTML=defaultCatalogTitle;}
  }
  async function browse(){
    if(['/','/stores/'+state.storeId].includes(history.state?.mallFrom)){history.back();return;}
    if(state.storeId){await store(state.storeId);return;}
    catalog({home:true});await loadProducts();scrollCatalog();
  }
  function restoreFilters(values){
    if(!values)return;
    for(const key of ['storeId','q','category','sort','page','onSale'])if(key in values)state[key]=values[key];
    syncFilters();
  }
  async function resolveLocation(){
    const productMatch=location.pathname.match(/^\/products\/(\d+)\/?$/);
    const storeMatch=location.pathname.match(/^\/stores\/(\d+)\/?$/);
    if(productMatch)await product(Number(productMatch[1]),{updateUrl:false});
    else if(storeMatch)await store(Number(storeMatch[1]),{updateUrl:false,restoreFilters:true});
    else{++page.sequence;catalog({home:true,updateUrl:false});await loadProducts();}
  }
  document.addEventListener('click',async event=>{
    const button=event.target.closest('[data-page-action]');if(!button||button.disabled)return;
    button.disabled=true;
    try{
      if(button.dataset.pageAction==='browse')await browse();
      if(button.dataset.pageAction==='retry-product')await product(page.productId);
      if(button.dataset.pageAction==='all-products'){state.q='';state.category='';state.sort='recommended';state.page=1;state.onSale=false;$('#search-input').value='';$('#store-filter').value='';$('#sort-select').value=state.sort;catalog({home:true});await setCategory('');scrollCatalog();}
    }catch(err){toast(friendlyError(err));}finally{button.disabled=false;}
  });
  window.addEventListener('popstate',async()=>{const scroll=history.state?.mallScroll;restoreFilters(history.state?.mallFilters);const pending=resolveLocation(),sequence=page.sequence;try{await pending;if(sequence===page.sequence&&Number.isFinite(scroll))window.scrollTo({top:scroll,behavior:'instant'});}catch{}});
  const directProduct=location.pathname.match(/^\/products\/(\d+)\/?$/);
  const directStore=location.pathname.match(/^\/stores\/(\d+)\/?$/);
  if(directProduct){page.productId=Number(directProduct[1]);mode('product');$('#product-detail').innerHTML='<p class="loading-state">正在加载商品信息…</p>';}
  if(directStore){state.storeId=Number(directStore[1]);mode('store');}
  window.mallPages={product,store,catalog,isProduct:()=>page.view==='product',initialize:async()=>{try{if(!page.sequence&&(directProduct||directStore))await resolveLocation();else remember();}catch{}}};
})();
