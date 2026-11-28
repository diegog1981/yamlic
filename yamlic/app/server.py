"""Yamlic: add-on unico. Ruoli (tutti opzionali): cliente (pagina + rete), nodo, smistatore.
Funziona anche fuori da Home Assistant (VPS, PC): senza SUPERVISOR_TOKEN parte solo come nodo/smistatore."""
import asyncio, json, os, re, threading, time, urllib.request
import yaml
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import core, micro, rete, verifica
import mappa
import memoria, racconto

OPT = core.load_options()


def flag(k, d=False):
    v = OPT.get(k, d)
    return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "si", "yes")


def lst(k, d=()):
    v = OPT.get(k, d)
    return [x.strip() for x in v.split(",") if x.strip()] if isinstance(v, str) else list(v or [])


TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
HA = os.environ.get("YAMLIC_HA_URL", "http://supervisor/core/api")
LLM_URL = (OPT.get("llm_url") or "").rstrip("/")
LOCAL_MODEL = os.environ.get("YAMLIC_LOCAL_MODEL") == "1"          # lo avvia run.sh
LLM = (LLM_URL or "http://127.0.0.1:8080") + "/v1/chat/completions"
HAS_LOCAL = bool(LLM_URL) or LOCAL_MODEL
RELAYS = lst("smistatori", ["wss://llm.narriq.app/ws"])
USA_RETE = flag("usa_rete", True)
ICE = lst("stun", ["stun:stun.l.google.com:19302"]) if flag("diretto") else []
IDENT = rete.Identity(os.environ.get("YAMLIC_IDENTITY", "/data/identity.json"))

SYSTEM = core.SYSTEM

