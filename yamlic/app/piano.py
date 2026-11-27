"""Piano di automazione (0.4): il modello scrive un piano breve in JSON, il codice lo compila in YAML.

Formato (chiavi corte per risparmiare token):
{"n": "alias",
 "t": [trigger...],   {"ora":"07:00"} {"sole":"tramonto","off":"-00:30:00"} {"stato":"binary_sensor.e3","a":"on","da":"off","per":"00:10:00","attr":"x"}
                      {"soglia":"sensor.e6","sopra":26,"sotto":10,"per":"00:05:00","attr":"x"} {"ogni":"/5","u":"minutes"} {"manuale":1}  (+ "id")
 "c": [condizione...],{"stato":e,"e":"on","per":..} {"soglia":e,"sopra":..,"sotto":..} {"dopo":"22:00","prima":"06:00","giorni":["mon"]}
                      {"sole_dopo":"tramonto","sole_prima":"alba"} {"buio":1} {"o":[cond...]} {"trigger":"id"}
 "a": [azione...]}    {"fai":"light.turn_on","su":["light.e1"],"dati":{...}} {"attendi":"00:05:00"}
                      {"fai":"notify.e2","dati":{"message":"..."}} {"scegli":[{"se":[cond],"fai":[azione]}],"altrimenti":[azione]}
 "m": "single"}       (modo, facoltativo)
"su" puo essere anche un descrittore risolto dal codice: {"cosa":"luci","dove":"cucina","tranne":["light.e3"]}.
Tutto cio che il piano non sa esprimere passa come {"yaml": {...}} (uscita di riserva), ma il compilatore lo valida comunque.
"""
import json
import re

import yaml

SOLE = {"tramonto": "sunset", "alba": "sunrise"}
SOLE_R = {v: k for k, v in SOLE.items()}
MODI = {"single", "restart", "queued", "parallel"}


class PianoErrato(ValueError):
    pass


def _ora(s):
    s = str(s).strip()
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(?::(\d{2}))?", s)
    if not m:
        raise PianoErrato("orario non valido: %s" % s)
    h, mi, se = int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)
    if h > 23 or mi > 59 or se > 59:
        raise PianoErrato("orario non valido: %s" % s)
    return "%02d:%02d:%02d" % (h, mi, se)


def _durata(s):
    if isinstance(s, dict):
        return {k: int(v) for k, v in s.items() if k in ("hours", "minutes", "seconds", "days")}
    return _ora(s) if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", str(s)) else str(s)


def _eid(x, entita):
    if not isinstance(x, str) or not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", x):
        raise PianoErrato("entita non valida: %r" % (x,))
    if entita is not None and x not in entita and not x.startswith(("zone.", "sun.")):
        raise PianoErrato("entita non in elenco: %s" % x)
    return x


def _uno_o_lista(v):
    return v[0] if isinstance(v, list) and len(v) == 1 else v


# ---------- compilatore: piano -> dict YAML ----------
def _trigger(t, E):
    if not isinstance(t, dict):
        raise PianoErrato("trigger non valido")
    o = {}
    if "ora" in t:
        o = {"trigger": "time", "at": _ora(t["ora"])}
    elif "sole" in t:
        if t["sole"] not in SOLE:
            raise PianoErrato("evento solare non valido: %s" % t["sole"])
        o = {"trigger": "sun", "event": SOLE[t["sole"]]}
        if t.get("off"):
            o["offset"] = str(t["off"])
    elif "stato" in t:
        o = {"trigger": "state", "entity_id": _uno_o_lista([_eid(e, E) for e in (t["stato"] if isinstance(t["stato"], list) else [t["stato"]])])}
        for k, kk in (("attr", "attribute"), ("da", "from"), ("a", "to")):
            if k in t:
                o[kk] = t[k]
    elif "soglia" in t:
        o = {"trigger": "numeric_state", "entity_id": _uno_o_lista([_eid(e, E) for e in (t["soglia"] if isinstance(t["soglia"], list) else [t["soglia"]])])}
        if "attr" in t:
            o["attribute"] = t["attr"]
        for k, kk in (("sopra", "above"), ("sotto", "below")):
            if k in t:
                o[kk] = t[k]
        if "above" not in o and "below" not in o:
            raise PianoErrato("soglia senza sopra/sotto")
    elif "ogni" in t:
        o = {"trigger": "time_pattern", {"hours": "hours", "minutes": "minutes", "seconds": "seconds"}.get(t.get("u", "minutes"), "minutes"): str(t["ogni"])}
    elif "manuale" in t:
        o = {"trigger": "event", "event_type": "yamlic_manuale"}
    elif "yaml" in t and isinstance(t["yaml"], dict):
        o = dict(t["yaml"])
    else:
        raise PianoErrato("trigger sconosciuto: %s" % json.dumps(t, ensure_ascii=False))
    if "per" in t and o.get("trigger") in ("state", "numeric_state"):
        o["for"] = _durata(t["per"])
    if "id" in t:
        o["id"] = str(t["id"])
    return o


