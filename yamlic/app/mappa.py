"""Mappa della casa (sola lettura): per ogni entita stanza, funzione e quanto ne siamo sicuri.
stato: ok = chiara; dubbia = da chiedere una volta all'utente; tecnica = da non usare nelle automazioni."""
import re

import core
import micro

DOMINI = ("light", "switch", "fan", "cover", "media_player", "climate", "lock", "vacuum", "input_boolean", "assist_satellite", "binary_sensor", "sensor", "person")
_TECNICA = re.compile(r"accesso a internet|internet access|wi.?fi|debug|solo audio|salvaschermo|kiosk|manutenzione|microphone|\bmute\b|wake sound|boot sound|night mode|"
                      r"countdown|amplifier|auto.?update|non disturbare|do not disturb|status.?ring|\bschermo\b|rilevamento del movimento|calibration|cloud|\bkey ?\d\b|remote ui|child lock|led indicator|identify|restart|reboot|firmware")
_TECNICA_CLASSE = {"connectivity", "problem", "battery", "update", "plug", "running"}
_CLASSI = {"motion": "movimento", "occupancy": "presenza", "presence": "presenza", "door": "porta", "window": "finestra", "garage_door": "garage", "opening": "apertura",
           "moisture": "perdita acqua", "smoke": "fumo", "gas": "gas", "vibration": "vibrazione", "sound": "rumore", "light": "luminosita",
           "temperature": "temperatura", "humidity": "umidita", "power": "consumo", "illuminance": "luminosita"}
_DOMINIO = {"light": "luce", "fan": "ventilatore", "climate": "clima", "lock": "serratura", "vacuum": "robot aspirapolvere", "assist_satellite": "assistente vocale",
            "input_boolean": "interruttore virtuale", "person": "persona", "cover": "tenda"}
_UNICA = {"soggiorno": "soggiorno", "salotto": "soggiorno", "salone": "soggiorno", "living": "soggiorno", "esterno": "giardino"}
_ELETTRO = re.compile(r"lavatric|lavastovigli|asciugatric|forno|bollitor|caffe|frigo|stufa|boiler|pompa|caldaia|irrigaz|ventol|deumid")


def stanze(testo):
    return sorted({_UNICA.get(r, r) for r in micro.STANZE if r not in ("isola", "tavolo") and re.search(r"\b%s\b" % r, testo)})


def funzione(eid, nome, classe):
    """(funzione, dubbio) oppure None se tecnica / non utile."""
    dom, t = eid.split(".")[0], micro._n(eid.split(".")[1].replace("_", " ") + " " + nome)
    if _TECNICA.search(micro._n(nome)) or classe in _TECNICA_CLASSE:
        return None
    if dom == "sensor":
        if classe not in ("temperature", "humidity", "power", "illuminance") or re.search(r"batter|\bcpu\b|chip|processor", t):
            return None
        return _CLASSI[classe], ""
    if dom == "binary_sensor":
        return (_CLASSI[classe], "") if classe in _CLASSI else None
    if dom == "cover":
        return {"garage": "garage", "gate": "cancello", "door": "porta motorizzata", "window": "finestra motorizzata"}.get(classe, "tenda"), ""
    if dom == "media_player":
        if classe == "tv" or re.search(r"\btv\b|televis", t):
            return "TV", ""
        return "audio", ""
    if dom in _DOMINIO:
        return _DOMINIO[dom], ""
    if re.search(r"cancell|garage|portone|portoncino", t):
        return "cancello/portone", ""
    if _ELETTRO.search(t):
        return "elettrodomestico", ""
    if core.LIGHT_LIKE.search(t):
        return "luce", ""
    return "interruttore", "cosa comanda?"


def costruisci(states):
    out = []
    for s in states:
        eid = s["entity_id"]
        dom = eid.split(".")[0]
        if dom not in DOMINI:
            continue
        a = s.get("attributes") or {}
        nome, area = a.get("friendly_name") or eid, a.get("_area") or ""
        f = funzione(eid, nome, a.get("device_class"))
        v = {"id": eid, "nome": nome, "area": area, "funzione": f[0] if f else "", "stanze": [], "stato": "tecnica", "dubbi": []}
        out.append(v)
        if not f:
            continue
        da_nome, da_area = stanze(micro._n(nome)), stanze(micro._n(area))
        v["stanze"] = sorted(set(da_nome) | set(da_area))
        v["stato"] = "ok"
        if s.get("state") == "unavailable":
            v["stato"] = "non disponibile"
            continue
        if f[1]:
            v["dubbi"].append(f[1])
        if not v["stanze"] and dom not in ("person", "input_boolean") and f[0] != "cancello/portone":
            v["dubbi"].append("in quale stanza?")
        elif da_nome and da_area and not set(da_nome) & set(da_area):
            v["dubbi"].append("nome e area dicono stanze diverse")
    # doppioni: piu TV (o impianti audio con lo stesso nome) nella stessa stanza
    gruppi = {}
    for v in out:
        if v["stato"] == "ok" and (v["funzione"] == "TV" or "soundbar" in micro._n(v["id"] + v["nome"])):
            k = (v["funzione"], tuple(v["stanze"]), "soundbar" if "soundbar" in micro._n(v["id"] + v["nome"]) else v["funzione"])
            gruppi.setdefault(k, []).append(v)
    for g in gruppi.values():
        if len(g) > 1:
            for v in g:
                v["dubbi"].append("doppione: quale usare? (%s)" % ", ".join(x["id"] for x in g if x is not v))
    for v in out:
        if v["dubbi"]:
            v["stato"] = "dubbia"
    return out


def riassunto(voci):
    conta, per_stanza = {}, {}
    for v in voci:
        conta[v["stato"]] = conta.get(v["stato"], 0) + 1
        if v["stato"] == "ok":
            for r in v["stanze"] or ["(senza stanza)"]:
                per_stanza.setdefault(r, {}).setdefault(v["funzione"], []).append(v["nome"])
    return {"conta": conta, "per_stanza": per_stanza,
            "dubbie": [{"id": v["id"], "nome": v["nome"], "funzione": v["funzione"], "dubbi": v["dubbi"]} for v in voci if v["stato"] == "dubbia"]}
