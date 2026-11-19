const assert = require('node:assert/strict');
const {environment,response} = require('./test_pages');

(async()=>{
  const env=environment();
  env.window.mallFeatures.orderExtras=()=>'';
  const now=Date.now();
  env.ctx.order={id:'HMTEST',status:'pending',items:[],created_at:new Date(now).toISOString(),
    server_time:new Date(now).toISOString(),payment_expires_at:new Date(now+900000).toISOString(),
    store_id:2,store_name:'测试店铺',recipient:'测试',phone:'13800138000',address:'测试地址',total:100};
  assert.match(env.run('renderOrder(order)'),/剩余支付时间 15:00/);
  assert.match(env.run('renderOrder(order)'),/data-action="pay-order"/);
  // Initial rendering uses server time even if the browser's wall clock differs.
  env.ctx.order.server_time=new Date(now-3600000).toISOString();
  env.ctx.order.payment_expires_at=new Date(now-3600000+900000).toISOString();
  assert.match(env.run('renderOrder(order)'),/剩余支付时间 15:00/);
  env.ctx.order.is_expired=true;env.ctx.order.status='cancelled';
  assert.match(env.run('renderOrder(order)'),/已过期/);
  assert.doesNotMatch(env.run('renderOrder(order)'),/data-action="pay-order"|data-payment-deadline/);
  env.ctx.order.is_expired=false;
  assert.match(env.run('renderOrder(order)'),/已取消/);
  assert.doesNotMatch(env.run('renderOrder(order)'),/已过期/);

  const button={disabled:false};
  const counter={dataset:{paymentDeadline:new Date(now-1000).toISOString(),clockOffset:0},
    closest:()=>({querySelector:()=>button})};
  env.ctx.document.querySelectorAll=selector=>selector==='[data-payment-deadline]'?[counter]:[];
  env.element('#orders-dialog').open=true;
  env.respond=async()=>response({items:[],total:0,page:1,page_size:10});
  env.run('updateOrderCountdowns();updateOrderCountdowns();updateOrderCountdowns()');
  assert.equal(button.disabled,true);
  assert.match(counter.textContent,/正在关闭订单/);
  assert.equal(env.requests.length,1,'Only one expiration refresh may be in flight');
  env.ctx.document.querySelectorAll=()=>[];
  await new Promise(setImmediate);
  const races=environment();
  races.window.mallFeatures.orderExtras=()=>'';
  races.respond=async url=>{
    const page=Number(new URL(url,'http://testserver').searchParams.get('page'));
    await new Promise(resolve=>setTimeout(resolve,(100-page)%20));
    return response({items:[{...env.ctx.order,id:`HM${page}`}],total:1000,page,page_size:10});
  };
  const pending=[];
  for(let page=1;page<=100;page++){
    races.state.ordersPage=page;
    pending.push(races.run('loadOrders()'));
  }
  await Promise.all(pending);
  assert.match(races.element('#orders-list').innerHTML,/HM100/,'Late responses must not replace the latest order page');
  console.log('Order countdown, server clock, expiration state and refresh checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
