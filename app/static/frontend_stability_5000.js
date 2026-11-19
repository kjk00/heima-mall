"use strict";
// 5,000 visitor workload; keep its asset URL distinct from earlier runs.
const testPaths=['/','/static/app.js','/static/style.css','/api/products?page=1&page_size=12',
  '/api/products/1','/api/products/1/comments?page=1','/api/search/suggestions?q=%E8%80%B3'];
const visitorStages=[{concurrency:1,visits:100},{concurrency:10,visits:400},
  {concurrency:50,visits:1000},{concurrency:100,visits:1500},{concurrency:200,visits:2000}];
const percentile=(values,p)=>{const sorted=[...values].sort((a,b)=>a-b);return Number((sorted[Math.min(sorted.length-1,Math.ceil(sorted.length*p)-1)]||0).toFixed(2));};
document.getElementById('run').addEventListener('click',async event=>{
  event.target.disabled=true;
  document.getElementById('save').disabled=true;
  const report={timestamp:new Date().toISOString(),target:location.origin,browser:navigator.userAgent,
    method:'One browser; real HTTP; read-only guest requests; browser connection pooling applies',
    paths:testPaths,planned_visits:5000,peak_concurrent_tasks:200,stages:[],errors:[]};
  document.getElementById('results').innerHTML='';
  async function request(path){
    const started=performance.now(),controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),15000);
    let status=0,error=null;
    try{
      const response=await fetch(path,{cache:'no-store',credentials:'omit',signal:controller.signal,
        headers:{'X-Requested-With':'HeimaMall'}});
      status=response.status;
      const body=await response.text();
      if(!response.ok)error=`HTTP ${status}`;
      else if(path==='/'){
        if(!body.includes('id="product-grid"')||!body.includes('/static/app.js'))error='Invalid storefront HTML';
      }else if(path.startsWith('/api/')){
        const data=JSON.parse(body);
        if(path.startsWith('/api/products?page')&&(!Array.isArray(data.items)||!data.items.length))error='Product catalog did not load';
        if(path==='/api/products/1'&&data.id!==1)error='Product detail did not load';
        if(path.includes('/comments')&&!Array.isArray(data.items))error='Comments did not load';
      }
    }catch(exception){error=exception.name+': '+exception.message;}
    finally{clearTimeout(timer);}
    return {path,status,ms:performance.now()-started,error};
  }
  try{
    for(const {concurrency,visits} of visitorStages){
      document.getElementById('status').textContent=`正在测试 ${concurrency} 并发 / ${visits} 名访客…`;
      const started=performance.now(),requests=[],journeys=[];
      let nextVisitor=0,completed=0;
      await Promise.all(Array.from({length:concurrency},async()=>{
        while(nextVisitor++<visits){
          const visitStart=performance.now();
          for(const path of testPaths)requests.push(await request(path));
          journeys.push(performance.now()-visitStart);
          completed++;
          if(completed%50===0)document.getElementById('status').textContent=`${concurrency} 并发：已完成 ${completed}/${visits} 名访客`;
        }
      }));
      const errors=requests.filter(row=>row.error);
      report.errors.push(...errors.map(row=>({concurrency,path:row.path,status:row.status,error:row.error})));
      const seconds=(performance.now()-started)/1000;
      const stage={concurrent_tasks:concurrency,visits,requests:requests.length,
        errors:errors.length,error_percent:Number((errors.length/requests.length*100).toFixed(2)),
        elapsed_seconds:Number(seconds.toFixed(2)),requests_per_second:Number((requests.length/seconds).toFixed(2)),
        request_p50_ms:percentile(requests.map(row=>row.ms),.5),request_p95_ms:percentile(requests.map(row=>row.ms),.95),
        request_max_ms:percentile(requests.map(row=>row.ms),1),visit_p95_ms:percentile(journeys,.95),
        statuses:requests.reduce((all,row)=>(all[row.status]=(all[row.status]||0)+1,all),{})};
      report.stages.push(stage);
      const row=document.createElement('tr');
      for(const value of [concurrency,stage.requests,stage.errors,stage.request_p95_ms+' ms',stage.visit_p95_ms+' ms']){
        const cell=document.createElement('td');cell.textContent=String(value);row.append(cell);
      }
      document.getElementById('results').append(row);
      document.getElementById('output').textContent=JSON.stringify(report,null,2);
      if(errors.length>requests.length/2)break;
    }
    report.passed=report.errors.length===0;
    report.total_requests=report.stages.reduce((sum,stage)=>sum+stage.requests,0);
    report.total_visits=report.stages.reduce((sum,stage)=>sum+stage.visits,0);
    window.frontendStabilityResult=report;
    document.getElementById('output').textContent=JSON.stringify(report,null,2);
    document.getElementById('status').textContent=report.passed?'测试完成：访问全部成功':'测试完成：发现失败请求';
    document.getElementById('save').disabled=false;
  }catch(error){
    report.errors.push({error:String(error.stack||error)});report.passed=false;
    window.frontendStabilityResult=report;document.getElementById('output').textContent=JSON.stringify(report,null,2);
    document.getElementById('status').textContent='测试中止';
  }finally{event.target.disabled=false;}
});
document.getElementById('save').addEventListener('click',async()=>{
  try{
    const handle=await window.showSaveFilePicker({suggestedName:'frontend-stability-5000.json',
      types:[{description:'JSON report',accept:{'application/json':['.json']}}]});
    const writable=await handle.createWritable();
    await writable.write(JSON.stringify(window.frontendStabilityResult,null,2)+'\n');
    await writable.close();
    document.getElementById('status').textContent='报告已保存';
  }catch(error){if(error.name!=='AbortError')document.getElementById('status').textContent='保存失败：'+error.message;}
});
