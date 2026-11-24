"""Yamlic broker: smista le richieste dei client ai nodi che si collegano in uscita (long polling).
API compatibile OpenAI: /v1/chat/completions. Se non ci sono nodi liberi usa il modello locale della VPS.
Solo libreria standard. Configurazione con variabili d'ambiente (vedi sotto) e tokens.json.
Privacy: i contenuti delle richieste non vengono scritti nei log ne su disco."""
import json, os, queue, threading, time, uuid, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("BROKER_PORT", "18090"))
TOKENS = os.environ.get("BROKER_TOKENS", "tokens.json")       # {"clients": {"token": "nome"}, "nodes": {"token": "nome"}}
FALLBACK_URL = os.environ.get("FALLBACK_URL", "http://127.0.0.1:18080")  # llama.cpp locale (VPS)
FALLBACK_KEY = os.environ.get("FALLBACK_KEY", "")
NODE_WAIT = int(os.environ.get("NODE_WAIT", "60"))             # secondi di attesa del risultato da un nodo
NODE_ALIVE = 60                                                # un nodo e "online" se ha fatto poll negli ultimi N secondi
RATE = int(os.environ.get("RATE_PER_MIN", "30"))               # richieste al minuto per client
STATS_FILE = os.environ.get("STATS_FILE", "stats.json")

jobs = queue.Queue()
pending = {}                      # id -> {"ev": Event, "res": dict|None, "node": str|None}
nodes_seen = {}                   # nome nodo -> ultimo poll
stats = {"clients": {}, "nodes": {}, "fallback": 0}
lock = threading.Lock()
rate = {}                         # client -> [timestamp]

def load_tokens():
    with open(TOKENS) as f:
        return json.load(f)

def bump(kind, name):
    with lock:
        stats[kind][name] = stats[kind].get(name, 0) + 1

def nodes_online():
    now = time.time()
    with lock:
        return [n for n, t in nodes_seen.items() if now - t < NODE_ALIVE]

def ratelimited(client):
    now = time.time()
    with lock:
        L = [t for t in rate.get(client, []) if now - t < 60]
        if len(L) >= RATE:
            rate[client] = L
            return True
        L.append(now); rate[client] = L
    return False

def via_fallback(payload):
    req = urllib.request.Request(FALLBACK_URL.rstrip("/") + "/v1/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + FALLBACK_KEY} if FALLBACK_KEY else {})})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)

def valid_result(r):
    try:
        return isinstance(r["choices"][0]["message"]["content"], str)
    except Exception:
        return False

ACK_WAIT = int(os.environ.get("ACK_WAIT", "8"))               # il nodo deve confermare la ricezione entro N secondi

def dispatch(payload):
    """Prova un nodo (con un secondo tentativo se non conferma la ricezione); altrimenti usa il modello locale."""
    if nodes_online():
        jid = uuid.uuid4().hex
        ent = {"ev": threading.Event(), "ack": threading.Event(), "res": None, "node": None}
        with lock: pending[jid] = ent
        got = False
        for _ in range(2):
            ent["ack"].clear(); ent["node"] = None
            jobs.put({"id": jid, "payload": payload})
            if ent["ack"].wait(ACK_WAIT):
                got = True
                break
            with lock:                   # nessuna conferma: il nodo e sparito
                if ent["node"]: nodes_seen.pop(ent["node"], None)
                for _ in range(jobs.qsize()):   # togli il lavoro rimasto in coda
                    try:
                        j = jobs.get_nowait()
                        if j["id"] != jid: jobs.put(j)
                    except queue.Empty:
                        break
            if not nodes_online(): break
        ok = got and ent["ev"].wait(NODE_WAIT)
        with lock: pending.pop(jid, None)
        if ok and ent["res"] is not None and valid_result(ent["res"]):
            bump("nodes", ent["node"] or "?")
            return ent["res"], "nodo"
    with lock: stats["fallback"] += 1
    return via_fallback(payload), "vps"

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):   # nessun log delle richieste
        pass

    def _json(self, code, obj, extra=None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)

    def _auth(self, group):
        h = self.headers.get("Authorization", "")
        tok = h[7:] if h.startswith("Bearer ") else ""
        return load_tokens().get(group, {}).get(tok)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self._json(200, {"status": "ok", "nodi_online": len(nodes_online())})
        if u.path == "/node/poll":
            name = self._auth("nodes")
            if not name: return self._json(401, {"error": "non autorizzato"})
            deadline = time.time() + 25
            while time.time() < deadline:
                with lock: nodes_seen[name] = time.time()
                try:
                    job = jobs.get(timeout=2)
                except queue.Empty:
                    continue
                with lock:
                    ent = pending.get(job["id"])
                    if ent is None:       # il client ha rinunciato: scarta
                        continue
                    ent["node"] = name
                try:
                    return self._json(200, job)
                except (BrokenPipeError, ConnectionError):
                    with lock: nodes_seen.pop(name, None)
                    jobs.put(job)          # il nodo e sparito: rimetti il lavoro in coda
                    return
            self.send_response(204); self.end_headers(); return
        return self._json(404, {"error": "non trovato"})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "json non valido"})
        if u.path == "/node/ack":
            name = self._auth("nodes")
            if not name: return self._json(401, {"error": "non autorizzato"})
            with lock: ent = pending.get(parse_qs(u.query).get("id", [""])[0])
            if ent is None or ent["node"] != name: return self._json(404, {"error": "lavoro sconosciuto"})
            ent["ack"].set()
            return self._json(200, {"ok": True})
        if u.path == "/node/result":
            name = self._auth("nodes")
            if not name: return self._json(401, {"error": "non autorizzato"})
            jid = parse_qs(u.query).get("id", [""])[0]
            with lock: ent = pending.get(jid)
            if ent is None or ent["node"] != name:
                return self._json(404, {"error": "lavoro sconosciuto"})
            ent["res"] = body
            ent["ev"].set()
            return self._json(200, {"ok": True})
        if u.path.endswith("/v1/chat/completions"):
            client = self._auth("clients")
            if not client: return self._json(401, {"error": "non autorizzato"})
            if ratelimited(client): return self._json(429, {"error": "troppe richieste"}, {"Retry-After": "10"})
            if not isinstance(body.get("messages"), list): return self._json(400, {"error": "messages mancante"})
            body["max_tokens"] = min(int(body.get("max_tokens", 450)), 600)   # limite
            body.pop("stream", None)
            bump("clients", client)
            try:
                res, via = dispatch(body)
            except Exception as ex:
                return self._json(502, {"error": "modello non disponibile"})
            return self._json(200, res, {"X-Yamlic-Via": via})
        return self._json(404, {"error": "non trovato"})

def save_stats():
    while True:
        time.sleep(60)
        try:
            with lock, open(STATS_FILE, "w") as f: json.dump(stats, f)
        except Exception:
            pass

if __name__ == "__main__":
    threading.Thread(target=save_stats, daemon=True).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
