"""Quello che l'utente ha detto sulla sua casa (resta sul suo Home Assistant, in /data/casa.json):
correzioni alle entita ("La tua casa") e scelte da ricordare. applica() prepara l'elenco che usano regole e AI."""
import json
import re
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
    return {"entita": d.get("entita") or {}, "memoria": d.get("memoria") or {}, "md": d.get("md") or ""}


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


_FUNZ = ((r"luc|lampad|farett|\bled\b|plafon|abat|striscia", "luce"), (r"tend|tappar|serrand", "tenda"), (r"robot|aspirapolvere", "robot aspirapolvere"),
         (r"\btv\b|televis", "TV"), (r"presa|spina", "presa"), (r"elettrodom|lavatric|forno|stufa|caffe|boiler", "elettrodomestico"))


def bozza(states):
    """Il file della casa generato in locale: l'utente lo corregge e lo salva."""
    voci = [v for v in mappa.costruisci(states) if v["stato"] in ("ok", "dubbia") and v["id"].split(".")[0] not in ("person", "binary_sensor", "sensor")]
    righe = ["# La mia casa", "Una riga per dispositivo: \"- nome in Home Assistant = cos'e / come lo chiami\". Cambia la stanza spostando la riga.",
             "Resta sul tuo Home Assistant.", ""]
    per = {}
    for v in voci:
        per.setdefault(v["stanze"][0] if v["stanze"] else "Senza stanza", []).append(v)
    for r in sorted(per, key=lambda x: (x == "Senza stanza", x)):
        righe.append("## " + r[:1].upper() + r[1:])
        for v in per[r]:
            nota = []
            if v["dubbi"]:
                nota.append("da chiarire: " + "; ".join(v["dubbi"]))
            if len(v["stanze"]) > 1:
                nota.append("anche: " + ", ".join(v["stanze"][1:]))
            righe.append("- %s = %s%s" % (v["nome"], v["funzione"] if v["funzione"] != "interruttore" else "?", ("   # " + " | ".join(nota)) if nota else ""))
        righe.append("")
    righe += ["## Soprannomi", "# - soprannome = nome in Home Assistant", "", "## Non usare", "# - nome in Home Assistant", ""]
    return "\n".join(righe)


def leggi(md, states):
    """Dal file della casa alle correzioni {entity_id: {funzione, stanza, nome, ignora}}."""
    per_nome = {}
    for s in states:
        a = s.get("attributes") or {}
        for k in (a.get("friendly_name"), s["entity_id"]):
            if k:
                per_nome.setdefault(" ".join(micro._n(k).split()), s["entity_id"])
    gia = {v["id"]: v for v in mappa.costruisci(states)}   # la bozza lasciata com'e non cambia nulla
    out, sez = {}, ""
    for riga in (md or "").splitlines():
        riga = riga.split("#", 1)[0].strip() if not riga.strip().startswith("##") else riga.strip()
        if riga.startswith("##"):
            sez = " ".join(micro._n(riga.strip("# ")).split())
            continue
        if not riga.startswith("-"):
            continue
        a, _, b = riga[1:].partition("=")
        a, b = " ".join(micro._n(a).split()), " ".join(micro._n(b).split())
        if sez == "soprannomi":
            e = per_nome.get(b)
            if e and a:
                out.setdefault(e, {})["nome"] = a[:40]
            continue
        e = per_nome.get(a)
        if not e:
            continue
        c = out.setdefault(e, {})
        if sez == "non usare":
            c["ignora"] = True
            continue
        v0 = gia.get(e) or {"stanze": [], "funzione": ""}
        if sez and sez != "senza stanza" and sez not in v0["stanze"]:
            c["stanza"] = sez[:40]
        if b and b != "?":
            f = next((v for rx, v in _FUNZ if re.search(rx, b)), b[:40])
            if f != v0["funzione"] or b != f.lower():
                c["funzione"] = f
            if b not in (f, "luce", "lampada", "tenda", "tv", "robot") and len(b.split()) >= 2:
                c.setdefault("nome", b[:40])   # "luce del bagno": come lo chiami
    return {e: c for e, c in out.items() if c}


def salva_md(testo):
    with _LOCK:
        d = carica()
        d["md"] = str(testo or "")[:20000]
        _salva(d)
        return d


def applica(states, dati, prompt=""):
    """Toglie le entita tecniche o escluse (a meno che la richiesta le chiami per nome) e aggiunge le correzioni."""
    voci = {v["id"]: v for v in mappa.costruisci(states)}
    da_md = leggi(dati.get("md", ""), states)
    p = " ".join(micro._n(prompt).split())
    out = []
    for s in states:
        e = s["entity_id"]
        v, c = voci.get(e), dict(da_md.get(e) or {}, **(dati["entita"].get(e) or {}))
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
