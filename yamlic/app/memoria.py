"""Quello che l'utente ha detto sulla sua casa (resta sul suo Home Assistant, in /data/casa.json):
correzioni alle entita ("La tua casa") e scelte da ricordare. applica() prepara l'elenco che usano regole e AI."""
import json
import os
import threading

import mappa
import micro

FILE = os.environ.get("YAMLIC_CASA", "/data/casa.json")
_LOCK = threading.Lock()
_FILTRABILI = ("light", "switch", "media_player", "cover", "binary_sensor")


def carica():
    try:
        with open(FILE, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    return {"entita": d.get("entita") or {}, "memoria": d.get("memoria") or {}}


def _salva(d):
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, FILE)


def correggi(entita):
    """entita: {entity_id: {funzione, stanza, nome, ignora} oppure None per tornare all'automatico}."""
    with _LOCK:
        d = carica()
        for e, v in (entita or {}).items():
            pulito = {}
            if isinstance(v, dict):
                pulito = {k: str(v[k]).strip()[:40] for k in ("funzione", "stanza", "nome") if str(v.get(k) or "").strip()}
                if v.get("ignora"):
                    pulito["ignora"] = True
            if pulito:
                d["entita"][str(e)] = pulito
            else:
                d["entita"].pop(str(e), None)
        _salva(d)
        return d


def ricorda(risposte):
    nuove = {str(v["ricorda"]): v["entita"] for v in (risposte or {}).values() if isinstance(v, dict) and v.get("ricorda") and v.get("entita")}
    if not nuove:
        return
    with _LOCK:
        d = carica()
        d["memoria"].update(nuove)
        _salva(d)


def dimentica(chiave=None):
    with _LOCK:
        d = carica()
        if chiave:
            d["memoria"].pop(chiave, None)
        else:
            d["memoria"] = {}
        _salva(d)


def applica(states, dati, prompt=""):
    """Toglie le entita tecniche o escluse (a meno che la richiesta le chiami per nome) e aggiunge le correzioni."""
    voci = {v["id"]: v for v in mappa.costruisci(states)}
    p = " ".join(micro._n(prompt).split())
    out = []
    for s in states:
        e = s["entity_id"]
        v, c = voci.get(e), dati["entita"].get(e) or {}
        a = dict(s.get("attributes") or {})
        nome = " ".join(micro._n(a.get("friendly_name") or "").split())
        nominata = len(nome) >= 8 and nome in p
        if c.get("ignora") and not nominata:
            continue
        if v and v["stato"] == "tecnica" and not c and not nominata and e.split(".")[0] in _FILTRABILI:
            continue
        if c.get("funzione"):
            a["_funzione"] = c["funzione"]
        if c.get("stanza"):
            a["_area"] = c["stanza"]
        if c.get("nome"):
            a["_soprannome"] = c["nome"]
        out.append(dict(s, attributes=a) if c else s)
    return out
