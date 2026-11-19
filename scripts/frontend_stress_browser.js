/* Standalone fixture: real storefront scripts, browser DOM, synthetic APIs. */
window.frontendStressRequests=[];
window.frontendStressErrors=[];
window.addEventListener('error',event=>frontendStressErrors.push(event.message));
window.addEventListener('unhandledrejection',event=>frontendStressErrors.push(String(event.reason)));
window.BroadcastChannel=undefined;
window.frontendStressMode='normal';
const fixtureProduct=(id=1,term='')=>({id,name:`测试商品 ${id} ${term}`,subtitle:'前端压力测试数据',
  category:'数码科技',badge:'测试',description:'商品测试说明',image:'headphones',color:'#e9e7f2',
  price:39900,original_price:59900,stock:80,sales:2680,store_id:2,store_name:'测试店铺',
  store_description:'独立测试店铺',store_phone:'13800138000',store_shipping_fee:1000,free_shipping_threshold:19900});
const fixtureStore=id=>({id,name:`测试店铺 ${id}`,description:'测试店铺介绍',phone:'13800138000',
  is_active:1,shipping_fee:1000,free_shipping_threshold:19900,products:12});
const fixtureOrder=(id=1,status='pending')=>({id:`HM${id}`,user_id:1,status,items:[{
  product_id:1,name:'测试商品',image:'headphones',price:39900,quantity:1}],total:39900,shipping_fee:0,
  created_at:new Date().toISOString(),server_time:new Date().toISOString(),
  payment_expires_at:new Date(Date.now()+900000).toISOString(),is_expired:false,
  store_id:2,store_name:'测试店铺',recipient:'测试顾客',phone:'13800138000',address:'测试收货地址',
  fulfillment_status:'unfulfilled',tracking_events:[],refund:null});
const fixtureResponse=(data,status=200)=>({ok:status<400,status,json:async()=>data});
window.fetch=async url=>{
  const parsed=new URL(url,'http://fixture.test');
  frontendStressRequests.push(parsed.pathname+parsed.search);
  const term=parsed.searchParams.get('q')||'';
  const page=Number(parsed.searchParams.get('page'))||1;
  if(frontendStressMode==='race-products'&&parsed.pathname==='/api/products')
    await new Promise(resolve=>setTimeout(resolve,(200-Number(term.replace('query','')))%30));
  if(frontendStressMode==='race-orders'&&parsed.pathname==='/api/orders')
    await new Promise(resolve=>setTimeout(resolve,(100-page)%30));
  if(frontendStressMode==='failure'&&parsed.pathname==='/api/products')return fixtureResponse({detail:'测试服务繁忙'},503);
  if(parsed.pathname==='/api/auth/me')return fixtureResponse({detail:'请先登录'},401);
  if(parsed.pathname==='/api/categories')return fixtureResponse([{name:'数码科技',count:12}]);
  if(parsed.pathname==='/api/products')return fixtureResponse({items:Array.from({length:12},(_,i)=>fixtureProduct(i+1,term)),total:1000,page,page_size:12});
  const productMatch=parsed.pathname.match(/^\/api\/products\/(\d+)$/);
  if(productMatch){if(frontendStressMode==='race-details')await new Promise(resolve=>setTimeout(resolve,(200-Number(productMatch[1]))%30));return fixtureResponse(fixtureProduct(Number(productMatch[1])));}
  if(/\/comments$/.test(parsed.pathname)||/\/reviews$/.test(parsed.pathname))return fixtureResponse({items:[],total:0,page,page_size:10,average_rating:0});
  if(parsed.pathname==='/api/stores')return fixtureResponse(term?[fixtureStore(2)]:[]);
  if(/^\/api\/stores\/\d+$/.test(parsed.pathname))return fixtureResponse(fixtureStore(Number(parsed.pathname.split('/').pop())));
  if(parsed.pathname==='/api/search/suggestions')return fixtureResponse({products:[fixtureProduct(1,term)],stores:[],personalized:false});
  if(parsed.pathname==='/api/orders')return fixtureResponse({items:[frontendStressMode==='expired-orders'?{...fixtureOrder(1,'cancelled'),is_expired:true}:fixtureOrder(page)],total:1000,page,page_size:10});
  if(parsed.pathname==='/api/cart')return fixtureResponse({items:[],groups:[],count:0,subtotal:0,shipping_fee:0,total:0});
  if(parsed.pathname==='/api/history')return fixtureResponse({items:[],total:0,page,page_size:10});
  return fixtureResponse({detail:'未定义的测试接口'},404);
};

