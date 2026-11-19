// Exercise the actual storefront, page, and search scripts without a network port.
const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');

const product = (id=1, storeId=2) => ({id, store_id:storeId, name:`耳机 ${id}<script>`,
  subtitle:'无线耳机', category:'数码科技', badge:'新品', description:'商品介绍\n第二行',
  image:'headphones', color:'#eee', price:9900, original_price:12900, stock:5, sales:32,
  store_name:'数码店<script>', store_description:'店铺介绍', store_phone:'13800138000',
  store_shipping_fee:1000, free_shipping_threshold:19900});
const store = id => ({id, name:`店铺 ${id}<script>`, description:'本店介绍',
  phone:'13800138000', shipping_fee:1000, free_shipping_threshold:19900, products:2});
const response = (data, status=200) => ({ok:status<400,status,json:async()=>data});
const deferred = () => {let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function memoryStorage(){
  const values=new Map();
  return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key)};
}

function environment(path='/',storage=memoryStorage()) {
  const elements=new Map(), documentEvents=new Map(), windowEvents=new Map();
  function element(selector) {
    if(!elements.has(selector)) elements.set(selector, {
      hidden:['#footer-admin-link','#profile-admin-link'].includes(selector), innerHTML:'', textContent:'', value:'', disabled:false, open:false,
      style:{}, attributes:{}, listeners:new Map(), classList:{add(){},remove(){},toggle(){}},
      addEventListener(type,fn){listen(this.listeners,type,fn);},
      setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];},
      scrollIntoView(){},close(){this.open=false;},querySelectorAll(){return[];},
      matches(){return false;},showPopover(){},hidePopover(){},reset(){},showModal(){this.open=true;},
      requestSubmit(){for(const fn of this.listeners.get('submit')||[])fn({preventDefault(){}});},
    });
    return elements.get(selector);
  }
  function listen(events,type,fn){if(!events.has(type))events.set(type,[]);events.get(type).push(fn);}
  const location={pathname:path,href:`http://testserver${path}`};
  const entries=[{path,state:null}];let position=0;
  const requests=[], discussionProducts=[];
  const window={scrollY:0,addEventListener:(type,fn)=>listen(windowEvents,type,fn),
    scrollTo({top}){this.scrollY=top;},matchMedia:()=>({matches:true}),
    mallFeatures:{async productOpened(p){discussionProducts.push(p.id);},accountChanged(){},reset(){},async loadStores(){}},
  };
  const history={get state(){return entries[position].state;},
    replaceState(state){entries[position].state=structuredClone(state);},
    pushState(state,unused,next){entries.splice(position+1);entries.push({path:next,state:structuredClone(state)});++position;location.pathname=next;location.href=`http://testserver${next}`;},
    back(){if(position){--position;location.pathname=entries[position].path;location.href=`http://testserver${location.pathname}`;env.lastPop=Promise.all(windowEvents.get('popstate').map(fn=>fn()));}},
  };
  const ctx={window,history,location,document:{title:'黑马商城',body:{style:{}},
    querySelector:element,getElementById:id=>element('#'+id),querySelectorAll:()=>[],addEventListener:(type,fn)=>listen(documentEvents,type,fn)},
    URLSearchParams,localStorage:storage,setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,requestAnimationFrame:fn=>fn(),
    fetch:async url=>{requests.push(url);return env.respond(url);},
  };
  element('#catalog-title').innerHTML='全部好物';
  const env={window,ctx,element,location,history,entries,requests,discussionProducts,lastPop:null,
    async respond(url){
      const u=new URL(url,'http://testserver');
      if(u.pathname==='/api/products'){
        const id=Number(u.searchParams.get('store_id'))||2;
        return response({items:[product(1,id),product(2,id)],total:2,page_size:12});
      }
      const p=u.pathname.match(/^\/api\/products\/(\d+)$/);
      if(p)return response(product(Number(p[1])));
      const s=u.pathname.match(/^\/api\/stores\/(\d+)$/);
      if(s)return response(store(Number(s[1])));
      if(u.pathname==='/api/stores')return response([]);
      if(u.pathname==='/api/search/suggestions')return response({products:[product()],stores:[store(2)],personalized:false});
      throw new Error(`Unexpected request: ${url}`);
    },
    run(code){return vm.runInContext(code,ctx);},
    async submit(term){element('#search-input').value=term;element('#search-form').requestSubmit();await new Promise(setImmediate);},
    async focusSearch(){for(const fn of element('#search-input').listeners.get('focus'))await fn();},
    async click(dataset,insideSearch=false){
      const button={dataset,disabled:false,hasAttribute:()=>false};
      const event={target:{closest:s=>s==='.hero-pagination span'?null:s==='#search-form'?(insideSearch?element('#search-form'):null):button}};
      for(const fn of documentEvents.get('click'))await fn(event);
    },
  };
  vm.createContext(ctx);
  for(const script of ['app','pages','search'])vm.runInContext(fs.readFileSync(`app/static/${script}.js`,'utf8'),ctx);
  env.state=env.run('state');env.pages=window.mallPages;
  return env;
}

