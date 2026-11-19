"use strict";
(() => {
  const panel=$('#search-suggestions'),input=$('#search-input');
  let sequence=0,timer,active=-1;
  const e=escapeHTML;
  const recentCache=new Map(),recentFallback=new Set(),recentLimit=10;
  const recentKey=()=>`heima-mall:recent-searches:${state.user?.id??'guest'}`;
  function cleanRecent(values){
    if(!Array.isArray(values))return [];
    return [...new Set(values.filter(v=>typeof v==='string').map(v=>v.trim()).filter(v=>v&&v.length<=100))].slice(0,recentLimit);
  }
  function readRecent(){
    const key=recentKey();
    if(recentFallback.has(key))return recentCache.get(key)||[];
    try{
      const stored=localStorage.getItem(key);
      const values=stored===null?[]:cleanRecent(JSON.parse(stored));recentCache.set(key,values);return values;
    }catch{recentFallback.add(key);}
    return recentCache.get(key)||[];
  }
  function saveRecent(values){
    const key=recentKey();recentCache.set(key,values);
    try{if(values.length)localStorage.setItem(key,JSON.stringify(values));else localStorage.removeItem(key);}catch{recentFallback.add(key);}
  }
  function recordRecent(){
    const term=input.value.trim();
    if(term&&term.length<=100)saveRecent([term,...readRecent().filter(v=>v!==term)].slice(0,recentLimit));
  }
  function renderRecent(){
    const values=readRecent();
    $('#search-recent').innerHTML=`<div class="search-recent-heading"><h3>最近搜索</h3>${values.length?'<button type="button" data-search-history-clear aria-label="清空最近搜索">清空</button>':''}</div>${values.length?`<div class="search-history-list">${values.map(term=>`<button type="button" class="search-history-item" data-search-term="${e(term)}" title="${e(term)}">${e(term)}</button>`).join('')}</div>`:'<p class="search-history-empty">暂无搜索记录</p>'}`;
    active=-1;
  }
  const hide=()=>{clearTimeout(timer);++sequence;panel.hidden=true;input.setAttribute('aria-expanded','false');active=-1;};
  async function suggestions(){
    const seq=++sequence,q=input.value.trim();
    panel.hidden=false;input.setAttribute('aria-expanded','true');
    renderRecent();
    $('#search-suggestion-content').innerHTML='<p class="search-empty">正在准备推荐…</p>';
    try {
      const data=await api('/search/suggestions?'+new URLSearchParams({q}));
      if(seq!==sequence||panel.hidden)return;
      const products=data.products.map(p=>`<button type="button" class="search-suggestion-item" data-search-product="${p.id}"><img src="${image(p)}" alt=""><span><strong>${e(p.name)}</strong><small>${e(p.store_name)} · ${e(p.suggestion_label)}</small></span><b>¥${money(p.price)}</b></button>`).join('');
      const stores=data.stores.map(s=>`<button type="button" class="search-suggestion-item store-suggestion" data-search-store="${s.id}"><span class="suggestion-store-icon">店</span><span><strong>${e(s.name)}</strong><small>${e(s.suggestion_label)} · ${s.products} 件在售商品</small></span><span>进店 ↗</span></button>`).join('');
      $('#search-suggestion-content').innerHTML=`<div class="search-suggestion-heading"><span>${q?'相关商品与店铺':data.personalized?'最近浏览与热门推荐':'热门商品与店铺'}</span><button type="button" data-search-close aria-label="关闭搜索推荐">×</button></div>${products?'<h3>商品</h3>'+products:''}${stores?'<h3>店铺</h3>'+stores:''}${!products&&!stores?'<p class="search-empty">没有找到匹配内容，试试其他名称。</p>':''}`;
      active=-1;
    }catch{if(seq===sequence)$('#search-suggestion-content').innerHTML='<p class="search-empty">推荐暂时无法加载，仍可输入名称搜索。</p>';}
  }
  async function enterStore(id){
    hide();
    await window.mallPages.store(id);
  }

  function renderStores(rows){
    const root=$('#store-search-results');root.hidden=!rows.length;
    root.innerHTML=rows.length?`<h3>找到 ${rows.length} 家店铺</h3><div class="store-result-grid">${rows.map(s=>`<article class="store-result-card"><div class="suggestion-store-icon">店</div><div><h4>${e(s.name)}</h4><p>${e(s.description)}</p><small>${s.products} 件在售商品 · 满 ¥${money(s.free_shipping_threshold)} 包邮</small><div class="feature-actions"><button class="button primary" data-search-store="${s.id}">进入店铺</button><button class="button outline" data-feature="support" data-store="${s.id}">联系本店客服</button></div></div></article>`).join('')}</div>`:'';
  }
  input.addEventListener('focus',suggestions);
  input.addEventListener('click',()=>{if(panel.hidden)suggestions();});
  input.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(suggestions,180);});
  input.addEventListener('keydown',event=>{
    if(event.key==='Escape'){hide();return;}
    const buttons=[...panel.querySelectorAll('[data-search-term],[data-search-product],[data-search-store]')];
    if(!panel.hidden&&['ArrowDown','ArrowUp'].includes(event.key)&&buttons.length){
      event.preventDefault();active=(active+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;
      buttons.forEach((b,i)=>b.classList.toggle('keyboard-active',i===active));buttons[active].scrollIntoView({block:'nearest'});
    }else if(event.key==='Enter'&&!panel.hidden&&active>=0&&buttons[active]){event.preventDefault();buttons[active].click();}
  });
  $('#search-form').addEventListener('submit',()=>{recordRecent();hide();state.storeId=null;$('#store-filter').value='';});
  document.addEventListener('click',async event=>{
    if(!event.target.closest('#search-form'))hide();
    const button=event.target.closest('[data-search-term],[data-search-history-clear],[data-search-product],[data-search-store],[data-search-close]');
    if(!button||button.disabled)return;
    if(button.hasAttribute('data-search-close')){hide();return;}
    if('searchHistoryClear' in button.dataset){saveRecent([]);renderRecent();return;}
    if(button.dataset.searchTerm){input.value=button.dataset.searchTerm;$('#search-form').requestSubmit();return;}
    button.disabled=true;
    try {
      if(button.dataset.searchProduct){hide();await showProduct(Number(button.dataset.searchProduct));}
      if(button.dataset.searchStore)await enterStore(Number(button.dataset.searchStore));
    }catch(err){toast(friendlyError(err));}finally{button.disabled=false;}
  });
  document.addEventListener('focusin',event=>{if(!event.target.closest('#search-form'))hide();});
  window.mallSearch={renderStores,hide};
})();