window.runFrontendStress=async()=>{
  const output=document.getElementById('stress-output');
  const result={timestamp:new Date().toISOString(),environment:navigator.userAgent,
    scope:'Actual frontend scripts and browser DOM; fixture APIs, no live HTTP/database load',
    render:[],checks:[],long_tasks:[],errors:frontendStressErrors};
  let observer;
  if(window.PerformanceObserver&&PerformanceObserver.supportedEntryTypes.includes('longtask')){
    observer=new PerformanceObserver(list=>result.long_tasks.push(...list.getEntries().map(entry=>Number(entry.duration.toFixed(2)))));
    observer.observe({type:'longtask',buffered:false});
  }
  const frame=()=>new Promise(resolve=>{
    const timeout=setTimeout(resolve,150);
    requestAnimationFrame(()=>requestAnimationFrame(()=>{clearTimeout(timeout);resolve();}));
  });
  const check=(name,passed,details)=>{result.checks.push({name,passed,details});output.textContent=`正在测试：${name}`;};
  async function render(kind,count,root,make,renderItem){
    document.getElementById('stress-status').textContent=`正在测试 ${count} 条 ${kind} 数据渲染…`;
    const samples=[];
    for(let round=0;round<3;round++){
      const rows=Array.from({length:count},(_,i)=>make(i+1));
      const started=performance.now();
      const html=rows.map(renderItem).join('');
      const built=performance.now();
      root.innerHTML=html;
      void root.offsetHeight;
      const laidOut=performance.now();
      await frame();
      samples.push({html_ms:built-started,dom_layout_ms:laidOut-built,total_ms:laidOut-started});
      root.innerHTML='';
    }
    const values=samples.map(sample=>sample.total_ms).sort((a,b)=>a-b);
    result.render.push({kind,count,rounds:3,median_ms:Number(values[1].toFixed(2)),max_ms:Number(values[2].toFixed(2)),
      html_ms:Number((samples.reduce((sum,s)=>sum+s.html_ms,0)/3).toFixed(2)),
      dom_layout_ms:Number((samples.reduce((sum,s)=>sum+s.dom_layout_ms,0)/3).toFixed(2))});
  }
  try{
    for(const count of [12,100,1000])await render('products',count,$('#product-grid'),fixtureProduct,productCard);
    $('#orders-dialog').showModal();
    for(const count of [10,100,500])await render('orders',count,$('#orders-list'),fixtureOrder,renderOrder);
    $('#orders-dialog').close();

    frontendStressMode='race-products';
    const products=[];
    const productStart=performance.now();
    for(let i=1;i<=200;i++){state.q=`query${i}`;products.push(loadProducts());}
    await Promise.all(products);
    check('200 concurrent product searches',state.products.every(product=>product.name.includes('query200')),
      {requests:200,elapsed_ms:Number((performance.now()-productStart).toFixed(2))});

    frontendStressMode='race-orders';
    const orders=[];
    for(let i=1;i<=100;i++){state.ordersPage=i;orders.push(loadOrders());}
    await Promise.all(orders);
    check('100 concurrent order page changes',$('#orders-list').textContent.includes('HM100'),
      {expected:'HM100',actual:$('#orders-list').textContent.match(/HM\d+/)?.[0]});

    frontendStressMode='race-details';
    const details=[];
    for(let i=1;i<=200;i++)details.push(window.mallPages.product(i,{updateUrl:false}));
    await Promise.all(details);
    check('200 concurrent product detail navigations',$('#product-detail h1')?.textContent==='测试商品 200 ',
      {expected:'测试商品 200',actual:$('#product-detail h1')?.textContent});

    frontendStressMode='normal';
    window.mallPages.catalog({home:true,updateUrl:false});
    const before=frontendStressRequests.filter(path=>path.startsWith('/api/search/suggestions')).length;
    for(let i=1;i<=200;i++){$('#search-input').value=`rapid${i}`;$('#search-input').dispatchEvent(new Event('input',{bubbles:true}));}
    await new Promise(resolve=>setTimeout(resolve,300));
    const calls=frontendStressRequests.filter(path=>path.startsWith('/api/search/suggestions')).length-before;
    check('200 input events are debounced',calls===1,{suggestion_requests:calls});

    frontendStressMode='failure';await loadProducts();
    const failed=!!$('#product-grid [data-action="retry-products"]');
    frontendStressMode='normal';await loadProducts();
    check('503 response and recovery',failed&&$('#product-grid .product-card')!==null,{retry_visible:failed});

    const expired={...fixtureOrder(),payment_expires_at:new Date(Date.now()-1000).toISOString()};
    $('#orders-list').innerHTML=renderOrder(expired);$('#orders-dialog').showModal();
    frontendStressMode='expired-orders';
    const beforeOrders=frontendStressRequests.filter(path=>path.startsWith('/api/orders?')).length;
    updateOrderCountdowns();updateOrderCountdowns();updateOrderCountdowns();
    const payDisabled=$('#orders-list [data-action="pay-order"]')?.disabled;
    await new Promise(resolve=>setTimeout(resolve,50));
    const expirationRequests=frontendStressRequests.filter(path=>path.startsWith('/api/orders?')).length-beforeOrders;
    check('Expired order blocks payment and refreshes once',payDisabled&&expirationRequests===1&&$('#orders-list').textContent.includes('已过期'),
      {payment_disabled:payDisabled,refresh_requests:expirationRequests});
    $('#orders-dialog').close();
    frontendStressMode='normal';
    check('No uncaught browser errors',frontendStressErrors.length===0,{errors:frontendStressErrors});
    result.passed=result.checks.every(check=>check.passed);
    result.heap_bytes=performance.memory?.usedJSHeapSize;
    await frame();observer?.disconnect();
    window.frontendStressResult=result;
    output.textContent=JSON.stringify(result,null,2);
    document.getElementById('stress-status').textContent=result.passed?'测试完成：全部功能检查通过':'测试完成：发现失败项';
  }catch(error){
    result.errors.push(String(error.stack||error));result.passed=false;
    window.frontendStressResult=result;output.textContent=JSON.stringify(result,null,2);
    document.getElementById('stress-status').textContent='测试中止：发生异常';
    observer?.disconnect();
  }
};