PAGE = r"""<!doctype html><html lang=it><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Yamlic</title>
<style>
:root{--bg:#f4f5f7;--card:#fff;--tx:#1d1d1f;--mu:#6b6f76;--ac:#2563eb;--ok:#16a34a;--wa:#b45309;--er:#dc2626;--bd:#e3e5e8;--code:#f0f2f5}
@media(prefers-color-scheme:dark){:root:not([data-tema]){--bg:#161719;--card:#222326;--tx:#ececee;--mu:#9a9ea6;--ac:#6ea0ff;--ok:#4ade80;--wa:#fbbf24;--er:#f87171;--bd:#34363a;--code:#1a1b1e}}
:root[data-tema=scuro]{--bg:#161719;--card:#222326;--tx:#ececee;--mu:#9a9ea6;--ac:#6ea0ff;--ok:#4ade80;--wa:#fbbf24;--er:#f87171;--bd:#34363a;--code:#1a1b1e}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:16px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:760px;margin:0 auto;padding:1.2em 1em 3em}
header{display:flex;justify-content:space-between;align-items:center;gap:1em;flex-wrap:wrap}
h1{margin:0;font-size:1.5em}h1 small{font-weight:400;color:var(--mu);font-size:.55em;margin-left:.4em}
.pill{font-size:.85em;padding:.25em .7em;border-radius:99px;background:var(--card);border:1px solid var(--bd);color:var(--mu)}
.pill b{color:var(--tx)}.dot{display:inline-block;width:.6em;height:.6em;border-radius:50%;margin-right:.4em;background:var(--mu)}
.card{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:1em 1.1em;margin:1em 0}
textarea{width:100%;font:inherit;color:var(--tx);background:var(--code);border:1px solid var(--bd);border-radius:10px;padding:.7em;resize:vertical}
textarea:focus{outline:2px solid var(--ac)}
button{font:inherit;border:0;border-radius:10px;padding:.6em 1.1em;cursor:pointer;background:var(--ac);color:#fff;font-weight:600}
.chips{display:flex;flex-wrap:wrap;gap:.4em;margin:.3em 0}.chips button{padding:.5em .8em}
button.s{background:var(--code);color:var(--tx);border:1px solid var(--bd);font-weight:500}button:disabled{opacity:.5;cursor:default}
.row{display:flex;gap:.6em;align-items:center;flex-wrap:wrap;margin-top:.7em}
pre{margin:.6em 0 0;padding:.9em;background:var(--code);border-radius:10px;overflow:auto;font:13.5px/1.5 ui-monospace,Menlo,Consolas,monospace;white-space:pre}
.chips{display:flex;gap:.5em;flex-wrap:wrap}.chip{font-size:.82em;padding:.2em .65em;border-radius:99px;background:var(--code);color:var(--mu)}.chip b{color:var(--tx)}
.warn{margin-top:.8em;padding:.6em .8em;border-radius:10px;border:1px solid var(--wa);color:var(--wa);font-size:.92em}
.err{border-color:var(--er);color:var(--er)}.q label{display:flex;gap:.6em;align-items:flex-start;padding:.35em 0;cursor:pointer}
.q .id{color:var(--mu);font-size:.8em}.q .g{margin:.8em 0 .2em;color:var(--mu);font-size:.8em;text-transform:uppercase;letter-spacing:.05em}
.sp{display:inline-block;width:1em;height:1em;border:2px solid var(--mu);border-top-color:var(--ac);border-radius:50%;animation:r .8s linear infinite;vertical-align:-.15em;margin-right:.5em}@keyframes r{to{transform:rotate(360deg)}}
.hide{display:none}.mu{color:var(--mu);font-size:.9em}
</style>
<main>
<header><h1>Yamlic<small id=ver></small></h1><span class=row style="margin:0"><button id=tm class=s type=button title="Tema">Tema: auto</button><span class=pill id=rete><span class=dot></span>rete...</span></span></header>
<div class=card>
<textarea id=p rows=3 placeholder="Scrivi cosa vuoi, per esempio: quando esco di casa spegni tutte le luci"></textarea>
<div class=row><button id=b>Genera automazione</button><span id=s class=mu></span></div>
</div>
<div class=row><button id=cb class=s type=button>La tua casa</button></div>
<div id=hm class="card q hide"></div>
<div id=q class="card q hide"></div>
<div id=r class="card hide">
<div class=chips id=st></div>
<div id=it class="warn hide" style="white-space:pre-wrap"></div><pre id=y></pre>
<div class=row><button id=cp>Copia YAML</button><button id=cs class=s>Copia per segnalare</button><button id=iv class=s>Invia a Diego</button><button id=md class=s>Modifica</button><button id=sv class="s hide">Salva in Home Assistant</button><span id=msg class=mu></span></div>
<div id=mb class="row hide"><input id=mi style="flex:1;min-width:12em;font:inherit;padding:.55em;border-radius:10px;border:1px solid var(--bd);background:var(--code);color:var(--tx)" placeholder="Cosa vuoi cambiare? (es. solo nei giorni feriali, luminosita al 40%)"><button id=mg>Applica</button></div>
<div id=w></div>
<div id=pv class="warn hide"><b>Cosa arriva a Diego</b> (nomi e testi sono gia sostituiti da codici, la tua casa non si riconosce):<pre id=pt></pre><textarea id=nt rows=2 placeholder="Cosa non va? (facoltativo)"></textarea><div class=row><button id=ok>Invia</button><button id=no class=s>Annulla</button><span id=pm class=mu></span></div></div>
</div>
<p class=mu>Controlla sempre l'automazione prima di usarla. Per metterla in Home Assistant: Impostazioni, Automazioni, Crea, Modifica in YAML, incolla.</p>
</main>
<script>
const $=i=>document.getElementById(i);let busy=false;
(function(){const T=['auto','chiaro','scuro'];let t='auto';try{t=localStorage.getItem('yamlic_tema')||'auto'}catch(e){}
 function ap(){if(t=='auto')document.documentElement.removeAttribute('data-tema');else document.documentElement.setAttribute('data-tema',t);$('tm').textContent='Tema: '+t}
 ap();$('tm').onclick=()=>{t=T[(T.indexOf(t)+1)%3];try{localStorage.setItem('yamlic_tema',t)}catch(e){}ap()}})();
function esc(t){return String(t).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function copia(t){const done=()=>{$('msg').textContent='Copiato';setTimeout(()=>$('msg').textContent='',2000)};
 if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t).then(done)}
 else{const a=document.createElement('textarea');a.value=t;a.style.position='fixed';a.style.opacity=0;document.body.appendChild(a);a.select();try{document.execCommand('copy');done()}catch(e){$('msg').textContent='Seleziona e copia a mano'}a.remove()}}
async function reteInfo(){try{const j=await (await fetch('api/rete')).json();$('ver').textContent='v'+j.versione;
 const el=$('rete');let d='var(--mu)',t='';
 if(!j.usa_rete&&!j.locale){t='rete spenta'}
 else if(j.locale&&!j.usa_rete){d='var(--ok)';t='modello locale'}
 else if(j.nodi>0){d='var(--ok)';t='<b>'+j.nodi+'</b> '+(j.nodi==1?'nodo attivo':'nodi attivi')+(j.tps?' &middot; fino a <b>'+j.tps+'</b> token/s':'')}
 else if(j.smistatore_ok){d='var(--wa)';t='nessun nodo attivo'}
 else{d='var(--er)';t='smistatore non raggiungibile'}
 el.innerHTML='<span class=dot style="background:'+d+'"></span>'+t;window.__salva=j.allow_create}catch(e){}}
function stats(j){const s=j.stats||{};const c=[];if(s.secondi!=null)c.push('<span class=chip><b>'+s.secondi.toString().replace('.',',')+' s</b></span>');
 if(s.via){const v=s.via.startsWith('rete:')?'rete &middot; nodo '+esc(s.via.slice(5)):s.via;c.push('<span class=chip>'+v+'</span>')}
 if(s.token_risposta&&s.secondi>0)c.push('<span class=chip><b>'+Math.round(s.token_risposta/s.secondi)+'</b> token/s</span>');
 $('st').innerHTML=c.join('')}
function risultato(j){$('q').classList.add('hide');const r=$('r');r.classList.remove('hide');
 $('it').textContent=j.riassunto||'';$('it').classList.toggle('hide',!j.riassunto);
 if(j.error){$('y').textContent='';$('st').innerHTML='';$('cp').classList.add('hide');$('cs').classList.add('hide');$('iv').classList.add('hide');$('sv').classList.add('hide');
  $('w').innerHTML='<div class="warn err">'+esc(j.error)+(j.problems?' ('+esc(j.problems.join(', '))+')':'')+'</div>';return}
 $('cp').classList.remove('hide');$('cs').classList.remove('hide');$('iv').classList.toggle('hide',!j.segn);$('pv').classList.add('hide');$('y').textContent=j.yaml||'';stats(j);
 $('w').innerHTML=(j.problems&&j.problems.length)?'<div class=warn><b>Da controllare</b><br>'+j.problems.map(esc).join('<br>')+'</div>':'';
 $('sv').classList.toggle('hide',!window.__salva);$('md').classList.toggle('hide',!!j.parti);$('mb').classList.add('hide');
 $('cp').onclick=()=>copia(j.yaml||'');
 $('md').onclick=()=>{$('mb').classList.toggle('hide');$('mi').focus()};
 $('mg').onclick=async()=>{const t=$('mi').value.trim();if(!t||busy)return;busy=true;$('mg').disabled=true;$('msg').innerHTML='<span class=sp></span>Modifico...';
  try{const x=await (await fetch('api/modifica',{method:'POST',body:JSON.stringify({yaml:j.yaml,istruzione:t})})).json();
   if(x.error){$('msg').textContent=x.error}else{j.yaml=x.yaml;j.problems=x.problems;j.stats=x.stats;j.segn=null;$('mi').value='';risultato(j)}}catch(e){$('msg').textContent='Errore: '+e}
  busy=false;$('mg').disabled=false;if($('msg').querySelector&&$('msg').querySelector('.sp'))$('msg').textContent=''};

 $('cs').onclick=()=>{const st=j.stats||{};copia(['Yamlic '+$('ver').textContent+' - '+(st.via||'')+(st.secondi!=null?' - '+st.secondi+' s':''),'','Richiesta: '+$('p').value.trim(),'','YAML:',j.yaml||'','','Avvisi: '+((j.problems&&j.problems.length)?j.problems.join(' | '):'nessuno'),'','Cosa non va: '].join('\n'))};
 $('iv').onclick=()=>{const g=j.segn;if(!g)return;$('pt').textContent=['Versione: '+$('ver').textContent,'Richiesta: '+g.prompt,'','YAML:',g.yaml,'','Avvisi: '+((g.problems&&g.problems.length)?g.problems.join(' | '):'nessuno')].join('\n');$('pm').textContent='';$('pv').classList.remove('hide')};
 $('no').onclick=()=>$('pv').classList.add('hide');
 $('ok').onclick=async()=>{$('ok').disabled=true;try{const x=await (await fetch('api/segnala',{method:'POST',body:JSON.stringify({segn:j.segn,via:(j.stats||{}).via,nota:$('nt').value})})).json();$('pm').textContent=x.ok?'Inviata, grazie':(x.error||'errore');if(x.ok)setTimeout(()=>$('pv').classList.add('hide'),1500)}catch(e){$('pm').textContent='errore'}$('ok').disabled=false};
 $('sv').onclick=async()=>{const x=await (await fetch('api/create',{method:'POST',body:JSON.stringify({yaml:j.yaml})})).json();$('msg').textContent=x.ok?(x.tipo=='scena'?'Scena salvata in Home Assistant':'Salvata in Home Assistant'):(x.error||'errore')}}
function domandaMicro(d,fatte){$('r').classList.add('hide');const q=$('q');q.classList.remove('hide');
 let h='<b>'+esc(d.testo)+'</b>';
 d.opzioni.forEach((o,i)=>{h+='<label><input type='+(d.multi?'checkbox':'radio')+' class=ck name=o value='+i+'><span>'+esc(o.nome)+(o.dettaglio?'<br><span class=id>'+esc(o.dettaglio)+'</span>':'')+'</span></label>'});
 if(d.riscrivi)h+='<div class=row><input id=rs style="flex:1;min-width:12em;font:inherit;padding:.55em;border-radius:10px;border:1px solid var(--bd);background:var(--code);color:var(--tx)" placeholder="'+(d.opzioni.length?'oppure riscrivi il pezzo':'riscrivi il pezzo')+'"></div>';
 if(d.ricorda)h+='<label><input type=checkbox id=rc checked><span>Ricorda questa scelta</span></label>';
 h+='<div class=row><button id=ok type=button>Continua</button><button class=s type=button id=sk>Nessuna: prova senza domande</button></div>';q.innerHTML=h;
 $('ok').onclick=()=>{const t=d.riscrivi?$('rs').value.trim():'';const s=[...q.querySelectorAll('.ck:checked')].map(c=>d.opzioni[+c.value].valore);let v=null;
  const w=s.find(x=>x.testo||x.yaml_ai);if(t)v={testo:t};else if(w)v=w;else if(s.length){const a=s.find(x=>x.azioni);v=a?a:{entita:s.flatMap(x=>x.entita)};if(!a&&d.ricorda&&$('rc').checked)v.ricorda=d.ricorda}
  if(v)gen({risposte:Object.assign({},fatte,{[d.chiave]:v})})};
 $('sk').onclick=()=>gen({salta_micro:true});q.scrollIntoView({behavior:'smooth',block:'nearest'})}
function domanda(d){$('r').classList.add('hide');const q=$('q');q.classList.remove('hide');
 let h='<b>'+esc(d.testo)+'</b>';
 if(d.tipo=='uno'){h+=d.opzioni.map((o,i)=>'<label><input type=radio name=o value="'+esc(o.id)+'"'+(i==0?' checked':'')+'><span>'+esc(o.nome)+'<br><span class=id>'+esc(o.id)+'</span></span></label>').join('')}
 else{let g='';h+='<div class=row><button class=s type=button id=ta>Seleziona tutto</button><button class=s type=button id=tn>Nessuno</button></div>';
  d.opzioni.forEach(o=>{if(o.gruppo&&o.gruppo!=g){g=o.gruppo;h+='<div class=g>'+(g=='light'?'Luci':g=='switch'?'Interruttori':g=='cover'?'Tapparelle e tende':esc(g))+'</div>'}
   h+='<label><input type=checkbox class=ck value="'+esc(o.id)+'" checked><span>'+esc(o.nome)+'<br><span class=id>'+esc(o.id)+'</span></span></label>'})}
 h+='<div class=row><button id=ok type=button>Continua</button><button class=s type=button id=sk>Salta</button></div>';q.innerHTML=h;
 if(d.tipo!='uno'){$('ta').onclick=()=>document.querySelectorAll('.ck').forEach(c=>c.checked=true);$('tn').onclick=()=>document.querySelectorAll('.ck').forEach(c=>c.checked=false)}
 $('ok').onclick=()=>{if(d.tipo=='uno'){gen({persona:document.querySelector('input[name=o]:checked').value})}
  else{const s=[...document.querySelectorAll('.ck')].filter(c=>c.checked).map(c=>c.value);if(!s.length){return gen({salta:true})}gen({scelte:s})}};
 $('sk').onclick=()=>gen({salta:true,salta_micro:true});q.scrollIntoView({behavior:'smooth',block:'nearest'})}
async function gen(extra){if(busy||!$('p').value.trim())return;busy=true;$('b').disabled=true;$('s').innerHTML='<span class=sp></span>Sto scrivendo...';
 try{const r=await fetch('api/generate',{method:'POST',body:JSON.stringify(Object.assign({prompt:$('p').value},extra||{}))});const j=await r.json();
  if(j.domanda)(j.domanda.tipo=='micro'?domandaMicro(j.domanda,j.risposte||{}):domanda(j.domanda));else risultato(j)}catch(e){risultato({error:'Errore di comunicazione: '+e})}
 busy=false;$('b').disabled=false;$('s').textContent=''}
$('b').onclick=()=>gen();
const FUNZ=['','luce','presa','interruttore','elettrodomestico','tenda','TV','audio','cancello/portone','ventilatore'];
const CAMPO='style="width:100%;font:inherit;padding:.4em;margin:.2em 0;border-radius:8px;border:1px solid var(--bd);background:var(--code);color:var(--tx)"';
const FUNZ_B=[['luce','Luce'],['presa','Presa'],['tenda','Tenda'],['elettrodomestico','Elettrodomestico'],['TV','TV'],['audio','Audio'],['interruttore','Altro']];
let J=null,MODO='riepilogo',SALTATE=new Set(),STORIA=[],APERTA='';
const kq=q=>q.tipo+':'+(q.ids||[q.id]).join(',');
const bt=(t,a,c)=>`<button type=button class="${c||'s'} cb2" data-a="${esc(a)}">${esc(t)}</button>`;
async function casaApi(b){try{return await (await fetch(b?'api/casa':'api/mappa',b?{method:'POST',body:JSON.stringify(b)}:undefined)).json()}catch(e){return {error:'Errore: '+e}}}
async function salva(patch,prima){if(prima)STORIA.push(prima);const r=await casaApi({patch});if(!r.error)J=r;disegna(r.error)}
function domandeAperte(){return (J.domande||[]).filter(q=>!SALTATE.has(kq(q)))}
function pulsantiStanze(id){return J.stanze.map(s=>bt(s,'st|'+id+'|'+s)).join('')+bt('Nessuna (esterno o tutta la casa)','st|'+id+'|nessuna')+`<span style="display:flex;gap:.4em;width:100%"><input id=ns ${CAMPO} placeholder="+ nuova stanza"><button type=button class="s cb2" data-a="nuova|${esc(id)}">Aggiungi</button></span>`}
function scheda(v){const m=v.mia||{};return `<div style="padding:.4em 0"><div class=mu>Cos'è</div><div class=chips>${FUNZ_B.map(f=>bt(f[1],'fz|'+v.id+'|'+f[0],v.funz_eff==f[0]?'':'s')).join('')}${bt('Non usare','no|'+v.id)}</div>
<div class=mu>Stanza</div><div class=chips>${pulsantiStanze(v.id).replace('>'+esc(v.stanza_eff)+'<',' style="background:var(--ac);color:#fff">'+esc(v.stanza_eff)+'<')}</div>
<div class=mu>Come lo chiami tu (facoltativo)</div><span style="display:flex;gap:.4em"><input id=sn ${CAMPO} value="${esc(m.nome||'')}" placeholder="es. luce del bagno"><button type=button class="s cb2" data-a="sn|${esc(v.id)}">Salva</button></span></div>`}
function disegna(err){const h=$('hm');if(err){h.innerHTML=esc(err);return}
 const dom=domandeAperte(),usati=J.voci.filter(v=>!(v.mia||{}).ignora),nonusati=J.voci.filter(v=>(v.mia||{}).ignora);
 let x='<b>La tua casa</b> <span class=mu>(resta sul tuo Home Assistant)</span>';
 if(MODO=='domande'&&dom.length){const q=dom[0];x+=`<p class=mu>Domande rimaste: ${dom.length}</p><p><b>${esc(q.testo)}</b></p><div class=chips>`;
  if(q.tipo=='funzione')x+=FUNZ_B.map(f=>bt(f[1],'fz|'+q.id+'|'+f[0])).join('')+bt('Non usare','no|'+q.id);
  else if(q.tipo=='stanza')x+=pulsantiStanze(q.id);
  else if(q.tipo=='doppione')x+=q.ids.map((id,i)=>bt('Uso '+q.nomi[i],'dp|'+q.ids.join(',')+'|'+id)).join('')+bt('Sono diverse, le uso tutte','dp|'+q.ids.join(',')+'|');
  x+=`</div><div class=row>${bt('Salta','salta|'+kq(q))}${STORIA.length?bt('Indietro','indietro'):''}${bt('Fine','riep')}</div>`}
 else if(MODO=='stanze'){const gr={};usati.forEach(v=>(gr[v.stanza_eff||'Senza stanza']=gr[v.stanza_eff||'Senza stanza']||[]).push(v));
  x+='<div class=row>'+bt('Riepilogo','riep')+'</div>';
  Object.keys(gr).sort().forEach(k=>{x+=`<div class=g>${esc(k)} (${gr[k].length})</div>`+gr[k].map(v=>`<div style="border-top:1px solid var(--bd);padding:.3em 0"><span class=cb2 data-a="apri|${esc(v.id)}" style="cursor:pointer"><b>${esc(v.nome)}</b> <span class=mu>· ${esc(v.funz_eff=='interruttore'?'da chiarire':(v.funz_eff||''))}${(v.mia||{}).nome?' · "'+esc(v.mia.nome)+'"':''} ✎</span></span>${APERTA==v.id?scheda(v):''}</div>`).join('')});
  if(nonusati.length)x+='<div class=g>Non usati</div>'+nonusati.map(v=>`<div class=row><span>${esc(v.nome)}</span>${bt('Ripristina','rip|'+v.id)}</div>`).join('');
  if(J.memoria.length)x+='<div class=g>Scelte che ricordo</div>'+J.memoria.map(m=>`<div class=row><span>${esc(m.testo)} → ${esc(m.scelta)}</span>${bt('Dimentica','dim|'+m.chiave)}</div>`).join('')}
 else{MODO='riepilogo';x+=`<p>${usati.length} dispositivi pronti${dom.length?', <b>'+dom.length+' domande</b>':(SALTATE.size?'':', nessuna domanda')}.</p><div class=row>${dom.length?bt('Rispondi alle domande','dom',''):''}${SALTATE.size?bt('Rivedi le '+SALTATE.size+' saltate','rivedi'):''}${bt('Vedi per stanza','stz')}${bt('Chiudi','chiudi')}</div>`}
 h.innerHTML=x;
 h.querySelectorAll('.cb2').forEach(b=>b.onclick=()=>azione(b.dataset.a))}
function mia(id){const v=J.voci.find(x=>x.id==id);return Object.assign({},(v&&v.mia)||{})}
async function azione(a){const p=a.split('|'),k=p[0];
 if(k=='riep'){MODO='riepilogo';return disegna()} if(k=='dom'){MODO='domande';return disegna()} if(k=='stz'){MODO='stanze';return disegna()}
 if(k=='chiudi')return $('hm').classList.add('hide');
 if(k=='rivedi'){SALTATE=new Set();MODO='domande';return disegna()}
 if(k=='salta'){SALTATE.add(a.slice(6));return disegna()}
 if(k=='apri'){APERTA=APERTA==p[1]?'':p[1];return disegna()}
 if(k=='indietro'){const u=STORIA.pop();const r=await casaApi({sostituisci:u});if(!r.error)J=r;return disegna(r.error)}
 if(k=='dim'){const r=await casaApi({dimentica:p[1]});if(!r.error)J=r;return disegna(r.error)}
 if(k=='fz')return salva({[p[1]]:{funzione:p[2],ignora:''}},{[p[1]]:mia(p[1])});
 if(k=='no')return salva({[p[1]]:{ignora:true}},{[p[1]]:mia(p[1])});
 if(k=='rip')return salva({[p[1]]:{ignora:''}},{[p[1]]:mia(p[1])});
 if(k=='st')return salva({[p[1]]:{stanza:p[2]}},{[p[1]]:mia(p[1])});
 if(k=='sn')return salva({[p[1]]:{nome:$('sn').value.trim()}},{[p[1]]:mia(p[1])});
 if(k=='nuova'){const n=$('ns').value.trim();if(!n)return;const r=await casaApi({stanza_nuova:n});if(!r.error)J=r;return salva({[p[1]]:{stanza:n}},{[p[1]]:mia(p[1])})}
 if(k=='dp'){const ids=p[1].split(','),sc=p[2],patch={},prima={};ids.forEach(id=>{prima[id]=mia(id);patch[id]=sc?(id==sc?{ignora:'',diverso:''}:{ignora:true}):{diverso:true}});return salva(patch,prima)}}
$('cb').onclick=async()=>{const h=$('hm');if(!h.classList.contains('hide')){h.classList.add('hide');return}h.classList.remove('hide');h.textContent='Leggo la casa...';const r=await casaApi();if(!r.error)J=r;MODO='riepilogo';SALTATE=new Set();STORIA=[];disegna(r.error)};
$('p').addEventListener('keydown',e=>{if(e.key=='Enter'&&(e.ctrlKey||e.metaKey))gen()});
reteInfo();setInterval(reteInfo,30000);
</script></html>"""


