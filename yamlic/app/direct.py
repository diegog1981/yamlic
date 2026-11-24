"""Collegamento diretto cliente-nodo con WebRTC (hole punching). SPERIMENTALE, spento di default.
La segnalazione passa dallo smistatore; poi il traffico (sempre cifrato) va direttamente tra i due.
Svantaggio: i due vedono l'IP l'uno dell'altro, per questo e un'opzione. Se non riesce, si usa lo smistatore."""
import asyncio, json, os
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription


def _cfg(ice):
    return RTCConfiguration([RTCIceServer(urls=u) if isinstance(u, str) else RTCIceServer(**u) for u in ice])


async def node_answer(ws, ident, m, handler, busy, ice, clean_payload, open_box, seal_reply):
    pc = RTCPeerConnection(_cfg(ice))

    @pc.on("datachannel")
    def on_dc(ch):
        @ch.on("message")
        def on_msg(raw):
            async def go():
                rid = "?"
                try:
                    j = json.loads(raw); rid = j["rid"]
                    if busy():
                        raise RuntimeError("nodo occupato")
                    plain, key = open_box(ident, rid, j["box"])
                    res = await asyncio.get_running_loop().run_in_executor(None, handler, clean_payload(json.loads(plain)))
                    ch.send(json.dumps({"rid": rid, "box": seal_reply(key, rid, json.dumps(res).encode())}))
                except Exception as ex:
                    ch.send(json.dumps({"rid": rid, "err": str(ex)[:80]}))
            asyncio.ensure_future(go())

    @pc.on("connectionstatechange")
    async def st():
        if pc.connectionState in ("failed", "closed"):
            await pc.close()

    await pc.setRemoteDescription(RTCSessionDescription(sdp=m["sdp"], type="offer"))
    await pc.setLocalDescription(await pc.createAnswer())
    await ws.send_json({"t": "sig", "to": m["from"], "type": "answer", "sdp": pc.localDescription.sdp})
    await asyncio.sleep(60)
    await pc.close()


async def client_direct(ws, node, data, ice, seal, open_reply, timeout=8):
    pc = RTCPeerConnection(_cfg(ice))
    try:
        ch = pc.createDataChannel("yamlic")
        opened, reply = asyncio.Event(), asyncio.get_running_loop().create_future()
        rid = os.urandom(8).hex()
        box, key = seal(node["ep"], rid, data)

        @ch.on("open")
        def _o():
            opened.set()

        @ch.on("message")
        def _m(raw):
            if not reply.done():
                reply.set_result(json.loads(raw))

        await pc.setLocalDescription(await pc.createOffer())
        await ws.send_json({"t": "sig", "to": node["id"], "type": "offer", "sdp": pc.localDescription.sdp})
        while True:
            import aiohttp
            msg = await ws.receive(timeout=timeout)
            if msg.type != aiohttp.WSMsgType.TEXT:
                raise ConnectionError("chiuso")
            j = json.loads(msg.data)
            if j.get("t") == "sig" and j.get("type") == "answer" and j.get("from") == node["id"]:
                await pc.setRemoteDescription(RTCSessionDescription(sdp=j["sdp"], type="answer"))
                break
        await asyncio.wait_for(opened.wait(), timeout)
        ch.send(json.dumps({"rid": rid, "box": box}))
        r = await asyncio.wait_for(reply, 90)
        if "box" not in r:
            raise RuntimeError(r.get("err", "errore"))
        return json.loads(open_reply(key, rid, r["box"]))
    finally:
        await pc.close()