def _cond(c, E):
    if not isinstance(c, dict):
        raise PianoErrato("condizione non valida")
    if "o" in c or "e_tutte" in c or "non" in c:
        k, kk = next((k, kk) for k, kk in (("o", "or"), ("e_tutte", "and"), ("non", "not")) if k in c)
        return {"condition": kk, "conditions": [_cond(x, E) for x in c[k]]}
    if "buio" in c:
        return {"condition": "or", "conditions": [{"condition": "sun", "after": "sunset"}, {"condition": "sun", "before": "sunrise"}]}
    if "stato" in c:
        o = {"condition": "state", "entity_id": _uno_o_lista([_eid(e, E) for e in (c["stato"] if isinstance(c["stato"], list) else [c["stato"]])]), "state": c.get("e", "on")}
        if "attr" in c:
            o["attribute"] = c["attr"]
        if "per" in c:
            o["for"] = _durata(c["per"])
        return o
    if "soglia" in c:
        o = {"condition": "numeric_state", "entity_id": _eid(c["soglia"], E)}
        if "attr" in c:
            o["attribute"] = c["attr"]
        for k, kk in (("sopra", "above"), ("sotto", "below")):
            if k in c:
                o[kk] = c[k]
        return o
    if "sole_dopo" in c or "sole_prima" in c:
        o = {"condition": "sun"}
        if "sole_dopo" in c:
            o["after"] = SOLE.get(c["sole_dopo"], c["sole_dopo"])
        if "sole_prima" in c:
            o["before"] = SOLE.get(c["sole_prima"], c["sole_prima"])
        for k, kk in (("off_dopo", "after_offset"), ("off_prima", "before_offset")):
            if k in c:
                o[kk] = c[k]
        return o
    if "dopo" in c or "prima" in c or "giorni" in c:
        o = {"condition": "time"}
        if "dopo" in c:
            o["after"] = _ora(c["dopo"])
        if "prima" in c:
            o["before"] = _ora(c["prima"])
        if "giorni" in c:
            g = c["giorni"]
            if any(x not in ("mon", "tue", "wed", "thu", "fri", "sat", "sun") for x in (g if isinstance(g, list) else [g])):
                raise PianoErrato("giorno non valido")
            o["weekday"] = g
        return o
    if "trigger" in c:
        return {"condition": "trigger", "id": str(c["trigger"])}
    if "yaml" in c and isinstance(c["yaml"], dict):
        return dict(c["yaml"])
    raise PianoErrato("condizione sconosciuta: %s" % json.dumps(c, ensure_ascii=False))