def ha(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(HA + path, data=body, method="POST" if body is not None else "GET",
                                 headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


RETE_LOCK = threading.Lock()
DEBUG = threading.local()
SENZA_GIRI = threading.local()   # le micro-frasi non fanno giri di correzione
MAX_GIRI = 2
_NOTIFY = {"t": 0, "d": []}


def servizi_notify():
    """Servizi notify.* reali di HA (cache 10 minuti)."""
    if time.time() - _NOTIFY["t"] < 600:
        return _NOTIFY["d"]
    try:
        d = next((x for x in (ha("/services") or []) if x.get("domain") == "notify"), {})
        _NOTIFY["d"] = ["notify." + k for k in sorted((d.get("services") or {}).keys())]
    except Exception:
        pass
    _NOTIFY["t"] = time.time()
    return _NOTIFY["d"]


_AREE = {"t": 0, "d": {}}
AREE_TPL = "{%- set o = namespace(l=[]) -%}{%- for s in states -%}{%- set a = area_name(s.entity_id) -%}{%- if a -%}{%- set o.l = o.l + [[s.entity_id, a]] -%}{%- endif -%}{%- endfor -%}{{ o.l | tojson }}"

def aree():
    """entita -> nome dell'area in Home Assistant (cache 60 s; se non riesce, vuoto)."""
    if time.time() - _AREE["t"] < 60:
        return _AREE["d"]
    try:
        req = urllib.request.Request(HA + "/template", data=json.dumps({"template": AREE_TPL}).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            _AREE["d"] = {e: a for e, a in json.loads(r.read().decode())}
    except Exception:
        pass
    _AREE["t"] = time.time()
    return _AREE["d"]


_NOMI_AREE = {"t": 0, "d": []}


def nomi_aree():
    """Tutte le aree di Home Assistant (anche vuote), cache 60 s."""
    if time.time() - _NOMI_AREE["t"] < 60:
        return _NOMI_AREE["d"]
    try:
        req = urllib.request.Request(HA + "/template", data=json.dumps({"template": "{{ areas() | map('area_name') | list | to_json }}"}).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            _NOMI_AREE["d"] = [str(x) for x in json.loads(r.read().decode()) if x]
    except Exception:
        pass
    _NOMI_AREE["t"] = time.time()
    return _NOMI_AREE["d"]


def llm_call(payload, timeout=900):
    h = {"Content-Type": "application/json"}
    if OPT.get("llm_key"):
        h["Authorization"] = "Bearer " + OPT["llm_key"]
    req = urllib.request.Request(LLM, data=json.dumps(payload).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def text_of(res):
    return res["choices"][0]["message"]["content"]


VERSIONE = "0.4.36"
_RETE = {"t": 0, "v": None}


def rete_info():
    """Stato della rete per la pagina: nodi attivi letti da /health degli smistatori (cache 10 s)."""
    if _RETE["v"] and time.time() - _RETE["t"] < 10:
        return _RETE["v"]
    nodi, tps, ok = 0, 0, 0
    if USA_RETE:
        for u in RELAYS:
            h = u.replace("wss://", "https://").replace("ws://", "http://").rsplit("/ws", 1)[0] + "/health"
            try:
                with urllib.request.urlopen(h, timeout=4) as r:
                    d = json.loads(r.read())
                ok += 1
                nodi += int(d.get("nodi", 0))
                tps = max([tps] + [x.get("tps") or 0 for x in d.get("lista", [])])
            except Exception:
                pass
    v = {"versione": VERSIONE, "usa_rete": USA_RETE, "locale": HAS_LOCAL, "nodi": nodi, "tps": tps or None,
         "smistatore_ok": ok > 0, "allow_create": flag("allow_create")}
    _RETE.update(t=time.time(), v=v)
    return v


# ---------- cliente ----------
def _stati(stati, prompt="", grezzi=False):
    prova = isinstance(stati, list) and bool(stati)
    states = stati if prova else ha("/states")   # stati: solo per le prove con case finte
    ar = {} if stati else aree()
    for st_ in states:
        if st_["entity_id"] in ar:
            st_.setdefault("attributes", {})["_area"] = ar[st_["entity_id"]]
    if grezzi or not flag("mappa", True):
        return states
    return memoria.applica(states, {"entita": {}, "memoria": {}} if prova else memoria.carica(), prompt)


def _micro(prompt, stati, risposte=None, persona="", domande=False):
    """Strada a micro-frasi: tutto in locale; il modello riceve solo le azioni non capite. None = usa il metodo classico."""
    states = _stati(stati, prompt)
    t0 = time.time()
    vie = []

    def chiedi(frase):
        try:
            r = _genera_uno(frase, None, "", True, states, micro_chiamata=True, extra=lambda y: core.blocco(y, frase, states))
            cfg = yaml.safe_load(r.get("yaml") or "")
            acts = cfg.get("actions") if isinstance(cfg, dict) else None
            if not acts or not all(isinstance(a, dict) and "action" in a for a in acts):
                return None
            if verifica.controlla(frase, yaml.safe_dump({"triggers": [{"trigger": "state"}], "actions": acts}), states):
                return None
            vie.append(r["stats"]["via"])
            return acts
        except Exception:
            return None
        finally:
            pass

    def pezzo(tipo, testo):
        """Un pezzo (evento o condizione) che le regole non capiscono: lo scrive l'AI, noi controlliamo e le rimandiamo gli errori."""
        frase = ("Quando %s, avvisami" if tipo == "evento" else "Alle 12:00, se %s, avvisami") % testo
        try:
            r = _genera_uno(frase, None, io, True, states, micro_chiamata=True, extra=lambda y: core.pezzo_errori(tipo, y, testo, states))
            y = r.get("yaml")
            if not y or core.pezzo_errori(tipo, y, testo, states):
                return None
            vie.append(r["stats"]["via"])
            return core.pezzo_estrai(tipo, y)
        except Exception as ex:
            print("pezzo AI: %s" % str(ex)[:120], flush=True)
            return None
    io = persona or OPT.get("io_person") or ""
    try:
        r = micro.costruisci(prompt, states, [] if stati else servizi_notify(), io, chiedi, risposte, domande, None if stati else memoria.carica()["memoria"],
                             pezzo if flag("ai_pezzi", True) else None)
    except Exception as ex:
        print("micro: errore %s" % str(ex)[:120], flush=True)
        return None
    if not r:
        if re.search(r"\bpiov", prompt.lower()):
            print("micro: nessun risultato per %r | meteo=%s" % (prompt[:60], [(x["entity_id"], x.get("state")) for x in states if x["entity_id"].startswith("weather.")]), flush=True)
        return None
    if "domanda" in r:
        d = r["domanda"]
        if str(d.get("chiave", "")).startswith("riscrivi:") and not d.get("opzioni") and str(d.get("testo", "")).startswith("Non ho capito") and flag("proposta_ai", True):
            _proponi(d, prompt, states, persona)
        return {"domanda": d, "risposte": risposte or {}}
    errori = [] if r.get("fisso") else verifica.controlla(r["frase"], r["yaml"], states)
    if errori and not risposte:   # il controllo finale non passa: meglio il metodo classico
        return None
    r["avvisi"] += ["controllo finale: " + e for e in errori]   # con le tue scelte: te lo dico, non butto via
    known = {s_["entity_id"] for s_ in states}
    if r.get("scena"):
        y, imp = r["yaml"], []
        problems = list(r["avvisi"])
    else:
        y, imp = core.impulsi(r["yaml"])
        problems = core.validate(y, known, prompt, remote=False) + r["avvisi"] + imp + core.domini_mancanti(states, prompt)
    try:
        an = core.Anon(core.select_entities(states, prompt, io)[0], prompt, [(s_.get("attributes") or {}).get("friendly_name", "") for s_ in states])
        segn = {"prompt": an.prompt, "yaml": an.forward_yaml(y), "problems": [an.forward_yaml(x) for x in problems]}
    except Exception:
        segn = None
    via = "regole locali" + (" + " + ", ".join(sorted(set(vie))) if vie else "")
    return {"segn": segn, "yaml": y, "problems": problems, "controllo": {"ok": not errori, "errori": errori}, "tipo": "scena" if r.get("scena") else "automazione",
            "stats": {"secondi": round(time.time() - t0, 1), "via": via, "token_risposta": None, "correzioni": 0}}


def _proponi(d, prompt, states, persona):
    """Le regole non bastano: l'AI scrive l'automazione, noi la controlliamo; se regge diventa una proposta da confermare."""
    try:
        r = _genera_uno(prompt, None, persona, True, states, micro_chiamata=True)
        y = r.get("yaml")
        if not y or not (r.get("controllo") or {}).get("ok") or core.blocco(y, prompt, states) or not core.coerente(y, prompt) or core.validate(y, {s["entity_id"] for s in states}, prompt):
            return
        testo = " / ".join(x.strip() for x in racconto.racconta(y, states).splitlines() if x.strip())
        d["opzioni"] = [{"valore": {"yaml_ai": y}, "nome": "Si, intendevo questo", "dettaglio": testo}]
        d["testo"] = "Non sono sicuro. Con l'AI ho capito questo: e giusto? Se no, riscrivi la frase."
        d["multi"] = False
    except Exception as ex:
        print("proposta AI: %s" % str(ex)[:120], flush=True)


def _genera_uno(prompt, scelte=None, persona="", salta=False, stati=None, micro_chiamata=False, extra=None):
    states = stati if micro_chiamata else _stati(stati, prompt)
    io = persona or OPT.get("io_person") or ""
    if not scelte and not salta and not persona:
        dom = core.ambiguity(states, prompt, io)
        if dom:
            return {"domanda": dom}
    intera = prompt
    coda = None if scelte else core.coda_condizionale(prompt, states)
    if coda:
        prompt, coda_az, coda_av = coda
    ents, known = core.select_entities(states, prompt, io, only=set(scelte) if scelte else None)
    t0 = time.time()
    via, res, y, anon, err = "locale", None, "", None, ""
    modo = OPT.get("modo", "auto")
    use_net = USA_RETE and RELAYS and modo != "locale"
    if HAS_LOCAL and modo != "rete":
        try:
            lista = "\n".join("- %s (%s)" % (e, n) for e, n in ents)
            sm = SYSTEM + ((" L'utente che scrive e %s: quando dice io, esco, rientro, arrivo, usa quella persona." % io) if io else "")
            req = {"messages": [{"role": "system", "content": sm}, {"role": "user", "content": "Entita disponibili:\n%s\n\nRichiesta: %s" % (lista, prompt)}],
                   "temperature": 0.2, "max_tokens": 450}
            if flag("grammatica", False):
                req["grammar"] = core.grammatica([e for e, _ in ents])
            res = llm_call(req)
            y = core.extract_yaml(text_of(res))
        except Exception as ex:
            err, res = str(ex)[:80], None
    if res is None and use_net:
        anon = core.Anon(ents, prompt, [(s.get("attributes") or {}).get("friendly_name", "") for s in states])
        out = anon.outgoing()
        if anon.leaks(out):
            return {"error": "controllo privacy fallito, non invio nulla alla rete", "problems": anon.leaks(out)}
        sm = SYSTEM + ((" L'utente che scrive e %s." % anon.alias[io]) if io in anon.alias else "")
        payload = {"messages": [{"role": "system", "content": sm}, {"role": "user", "content": out}], "temperature": 0.2, "max_tokens": 450}
        if flag("grammatica", False):
            payload["grammar"] = core.grammatica(re.findall(r"^- ([a-z_]+\.\S+) \(", out, re.M))

        def accept(r):
            try:
                return not core.validate(core.clean(anon.restore(core.extract_yaml(text_of(r))), known)[0], known, prompt, remote=True)
            except Exception:
                return False
        try:
            def dubbio(r):
                """Risposta valida ma poco fiduciosa: le regole hanno dovuto intervenire o il trigger e solo manuale."""
                try:
                    yy = core.clean(anon.restore(core.extract_yaml(text_of(r))), known)[0]
                    return core.trigger_da_testo(yy, states, prompt) != yy or bool(core.regole_028(yy, prompt)[1]) or bool(core.ripara(yy, states, prompt)[1]) or bool(core.ibrido(yy, states, prompt)[1])
                except Exception:
                    return True
            def chiedi():
                with RETE_LOCK:   # lo smistatore vecchio perde le richieste parallele della stessa identita
                    return asyncio.run(rete.ask(IDENT, RELAYS, payload, accept=accept, direct=flag("diretto"), ice=ICE,
                                            adaptive=flag("voto_adattivo", True), escalate=dubbio, firma=lambda r: core.firma(core.extract_yaml(text_of(r)))))
            try:
                res, node = chiedi()
            except Exception as ex1:   # linea instabile: un secondo tentativo dopo 2 secondi
                if not re.search(r"connect|timeout|reset|closed|ssl", str(ex1), re.I):
                    raise
                time.sleep(2)
                res, node = chiedi()
            via = "rete:" + node[:6]
            y = anon.restore(core.extract_yaml(text_of(res)))
        except Exception as ex:
            return {"error": "rete non disponibile: %s%s" % (ex, (" / locale: " + err) if err else "")}
    if res is None:
        return {"error": "nessun modello disponibile (imposta llm_url, avvia un modello locale o attiva la rete)" + ((" - " + err) if err else "")}
    def correggi(y0):
        """Regole dopo il modello. Rete di sicurezza: se un passaggio peggiora il confronto con la richiesta, viene scartato."""
        y, tolte = core.clean(y0, known)
        avvisi = []
        if coda:   # la coda costruita con regole fa parte del risultato anche per il controllo
            pieno = lambda z: core.aggiungi_azione(z, coda_az)
        else:
            pieno = lambda z: z

        def voto(z):
            return len(verifica.controlla(intera, pieno(z), states))

        def passo(f):
            nonlocal y
            r = f(y)
            nuovo, av = r if isinstance(r, tuple) else (r, [])
            if nuovo != y and voto(nuovo) > voto(y):
                return
            y = nuovo
            avvisi.extend(av)
        passo(lambda z: core.force_targets(z, scelte or [], prompt, known))
        passo(lambda z: core.fill_empty(z, states, prompt))
        if not scelte:
            for f in (core.room_guard, core.nome_esatto, core.tranne, core.trigger_da_testo):
                passo(lambda z, f=f: f(z, states, prompt))
        passo(lambda z: core.via_pausa(z, prompt))
        passo(core.impulsi)
        passo(lambda z: core.regole_028(z, prompt))
        if flag("ripara", True):
            passo(lambda z: core.ripara(z, states, prompt, [] if stati else servizi_notify()))
        if flag("ibrido", True):
            passo(lambda z: core.ibrido(z, states, prompt))
        passo(lambda z: verifica.ripulisci(intera, z, states))
        if coda:
            y = pieno(y)
            avvisi.append(coda_av)
        return y, tolte, avvisi

    grezzo = y
    y, tolte, avvisi = correggi(y)
    errori = verifica.controlla(intera, y, states) + (extra(y) if extra else [])
    giri = 0
    # domanda e risposta: rimando al modello gli errori precisi (massimo 2 correzioni, tengo la migliore)
    testo_prec = text_of(res)
    while errori and giri < MAX_GIRI and not scelte and flag("correzioni", True) and not getattr(SENZA_GIRI, "on", False):
        giri += 1
        nota = "L'automazione ha questi errori:\n- %s\nRiscrivi tutta l'automazione corretta per la stessa richiesta. Solo YAML." % "\n- ".join(errori[:5])
        try:
            msgs = payload["messages"] if via != "locale" else req["messages"]
            nuovi = msgs[:2] + [{"role": "assistant", "content": testo_prec}, {"role": "user", "content": anon.forward_yaml(nota) if anon else nota}]
            if anon and anon.leaks(nuovi[-1]["content"]):
                break
            if via != "locale":
                with RETE_LOCK:
                    r2, node2 = asyncio.run(rete.ask(IDENT, RELAYS, dict(payload, messages=nuovi, temperature=0.1 + 0.2 * giri), accept=accept, direct=flag("diretto"), ice=ICE))
            else:
                r2 = llm_call(dict(req, messages=nuovi))
            testo_prec = text_of(r2)
            y2 = core.extract_yaml(testo_prec)
            if anon:
                y2 = anon.restore(y2)
            y2, tolte2, avvisi2 = correggi(y2)
            err2 = verifica.controlla(intera, y2, states) + (extra(y2) if extra else [])
        except Exception:
            break
        if len(err2) < len(errori):
            y, tolte, avvisi, errori = y2, tolte2, avvisi2, err2
    problems = core.validate(y, known, prompt, remote=via != "locale")
    if tolte:
        problems.append("entita non trovate in casa, tolte dall'elenco: " + ", ".join(tolte))
    problems += avvisi
    problems += core.domini_mancanti(states, prompt)
    for sv in sorted(set(re.findall(r"\bnotify\.([a-z0-9_]+)", y))):
        if not sv.startswith("mobile_app_") and sv not in ("persistent_notification", "send_message"):
            problems.append("servizio notify.%s da verificare: in HA di solito e notify.mobile_app_<telefono>" % sv)
    for w in core.stanze_mancanti(states, prompt):
        problems.append("in casa non ho trovato nessuna entita o area per \u00ab%s\u00bb: controlla a mano il bersaglio" % w)
    ignote = core.unknown_entities(y, known)
    if ignote:
        problems.append("entita che non esistono in casa, da correggere a mano: " + ", ".join(ignote))
    if anon and anon.unresolved(y):
        problems.append("alias rimasti: " + ", ".join(anon.unresolved(y)))
    usage = res.get("usage", {})
    try:
        an = anon or core.Anon(ents, prompt, [(s.get("attributes") or {}).get("friendly_name", "") for s in states])
        segn = {"prompt": an.prompt, "yaml": an.forward_yaml(y), "problems": [an.forward_yaml(p) for p in problems]}
    except Exception:
        segn = None
    problems += ["controllo finale: %s" % e for e in errori]
    out = {"segn": segn, "yaml": y, "problems": problems, "stats": {"secondi": round(time.time() - t0, 1), "via": via, "token_risposta": usage.get("completion_tokens"), "correzioni": giri},
           "controllo": {"ok": not errori, "errori": errori}}
    if getattr(DEBUG, "on", False):
        out["grezzo"] = grezzo
    return out


def generate(prompt, scelte=None, persona="", salta=False, stati=None, risposte=None, salta_micro=False, doppia=False):
    proposta = next((v for v in (risposte or {}).values() if isinstance(v, dict) and v.get("yaml_ai")), None) if isinstance(risposte, dict) else None
    if proposta:
        r = {"yaml": str(proposta["yaml_ai"])[:6000], "problems": ["automazione proposta dall'AI e scelta da te: rileggila"], "segn": None,
             "stats": {"secondi": 0, "via": "proposta AI", "token_risposta": None, "correzioni": 0}}
    else:
        r = _generate(prompt, scelte, persona, salta, stati, risposte, salta_micro)
    if r.get("yaml") and (doppia or flag("doppia_lettura", False)) and ((r.get("stats") or {}).get("via") or "").startswith("regole locali"):
        try:   # seconda lettura: l'AI legge la frase da sola (per ora solo misura, il risultato non cambia)
            ai = _genera_uno(prompt, None, persona, True, stati)
            esito, diff = core.confronta(r["yaml"], ai.get("yaml") or "")
            r["doppia"] = {"esito": esito, "diff": diff, "ai_yaml": ai.get("yaml"), "ai_errore": ai.get("error"), "secondi": (ai.get("stats") or {}).get("secondi")}
        except Exception as ex:
            r["doppia"] = {"esito": "errore", "diff": [str(ex)[:120]]}
    if r.get("yaml"):
        usate = [e for v in (risposte or {}).values() if isinstance(v, dict) for e in (v.get("entita") or [])] + list(scelte or [])
        tutti = {x["entity_id"]: x for x in _stati(stati, grezzi=True)}
        tutti.update({x["entity_id"]: x for x in _stati(stati, prompt)})   # con le tue scelte (stanza, cos'e, soprannome)
        motivi = core.blocco(r["yaml"], prompt + " " + " ".join(str((v or {}).get("testo") or "") for v in (risposte or {}).values() if isinstance(v, dict)), list(tutti.values()), usate)
        if ((r.get("stats") or {}).get("via") or "") != "regole locali" and not usate and not core.coerente(r["yaml"], prompt):
            motivi.append("l'automazione non corrisponde a quello che hai chiesto")
        if motivi and not (usate and all("non c'entra" in m for m in motivi)):
            return {"error": "Non sono riuscito a creare un'automazione affidabile, quindi non te la do. Prova a riscrivere la richiesta", "problems": motivi}
    if r.get("yaml"):
        try:
            r["riassunto"] = racconto.racconta(r["yaml"], _stati(stati, grezzi=True))
        except Exception:
            pass
        if not stati:
            try:
                memoria.ricorda(risposte)   # solo quando si arriva in fondo
            except Exception as ex:
                print("memoria: %s" % str(ex)[:100], flush=True)
    return r


def _chiave_stanza(r):
    r = " ".join(str(r or "").lower().split())
    return mappa._UNICA.get(r, r)


def vista_casa():
    """Dati per "La tua casa": dispositivi con le scelte dell'utente, elenco unico delle stanze, domande da fare."""
    voci = mappa.costruisci(_stati(None, grezzi=True))
    d = memoria.carica()
    ent = d["entita"]
    nomi = {v["id"]: v["nome"] for v in voci}
    st_ = {}
    ufficiali = d["stanze"] + [c.get("stanza") for c in ent.values()] + nomi_aree()
    dai_nomi = [r for v in voci for r in v["stanze"] if not any(re.search(r"\b%s\b" % re.escape(r), str(u).lower()) for u in ufficiali if u)]
    for r in ufficiali + dai_nomi:   # le stanze di HA e le tue vincono; quelle ricavate dai nomi solo se mancano
        if r and str(r).strip() and str(r).lower() != "nessuna":
            r = str(r).strip()
            st_.setdefault(_chiave_stanza(r), r[:1].upper() + r[1:])
    out = mappa.riassunto(voci)
    out["stanze"] = sorted(st_.values())
    out["voci"] = []
    for v in voci:
        c = ent.get(v["id"]) or {}
        if (v["stato"] not in ("ok", "dubbia") and v["id"] not in ent) or v["id"].split(".")[0] in ("person", "sensor", "binary_sensor", "assist_satellite"):
            continue
        stz = c.get("stanza") or (v["stanze"][0] if v["stanze"] else "")
        out["voci"].append(dict(v, mia=c, funz_eff=c.get("funzione") or v["funzione"],
                                stanza_eff="" if stz == "nessuna" else st_.get(_chiave_stanza(stz), stz)))
    out["domande"] = domande_casa(voci, ent, st_)
    out["memoria"] = [{"chiave": k, "testo": k.split(":", 1)[-1], "scelta": ", ".join(nomi.get(e, e) for e in v)} for k, v in d["memoria"].items()]
    return out


def domande_casa(voci, ent, etichette=None):
    out, gruppi = [], {}
    for v in voci:
        c = ent.get(v["id"]) or {}
        if v["stato"] in ("tecnica", "non disponibile") or c.get("ignora"):
            continue
        dom = v["id"].split(".")[0]
        f = c.get("funzione") or v["funzione"]
        if f == "interruttore" and not c.get("funzione"):
            out.append({"tipo": "funzione", "id": v["id"], "testo": "\"%s\": cos'è?" % v["nome"]})
        elif not c.get("stanza") and dom not in ("person", "input_boolean", "vacuum", "assist_satellite") and f != "cancello/portone":
            if not v["stanze"]:
                out.append({"tipo": "stanza", "id": v["id"], "testo": "\"%s\": in che stanza è?" % v["nome"]})
            elif len(v["stanze"]) > 1 or any("diverse" in x for x in v["dubbi"]):
                out.append({"tipo": "stanza", "id": v["id"], "testo": "\"%s\": in che stanza è? (il nome dice una cosa, l'area un'altra: %s)" % (v["nome"], ", ".join(v["stanze"]))})
        if c.get("diverso"):
            continue
        tipo = "TV" if f == "TV" else "soundbar" if "soundbar" in (v["id"] + v["nome"]).lower() else ""
        if tipo:
            stz = c.get("stanza") or (v["stanze"][0] if v["stanze"] else "")
            gruppi.setdefault((tipo, _chiave_stanza(stz)), []).append(v)
    for (tipo, stz), g in gruppi.items():
        if len(g) > 1:
            out.append({"tipo": "doppione", "ids": [x["id"] for x in g], "nomi": [x["nome"] for x in g],
                        "testo": "In %s ci sono %d %s: quale usi?" % ((etichette or {}).get(stz, stz) or "casa", len(g), "TV" if tipo == "TV" else "soundbar")})
    return out


def _generate(prompt, scelte, persona, salta, stati, risposte, salta_micro):
    """Prima le micro-frasi (regole locali). Se non bastano: metodo classico; richieste con piu 'quando' indipendenti divise in parti."""
    if flag("micro", True) and not scelte and not salta_micro:
        r = _micro(prompt, stati, risposte if isinstance(risposte, dict) else None, persona, flag("domande", True))
        if r:
            d = r.get("domanda") or {}
            if d and (d.get("riscrivi") or str(d.get("chiave", "")).startswith("riscrivi:")) and not any(isinstance(o_, dict) and isinstance(o_.get("valore"), dict) and "yaml_ai" in o_["valore"] for o_ in (d.get("opzioni") or [])) and not risposte and flag("ai_intera", True):
                # le regole e l'AI sul pezzo non bastano: prima di chiedere all'utente, l'AI sull'intera frase con il controllo finale
                try:
                    r2 = _genera_uno(prompt, scelte, persona, salta, stati)
                except Exception as ex:
                    print("AI intera: %s" % str(ex)[:120], flush=True)
                    r2 = None
                if r2 and r2.get("yaml") and not r2.get("error") and not r2.get("domanda") and (r2.get("controllo") or {}).get("ok", True) \
                        and not any(("inesisten" in str(p_) or "non esist" in str(p_)) for p_ in (r2.get("problems") or [])):
                    try:   # stesso controllo di sicurezza che fa generate() dopo: se non regge, resta la domanda mirata
                        tutti = {x["entity_id"]: x for x in _stati(stati, grezzi=True)}
                        tutti.update({x["entity_id"]: x for x in _stati(stati, prompt)})
                        if core.blocco(r2["yaml"], prompt, list(tutti.values()), []) or not core.coerente(r2["yaml"], prompt):
                            return r
                    except Exception:
                        return r
                    r2.setdefault("stats", {})["via"] = "AI sull'intera frase (le regole non bastavano)"
                    return r2
            return r
    parti = core.scomponi(prompt) if (flag("scomponi", True) and not scelte and not persona) else [prompt]
    if len(parti) < 2:
        return _genera_uno(prompt, scelte, persona, salta, stati)
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    if not salta:   # chiarimenti una volta sola, sull'intera richiesta
        states = stati if isinstance(stati, list) and stati else ha("/states")
        ar = {} if stati else aree()
        for st_ in states:
            if st_["entity_id"] in ar:
                st_.setdefault("attributes", {})["_area"] = ar[st_["entity_id"]]
        dom = core.ambiguity(states, prompt, OPT.get("io_person") or "")
        if dom:
            return {"domanda": dom}
    with ThreadPoolExecutor(min(3, len(parti))) as ex:
        ris = list(ex.map(lambda p: _genera_uno(p, None, "", True, stati), parti))
    dom = [r for r in ris if r.get("domanda")]
    if dom and len(dom) == len(ris):
        return _genera_uno(prompt, scelte, persona, salta, stati)
    ris = core.unisci_stesso_trigger(ris)
    y, pr = core.unisci(ris)
    pr += core.controlla_jinja(y)
    try:
        list(yaml.safe_load_all(y))
    except Exception:
        pr.append("il YAML unito non e valido, controllalo")
    try:   # controllo finale sull'insieme
        pr += ["controllo finale (insieme): %s" % e for e in verifica.controlla(prompt, y, _stati(stati))]
    except Exception:
        pass
    ok = [r for r in ris if r.get("stats")]
    via = ", ".join(sorted({r["stats"]["via"] for r in ok})) or "-"
    return {"segn": (ok[0].get("segn") if ok else None), "yaml": y, "problems": ["richiesta divisa in %d automazioni, controllale una per una" % len(parti)] + pr,
            "stats": {"secondi": round(time.time() - t0, 1), "via": via, "token_risposta": None}, "parti": [{"prompt": p, "ok": not r.get("error")} for p, r in zip(parti, ris)]}


def modifica(d):
    """Modifica un'automazione esistente: prima regole fisse (orario, giorni, luminosita, condizioni), poi il modello."""
    y0, istr = str(d.get("yaml") or "").strip(), str(d.get("istruzione") or "").strip()
    if not y0 or not istr:
        return {"error": "servono il YAML e cosa vuoi cambiare"}
    try:
        if not isinstance(yaml.safe_load(y0), dict):
            return {"error": "il YAML non e un'automazione"}
    except Exception:
        return {"error": "il YAML non e valido"}
    stati = d.get("stati") if isinstance(d.get("stati"), list) and d.get("stati") else None
    states = stati or ha("/states")
    ar = {} if stati else aree()
    for st_ in states:
        if st_["entity_id"] in ar:
            st_.setdefault("attributes", {})["_area"] = ar[st_["entity_id"]]
    nomi = {s_["entity_id"]: (s_.get("attributes") or {}).get("friendly_name", "") for s_ in states}
    io = OPT.get("io_person") or ""
    ents, known = core.select_entities(states, istr, io)
    usate = [e for e in dict.fromkeys(re.findall(r"\b[a-z_]+\.[a-z0-9_]+\b", y0)) if e in nomi]
    elenco = list(dict.fromkeys(usate + [e for e, _ in ents]))[:15]
    ents = [(e, nomi.get(e, "")) for e in elenco]
    t0 = time.time()
    via, y, extra = "regole", "", []
    r = core.modifica_regole(y0, istr)
    if r:
        y, extra = r[0], ["modifica fatta con regole fisse: " + r[1]]
    else:
        res = None
        if HAS_LOCAL and OPT.get("modo", "auto") != "rete":
            try:
                lista = "\n".join("- %s (%s)" % (e, n) for e, n in ents)
                u = "Entita disponibili:\n%s\n\nAutomazione attuale:\n```yaml\n%s\n```\n\nModifica richiesta: %s" % (lista, y0, istr)
                res = llm_call({"messages": [{"role": "system", "content": core.SYSTEM_MOD}, {"role": "user", "content": u}], "temperature": 0.2, "max_tokens": 600})
                y, via = core.extract_yaml(text_of(res)), "locale"
            except Exception:
                res = None
        if res is None and USA_RETE and RELAYS and OPT.get("modo", "auto") != "locale":
            anon = core.Anon(ents, istr, list(nomi.values()))
            u = "Entita disponibili:\n%s\n\nAutomazione attuale:\n```yaml\n%s\n```\n\nModifica richiesta: %s" % (anon.entity_lines(), anon.forward_yaml(y0), anon.prompt)
            if anon.leaks(u):
                return {"error": "controllo privacy fallito, non invio nulla alla rete", "problems": anon.leaks(u)}
            payload = {"messages": [{"role": "system", "content": core.SYSTEM_MOD}, {"role": "user", "content": u}], "temperature": 0.2, "max_tokens": 600}

            def accept(rr):
                try:
                    return not core.validate(core.clean(anon.restore(core.extract_yaml(text_of(rr))), known)[0], known, istr, remote=True)
                except Exception:
                    return False
            try:
                res, node = asyncio.run(rete.ask(IDENT, RELAYS, payload, accept=accept, direct=flag("diretto"), ice=ICE))
                y, via = anon.restore(core.extract_yaml(text_of(res))), "rete:" + node[:6]
            except Exception as ex:
                return {"error": "rete non disponibile: %s" % ex}
        if res is None:
            return {"error": "nessun modello disponibile per questa modifica"}
    y, tolte = core.clean(y, known)
    y = core.via_pausa(y, istr) if False else y
    problems = core.validate(y, known, istr, remote=via != "locale" and via != "regole") + extra
    if tolte:
        problems.append("entita non trovate in casa, tolte dall'elenco: " + ", ".join(tolte))
    try:
        if yaml.safe_load(y) == yaml.safe_load(y0):
            problems.append("l'automazione e rimasta uguale: prova a dire la modifica in altro modo")
    except Exception:
        pass
    return {"yaml": y, "problems": problems, "stats": {"secondi": round(time.time() - t0, 1), "via": via}}


def segnala(d):
    """Invia al relay (https) una segnalazione GIA anonima, solo su richiesta esplicita dell'utente."""
    s = d.get("segn") or {}
    body = {"v": VERSIONE, "via": str(d.get("via", ""))[:40], "prompt": str(s.get("prompt", ""))[:600], "yaml": str(s.get("yaml", ""))[:6000],
            "problems": [str(p)[:300] for p in (s.get("problems") or [])][:12], "nota": str(d.get("nota", ""))[:600]}
    ultimo = "nessuno smistatore"
    for u in RELAYS:
        url = u.replace("wss://", "https://").replace("ws://", "http://").rsplit("/ws", 1)[0] + "/segnala"
        try:
            rq = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(rq, timeout=10) as r:
                return 200, json.loads(r.read().decode())
        except Exception as ex:
            ultimo = str(ex)[:80]
    return 502, {"error": "invio non riuscito: " + ultimo}


def create(y):
    if not flag("allow_create"):
        return 403, {"error": "Creazione disattivata (allow_create = false)."}
    import yaml
    cfg = yaml.safe_load(y)
    known = {s["entity_id"] for s in ha("/states")}
    cid = str(int(time.time() * 1000))
    if isinstance(cfg, dict) and isinstance(cfg.get("entities"), dict) and "triggers" not in cfg:   # scena
        ignote = [e for e in cfg["entities"] if e not in known]
        if ignote or not cfg.get("name"):
            return 400, {"error": "scena non valida", "problems": ["entita inesistenti: " + ", ".join(ignote)] if ignote else ["manca il nome"]}
        ha("/config/scene/config/" + cid, {"id": cid, "name": str(cfg["name"]), "entities": cfg["entities"]})
        return 200, {"ok": True, "id": cid, "tipo": "scena"}
    probs = core.validate(y, known, "", remote=True)
    if probs:
        return 400, {"error": "non valida", "problems": probs}
    ha("/config/automation/config/" + cid, cfg)
    return 200, {"ok": True, "id": cid}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split("?")[0].endswith("/api/casa_md"):   # file della casa: salvato oppure bozza generata in locale
            try:
                d = memoria.carica()
                nuovo = "nuovo=1" in self.path or not d["md"]
                return self._send(200, {"testo": memoria.bozza(_stati(None, grezzi=True)) if nuovo else d["md"], "salvato": not nuovo})
            except Exception as ex:
                return self._send(200, {"error": str(ex)[:200]})
        if self.path.split("?")[0].endswith("/api/mappa"):   # sola lettura
            try:
                return self._send(200, vista_casa())
            except Exception as ex:
                return self._send(200, {"error": str(ex)[:200]})
        if self.path.split("?")[0].endswith("/api/rete"):
            self._send(200, rete_info())
        else:
            self._send(200, PAGE.encode(), "text/html")

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            if self.path.endswith("/api/generate"):
                DEBUG.on = bool(data.get("debug"))
                self._send(200, generate(data.get("prompt", ""), data.get("scelte"), data.get("persona", ""), bool(data.get("salta")), data.get("stati"), data.get("risposte"), bool(data.get("salta_micro"))))
            elif self.path.endswith("/api/casa"):
                if "entita" in data:
                    memoria.correggi(data.get("entita"))
                if "dimentica" in data:
                    memoria.dimentica(data.get("dimentica") or None)
                if "md" in data:
                    memoria.salva_md(data.get("md"))
                if "stanza_nuova" in data:
                    memoria.aggiungi_stanza(data.get("stanza_nuova"))
                if "patch" in data:
                    memoria.aggiorna(data.get("patch"))
                if "sostituisci" in data:
                    memoria.correggi(data.get("sostituisci"))
                self._send(200, vista_casa())
            elif self.path.endswith("/api/prova_doppia"):   # misura: piu frasi su una casa finta, nessuna modifica a Home Assistant
                out = []
                for f in (data.get("frasi") or [])[:10]:
                    r = generate(str(f), salta=True, stati=data.get("stati"), doppia=True)
                    out.append({"frase": f, "yaml": r.get("yaml"), "domanda": bool(r.get("domanda")), "errore": r.get("error"), "doppia": r.get("doppia")})
                self._send(200, {"risultati": out})
            elif self.path.endswith("/api/modifica"):
                self._send(200, modifica(data))
            elif self.path.endswith("/api/segnala"):
                code, res = segnala(data)
                self._send(code, res)
            elif self.path.endswith("/api/create"):
                code, res = create(data.get("yaml", ""))
                self._send(code, res)
            else:
                self._send(404, {"error": "non trovato"})
        except Exception as ex:
            self._send(500, {"error": str(ex)})

    def log_message(self, *a):
        pass


# ---------- nodo e smistatore ----------
def busy():
    if not flag("solo_se_libero", True):
        return False
    try:
        return os.getloadavg()[0] / (os.cpu_count() or 1) > float(OPT.get("carico_max", 0.6))
    except OSError:
        return False


def node_handler(payload):
    return llm_call(payload, timeout=300)


def benchmark():
    try:
        t = time.time()
        r = llm_call({"messages": [{"role": "user", "content": "Scrivi un elenco numerato da 1 a 30."}], "max_tokens": 40, "temperature": 0}, timeout=300)
        n = (r.get("usage") or {}).get("completion_tokens") or 0
        return round(n / max(time.time() - t, 0.1), 1)
    except Exception:
        return 0


async def rete_main():
    tasks = []
    port = int(OPT.get("porta_smistatore", 8100))
    if flag("smistatore"):
        await rete.run_relay("0.0.0.0", port, lst("altri_smistatori"), int(OPT.get("limite_cliente_min", 12)))
        print("Smistatore attivo sulla porta", port, flush=True)
    if flag("nodo") and HAS_LOCAL:
        tps = await asyncio.get_running_loop().run_in_executor(None, benchmark)
        print("Nodo attivo, velocita %s token/s, id %s" % (tps, IDENT.id), flush=True)
        urls = RELAYS + (["ws://127.0.0.1:%d/ws" % port] if flag("smistatore") else [])
        tasks.append(rete.run_node(IDENT, urls, node_handler, busy, {"tps": tps, "model": "qwen2.5-coder-1.5b", "slots": 1},
                                   direct=flag("diretto"), ice=ICE))
    await (asyncio.gather(*tasks) if tasks else asyncio.Event().wait())


def main():
    if flag("nodo") or flag("smistatore"):
        threading.Thread(target=lambda: asyncio.run(rete_main()), daemon=True).start()
    if TOKEN:
        ThreadingHTTPServer(("0.0.0.0", 8099), H).serve_forever()
    else:
        threading.Event().wait()


if __name__ == "__main__":
    main()
