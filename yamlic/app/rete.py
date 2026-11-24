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
    return {"messages": out, "max_tokens": min(int(p.get("max_tokens", 450)), 600),
            "temperature": min(float(p.get("temperature", 0.2)), 1.0)}


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


class Relay:
    def __init__(self, other_relays=(), client_per_min=12):
        self.peers = {}            # id -> dict(ws, sp, ep, sig, roles, cap)
        self.pending = {}          # (rid, node) -> (client_id, t)
        self.buckets = {}
        self.others = list(other_relays)
        self.client_per_min = client_per_min
        self.stats = {"jobs": 0, "ok": 0, "err": 0, "since": int(time.time())}

    def app(self):
        a = web.Application()
        a.add_routes([web.get("/ws", self.ws), web.get("/health", self.health)])
        return a

    async def health(self, req):
        nodes = [p for p in self.peers.values() if "node" in p["roles"]]
        return web.json_response({"ok": True, "nodi": len(nodes), "clienti": len(self.peers) - len(nodes), **self.stats})

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
                    me = node_id(m["sp"])
                    self.peers[me] = {"ws": ws, "sp": m["sp"], "ep": m["ep"], "bind": m.get("bind", ""), "roles": m.get("roles", []), "cap": m.get("cap", {})}
                    await ws.send_json({"t": "ok", "id": me, "relays": self.others})
                    continue
                await self.handle(me, m)
        except Exception:
            pass
        finally:
            if me and self.peers.get(me, {}).get("ws") is ws:
                del self.peers[me]
        return ws

    async def handle(self, me, m):
        t, ws = m.get("t"), self.peers[me]["ws"]
        if t == "nodes":
            lst = [{"id": i, "sp": p["sp"], "ep": p["ep"], "bind": p["bind"], "cap": p["cap"]}
                   for i, p in self.peers.items() if "node" in p["roles"] and i != me]
            random.shuffle(lst)
            await ws.send_json({"t": "nodes", "list": lst[:20], "relays": self.others})
        elif t == "job":
            b = self.buckets.setdefault(me, Bucket(self.client_per_min))
            to = self.peers.get(m.get("to"))
            if not b.take():
                await ws.send_json({"t": "err", "rid": m.get("rid"), "m": "troppe richieste"})
            elif not to or len(json.dumps(m)) > MAX_BLOB:
                await ws.send_json({"t": "err", "rid": m.get("rid"), "m": "nodo non disponibile"})
            else:
                self.pending[(m["rid"], m["to"])] = (me, time.time())
                self.stats["jobs"] += 1
                await to["ws"].send_json({"t": "job", "rid": m["rid"], "from": me, "box": m["box"]})
        elif t == "sig":
            b = self.buckets.setdefault(me, Bucket(self.client_per_min * 3))
            to = self.peers.get(m.get("to"))
            if to and b.take() and len(json.dumps(m)) < 20000:
                await to["ws"].send_json({"t": "sig", "from": me, "type": m.get("type"), "sdp": m.get("sdp")})
        elif t in ("res", "err"):
            k = (m.get("rid"), me)
            c = self.pending.pop(k, None)
            if c and c[0] in self.peers:
                self.stats["ok" if t == "res" else "err"] += 1
                await self.peers[c[0]]["ws"].send_json({"t": t, "rid": m["rid"], "from": me, "box": m.get("box"), "m": m.get("m")})
        for k in [k for k, v in self.pending.items() if time.time() - v[1] > 300]:
            self.pending.pop(k, None)


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
async def ask(ident, relays, payload, accept=lambda r: True, k=2, timeout=90, direct=False, ice=(), min_tps=2.0):
    """Prova gli smistatori in ordine. Invia a k nodi, restituisce (risultato, id_nodo) del primo che passa `accept`.
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
                keys, sent = {}, {}
                for n in lst[:k]:
                    rid = os.urandom(8).hex()
                    box, key = seal(n["ep"], rid, data)
                    keys[rid], sent[rid] = key, n["id"]
                    await ws.send_json({"t": "job", "rid": rid, "to": n["id"], "box": box})
                got, deadline = [], time.time() + timeout
                while len(got) < len(sent) and time.time() < deadline:
                    try:
                        msg = await ws.receive(timeout=max(1, deadline - time.time()))
                    except asyncio.TimeoutError:
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        break
                    m = json.loads(msg.data)
                    if m.get("rid") not in keys:
                        continue
                    if m["t"] == "res":
                        try:
                            r = json.loads(open_reply(keys[m["rid"]], m["rid"], m["box"]))
                        except Exception:
                            got.append(None); continue
                        got.append((r, sent[m["rid"]]))
                        if accept(r):
                            return r, sent[m["rid"]]
                    else:
                        got.append(None); last = m.get("m") or "errore del nodo"
                ok = [g for g in got if g]
                if ok:
                    return ok[0]
        except Exception as ex:
            last = str(ex)[:80] or type(ex).__name__
    raise RuntimeError(last)
