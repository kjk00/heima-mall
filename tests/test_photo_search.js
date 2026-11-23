// Exercise the real controller, including cancellation, retry, and escaped results.
const vm = require('node:vm'), fs = require('node:fs'), assert = require('node:assert/strict');
function environment() {
  const elements = new Map(), requests = [], revoked = [], submitted = [], opened = [];
  let nextURL = 0, deferred;
  function el(selector) {
    if (!elements.has(selector)) elements.set(selector, {
      hidden:false, disabled:false, open:false, value:'', innerHTML:'', textContent:'', files:[], dataset:{}, listeners:new Map(),
      addEventListener(type, fn){this.listeners.set(type, fn);}, removeAttribute(name){delete this[name];},
      click(){return this.listeners.get('click')?.({target:this});},
    });
    return elements.get(selector);
  }
  const dialog = el('#photo-search-dialog'), state = {category:'家居',storeId:2,onSale:true};
  el('#search-form').requestSubmit = () => submitted.push(el('#search-input').value);
  const context = vm.createContext({console, AbortController, state, $:el,
    document:{querySelectorAll:()=>[],createElement:()=>({getContext:()=>({fillRect(){},drawImage(){}}),toBlob(fn){fn({size:1000});}})},
    window:{mallSearch:{hide(){}},mallPages:{catalog(){}}},
    URL:{createObjectURL(){return 'blob:fixture-'+(++nextURL);},revokeObjectURL(url){revoked.push(url);}},
    Image:class {constructor(){this.naturalWidth=640;this.naturalHeight=480;}set src(value){queueMicrotask(()=>this.onload());}},
    FileReader:class {readAsDataURL(){this.result='data:image/jpeg;base64,prepared-image';queueMicrotask(()=>this.onload());}},
    showDialog(){dialog.open=true;},closeDialog(){dialog.open=false;dialog.listeners.get('close')?.();},
    escapeHTML:value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll(String.fromCharCode(34),'&quot;'),
    image:p=>'/static/images/'+p.image+'.svg',money:value=>(value/100).toFixed(2),friendlyError:error=>error.message,toast(){},
    async showProduct(id){opened.push(id);}, enabled:true,providerError:false,
    async api(path, options){
      requests.push({path,options});
      if(path.endsWith('/status'))return {enabled:context.enabled};
      if(deferred)return deferred;
      if(context.providerError)throw new Error('识别服务暂时不可用');
      return {keywords:['耳机'],total:1,items:[{id:4,name:'耳机<script>',store_name:'商城',image:'headphones',price:9900}]};
    },
  });
  vm.runInContext(fs.readFileSync('app/static/photo-search.js','utf8'),context);
  const dispatch = async(selector,type,event={})=>el(selector).listeners.get(type)?.(event);
  return {el,context,requests,revoked,submitted,opened,state,dispatch,
    open:()=>dispatch('#photo-search-open','click'),close:()=>context.closeDialog(),
    async choose(type='image/jpeg',size=1000){const input=el('#photo-search-upload-file');input.files=[{type,size}];await dispatch('#photo-search-upload-file','change',{target:input});},
    defer(){let resolve;deferred=new Promise(r=>resolve=r);return resolve;},
  };
}
(async()=>{
  {
    const e=environment();e.context.enabled=false;await e.open();
    assert.equal(e.el('#photo-search-upload').disabled,true);
    assert.match(e.el('#photo-search-status').textContent,/尚未开通/);assert.equal(e.requests.length,1);
  }
  {
    const e=environment();await e.open();await e.choose();
    assert.equal(e.el('#photo-search-submit').disabled,false);assert.equal(e.requests.length,1);
    await e.dispatch('#photo-search-submit','click');
    assert.equal(e.el('#photo-search-results').hidden,false);
    assert.match(e.el('#photo-search-products').innerHTML,/耳机&lt;script&gt;/);
    assert.doesNotMatch(e.el('#photo-search-products').innerHTML,/<script>/);
    const request=e.requests.find(r=>r.path==='/search/photo');assert.equal(JSON.parse(request.options.body).data_base64,'prepared-image');
    const url=e.el('#photo-search-preview').src;e.close();assert(e.revoked.includes(url));assert.equal(e.el('#photo-search-submit').disabled,true);
  }
  {
    const e=environment();await e.open();await e.choose('image/svg+xml');
    assert.equal(e.el('#photo-search-submit').disabled,true);assert.match(e.el('#photo-search-status').textContent,/JPG/);assert.equal(e.requests.length,1);
    await e.choose('image/jpeg',16*1024*1024);assert.match(e.el('#photo-search-status').textContent,/15 MB/);
  }
  {
    const e=environment();await e.open();await e.choose();const resolve=e.defer();const running=e.dispatch('#photo-search-submit','click');
    await e.dispatch('#photo-search-submit','click');assert.equal(e.requests.filter(r=>r.path==='/search/photo').length,1);
    const request=e.requests.find(r=>r.path==='/search/photo');e.close();assert.equal(request.options.signal.aborted,true);
    resolve({keywords:['旧结果'],items:[],total:0});await running;
    assert.equal(e.el('#photo-search-results').hidden,true);assert.equal(e.el('#photo-search-status').textContent,'');
  }
  {
    const e=environment();await e.open();await e.choose();e.context.providerError=true;await e.dispatch('#photo-search-submit','click');
    assert.match(e.el('#photo-search-status').textContent,/暂时不可用/);assert.equal(e.el('#photo-search-submit').disabled,false);
  }
  {
    const e=environment();await e.open();await e.choose();await e.dispatch('#photo-search-submit','click');
    await e.dispatch('#photo-search-results','click',{target:{closest:()=>({dataset:{photoKeyword:'耳机'}})}});
    assert.deepEqual(e.submitted,['耳机']);assert.equal(e.state.category,'');assert.equal(e.state.storeId,null);assert.equal(e.state.onSale,false);
  }
  {
    const e=environment();await e.open();await e.dispatch('#photo-search-results','click',{target:{closest:()=>({dataset:{photoProduct:'4'}})}});
    assert.deepEqual(e.opened,[4]);
  }
  console.log('Photo search controller: 7 scenarios passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
