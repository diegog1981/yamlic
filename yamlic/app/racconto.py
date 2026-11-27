"""Riassunto in italiano dello YAML, per controllare cosa ha capito Yamlic senza leggere il codice."""
import yaml

import micro

_STATI = {"on": "acceso", "off": "spento", "open": "aperto", "closed": "chiuso", "home": "a casa", "not_home": "fuori", "below_horizon": "sotto l'orizzonte (buio)",
          "above_horizon": "sopra l'orizzonte (giorno)", "standby": "in standby", "unavailable": "non disponibile"}
_GIORNI = {v: k for k, v in micro.GIORNI_IT.items()}


def _durata(d):
    if isinstance(d, dict):
        return " e ".join("%s %s" % (v, {"hours": "ore", "minutes": "minuti", "seconds": "secondi"}.get(k, k)) for k, v in d.items())
    return str(d)


def _nomi(casa, x):
    return ", ".join(casa.nome(str(e)) for e in micro._lista(x))


def _stato(x):
    return " o ".join(_STATI.get(str(s), str(s)) for s in micro._lista(x))


def _soglie(n):
    return " e ".join("%s %s" % (t, n[k]) for k, t in (("above", "sopra"), ("below", "sotto")) if k in n)


def trigger(t, casa):
    k = t.get("trigger") or t.get("platform")
    if k == "time":
        return "alle " + ", ".join(str(a)[:5] for a in micro._lista(t.get("at")))
    if k == "sun":
        o = str(t.get("offset") or "")
        return ("al tramonto" if t.get("event") == "sunset" else "all'alba") + (" (scarto %s)" % o if o else "")
    if k in ("state", "numeric_state"):
        e = t.get("entity_id")
        if e == "zone.home":
            testo = "la casa resta vuota" if "below" in t else "arriva qualcuno in casa"
        elif k == "numeric_state":
            testo = "%s va %s" % (_nomi(casa, e), _soglie(t))
        else:
            testo = "%s %s" % (_nomi(casa, e), "diventa " + _stato(t["to"]) if "to" in t else "cambia da " + _stato(t["from"]) if "from" in t else "cambia")
        return testo + (" per " + _durata(t["for"]) if t.get("for") else "")
    return "evento %s" % k


def condizione(c, casa):
    if not isinstance(c, dict):
        return "condizione scritta a codice"
    k = c.get("condition")
    if k is None:
        for kk in ("or", "and", "not"):
            if kk in c:
                k, c = kk, {"conditions": c[kk]}
    if k in ("and", "or", "not"):
        dentro = (" oppure " if k == "or" else " e ").join(condizione(x, casa) for x in c.get("conditions") or [])
        return "NON (%s)" % dentro if k == "not" else "(%s)" % dentro
    if k == "time":
        pezzi = []
        if c.get("after"):
            pezzi.append("dopo le " + str(c["after"])[:5])
        if c.get("before"):
            pezzi.append("prima delle " + str(c["before"])[:5])
        if c.get("weekday"):
            pezzi.append("solo " + ", ".join(_GIORNI.get(g, g) for g in micro._lista(c["weekday"])))
        return " ".join(pezzi)
    if k == "sun":
        nomi = {"sunset": "il tramonto", "sunrise": "l'alba"}
        return " e ".join("%s %s" % (t, nomi.get(c[x], c[x])) for x, t in (("after", "dopo"), ("before", "prima di")) if x in c)
    if k == "state":
        if c.get("entity_id") == "sun.sun":
            return "il sole e " + _stato(c.get("state"))
        return "%s e %s" % (_nomi(casa, c.get("entity_id")), _stato(c.get("state")))
    if k == "numeric_state":
        if c.get("entity_id") == "zone.home":
            return "non c'e nessuno in casa" if "below" in c else "c'e qualcuno in casa"
        return "%s %s" % (_nomi(casa, c.get("entity_id")), _soglie(c))
    if k == "trigger":
        return ""
    return "condizione %s" % k


def passi(lst, casa, rientro=0):
    out, pre = [], "  " * rientro
    for a in lst or []:
        if not isinstance(a, dict):
            continue
        if "action" in a or "service" in a:
            nome = str(a.get("action") or a.get("service"))
            if nome.startswith("notify."):
                out.append(pre + "mando la notifica \"%s\"" % (a.get("data") or {}).get("message", ""))
            elif nome.startswith("assist_satellite."):
                out.append(pre + "annuncio \"%s\" su %s" % ((a.get("data") or {}).get("message", ""), _nomi(casa, (a.get("target") or {}).get("entity_id"))))
            else:
                out.append(pre + micro.descrivi([a], casa))
        elif "delay" in a:
            out.append(pre + "aspetto " + _durata(a["delay"]))
        elif "if" in a:
            out.append(pre + "se " + " e ".join(condizione(c, casa) for c in micro._lista(a["if"])) + ":")
            out += passi(a.get("then"), casa, rientro + 1)
            if a.get("else"):
                out.append(pre + "altrimenti:")
                out += passi(a["else"], casa, rientro + 1)
        elif "choose" in a:
            for ramo in micro._lista(a["choose"]):
                conds = micro._lista(ramo.get("conditions"))
                quale = [str(c.get("id")) for c in conds if isinstance(c, dict) and c.get("condition") == "trigger"]
                altre = [x for x in (condizione(c, casa) for c in conds) if x]
                testa = " e ".join((["nel caso %s" % ", ".join(q.replace("ramo", "") for q in quale)] if quale else []) + altre)
                out.append(pre + testa + ":")
                out += passi(ramo.get("sequence"), casa, rientro + 1)
        else:
            out.append(pre + "altro passaggio: " + ", ".join(a.keys()))
    return out


def racconta(y, states):
    try:
        cfg = yaml.safe_load(y)
        casa = micro.Casa(states)
        trig = micro._lista(cfg.get("triggers") or cfg.get("trigger"))
        con_id = any(t.get("id") for t in trig)
        righe = ["Quando:"] + ["  " + ("%s) " % str(t["id"]).replace("ramo", "") if con_id and t.get("id") else "") + trigger(t, casa) for t in trig]
        conds = [x for x in (condizione(c, casa) for c in micro._lista(cfg.get("conditions") or cfg.get("condition"))) if x]
        if conds:
            righe += ["Solo se:"] + ["  " + c for c in conds]
        righe += ["Faccio:"] + passi(micro._lista(cfg.get("actions") or cfg.get("action")), casa, 1)
        return "\n".join(righe)
    except Exception:
        return ""
