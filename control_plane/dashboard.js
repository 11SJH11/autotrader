'use strict';
const node=id=>document.getElementById(id);
const write=(id,value)=>{node(id).textContent=typeof value==='string'?value:JSON.stringify(value,null,2);};
function table(id,items,fields){
 const root=node(id);root.replaceChildren();
 if(!items?.length){root.textContent='No records';return;}
 const t=document.createElement('table'),head=t.createTHead().insertRow();
 fields.forEach(f=>{const c=document.createElement('th');c.textContent=f;head.appendChild(c);});
 const body=t.createTBody();items.slice(0,30).forEach(item=>{const r=body.insertRow();fields.forEach(f=>{r.insertCell().textContent=String(item[f]??'—');});});root.appendChild(t);
}
async function refresh(){
 try{
  const response=await fetch('/diagnostics',{cache:'no-store'});if(!response.ok)throw Error('Diagnostic data unavailable');
  const data=await response.json(),w=data.worker??{},b=data.broker_observer??{};
  write('error','');write('health',w.heartbeat_fresh?w.status??'Unknown':'NO FRESH WORKER HEARTBEAT');
  write('worker-info',`Heartbeat: ${w.heartbeat??'unavailable'}\nReplay market time: ${w.replay_market_time??'unavailable'}\nLast signal: ${w.last_signal??'none'}`);
  write('broker-health',b.health??'Not configured');write('broker-info',{environment:b.environment??'DEMO only',scope:b.view_authorized?'VIEW_ONLY':'not verified',account:b.account??{},symbol:b.symbol??{},quote:b.quote??null});
  write('strategies',w.configurations??data.strategies??[]);
  write('freshness',{input_fresh:w.fresh_input??false,last_quote:w.last_quote??null,paper_reconciliation:w.reconciliation??'unavailable',central_engine:data.engine,broker_snapshot_fresh:b.snapshot_fresh??false,broker_quote_fresh:b.quote_fresh??false,broker_history_complete:b.history_complete??false});
  table('decisions',w.recent_decisions??[],['at','strategy','reason','intent_count']);
  table('orders',w.paper_orders??data.pending_orders??[],['strategy','side','status','entry','quantity']);
  table('positions',w.paper_positions??data.positions??[],['strategy','side','status','entry','stop','target','pnl']);
  write('attention',{worker_errors:w.errors??[],last_error:w.last_error??null,rejected:w.recent_rejections??data.recent_rejections??[],broker_error:b.last_error??null});
 }catch(error){write('error','Status unavailable; do not infer healthy operation from retained values.');write('health','STATUS UNAVAILABLE');}
}
refresh();setInterval(refresh,2000);