def _azione(a, E, risolvi):
    if not isinstance(a, dict):
        raise PianoErrato("azione non valida")
    if "attendi" in a:
        return {"delay": _durata(a["attendi"])}
    if "scegli" in a:
        o = {"choose": [{"conditions": [_cond(c, E) for c in r.get("se", [])], "sequence": [_azione(x, E, risolvi) for x in r.get("fai", [])]} for r in a["scegli"]]}
        if a.get("altrimenti"):
            o["default"] = [_azione(x, E, risolvi) for x in a["altrimenti"]]
        return o
    if "fai" in a:
        s = str(a["fai"])
        if not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", s):
            raise PianoErrato("servizio non valido: %s" % s)
        o = {"action": s}
        su = a.get("su")
        if isinstance(su, dict) and risolvi:
            su = risolvi(su, s)
        if su:
            ids = [_eid(e, E) for e in (su if isinstance(su, list) else [su])]
            o["target"] = {"entity_id": _uno_o_lista(ids)}
        if a.get("dati"):
            if not isinstance(a["dati"], dict):
                raise PianoErrato("dati non validi")
            o["data"] = a["dati"]
        return o
    if "yaml" in a and isinstance(a["yaml"], dict):
        return dict(a["yaml"])
    raise PianoErrato("azione sconosciuta: %s" % json.dumps(a, ensure_ascii=False))


def compila(piano, entita=None, risolvi=None):
    """Piano (dict o testo JSON) -> testo YAML. entita: insieme degli entity_id ammessi (None = non controllare).
    risolvi(descrittore, servizio) -> lista di entity_id, per i bersagli descritti a parole."""
    if isinstance(piano, str):
        try:
            piano = json.loads(piano)
        except Exception as ex:
            raise PianoErrato("JSON non valido: %s" % ex)
    if not isinstance(piano, dict):
        raise PianoErrato("il piano non e un oggetto")
    E = set(entita) if entita is not None else None
    t = [_trigger(x, E) for x in (piano.get("t") or [])] or [{"trigger": "event", "event_type": "yamlic_manuale"}]
    a = [_azione(x, E, risolvi) for x in (piano.get("a") or [])]
    if not a:
        raise PianoErrato("nessuna azione")
    cfg = {"alias": str(piano.get("n") or "Automazione Yamlic")[:100], "mode": piano.get("m") if piano.get("m") in MODI else "single",
           "triggers": t, "conditions": [_cond(x, E) for x in (piano.get("c") or [])], "actions": a}
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n")


# ---------- convertitore: YAML -> piano (per il dataset) ----------
def _t_da(t):
    k = t.get("trigger")
    p = {}
    if k == "time" and isinstance(t.get("at"), str) and re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", t["at"]) and set(t) <= {"trigger", "at", "id"}:
        h, m, *s = t["at"].split(":")
        p = {"ora": "%s:%s" % (h.zfill(2), m) + (":" + s[0] if s and s[0] != "00" else "")}
    elif k == "sun" and t.get("event") in SOLE_R and set(t) <= {"trigger", "event", "offset", "id"}:
        p = {"sole": SOLE_R[t["event"]]}
        if t.get("offset"):
            p["off"] = t["offset"]
    elif k == "state" and set(t) <= {"trigger", "entity_id", "to", "from", "for", "attribute", "id"}:
        p = {"stato": t["entity_id"]}
        for kk, k2 in (("attribute", "attr"), ("from", "da"), ("to", "a"), ("for", "per")):
            if kk in t:
                p[k2] = t[kk]
    elif k == "numeric_state" and set(t) <= {"trigger", "entity_id", "above", "below", "for", "attribute", "id"}:
        p = {"soglia": t["entity_id"]}
        for kk, k2 in (("attribute", "attr"), ("above", "sopra"), ("below", "sotto"), ("for", "per")):
            if kk in t:
                p[k2] = t[kk]
    elif k == "time_pattern" and len([x for x in t if x in ("hours", "minutes", "seconds")]) == 1 and set(t) <= {"trigger", "hours", "minutes", "seconds", "id"}:
        u = next(x for x in ("hours", "minutes", "seconds") if x in t)
        p = {"ogni": str(t[u]), "u": u}
    elif k == "event" and t.get("event_type") == "yamlic_manuale":
        p = {"manuale": 1}
    else:
        return {"yaml": t}
    if "id" in t:
        p["id"] = t["id"]
    return p


