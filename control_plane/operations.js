'use strict';
async function update(){
 const note=document.getElementById('freshness'),status=document.getElementById('live-status');
 try{
  const response=await fetch('/status',{cache:'no-store'});
  if(!response.ok)throw Error('unavailable');
  const data=await response.json();
  status.textContent=JSON.stringify(data,null,2);
  document.getElementById('health-summary').textContent=[
   'Mode: observation / offline preparation — real execution disabled',
   'Razor read-only observer: '+(data.broker?.health??'NOT CONFIGURED'),
   'Replay worker: '+(data.worker?.status??'NOT CONFIGURED'),
   'New entry decisions: '+(data.entry_suspended?'SUSPENDED':'governed by per-bot observation controls'),
   ...(data.strategies??[]).map(s=>s.strategy+': '+s.state+' · preview '+s.lots+' lots')
  ].join('\n');
  note.textContent='Status fetched '+new Date().toISOString()+' · real execution remains disabled';
 }catch(error){note.textContent='STATUS UNAVAILABLE — retained values do not establish healthy operation';document.getElementById('health-summary').textContent='STATUS UNAVAILABLE · real execution disabled';}
}
update();setInterval(update,2000);
