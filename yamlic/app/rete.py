"""Rete Yamlic. Ogni installazione puo avere tre ruoli, tutti opzionali:
  - cliente:     chiede lavori alla rete
  - nodo:        esegue lavori con il modello locale (solo quando la macchina e libera)
  - smistatore:  mette in contatto clienti e nodi (serve una porta raggiungibile, es. una VPS)
Clienti e nodi si collegano solo IN USCITA (WebSocket su 443): nessuna porta da aprire.
Il contenuto delle richieste e cifrato dal cliente verso il nodo: lo smistatore non lo legge.
Identita = chiave Ed25519 locale. Nessun log dei contenuti."""
import asyncio, hashlib, json, os, random, time
import aiohttp
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization as S

RAW = dict(encoding=S.Encoding.Raw, format=S.PublicFormat.Raw)
RAWPRIV = dict(encoding=S.Encoding.Raw, format=S.PrivateFormat.Raw, encryption_algorithm=S.NoEncryption())
MAX_BLOB = 24000


# ---------- identita e cifratura ----------
class Identity:
    def __init__(self, path=None):
        self.path = path
        d = {}
        if path and os.path.exists(path):
            d = json.load(open(path))
        if d:
            self.sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(d["sk"]))
            self.ek = X25519PrivateKey.from_private_bytes(bytes.fromhex(d["ek"]))
        else:
            self.sk, self.ek = Ed25519PrivateKey.generate(), X25519PrivateKey.generate()
            if path:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                json.dump({"sk": self.sk.private_bytes(**RAWPRIV).hex(), "ek": self.ek.private_bytes(**RAWPRIV).hex()}, open(path, "w"))
                os.chmod(path, 0o600)
        self.sp = self.sk.public_key().public_bytes(**RAW).hex()
        self.ep = self.ek.public_key().public_bytes(**RAW).hex()
        self.id = node_id(self.sp)

    def sign(self, data: bytes):
        return self.sk.sign(data).hex()


def node_id(sp_hex):
    return hashlib.sha256(bytes.fromhex(sp_hex)).hexdigest()[:16]


def verify(sp_hex, sig_hex, data: bytes):
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(sp_hex)).verify(bytes.fromhex(sig_hex), data)
        return True
    except Exception:
        return False


def _key(shared):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"yamlic-v1").derive(shared)


def seal(peer_ep_hex, rid, plain: bytes):
    eph = X25519PrivateKey.generate()
    key = _key(eph.exchange(X25519PublicKey.from_public_bytes(bytes.fromhex(peer_ep_hex))))
    n = os.urandom(12)
    return {"eph": eph.public_key().public_bytes(**RAW).hex(), "n": n.hex(), "ct": ChaCha20Poly1305(key).encrypt(n, plain, rid.encode()).hex()}, key


def open_box(ident, rid, box):
    key = _key(ident.ek.exchange(X25519PublicKey.from_public_bytes(bytes.fromhex(box["eph"]))))
    return ChaCha20Poly1305(key).decrypt(bytes.fromhex(box["n"]), bytes.fromhex(box["ct"]), rid.encode()), key


def seal_reply(key, rid, plain: bytes):
    n = os.urandom(12)
    return {"n": n.hex(), "ct": ChaCha20Poly1305(key).encrypt(n, plain, rid.encode()).hex()}


def open_reply(key, rid, box):
    return ChaCha20Poly1305(key).decrypt(bytes.fromhex(box["n"]), bytes.fromhex(box["ct"]), rid.encode())


def clean_payload(p):
    """Il nodo accetta solo richieste di chat piccole e semplici."""
    msgs = p.get("messages")
    if not isinstance(msgs, list) or not (1 <= len(msgs) <= 3):
        raise ValueError("messaggi non validi")
    out = []
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") not in {"system", "user"} or not isinstance(m.get("content"), str):
            raise ValueError("messaggio non valido")
        out.append({"role": m["role"], "content": m["content"]})
    if sum(len(m["content"]) for m in out) > 8000:
        raise ValueError("richiesta troppo lunga")
    res = {"messages": out, "max_tokens": min(int(p.get("max_tokens", 450)), 600),
           "temperature": min(float(p.get("temperature", 0.2)), 1.0)}
    g = p.get("grammar")
    if isinstance(g, str) and 0 < len(g) <= 8000:           # grammatica GBNF opzionale (solo entita e servizi dell'elenco)
        res["grammar"] = g
    return res