module.exports={environment,product,store,response,deferred,memoryStorage};

if(require.main===module)(async()=>{
  const permissions=environment();
  for(const id of ['footer-admin-link','profile-admin-link'])assert.equal(permissions.element('#'+id).hidden,true);
  for(const [user,visible] of [[null,false],[{name:'顾客',role:'customer',is_active:1},false],
    [{name:'终端管理员',role:'admin',is_active:1,is_super_admin:1},true],
    [{name:'店铺管理员',role:'admin',is_active:1,store_id:2},true],
    [{name:'未绑定管理员',role:'admin',is_active:1},false],
    [{name:'已停用管理员',role:'admin',is_active:0,is_super_admin:1},false]]){
    permissions.state.user=user;permissions.run('updateAccount()');
    for(const id of ['footer-admin-link','profile-admin-link'])assert.equal(permissions.element('#'+id).hidden,!visible);
  }
  permissions.state.user={name:'店铺管理员',role:'admin',is_active:1,store_id:2};permissions.run('updateAccount()');
  permissions.respond=async()=>response({detail:'请先登录'},401);
  await assert.rejects(permissions.run("api('/auth/me')"));
  for(const id of ['footer-admin-link','profile-admin-link'])assert.equal(permissions.element('#'+id).hidden,true);
  const permissionPending=environment(),auth=deferred(),baseRespond=permissionPending.respond;
  permissionPending.respond=async url=>url==='/api/auth/me'?auth.promise:url==='/api/categories'?response([]):url==='/api/cart'?response({items:[],count:0}):baseRespond(url);
  const initial=permissionPending.run('initialize()');await new Promise(setImmediate);
  assert.equal(permissionPending.element('#footer-admin-link').hidden,true);
  auth.resolve(response({id:7,name:'店铺管理员',role:'admin',is_active:1,store_id:2}));await initial;
  assert.equal(permissionPending.element('#footer-admin-link').hidden,false);
  permissionPending.state.user=null;permissionPending.run('updateAccount()');assert.equal(permissionPending.element('#footer-admin-link').hidden,true);
  const e=environment();await e.pages.initialize();
  e.state.q='耳机';e.window.scrollY=640;
  await e.click({action:'detail',id:'1'});
  assert.equal(e.location.pathname,'/products/1');assert.equal(e.pages.isProduct(),true);
  const html=e.element('#product-detail').innerHTML;
  for(const label of ['商品信息','可售库存','商品销量','进入店铺','本店商品满','商品介绍'])assert.ok(html.includes(label),label);
  assert.ok(html.includes('耳机 1&lt;script&gt;'));assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('data-search-store="2"'));assert.ok(html.includes('data-feature="support" data-store="2"'));
  assert.equal(e.element('#catalog').hidden,true);assert.deepEqual(e.discussionProducts,[1]);
  await e.click({pageAction:'browse'});await e.lastPop;
  assert.equal(e.location.pathname,'/');assert.equal(e.state.q,'耳机');assert.equal(e.window.scrollY,640);
  assert.equal(e.element('#catalog').hidden,false);
  // Search submission must preserve the typed keyword while leaving a product.
  await e.pages.product(1);e.element('#search-input').value='数码店';
  for(const fn of e.element('#search-form').listeners.get('submit'))fn({preventDefault(){}});
  await new Promise(setImmediate);
  assert.equal(e.state.q,'数码店');assert.equal(e.location.pathname,'/');
  assert.ok(e.requests.some(url=>url.includes('q=%E6%95%B0%E7%A0%81%E5%BA%97')));
  await e.pages.product(1);await e.click({searchStore:'2'});
  assert.equal(e.location.pathname,'/stores/2');assert.equal(e.state.storeId,2);
  assert.equal(e.state.q,'');assert.equal(e.element('.hero').hidden,true);
  assert.ok(e.element('#storefront-store-banner').innerHTML.includes('店铺 2&lt;script&gt;'));
  assert.ok(e.state.products.every(p=>p.store_id===2));
  e.state.category='数码科技';e.state.sort='price_desc';e.state.page=2;e.window.scrollY=720;
  await e.pages.product(1);e.history.back();await e.lastPop;
  assert.equal(e.state.category,'数码科技');assert.equal(e.state.sort,'price_desc');
  assert.equal(e.state.page,2);assert.equal(e.window.scrollY,720);
  await e.click({pageAction:'all-products'});assert.equal(e.location.pathname,'/');assert.equal(e.state.storeId,null);

  // Late metadata and catalog responses cannot replace a newer page.
  const races=environment(), original=races.respond;
  let wait=deferred();races.respond=url=>url==='/api/stores/3'?wait.promise:original(url);
  let pending=races.pages.store(3);races.pages.catalog({home:true});await races.run('loadProducts()');
  wait.resolve(response(store(3)));await pending;
  assert.equal(races.location.pathname,'/');assert.equal(races.ctx.document.title,'黑马商城');
  assert.ok(races.state.products.every(p=>p.store_id===2));
  wait=deferred();pending=races.pages.store(3);await races.pages.product(1);
  wait.resolve(response(store(3)));await pending;
  assert.equal(races.pages.isProduct(),true);assert.equal(races.ctx.document.title,'耳机 1<script> · 黑马商城');
  wait=deferred();races.respond=url=>url==='/api/products/4'?wait.promise:original(url);
  pending=races.pages.product(4);await races.pages.store(2);
  wait.resolve(response(product(4)));await pending;
  assert.equal(races.pages.isProduct(),false);assert.equal(races.ctx.document.title,'店铺 2<script> · 黑马商城');
  assert.ok(!races.discussionProducts.includes(4));
  wait=deferred();pending=races.pages.product(4);await races.pages.product(5);
  wait.resolve(response(product(4)));await pending;
  assert.ok(races.element('#product-detail').innerHTML.includes('耳机 5&lt;script&gt;'));

  const direct=environment('/products/1');assert.equal(direct.element('#catalog').hidden,true);
  await direct.pages.initialize();assert.ok(direct.element('#product-detail').innerHTML.includes('商品信息'));
  const directStore=environment('/stores/3');await directStore.pages.initialize();
  assert.equal(directStore.state.storeId,3);assert.ok(directStore.state.products.every(p=>p.store_id===3));
  const errors=environment('/products/999');errors.respond=async()=>response({detail:'商品不存在或已下架'},404);
  await errors.pages.initialize();assert.ok(errors.element('#product-detail').innerHTML.includes('商品不存在或已下架'));
  assert.equal(errors.element('#product-detail').attributes['aria-busy'],undefined);
  const comments=environment();comments.window.mallFeatures.productOpened=async()=>{throw new Error('服务忙');};
  assert.equal((await comments.pages.product(1)).id,1);
  assert.ok(comments.element('#product-detail').innerHTML.includes('商品信息'));

  // Only submitted queries are recorded; they survive reload and stay private to each browser account.
  const storage=memoryStorage(),recent=environment('/',storage);
  recent.element('#search-input').value='只输入未搜索';await recent.focusSearch();
  assert.ok(recent.element('#search-recent').innerHTML.includes('暂无搜索记录'));
  await recent.submit('   ');assert.equal(storage.getItem('heima-mall:recent-searches:guest'),null);
  for(let i=0;i<12;i++)await recent.submit(`关键词${i}`);
  await recent.submit('  关键词5  ');
  let terms=JSON.parse(storage.getItem('heima-mall:recent-searches:guest'));
  assert.equal(terms.length,10);assert.equal(terms[0],'关键词5');assert.equal(terms.filter(t=>t==='关键词5').length,1);
  assert.ok(!terms.includes('关键词0'));assert.ok(!terms.includes('只输入未搜索'));
  const escaped='耳机"<script>&';await recent.submit(escaped);await recent.focusSearch();
  assert.ok(recent.element('#search-recent').innerHTML.includes('耳机&quot;&lt;script&gt;&amp;'));
  assert.ok(!recent.element('#search-recent').innerHTML.includes('<script>'));
  const refreshed=environment('/',storage);await refreshed.focusSearch();
  assert.ok(refreshed.element('#search-recent').innerHTML.includes('关键词5'));
  await refreshed.pages.store(2);await refreshed.click({searchTerm:'关键词5'},true);await new Promise(setImmediate);
  assert.equal(refreshed.state.q,'关键词5');assert.equal(refreshed.state.storeId,null);
  assert.equal(refreshed.location.pathname,'/');assert.equal(refreshed.element('#search-suggestions').hidden,true);
  assert.equal(JSON.parse(storage.getItem('heima-mall:recent-searches:guest'))[0],'关键词5');
  refreshed.state.user={id:7};await refreshed.submit('账号7的搜索');await refreshed.focusSearch();
  assert.ok(refreshed.element('#search-recent').innerHTML.includes('账号7的搜索'));
  assert.ok(!refreshed.element('#search-recent').innerHTML.includes('关键词5'));
  refreshed.state.user={id:8};await refreshed.focusSearch();
  assert.ok(refreshed.element('#search-recent').innerHTML.includes('暂无搜索记录'));
  refreshed.state.user={id:7};await refreshed.focusSearch();await refreshed.click({searchHistoryClear:''},true);
  assert.equal(refreshed.element('#search-suggestions').hidden,false);
  assert.ok(refreshed.element('#search-recent').innerHTML.includes('暂无搜索记录'));
  assert.equal(storage.getItem('heima-mall:recent-searches:7'),null);
  assert.ok(storage.getItem('heima-mall:recent-searches:guest'));
  refreshed.state.user=null;await refreshed.focusSearch();storage.removeItem('heima-mall:recent-searches:guest');
  await refreshed.focusSearch();assert.ok(refreshed.element('#search-recent').innerHTML.includes('暂无搜索记录'));
  const denied=environment('/',{getItem(){throw Error('Denied');},setItem(){throw Error('Denied');},removeItem(){throw Error('Denied');}});
  await denied.submit('无法存储时仍可搜索');await denied.focusSearch();
  assert.equal(denied.state.q,'无法存储时仍可搜索');
  assert.ok(denied.element('#search-recent').innerHTML.includes('无法存储时仍可搜索'));
  await denied.click({searchHistoryClear:''},true);await denied.focusSearch();
  assert.ok(denied.element('#search-recent').innerHTML.includes('暂无搜索记录'));
  storage.setItem('heima-mall:recent-searches:guest','{"bad":"data"}');
  const invalid=environment('/',storage);await invalid.focusSearch();
  assert.ok(invalid.element('#search-recent').innerHTML.includes('暂无搜索记录'));
  const failed=environment('/',storage);await failed.submit('离线搜索');
  failed.respond=async()=>response({detail:'推荐不可用'},503);await failed.focusSearch();
  assert.ok(failed.element('#search-recent').innerHTML.includes('离线搜索'));
  assert.ok(failed.element('#search-suggestion-content').innerHTML.includes('推荐暂时无法加载'));
  console.log('Page and search checks passed: navigation, recent query persistence, replay, isolation, clearing, escaping, and storage failures');
})().catch(err=>{console.error(err);process.exitCode=1;});
