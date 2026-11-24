"""Agente del nodo Yamlic: chiede lavori al broker (in uscita, nessuna porta da aprire),
li fa eseguire dal modello locale e restituisce il risultato. Lavora solo se la macchina e libera."""
import json, os, time, urllib.request, urllib.error

OPT = json.load(open("/data/options.json")) if os.path.exists("/data/options.json") else {}
BROKER = (os.environ.get("BROKER_URL") or OPT.get("broker_url") or "https://llm.narriq.app").rstrip("/")
TOKEN = os.environ.get("NODE_TOKEN") or OPT.get("node_token") or ""
LOCAL = os.environ.get("LOCAL_URL", "http://127.0.0.1:8081") + "/v1/chat/completions"
IDLE_ONLY = OPT.get("only_when_idle", True) if "only_when_idle" in OPT else os.environ.get("ONLY_IDLE", "1") == "1"
MAX_LOAD = float(os.environ.get("MAX_LOAD") or OPT.get("max_load") or 0.6)   # carico medio per core oltre cui non accetta lavori
H = {"Authorization": "Bearer " + TOKEN}

def busy():
    if not IDLE_ONLY: return False
    try:
        return os.getloadavg()[0] / (os.cpu_count() or 1) > MAX_LOAD
    except OSError:
        return False

def http(url, data=None, timeout=40):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={**H, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, (json.load(r) if r.status == 200 else None)

def run_local(payload):
    req = urllib.request.Request(LOCAL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)

def main():
    if not TOKEN:
        raise SystemExit("node_token mancante: chiedilo a chi gestisce la rete")
    print("Nodo attivo. Broker:", BROKER, flush=True)
    wait = 2
    while True:
        if busy():
            time.sleep(15); continue
        try:
            st, job = http(BROKER + "/node/poll")
            wait = 2
            if st != 200 or not job: continue
            http(BROKER + "/node/ack?id=" + job["id"], {})
            try:
                res = run_local(job["payload"])
            except Exception as ex:
                res = {"error": "nodo: " + str(ex)[:100]}
            http(BROKER + "/node/result?id=" + job["id"], res)
        except urllib.error.HTTPError as e:
            if e.code == 401: raise SystemExit("token del nodo non valido")
            time.sleep(wait); wait = min(wait * 2, 60)
        except Exception:
            time.sleep(wait); wait = min(wait * 2, 60)

if __name__ == "__main__":
    main()
