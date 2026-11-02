import {fresh,interact,due} from './engine.mjs';
const json=x=>JSON.stringify(x);
const allowed=env=>new Set(String(env.ALLOWED_USER_IDS||'').split(',').filter(x=>/^\d+$/.test(x.trim())).map(Number));
const canUse=(env,owner)=>Number.isSafeInteger(owner)&&owner>0&&(env.PUBLIC_ACCESS==='1'||allowed(env).has(owner));
const admins=env=>new Set(String(env.ADMIN_USER_IDS||'').split(',').filter(x=>/^\d+$/.test(x.trim())).map(Number));
const stmt=(db,sql,...args)=>db.prepare(sql).bind(...args);

export class Repository {
  constructor(db){this.db=db;}
  async load(owner){
    const initial=fresh();delete initial.notes;
    await stmt(this.db,'INSERT OR IGNORE INTO owners(owner,data) VALUES(?,?)',owner,json(initial)).run();
    const [meta,notes]=await this.db.batch([stmt(this.db,'SELECT revision,data FROM owners WHERE owner=?',owner),stmt(this.db,'SELECT id,data FROM calendar_notes WHERE owner=? ORDER BY id',owner)]);
    const row=meta.results[0],state=JSON.parse(row.data);state.notes=notes.results.map(r=>JSON.parse(r.data));
    return {revision:row.revision,state,original:new Map(notes.results.map(r=>[r.id,r.data]))};
  }
  async save(owner,snapshot){
    const nonce=crypto.randomUUID(),meta={...snapshot.state};delete meta.notes;
    const queries=[stmt(this.db,'UPDATE owners SET revision=revision+1,token=?,data=? WHERE owner=? AND revision=?',nonce,json(meta),owner,snapshot.revision)];
    const current=new Set();
    for(const note of snapshot.state.notes){current.add(note.id);const data=json(note);if(snapshot.original.get(note.id)!==data)queries.push(stmt(this.db,`INSERT INTO calendar_notes(owner,id,data) SELECT ?,?,? WHERE EXISTS(SELECT 1 FROM owners WHERE owner=? AND token=?) ON CONFLICT(owner,id) DO UPDATE SET data=excluded.data`,owner,note.id,data,owner,nonce));}
    for(const id of snapshot.original.keys())if(!current.has(id))queries.push(stmt(this.db,'DELETE FROM calendar_notes WHERE owner=? AND id=? AND EXISTS(SELECT 1 FROM owners WHERE owner=? AND token=?)',owner,id,owner,nonce));
    if(queries.length>40)throw Error('Batch too large');
    const result=await this.db.batch(queries);return result[0].meta.changes===1;
  }
  async change(owner,fn){for(let i=0;i<4;i++){const snapshot=await this.load(owner);const value=fn(snapshot.state);if(value===false)return false;if(await this.save(owner,snapshot))return value===undefined?true:value;}throw Error('Concurrent modification, retry');}
}

export async function telegram(env,method,payload){
  try{
    const response=await fetch(`https://api.telegram.org/bot${env.BOT_TOKEN}/${method}`,{method:'POST',headers:{'content-type':'application/json'},body:json(payload),signal:AbortSignal.timeout(12000)});
    const data=await response.json();return data;
  }catch{return {ok:false,error_code:0};}
}

export async function processUpdate(env,update,now=Date.now()){
  const cb=update.callback_query,msg=cb?.message||update.message,owner=(cb?.from||msg?.from)?.id;
  if(!Number.isSafeInteger(update.update_id)||!canUse(env,owner)||msg?.chat?.type!=='private'||msg.chat.id!==owner)return null;
  const repo=new Repository(env.DB);
  const command=(msg.text||'').trim().split(/[\s@]/)[0];
  const statsRequest=['/admin','/stats'].includes(command)||cb?.data==='stats';
  let statsText='Эта команда недоступна.';
  // Create this user's row first, so a new admin is counted in their own view.
  if(statsRequest&&admins(env).has(owner)){
    await repo.load(owner);
    const counts=await stmt(env.DB,`SELECT COUNT(*) total,
      SUM(CASE WHEN json_extract(data,'$.lastSeen')>=? THEN 1 ELSE 0 END) day,
      SUM(CASE WHEN json_extract(data,'$.lastSeen')>=? THEN 1 ELSE 0 END) week,
      SUM(CASE WHEN json_extract(data,'$.lastSeen')>=? THEN 1 ELSE 0 END) month FROM owners`,now-86400000,now-604800000,now-2592000000).all();
    const c=counts.results[0];statsText=`📊 Статистика бота\n\nВсего пользователей: ${c.total}\nАктивны за 24 часа: ${c.day||0}\nЗа 7 дней: ${c.week||0}\nЗа 30 дней: ${c.month||0}\n\nС момента запуска статистики. Тексты чужих заметок не отображаются.`;
  }
  await repo.change(owner,s=>{
    if(update.update_id<(s.minUpdate||0)||s.seen.includes(update.update_id))return false;
    s.lastSeen=now;s.firstSeen??=now;
    if(statsRequest){
      s.seen.push(update.update_id);s.seen=s.seen.slice(-128);
      if(cb)s.effects.push({id:s.nextEffect++,method:'answerCallbackQuery',payload:{callback_query_id:cb.id}});
      const payload={text:statsText,reply_markup:{inline_keyboard:[...(admins(env).has(owner)?[[{text:'↻ Обновить',callback_data:'stats'}]]:[]),[{text:'Главное меню',callback_data:'menu'}]]}};
      if(cb)payload.message_id=cb.message.message_id;
      s.effects.push({id:s.nextEffect++,method:cb?'editMessageText':'sendMessage',payload});
    }else{
      interact(s,update,now);
      if(admins(env).has(owner))for(const e of s.effects)if(e.payload?.text?.startsWith('📓 Календарь записей')){
        const rows=e.payload.reply_markup.inline_keyboard;
        if(!rows.some(r=>r.some(b=>b.callback_data==='stats')))rows.push([{text:'📊 Статистика',callback_data:'stats'}]);
      }
    }
  });
  return owner;
}