STATO = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Rete Yamlic</title>
<style>body{font-family:system-ui,sans-serif;max-width:640px;margin:2em auto;padding:0 1em;color:#1c1c1e;background:#f5f5f7}
@media(prefers-color-scheme:dark){body{color:#eee;background:#1b1b1d}.c{background:#2a2a2d!important}}
.c{background:#fff;border-radius:12px;padding:1em 1.2em;margin:1em 0}.n{font-size:2.2em;font-weight:700}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:.4em .2em;border-bottom:1px solid #8884}.s{opacity:.65;font-size:.9em}</style>
<h2>Rete Yamlic</h2><div class=c><div class=n id=n>-</div><div class=s>nodi attivi adesso</div></div>
<div class=c><table id=t><tr><th>Nodo</th><th>Velocit&agrave;</th><th>Affidabilit&agrave;</th></tr></table></div>
<div class=s id=x></div>
<script>async function g(){try{const j=await (await fetch('health')).json();n.textContent=j.nodi;
t.innerHTML='<tr><th>Nodo</th><th>Velocit&agrave;</th><th>Affidabilit&agrave;</th></tr>'+(j.lista||[]).map(o=>'<tr><td>'+o.id+'</td><td>'+(o.tps==null?'-':o.tps+' token/s')+'</td><td>'+(o.score==null?'in prova':Math.round(o.score*100)+'% ('+o.prove+' prove)')+'</td></tr>').join('');
x.textContent='Richieste servite: '+j.riusciti+' su '+j.jobs+' &middot; clienti collegati: '+j.clienti;x.innerHTML=x.textContent}catch(e){x.textContent='Smistatore non raggiungibile'}}
g();setInterval(g,10000)</script>"""


# ---------- smistatore ----------
class Bucket:
    def __init__(self, per_min):
        self.cap, self.t, self.n = per_min, time.time(), per_min

    def take(self):
        now = time.time()
        self.n = min(self.cap, self.n + (now - self.t) * self.cap / 60)
        self.t = now
        if self.n >= 1:
            self.n -= 1
            return True
        return False


# ---------- canary: richieste di prova cifrate come quelle vere, controllate dal validatore ----------
_CANARY_SYS = None


def _canary_cases():
    """(righe entita, richiesta, [stringhe che devono esserci], [stringhe che non devono esserci], ids)"""
    z, l1, l2, c1, f1 = "- zone.home (Casa)", "- light.e1 (Luce _A1)", "- light.e2 (Luce _A2)", "- cover.e3 (Tapparella _A1)", "- fan.e4 (Ventola _A2)"
    ids = {"zone.home", "light.e1", "light.e2", "cover.e3", "fan.e4"}
    base = "\n".join([z, l1, l2, c1, f1])
    return [
        (base, "accendi la luce _A1", ["light.turn_on", "light.e1"], ["light.e2"], ids),
        (base, "spegni la luce _A2", ["light.turn_off", "light.e2"], ["light.e1"], ids),
        (base, "spegni la luce _A2 alle 23", ["light.turn_off", "light.e2", "23:00"], ["light.e1"], ids),
        (base, "chiudi la tapparella _A1 al tramonto", ["cover.close_cover", "cover.e3", "sunset"], [], ids),
        (base, "quando esco di casa spegni tutte le luci", ["light.turn_off", "light.e1", "light.e2", "zone.home"], [], ids),
        (base, "apri la tapparella _A1 ogni mattina alle 7", ["cover.open_cover", "cover.e3", "07:00"], [], ids),
        (base, "accendi la ventola _A2 se la luce _A2 e accesa da dieci minuti", ["fan.turn_on", "fan.e4"], [], ids),
        (base, "quando torno a casa accendi la luce _A1", ["light.turn_on", "light.e1", "zone.home"], ["light.e2"], ids),
    ]


def _canary_ok(testo, case):
    import core
    _, _, deve, non, ids = case
    try:
        y = core.extract_yaml(testo)
        if core.validate(y, ids, "", remote=True):
            return False
        return all(x in y for x in deve) and not any(x in y for x in non)
    except Exception:
        return False


class Relay:
    def __init__(self, other_relays=(), client_per_min=12):
        self.peers = {}            # id -> dict(ws, sp, ep, sig, roles, cap)
        self.pending = {}          # (rid, node) -> (client_id, t)
        self.buckets = {}
        self.others = list(other_relays)
        self.client_per_min = client_per_min
        self.stats = {"jobs": 0, "riusciti": 0, "err": 0, "since": int(time.time())}
        self.segn_file = os.environ.get("YAMLIC_SEGNALAZIONI", "/opt/yamlic/data/segnalazioni.jsonl")
        self.segn_ip = {}          # ip -> orari degli ultimi invii
        self.rep = {}              # id nodo -> reputazione (canary)
        self.inflight = {}         # id nodo -> lavori in corso
        self.canary_wait = {}      # rid -> (nodo, t0, caso, chiave)
        self.rep_file = os.environ.get("YAMLIC_REPUTAZIONE", "/opt/yamlic/data/reputazione.json")
        self.canary_on = os.environ.get("YAMLIC_CANARY", "1") != "0"
        self.t_loop, self.t_nuovo, self.t_range = 15, 40, (240, 420)    # secondi (cambiati nei test)
        self._carica_rep()

    def app(self):
        a = web.Application()
        a.add_routes([web.get("/ws", self.ws), web.get("/health", self.health), web.get("/stato", self.stato), web.post("/segnala", self.segnala)])
        a.on_startup.append(self._avvia)
        a.on_cleanup.append(self._ferma)
        return a

    async def _avvia(self, app):
        self._task = asyncio.create_task(self.canary_loop()) if self.canary_on else None

    async def _ferma(self, app):
        if getattr(self, "_task", None):
            self._task.cancel()

    # ----- reputazione -----
    def _carica_rep(self):
        try:
            with open(self.rep_file, encoding="utf-8") as f:
                d = json.load(f)
            now = time.time()
            self.rep = {k: v for k, v in d.items() if isinstance(v, dict) and now - v.get("visto", 0) < 30 * 86400}
        except Exception:
            self.rep = {}

    def _salva_rep(self):
        try:
            os.makedirs(os.path.dirname(self.rep_file), exist_ok=True)
            tmp = self.rep_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.rep, f)
            os.replace(tmp, self.rep_file)
        except Exception:
            pass

    def _r(self, nid):
        return self.rep.setdefault(nid, {"ok": 0, "tot": 0, "ewma": None, "tps": None, "visto": time.time(), "next": 0})

    def affidabile(self, nid):
        r = self.rep.get(nid)
        return bool(r) and r["tot"] >= 3 and (r["ewma"] or 0) >= 0.6

    def tps_nodo(self, nid):
        r = self.rep.get(nid) or {}
        dich = (self.peers.get(nid, {}).get("cap") or {}).get("tps")
        return r.get("tps") or dich

    def _esito(self, nid, ok, secondi=None, ntok=None):
        r = self._r(nid)
        r["tot"] += 1
        r["ok"] += 1 if ok else 0
        r["ewma"] = (1.0 if ok else 0.0) if r["ewma"] is None else round(0.8 * r["ewma"] + 0.2 * (1.0 if ok else 0.0), 3)
        if ok and secondi and ntok:
            t = ntok / max(secondi, 0.2)
            r["tps"] = round(t if not r["tps"] else 0.7 * r["tps"] + 0.3 * t, 1)
        r["visto"] = time.time()

    async def canary_loop(self):
        """Ogni nodo riceve ogni tanto una richiesta di prova indistinguibile da una vera; la risposta e controllata col validatore."""
        ultimo_salvataggio = 0
        while True:
            try:
                await asyncio.sleep(self.t_loop)
                now = time.time()
                for rid, (nid, t0, caso, key) in list(self.canary_wait.items()):
                    if now - t0 > 90:                       # nessuna risposta: errore
                        del self.canary_wait[rid]
                        self.pending.pop((rid, nid), None)
                        self.inflight[nid] = max(0, self.inflight.get(nid, 1) - 1)
                        self._esito(nid, False)
                for nid, p in list(self.peers.items()):
                    if "node" not in p["roles"]:
                        continue
                    r = self._r(nid)
                    if now < r["next"] or self.inflight.get(nid, 0) > 0:
                        continue
                    r["next"] = now + (self.t_nuovo if r["tot"] < 3 else random.uniform(*self.t_range))
                    await self._manda_canary(nid, p)
                if now - ultimo_salvataggio > 120:
                    self._salva_rep(); ultimo_salvataggio = now
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

    async def _manda_canary(self, nid, p):
        import core
        caso = random.choice(_canary_cases())
        rid = os.urandom(8).hex()
        ents, req = caso[0], caso[1]
        plain = json.dumps({"messages": [{"role": "system", "content": core.SYSTEM}, {"role": "user", "content": "Entita disponibili:\n%s\n\nRichiesta: %s" % (ents, req)}],
                            "temperature": 0.2, "max_tokens": 450}).encode()
        box, key = seal(p["ep"], rid, plain)
        self.pending[(rid, nid)] = ("__canary__", time.time(), None)
        self.canary_wait[rid] = (nid, time.time(), caso, key)
        self.inflight[nid] = self.inflight.get(nid, 0) + 1
        try:
            await p["ws"].send_json({"t": "job", "rid": rid, "from": os.urandom(8).hex(), "box": box})
        except Exception:
            self.canary_wait.pop(rid, None); self.pending.pop((rid, nid), None)
            self.inflight[nid] = max(0, self.inflight.get(nid, 1) - 1)

    def _canary_risposta(self, nid, m):
        w = self.canary_wait.pop(m.get("rid"), None)
        if not w:
            return
        _, t0, caso, key = w
        secondi = time.time() - t0
        if m.get("t") == "err":
            if "occupato" in str(m.get("m", "")):           # nodo impegnato: non e una colpa, riprova presto
                self._r(nid)["next"] = time.time() + 60
                return
            return self._esito(nid, False)
        try:
            res = json.loads(open_reply(key, m["rid"], m["box"]))
            testo = res["choices"][0]["message"]["content"]
            ntok = (res.get("usage") or {}).get("completion_tokens") or max(1, len(testo) // 3)
        except Exception:
            return self._esito(nid, False)
        self._esito(nid, _canary_ok(testo, caso), secondi, ntok)

    async def segnala(self, req):
        """Riceve una segnalazione gia anonima dall'utente. Max 10 all'ora per indirizzo, 20 KB, solo testo."""
        ip = req.headers.get("X-Real-IP") or req.remote or "?"
        now = time.time()
        L = [x for x in self.segn_ip.get(ip, []) if now - x < 3600]
        if len(L) >= 10:
            return web.json_response({"error": "troppi invii, riprova piu tardi"}, status=429)
        raw = await req.content.read(20001)
        if len(raw) > 20000:
            return web.json_response({"error": "troppo grande"}, status=413)
        try:
            d = json.loads(raw.decode())
            rec = {"t": int(now), "ip": hashlib.sha256(ip.encode()).hexdigest()[:8], "v": str(d.get("v", ""))[:12], "via": str(d.get("via", ""))[:40],
                   "prompt": str(d.get("prompt", ""))[:600], "yaml": str(d.get("yaml", ""))[:6000],
                   "problems": [str(p)[:300] for p in (d.get("problems") or [])][:12], "nota": str(d.get("nota", ""))[:600]}
            os.makedirs(os.path.dirname(self.segn_file), exist_ok=True)
            with open(self.segn_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            return web.json_response({"error": "formato non valido"}, status=400)
        L.append(now)
        self.segn_ip[ip] = L
        return web.json_response({"ok": True})

    async def health(self, req):
        nodes = [(i, p) for i, p in self.peers.items() if "node" in p["roles"]]
        def voce(i, p):
            r = self.rep.get(i) or {}
            return {"id": i[:6], "tps": self.tps_nodo(i), "modello": (p.get("cap") or {}).get("model"), "score": r.get("ewma"), "prove": r.get("tot", 0),
                    "affidabile": self.affidabile(i), "in_corso": self.inflight.get(i, 0)}
        lista = sorted((voce(i, p) for i, p in nodes), key=lambda x: -((x["score"] or 0) * (x["tps"] or 0)))
        return web.json_response({"ok": True, "nodi": len(nodes), "clienti": len(self.peers) - len(nodes), "lista": lista, **self.stats})

    async def stato(self, req):
        return web.Response(text=STATO, content_type="text/html")

    async def ws(self, req):
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_BLOB * 2)
        await ws.prepare(req)
        nonce = os.urandom(16).hex()
        await ws.send_json({"t": "challenge", "n": nonce})
        me = None
        try:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                m = json.loads(msg.data)
                t = m.get("t")
                if me is None:
                    if t != "hello" or not verify(m["sp"], m["sig"], nonce.encode() + bytes.fromhex(m["ep"])):
                        await ws.send_json({"t": "err", "m": "autenticazione"}); break
                    ident = node_id(m["sp"])
                    # i clienti possono avere piu connessioni insieme (richieste in parallelo): chiave unica per connessione
                    me = ident if "node" in m.get("roles", []) else ident + ":" + os.urandom(3).hex()
                    self.peers[me] = {"ws": ws, "sp": m["sp"], "ep": m["ep"], "bind": m.get("bind", ""), "roles": m.get("roles", []), "cap": m.get("cap", {}), "ident": ident}
                    await ws.send_json({"t": "ok", "id": ident, "relays": self.others})
                    continue
                await self.handle(me, m)
        except Exception:
            pass
        finally:
            if me and self.peers.get(me, {}).get("ws") is ws:
                del self.peers[me]
                await self._nodo_caduto(me)
        return ws

    async def _nodo_caduto(self, nid):
        """Il nodo si e scollegato: i clienti in attesa ricevono subito un errore invece di aspettare il timeout."""
        self.inflight.pop(nid, None)
        for (rid, n), v in list(self.pending.items()):
            if n != nid:
                continue
            del self.pending[(rid, n)]
            if v[0] == "__canary__":
                self.canary_wait.pop(rid, None)
            elif v[0] in self.peers:
                try:
                    await self.peers[v[0]]["ws"].send_json({"t": "err", "rid": rid, "from": nid, "m": "nodo scollegato"})
                except Exception:
                    pass

    async def handle(self, me, m):
        t, ws = m.get("t"), self.peers[me]["ws"]
        if t == "nodes":
            tutti = [(i, p) for i, p in self.peers.items() if "node" in p["roles"] and i != me]
            buoni = [(i, p) for i, p in tutti if self.affidabile(i)]
            if not buoni:                                    # nessun nodo ancora verificato: meglio questi che niente
                buoni = [(i, p) for i, p in tutti if (self.rep.get(i) or {}).get("ewma") is None or (self.rep[i]["ewma"] or 0) >= 0.3]
            def peso(ip):
                i, p = ip
                return -((self.tps_nodo(i) or 1) * ((self.rep.get(i) or {}).get("ewma") or 0.5) / (1 + self.inflight.get(i, 0)))
            buoni.sort(key=lambda ip: peso(ip) * random.uniform(0.85, 1.0))
            lst = [{"id": i, "sp": p["sp"], "ep": p["ep"], "bind": p["bind"],
                    "cap": dict(p["cap"], tps=self.tps_nodo(i) or p["cap"].get("tps")), "rep": {"score": (self.rep.get(i) or {}).get("ewma"), "prove": (self.rep.get(i) or {}).get("tot", 0)}}
                   for i, p in buoni]
            await ws.send_json({"t": "nodes", "list": lst[:20], "relays": self.others})
        elif t == "job":
            b = self.buckets.setdefault(self.peers[me].get("ident", me), Bucket(self.client_per_min))
            to = self.peers.get(m.get("to"))
            if not b.take():
                await ws.send_json({"t": "err", "rid": m.get("rid"), "m": "troppe richieste"})
            elif not to or len(json.dumps(m)) > MAX_BLOB:
                await ws.send_json({"t": "err", "rid": m.get("rid"), "m": "nodo non disponibile"})
            elif self.inflight.get(m["to"], 0) >= 3 * int((to.get("cap") or {}).get("slots", 1)):     # oltre la coda ragionevole: meglio un altro nodo
                await ws.send_json({"t": "err", "rid": m.get("rid"), "m": "nodo occupato"})
            else:
                self.pending[(m["rid"], m["to"])] = (me, time.time(), None)
                self.inflight[m["to"]] = self.inflight.get(m["to"], 0) + 1
                self.stats["jobs"] += 1
                await to["ws"].send_json({"t": "job", "rid": m["rid"], "from": me, "box": m["box"]})
        elif t == "sig":
            b = self.buckets.setdefault(self.peers[me].get("ident", me), Bucket(self.client_per_min * 3))
            to = self.peers.get(m.get("to"))
            if to and b.take() and len(json.dumps(m)) < 20000:
                await to["ws"].send_json({"t": "sig", "from": me, "type": m.get("type"), "sdp": m.get("sdp")})
        elif t in ("res", "err"):
            k = (m.get("rid"), me)
            c = self.pending.pop(k, None)
            if c:
                self.inflight[me] = max(0, self.inflight.get(me, 1) - 1)
            if c and c[0] == "__canary__":
                self._canary_risposta(me, m)
            elif c and c[0] in self.peers:
                self.stats["riusciti" if t == "res" else "err"] += 1
                await self.peers[c[0]]["ws"].send_json({"t": t, "rid": m["rid"], "from": me, "box": m.get("box"), "m": m.get("m")})
        for k in [k for k, v in self.pending.items() if time.time() - v[1] > 300]:
            self.pending.pop(k, None)
            self.inflight[k[1]] = max(0, self.inflight.get(k[1], 1) - 1)


async def run_relay(host, port, others=(), per_min=12):
    r = Relay(others, per_min)
    runner = web.AppRunner(r.app())
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    return r, runner


# ---------- nodo ----------
async def run_node(ident, urls, handler, busy=lambda: False, cap=None, direct=False, ice=()):
    """handler(payload)->dict (bloccante, eseguito in un thread). Si ricollega da solo."""
    async def one(url):
        wait = 2
        while True:
            try:
                async with aiohttp.ClientSession() as s, s.ws_connect(url, heartbeat=20, max_msg_size=MAX_BLOB * 2) as ws:
                    await _hello(ws, ident, ["node"], dict(cap or {}, direct=bool(direct)))
                    wait = 2
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        m = json.loads(msg.data)
                        if m.get("t") == "job":
                            asyncio.create_task(_do_job(ws, ident, m, handler, busy))
                        elif m.get("t") == "sig" and direct and m.get("type") == "offer":
                            from direct import node_answer
                            asyncio.create_task(node_answer(ws, ident, m, handler, busy, ice, clean_payload, open_box, seal_reply))
            except Exception:
                pass
            await asyncio.sleep(wait)
            wait = min(wait * 2, 60)
    await asyncio.gather(*(one(u) for u in urls))


async def _hello(ws, ident, roles, cap):
    ch = json.loads((await ws.receive(timeout=10)).data)
    ep = bytes.fromhex(ident.ep)
    await ws.send_json({"t": "hello", "sp": ident.sp, "ep": ident.ep, "sig": ident.sign(ch["n"].encode() + ep),
                        "bind": ident.sign(ep), "roles": roles, "cap": cap})
    ok = json.loads((await ws.receive(timeout=10)).data)
    if ok.get("t") != "ok":
        raise ConnectionError(ok.get("m", "rifiutato"))
    return ok


async def _do_job(ws, ident, m, handler, busy):
    rid = m["rid"]
    try:
        if busy():
            raise RuntimeError("nodo occupato")
        plain, key = open_box(ident, rid, m["box"])
        payload = clean_payload(json.loads(plain))
        res = await asyncio.get_running_loop().run_in_executor(None, handler, payload)
        await ws.send_json({"t": "res", "rid": rid, "box": seal_reply(key, rid, json.dumps(res).encode())})
    except Exception as ex:
        await ws.send_json({"t": "err", "rid": rid, "m": str(ex)[:80]})


# ---------- cliente ----------
async def ask(ident, relays, payload, accept=lambda r: True, k=2, timeout=90, direct=False, ice=(), min_tps=2.0,
              adaptive=False, hedge=4.0, escalate=lambda r: False, n_voto=3, firma=None):
    """Prova gli smistatori in ordine. Invia a k nodi, restituisce (risultato, id_nodo) del primo che passa `accept`.
    adaptive=True: prima un solo nodo (il migliore); se non risponde entro `hedge` secondi parte la staffetta verso il successivo; se la risposta non e valida
    o `escalate(r)` la giudica dubbia, si interpellano fino a `n_voto` nodi e vince la struttura piu comune (`firma(r)`), a parita il nodo piu in alto.
    Se nessuno passa restituisce il primo risultato ricevuto (il chiamante lo scarta o lo mostra con i problemi)."""
    last = "nessuno smistatore raggiungibile"
    for url in relays:
        try:
            async with aiohttp.ClientSession() as s, s.ws_connect(url, heartbeat=20, max_msg_size=MAX_BLOB * 2, timeout=10) as ws:
                await _hello(ws, ident, ["client"], {})
                await ws.send_json({"t": "nodes"})
                lst = []
                while not lst:
                    m = json.loads((await ws.receive(timeout=10)).data)
                    if m.get("t") == "nodes":
                        lst = [n for n in m["list"] if node_id(n["sp"]) == n["id"] and verify(n["sp"], n["bind"], bytes.fromhex(n["ep"]))]
                        break
                lst = [n for n in lst if n["cap"].get("tps", 99) >= min_tps]
                lst.sort(key=lambda n: -n["cap"].get("tps", 0) * random.uniform(0.7, 1.0))
                if not lst:
                    last = "nessun nodo abbastanza veloce disponibile"; continue
                data = json.dumps(payload).encode()
                if direct:
                    from direct import client_direct
                    for n in [n for n in lst if n["cap"].get("direct")][:2]:
                        try:
                            r = await client_direct(ws, n, data, ice, seal, open_reply)
                            if accept(r):
                                return r, n["id"]
                        except Exception:
                            pass          # il diretto non e riuscito: si passa dallo smistatore
                keys, sent, ordine = {}, {}, []

                async def invia(n):
                    rid = os.urandom(8).hex()
                    box, key = seal(n["ep"], rid, data)
                    keys[rid], sent[rid] = key, n["id"]
                    ordine.append(rid)
                    await ws.send_json({"t": "job", "rid": rid, "to": n["id"], "box": box})
                for n in lst[:(1 if adaptive else k)]:
                    await invia(n)
                prossimo = 1 if adaptive else k          # indice del prossimo nodo da interpellare
                got, validi, deadline = [], [], time.time() + timeout
                voto = False                             # True: servono piu risposte da confrontare
                t_hedge = time.time() + hedge
                while len(got) < len(sent) or prossimo < min(len(lst), n_voto) and voto:
                    attesa = max(0.2, (t_hedge if adaptive and not voto and prossimo < len(lst) and prossimo < n_voto else deadline) - time.time())
                    try:
                        msg = await ws.receive(timeout=min(attesa, max(0.2, deadline - time.time())))
                    except asyncio.TimeoutError:
                        if time.time() >= deadline:
                            break
                        if adaptive and not voto and prossimo < min(len(lst), n_voto):
                            await invia(lst[prossimo]); prossimo += 1          # staffetta: il primo nodo e lento
                            t_hedge = time.time() + hedge
                            continue
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        break
                    m = json.loads(msg.data)
                    if m.get("rid") not in keys:
                        continue
                    r = None
                    if m["t"] == "res":
                        try:
                            r = json.loads(open_reply(keys[m["rid"]], m["rid"], m["box"]))
                        except Exception:
                            r = None
                    else:
                        last = m.get("m") or "errore del nodo"
                    got.append((r, sent[m["rid"]], m["rid"]) if r is not None else None)
                    if r is not None and accept(r):
                        validi.append((r, sent[m["rid"]], m["rid"]))
                        if not adaptive or not (escalate(r) if len(validi) == 1 else False) and len(validi) == 1 and not voto:
                            return r, sent[m["rid"]]                                # risposta buona e non dubbia: fatto
                        voto = True
                        if firma:
                            sg = [firma(x[0]) for x in validi]
                            if len(validi) >= 2 and any(sg.count(x) >= 2 for x in sg):
                                break                                              # due risposte concordano
                    elif adaptive and not voto:
                        voto = True                                                # primo nodo: errore, non valida o dubbia
                    if adaptive and voto:
                        while prossimo < min(len(lst), n_voto) and len(sent) < n_voto and prossimo - 0 < len(lst) and len(sent) <= prossimo:
                            await invia(lst[prossimo]); prossimo += 1
                if validi:
                    if firma and len(validi) > 1:
                        sg = [firma(x[0]) for x in validi]
                        best = max(sg, key=lambda x: (sg.count(x), -sg.index(x)))
                        return validi[sg.index(best)][:2]
                    return validi[0][:2]
                ok = [g for g in got if g]
                if ok:
                    return ok[0][:2]
        except Exception as ex:
            last = str(ex)[:80] or type(ex).__name__
    raise RuntimeError(last)