def _c_da(c):
    k = c.get("condition")
    if k in ("or", "and", "not") and isinstance(c.get("conditions"), list) and set(c) == {"condition", "conditions"}:
        if k == "or" and c["conditions"] == [{"condition": "sun", "after": "sunset"}, {"condition": "sun", "before": "sunrise"}]:
            return {"buio": 1}
        return {{"or": "o", "and": "e_tutte", "not": "non"}[k]: [_c_da(x) for x in c["conditions"]]}
    if k == "state" and set(c) <= {"condition", "entity_id", "state", "for", "attribute"}:
        p = {"stato": c["entity_id"], "e": c.get("state")}
        if "attribute" in c:
            p["attr"] = c["attribute"]
        if "for" in c:
            p["per"] = c["for"]
        return p
    if k == "numeric_state" and isinstance(c.get("entity_id"), str) and set(c) <= {"condition", "entity_id", "above", "below", "attribute"}:
        p = {"soglia": c["entity_id"]}
        for kk, k2 in (("attribute", "attr"), ("above", "sopra"), ("below", "sotto")):
            if kk in c:
                p[k2] = c[kk]
        return p
    if k == "sun" and set(c) <= {"condition", "after", "before", "after_offset", "before_offset"}:
        p = {}
        for kk, k2 in (("after", "sole_dopo"), ("before", "sole_prima")):
            if kk in c:
                p[k2] = SOLE_R.get(c[kk], c[kk])
        for kk, k2 in (("after_offset", "off_dopo"), ("before_offset", "off_prima")):
            if kk in c:
                p[k2] = c[kk]
        return p
    if k == "time" and set(c) <= {"condition", "after", "before", "weekday"} and all(re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", str(c[x])) for x in ("after", "before") if x in c):
        p = {}
        for kk, k2 in (("after", "dopo"), ("before", "prima")):
            if kk in c:
                h, m, *s = str(c[kk]).split(":")
                p[k2] = "%s:%s" % (h.zfill(2), m) + (":" + s[0] if s and s[0] != "00" else "")
        if "weekday" in c:
            p["giorni"] = c["weekday"]
        return p
    if k == "trigger" and set(c) == {"condition", "id"}:
        return {"trigger": c["id"]}
    return {"yaml": c}


def _a_da(a):
    if isinstance(a, dict) and set(a) == {"delay"}:
        return {"attendi": a["delay"]}
    if isinstance(a, dict) and "choose" in a and set(a) <= {"choose", "default"} and all(isinstance(r, dict) and set(r) <= {"conditions", "sequence"} for r in a["choose"]):
        p = {"scegli": [{"se": [_c_da(c) for c in (r.get("conditions") or [])], "fai": [_a_da(x) for x in (r.get("sequence") or [])]} for r in a["choose"]]}
        if a.get("default"):
            p["altrimenti"] = [_a_da(x) for x in a["default"]]
        return p
    if isinstance(a, dict) and "action" in a and set(a) <= {"action", "target", "data"} and (not a.get("target") or set(a["target"]) == {"entity_id"}):
        p = {"fai": a["action"]}
        if a.get("target"):
            p["su"] = a["target"]["entity_id"] if isinstance(a["target"]["entity_id"], list) else [a["target"]["entity_id"]]
        if a.get("data"):
            p["dati"] = a["data"]
        return p
    return {"yaml": a}


def da_yaml(y):
    """Testo YAML (automazione) -> piano dict. Le parti non esprimibili restano come {"yaml": ...}."""
    cfg = yaml.safe_load(y) if isinstance(y, str) else y
    if not isinstance(cfg, dict) or "use_blueprint" in cfg:
        return None
    p = {"n": cfg.get("alias", "")}
    tr = [_t_da(t) for t in (cfg.get("triggers") or []) if isinstance(t, dict)]
    if tr and tr != [{"manuale": 1}]:
        p["t"] = tr
    if cfg.get("conditions"):
        p["c"] = [_c_da(c) for c in cfg["conditions"] if isinstance(c, dict)]
    p["a"] = [_a_da(a) for a in (cfg.get("actions") or [])]
    if cfg.get("mode") and cfg["mode"] != "single":
        p["m"] = cfg["mode"]
    return p


def riserve(p):
    """Quante parti del piano usano l'uscita di riserva {"yaml": ...}."""
    return json.dumps(p, ensure_ascii=False).count('{"yaml":')
