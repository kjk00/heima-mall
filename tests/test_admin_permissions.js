const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');

const elements = new Map();
function element(selector) {
  if (!elements.has(selector)) elements.set(selector, {
    hidden:false,innerHTML:'',value:'',textContent:'',disabled:false,open:false,style:{},dataset:{},
    addEventListener(){},setAttribute(){},removeAttribute(){},querySelector:element,querySelectorAll(){return[];},
    classList:{add(){},remove(){},toggle(){}},matches(){return false;},showPopover(){},hidePopover(){},
    close(){this.open=false;},elements:new Proxy({}, {get:()=>element('field')}),
  });
  return elements.get(selector);
}
let responseStatus = 200;
let responseData;
const context = vm.createContext({
  document:{querySelector:element,getElementById:id=>element('#'+id),querySelectorAll:selector=>selector==='[data-store-action="new"]'?[element('new-store')]:[],addEventListener(){},body:{style:{}}},
  window:{addEventListener(){}},location:{hash:'',pathname:'/admin'},history:{replaceState(){}},
  URLSearchParams,setInterval(){},setTimeout(){},clearTimeout(){},
  fetch:async()=>({ok:responseStatus<400,status:responseStatus,json:async()=>responseData}),
});
vm.runInContext(fs.readFileSync('app/static/admin.js','utf8'),context);
async function run(code){return vm.runInContext(code,context);}
(async()=>{
  await run("state.user={id:1,name:'最高管理员',role:'admin',is_active:1,is_super_admin:1}");
  responseData={categories:['数码','家居'],images:[],stores:[{id:2,name:'数码店',is_active:1},{id:3,name:'家居店',is_active:1}]};
  await run('loadOptions()');
  assert.match(element('#products-store').innerHTML,/全部店铺/);
  assert.equal(element('#products-store').disabled,false);
  assert.equal(element('new-store').hidden,false);
  assert.match(element('#admin-profile-name').textContent,/终端管理员/);
  const customer={id:9,name:'顾客',phone:'13600136000',role:'customer',is_active:1,order_count:1,spent:100,created_at:'2026-01-01T00:00:00Z'};
  context.customer=customer;
  assert.match(await run('userRows([customer])'),/data-action="user-status"/);

  await run("state.user={id:2,name:'店长',role:'admin',is_active:1,is_super_admin:0,store_id:2}");
  responseData={categories:['数码'],images:[],stores:[{id:2,name:'数码店',is_active:1}]};
  await run('loadOptions()');
  for(const selector of ['#products-store','#orders-store','#admin-support-store']){
    assert.doesNotMatch(element(selector).innerHTML,/全部店铺|家居店/);
    assert.equal(element(selector).value,'2');
    assert.equal(element(selector).disabled,true);
  }
  assert.equal(element('new-store').hidden,true);
  assert.match(element('#admin-profile-name').textContent,/数码店/);
  assert.doesNotMatch(await run('userRows([customer])'),/data-action="user-status"/);

  responseStatus=403;responseData={detail:'需要终端管理员权限'};
  await assert.rejects(run("api('/admin/stores',{method:'POST'})"));
  assert.equal(await run('state.user.id'),2);
  responseStatus=401;responseData={detail:'请先登录'};
  await assert.rejects(run("api('/admin/auth/me')"));
  assert.equal(await run('state.user'),null);
  console.log('Admin UI permission checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
