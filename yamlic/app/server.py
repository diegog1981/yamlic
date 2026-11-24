"""Yamlic: add-on unico. Ruoli (tutti opzionali): cliente (pagina + rete), nodo, smistatore.
Funziona anche fuori da Home Assistant (VPS, PC): senza SUPERVISOR_TOKEN parte solo come nodo/smistatore."""
import asyncio, json, os, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import core, rete

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
@media(prefers-color-scheme:dark){:root{--bg:#161719;--card:#222326;--tx:#ececee;--mu:#9a9ea6;--ac:#6ea0ff;--ok:#4ade80;--wa:#fbbf24;--er:#f87171;--bd:#34363a;--code:#1a1b1e}}
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
<header><h1>Yamlic<small id=ver></small></h1><span class=pill id=rete><span class=dot></span>rete...</span></header>
<div class=card>
<textarea id=p rows=3 placeholder="Scrivi cosa vuoi, per esempio: quando esco di casa spegni tutte le luci"></textarea>
<div class=row><button id=b>Genera automazione</button><span id=s class=mu></span></div>
</div>
<div id=q class="card q hide"></div>
<div id=r class="card hide">
<div class=chips id=st></div>
<pre id=y></pre>
<div class=row><button id=cp>Copia YAML</button><button id=sv class="s hide">Salva in Home Assistant</button><span id=msg class=mu></span></div>
<div id=w></div>
</div>
<p class=mu>Controlla sempre l'automazione prima di usarla. Per metterla in Home Assistant: Impostazioni, Automazioni, Crea, Modifica in YAML, incolla.</p>
</main>
<script>
const $=i=>document.getElementById(i);let busy=false;
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
 if(j.error){$('y').textContent='';$('st').innerHTML='';$('cp').classList.add('hide');$('sv').classList.add('hide');
  $('w').innerHTML='<div class="warn err">'+esc(j.error)+(j.problems?' ('+esc(j.problems.join(', '))+')':'')+'</div>';return}
 $('cp').classList.remove('hide');$('y').textContent=j.yaml||'';stats(j);
 $('w').innerHTML=(j.problems&&j.problems.length)?'<div class=warn><b>Da controllare</b><br>'+j.problems.map(esc).join('<br>')+'</div>':'';
 $('sv').classList.toggle('hide',!window.__salva);
 $('cp').onclick=()=>copia(j.yaml||'');
 $('sv').onclick=async()=>{const x=await (await fetch('api/create',{method:'POST',body:JSON.stringify({yaml:j.yaml})})).json();$('msg').textContent=x.ok?'Salvata in Home Assistant':(x.error||'errore')}}
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
 $('sk').onclick=()=>gen({salta:true});q.scrollIntoView({behavior:'smooth',block:'nearest'})}
async function gen(extra){if(busy||!$('p').value.trim())return;busy=true;$('b').disabled=true;$('s').innerHTML='<span class=sp></span>Sto scrivendo...';
 try{const r=await fetch('api/generate',{method:'POST',body:JSON.stringify(Object.assign({prompt:$('p').value},extra||{}))});const j=await r.json();
  if(j.domanda)domanda(j.domanda);else risultato(j)}catch(e){risultato({error:'Errore di comunicazione: '+e})}
 busy=false;$('b').disabled=false;$('s').textContent=''}
$('b').onclick=()=>gen();$('p').addEventListener('keydown',e=>{if(e.key=='Enter'&&(e.ctrlKey||e.metaKey))gen()});
reteInfo();setInterval(reteInfo,30000);
</script></html>"""


def ha(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(HA + path, data=body, method="POST" if body is not None else "GET",
                                 headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def llm_call(payload, timeout=900):
    h = {"Content-Type": "application/json"}
    if OPT.get("llm_key"):
        h["Authorization"] = "Bearer " + OPT["llm_key"]
    req = urllib.request.Request(LLM, data=json.dumps(payload).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def text_of(res):
    return res["choices"][0]["message"]["content"]


VERSIONE = "0.3.1"
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
def generate(prompt, scelte=None, persona="", salta=False):
    states = ha("/states")
    io = persona or OPT.get("io_person") or ""
    if not scelte and not salta and not persona:
        dom = core.ambiguity(states, prompt, io)
        if dom:
            return {"domanda": dom}
    ents, known = core.select_entities(states, prompt, io, only=set(scelte) if scelte else None)
    t0 = time.time()
    via, res, y, anon, err = "locale", None, "", None, ""
    modo = OPT.get("modo", "auto")
    use_net = USA_RETE and RELAYS and modo != "locale"
    if HAS_LOCAL and modo != "rete":
        try:
            lista = "\n".join("- %s (%s)" % (e, n) for e, n in ents)
            sm = SYSTEM + ((" L'utente che scrive e %s: quando dice io, esco, rientro, arrivo, usa quella persona." % io) if io else "")
            res = llm_call({"messages": [{"role": "system", "content": sm}, {"role": "user", "content": "Entita disponibili:\n%s\n\nRichiesta: %s" % (lista, prompt)}],
                            "temperature": 0.2, "max_tokens": 450})
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

        def accept(r):
            try:
                return not core.validate(core.clean(anon.restore(core.extract_yaml(text_of(r))), known)[0], known, prompt, remote=True)
            except Exception:
                return False
        try:
            res, node = asyncio.run(rete.ask(IDENT, RELAYS, payload, accept=accept, direct=flag("diretto"), ice=ICE))
            via = "rete:" + node[:6]
            y = anon.restore(core.extract_yaml(text_of(res)))
        except Exception as ex:
            return {"error": "rete non disponibile: %s%s" % (ex, (" / locale: " + err) if err else "")}
    if res is None:
        return {"error": "nessun modello disponibile (imposta llm_url, avvia un modello locale o attiva la rete)" + ((" - " + err) if err else "")}
    y, tolte = core.clean(y, known)
    y = core.force_targets(y, scelte or [], prompt)
    problems = core.validate(y, known, prompt, remote=via != "locale")
    if tolte:
        problems.append("entita non trovate in casa, tolte dall'elenco: " + ", ".join(tolte))
    if anon and anon.unresolved(y):
        problems.append("alias rimasti: " + ", ".join(anon.unresolved(y)))
    usage = res.get("usage", {})
    return {"yaml": y, "problems": problems, "stats": {"secondi": round(time.time() - t0, 1), "via": via, "token_risposta": usage.get("completion_tokens")}}


def create(y):
    if not flag("allow_create"):
        return 403, {"error": "Creazione disattivata (allow_create = false)."}
    import yaml
    probs = core.validate(y, {s["entity_id"] for s in ha("/states")}, "", remote=True)
    if probs:
        return 400, {"error": "non valida", "problems": probs}
    cid = str(int(time.time() * 1000))
    ha("/config/automation/config/" + cid, yaml.safe_load(y))
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
        if self.path.split("?")[0].endswith("/api/rete"):
            self._send(200, rete_info())
        else:
            self._send(200, PAGE.encode(), "text/html")

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            if self.path.endswith("/api/generate"):
                self._send(200, generate(data.get("prompt", ""), data.get("scelte"), data.get("persona", ""), bool(data.get("salta"))))
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
        tasks.append(rete.run_node(IDENT, urls, node_handler, busy, {"tps": tps, "model": "qwen2.5-coder-1.5b"},
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
