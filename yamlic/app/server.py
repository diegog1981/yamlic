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

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Yamlic</title>
<body style="font-family:sans-serif;max-width:720px;margin:2em auto;padding:0 1em">
<h2>Yamlic</h2>
<textarea id=p rows=3 style="width:100%" placeholder="Es. quando esco di casa spegni le luci"></textarea>
<p><button id=b>Genera</button> <span id=s></span></p>
<pre id=y style="background:#eee;padding:1em;white-space:pre-wrap"></pre>
<div id=w style="color:#b00"></div>
<script>
b.onclick=async()=>{s.textContent='Sto scrivendo...';y.textContent='';w.textContent='';
const t=Date.now();
const r=await fetch('api/generate',{method:'POST',body:JSON.stringify({prompt:p.value})});
const j=await r.json();s.textContent=((Date.now()-t)/1000).toFixed(1)+' s  '+JSON.stringify(j.stats||{});
y.textContent=j.yaml||j.error||'';w.textContent=(j.problems||[]).join(' | ');};
</script></body>"""


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


# ---------- cliente ----------
def generate(prompt):
    states = ha("/states")
    io = OPT.get("io_person") or ""
    ents, known = core.select_entities(states, prompt, io)
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
                return not core.validate(core.prune_unknown(anon.restore(core.extract_yaml(text_of(r))), known)[0], known, prompt, remote=True)
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
    y, tolte = core.prune_unknown(y, known)
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
        self._send(200, PAGE.encode(), "text/html")

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            if self.path.endswith("/api/generate"):
                self._send(200, generate(data.get("prompt", "")))
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