export async function deliver(env,owner,now=Date.now(),api=(m,p)=>telegram(env,m,p)){
  if(!canUse(env,owner))return;
  const repo=new Repository(env.DB);
  // Two effects per owner bounds cron SQL/API subrequests on the Free plan.
  for(let i=0;i<2;i++){
    let selected;
    const lease=crypto.randomUUID();
    await repo.change(owner,s=>{
      const effect=s.effects.find(e=>(e.retryAt||0)<=now&&(!e.leaseUntil||e.leaseUntil<now));
      if(!effect)return false;
      if(effect.reminder&&(!s.enabled||!s.notes.some(n=>n.id===effect.reminder))){s.effects=s.effects.filter(e=>e.id!==effect.id);return;}
      effect.lease=lease;effect.leaseUntil=now+60000;selected=structuredClone(effect);
    });
    if(!selected)return;
    const payload={...selected.payload};if(selected.method!=='answerCallbackQuery')payload.chat_id=owner;
    let result=await api(selected.method,payload);
    // A deleted/obsolete panel can be replaced; "not modified" is already successful.
    if(!result.ok&&result.error_code===400&&selected.method==='editMessageText'){
      if(String(result.description||'').includes('message is not modified'))result={ok:true};
      else{delete payload.message_id;result=await api('sendMessage',payload);}
    }
    await repo.change(owner,s=>{
      const effect=s.effects.find(e=>e.id===selected.id&&e.lease===lease);if(!effect)return false;
      if(result.ok||[400,401,403,404].includes(result.error_code)){
        s.effects=s.effects.filter(e=>e.id!==effect.id);
        if(result.ok&&effect.reminder){const n=s.notes.find(n=>n.id===effect.reminder);if(n)n.reminded=true;}
        if(!result.ok)s.lastDeliveryError={code:result.error_code,at:now};
      }else{
        effect.attempts=(effect.attempts||0)+1;effect.retryAt=now+1000*Math.max(Number(result.parameters?.retry_after)||0,Math.min(900,2**Math.min(effect.attempts,9)));delete effect.lease;delete effect.leaseUntil;
      }
    });
    if(!result.ok&&![400,403,404].includes(result.error_code))break;
  }
}

export async function runSchedule(env,now=Date.now(),api){
  const repo=new Repository(env.DB);
  // Work only on owners with due reminders or an outbox. At most two owners per
  // invocation fit within Workers Free's D1/API subrequest budget. Oldest pending
  // work is served first; an unusually large simultaneous burst can be delayed.
  const candidates=await stmt(env.DB,`SELECT o.owner,MIN(COALESCE(json_extract(n.data,'$.alarm'),0)) priority
    FROM owners o LEFT JOIN calendar_notes n ON n.owner=o.owner
    WHERE json_array_length(json_extract(o.data,'$.effects'))>0 OR
      (json_extract(o.data,'$.enabled')=1 AND json_extract(n.data,'$.alarm')<=? AND json_extract(n.data,'$.alarm') IS NOT NULL AND COALESCE(json_extract(n.data,'$.reminded'),0)=0)
    GROUP BY o.owner ORDER BY priority,o.owner LIMIT 2`,now).all();
  for(const {owner} of candidates.results){
    if(!canUse(env,owner))continue;
    await repo.change(owner,s=>{const count=s.effects.length;due(s,now);return s.effects.length===count?false:undefined;});
    await deliver(env,owner,now,api);
  }
}

export default {
  async fetch(request,env){
    const url=new URL(request.url);
    if(request.method==='GET'&&url.pathname==='/health')return Response.json({ok:true,service:'daynote-calendar',version:'1.0.0'});
    if(url.pathname!=='/telegram'||request.method!=='POST')return new Response('Not found',{status:404});
    if(!env.WEBHOOK_SECRET||request.headers.get('X-Telegram-Bot-Api-Secret-Token')!==env.WEBHOOK_SECRET)return new Response('Forbidden',{status:403});
    if(Number(request.headers.get('content-length')||0)>1000000)return new Response('Too large',{status:413});
    try{
      const body=await request.text();if(body.length>1000000)return new Response('Too large',{status:413});
      const update=JSON.parse(body);const owner=await processUpdate(env,update);
      if(owner!==null)await deliver(env,owner);
      return new Response('ok');
    }catch{return new Response('Retry later',{status:503});}
  },
  async scheduled(controller,env){await runSchedule(env,controller.scheduledTime);}
};
