"""Controllo finale in locale: lo YAML viene riletto e confrontato con la richiesta, prima di darlo all'utente.
Ogni voce e un errore scritto in modo che si possa rimandare al modello per la correzione."""
import json
import re

import yaml

import micro

_UNITA = {"second": "seconds", "minut": "minutes", "or": "hours"}


_VUOLE_ACCENDERE = re.compile(r"accend|attiv|avvia|avvio|illumin|metti|imposta|fai partire|riprend|\bluc[ei] a|%|apri")

_VUOLE_POSIZIONE = re.compile(r"%|per ?cento|\bmeta\b|posizione|\b(?:al|a) \d{1,3}\b(?![:.]\d| minut| second| or)")


def ripulisci(prompt, y, states):
    """Correzioni sicure: toglie le accensioni non richieste e sostituisce una TV spenta/inesistente con quella vera."""
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y, []
    if not isinstance(cfg, dict) or not isinstance(cfg.get("actions"), list):
        return y, []
    p, avv, casa = " ".join(micro._n(prompt).split()), [], micro.Casa(states)
    stanze = [r for r in micro.STANZE if re.search(r"\btv\b[^,.]{0,15}\b%s\b" % r, p)]
    tv = casa.tv(stanze or ["soggiorno", "salotto"])
    if tv and re.search(r"\b(tv|televisione|televisore)\b", p):
        for n in list(_passi(cfg)):
            v = n.get("entity_id")
            for e in _lista(v):
                if isinstance(e, str) and e.startswith("media_player.") and re.search(r"tv|televis", e) and e != tv[0] \
                        and casa.by.get(e, {}).get("state", "unavailable") == "unavailable":
                    n["entity_id"] = tv[0] if not isinstance(v, list) else [tv[0] if x == e else x for x in v]
                    avv.append("%s non disponibile: uso %s" % (e, tv[0]))
    if not _VUOLE_ACCENDERE.search(p):
        def pulisci(lst):
            out = []
            for a in lst:
                if isinstance(a, dict):
                    ac = str(a.get("action", ""))
                    if ac.endswith(".turn_on") and ac.split(".")[0] in ("light", "switch", "fan", "media_player"):
                        avv.append("tolta un'accensione non richiesta")
                        continue
                    for k in ("then", "else", "sequence", "default", "choose"):
                        if isinstance(a.get(k), list):
                            a[k] = pulisci(a[k])
                out.append(a)
            return out
        nuove = pulisci(cfg["actions"])
        if nuove:
            cfg["actions"] = nuove
    if re.search(r"\bbuio\b", p):   # "se e buio": after sunset da solo vale solo fino a mezzanotte
        for n in list(_passi(cfg)):
            if n.get("condition") == "sun" and not any(k.endswith("offset") for k in n):
                n.clear(); n.update({"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"})
                avv.append("'buio' = sole sotto l'orizzonte (sun.sun)")
    if not _VUOLE_POSIZIONE.search(p):
        for n in list(_passi(cfg)):
            pos = (n.get("data") or {}).get("position") if n.get("action") == "cover.set_cover_position" else None
            if pos in (0, 100):
                n["action"] = "cover.open_cover" if pos else "cover.close_cover"
                del n["data"]
                avv.append("posizione %d scritta come apri/chiudi" % pos)
    if not avv:
        return y, []
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n"), list(dict.fromkeys(avv))


def _passi(x):
    """Tutti i dizionari contenuti nello YAML."""
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from _passi(v)
    elif isinstance(x, list):
        for v in x:
            yield from _passi(v)


def _lista(x):
    return x if isinstance(x, list) else [x] if x else []


def controlla(prompt, y, states):
    try:
        docs = [d for d in yaml.safe_load_all(y) if d]
    except Exception:
        return ["lo YAML non e leggibile"]
    if not docs or not all(isinstance(d, dict) for d in docs):
        return ["manca un'automazione valida"]
    p = micro.parlato(" ".join(micro._n(prompt).split()))
    # il testo di notifiche e annunci non conta come richiesta
    q = re.sub(r"\b(avvis\w+|notific\w+|annunci\w+|notifica)\b(.*?)\bche\b.*?(?=$|[.;]| e poi | poi )", r"\1\2", p)
    # "la striscia LED sotto la TV" e il nome di una luce: li la TV non e un bersaglio
    q = re.sub(r"\b(led|striscia|luc[ei]|lampad\w*|retroillumin\w*)\b(?:\s+(?:led|luminosa))?\s+(?:sotto|dietro|sopra|di|della)\s+(?:la\s+)?tv\b", r"\1", q)
    err = []
    known = {s["entity_id"]: s for s in states}
    trig = [t for d in docs for t in _lista(d.get("triggers")) if isinstance(t, dict)]
    tutti = list(_passi(docs))
    azioni = [a for d in docs for a in _passi(d.get("actions")) if isinstance(a.get("action"), str)]
    servizi = [a["action"] for a in azioni]
    ids = [e for n in tutti for e in _lista(n.get("entity_id")) if isinstance(e, str)]
    if not azioni:
        err.append("non c'e nessuna azione")
    for d in docs:
        if not d.get("triggers"):
            err.append("manca il trigger")
        presenti = {str(t.get("id")) for t in _lista(d.get("triggers")) if isinstance(t, dict) and t.get("id") is not None}
        for n in _passi(d):
            if n.get("condition") == "trigger":
                for i in _lista(n.get("id")):
                    if str(i) not in presenti:
                        err.append("la condizione usa il trigger con id '%s' che non esiste" % i)
    for e in sorted(set(ids)):
        if e not in known and not e.startswith(("zone.", "sun.")):
            err.append("l'entita %s non esiste" % e)
        elif known.get(e, {}).get("state") == "unavailable" and not ((known[e].get("attributes") or {}).get("_stanza_utente") or (known[e].get("attributes") or {}).get("_funzione")):
            err.append("l'entita %s non e disponibile, usane un'altra" % e)
    if re.search(r"temperatura", q) and not re.search(r"batter|tablet|telefono|\bcpu\b|router", q):
        for e in sorted(set(ids)):
            if e.startswith("sensor.") and re.search(r"batter|cpu|chip|processor", micro._n(e + " " + ((known.get(e, {}).get("attributes") or {}).get("friendly_name") or ""))):
                err.append("%s e la temperatura di un dispositivo (batteria o processore), non di una stanza" % e)
    # la grandezza chiesta deve venire da un sensore di quel tipo
    for parola, classe in (("temperatura", "temperature"), ("umidita", "humidity")):
        if not re.search(r"\b%s\b" % parola, q):
            continue
        for n in tutti:
            if (n.get("trigger") == "numeric_state" or n.get("condition") == "numeric_state"):
                for e in _lista(n.get("entity_id")):
                    a = known.get(e, {}).get("attributes") or {}
                    if str(e).startswith("sensor.") and a.get("device_class") != classe and not (classe == "temperature" and str(a.get("unit_of_measurement", "")).startswith("°")) and parola not in micro._n(str(e)):
                        err.append("%s non e un sensore di %s: se in casa non ce n'e uno adatto, l'automazione non si puo fare" % (e, parola))
    # "quando apro il cancello": il trigger deve riguardare il cancello
    m = re.search(r"\b(?:quando|appena|se)\b[^,.]{0,25}\b(cancello|portone|garage|portoncino)\b", q)
    if m and not any(m.group(1) in micro._n(str(e) + " " + micro.Casa(states).nome(e)) for t in trig for e in _lista(t.get("entity_id"))) and not any(t.get("trigger") == "event" for t in trig):
        err.append("il trigger deve riguardare il %s" % m.group(1))
    if re.search(r"\bvolume\b", q) and "media_player.volume_set" not in servizi and not re.search(r"\b(alza|abbassa) il volume", q):
        err.append("manca la regolazione del volume (media_player.volume_set)")
    # orari
    orari_yaml = {str(t.get("at"))[:5].zfill(5) for t in trig if t.get("trigger") == "time"}
    for n in tutti:
        if n.get("condition") == "time":
            orari_yaml |= {str(n[k])[:5] for k in ("after", "before") if k in n}
    chiesti = []
    for m in re.finditer(r"\b(?:alle|dalle|dopo le|prima delle|tra le|e le)\s+" + micro._ORA, q):
        o = micro._ora_gruppi(m.groups())
        if o:
            chiesti.append(o[:5])
    if re.search(r"\ba mezzanotte\b", q):
        chiesti.append("00:00")
    for o in sorted(set(chiesti)):
        if o not in orari_yaml:
            err.append("manca l'orario %s" % o)
    for t in trig:
        if t.get("trigger") == "time" and not chiesti and not re.search(r"mezzogiorno|mattina|sera|notte|\d", q):
            err.append("c'e un trigger a orario che non e stato chiesto")
    for parola, ev in (("tramonto", "sunset"), ("alba", "sunrise")):
        if re.search(r"\b%s\b" % parola, q) and not any(n.get("event") == ev or ev in (n.get("after"), n.get("before")) for n in tutti) \
                and not any(n.get("entity_id") == "sun.sun" for n in tutti):
            err.append("manca il %s" % parola)
    # durate
    for m in re.finditer(r"\b(?:per|da)\s+(?:piu di\s+|oltre\s+|almeno\s+)?(\d+)\s*(second|minut|or)[aei]?\b", q):
        n_, k = int(m.group(1)), _UNITA[m.group(2)]
        sec = n_ * {"seconds": 1, "minutes": 60, "hours": 3600}[k]
        ok = False
        for n in tutti:
            for key in ("for", "delay"):
                v = n.get(key)
                if isinstance(v, dict):
                    ok |= v.get("hours", 0) * 3600 + v.get("minutes", 0) * 60 + v.get("seconds", 0) == sec
                elif isinstance(v, str) and re.fullmatch(r"\d+:\d\d:\d\d", v):
                    h, mi, se = map(int, v.split(":")); ok |= h * 3600 + mi * 60 + se == sec
        if not ok:
            err.append("manca la durata di %d %s" % (n_, {"seconds": "secondi", "minutes": "minuti", "hours": "ore"}[k]))
    if not re.search(r"\bdopo\s+(?:\d|un|mezz)|\baspetta|\battendi|minut|second|\bor[ae]\b|temporizz|timer", q) and any("delay" in n for n in tutti):
        err.append("c'e un'attesa (delay) che non e stata chiesta")
    # giorni
    giorni = {g for n in tutti for g in _lista(n.get("weekday"))}
    attesi = set()
    if re.search(r"feriali|lavorativi|dal lunedi al venerdi|(?:tranne|escluso|eccetto) (?:il |nel )?(?:weekend|fine settimana)", q):
        attesi = set(micro.FERIALI)
    elif re.search(r"weekend|fine settimana|sabato e (?:la )?domenica", q):
        attesi = set(micro.WEEKEND)
    else:
        attesi = {v for k, v in micro.GIORNI_IT.items() if re.search(r"\b(?:ogni|il|la|solo)\s+%s\b" % k, q)}
    if attesi and giorni != attesi:
        err.append("i giorni devono essere: %s" % ", ".join(sorted(attesi)))
    # avvisi
    vuole_notifica = bool(re.search(r"\b(avvisami|avvertimi|notificami|notifica\w*|mandami|inviami|messaggio)\b", p))
    vuole_voce = bool(re.search(r"\b(annunci\w*|di' su|fai dire)\b", p))
    ha_notifica = any(s.startswith("notify.") for s in servizi)
    ha_voce = any(s.startswith(("assist_satellite.", "tts.")) for s in servizi)
    if vuole_notifica and not ha_notifica:
        err.append("manca la notifica")
    if vuole_voce and not ha_voce:
        err.append("manca l'annuncio vocale (assist_satellite.announce)")
    if ha_notifica and not vuole_notifica and not re.search(r"avvis|mand|scriv|dimmi|ricord", p):
        err.append("c'e una notifica che non e stata chiesta")
    # cose e verbi
    comandi = [a for a in azioni if not a["action"].startswith(("notify.", "assist_satellite.", "tts."))]
    dom_az = {a["action"].split(".")[0] for a in comandi}
    verbi_yaml = {a["action"].split(".")[1] for a in comandi}
    cmd = r"\b(?:accendi|spegni|accendere|spegnere|riaccendi)\w*"
    if re.search(r"\b(?:accendi|accendere|riaccendi)\w*", q) and "turn_on" not in verbi_yaml and not dom_az & {"scene", "script"}:
        err.append("manca l'azione di accensione")
    if re.search(r"\b(?:spegni|spegnere)\w*", q) and not verbi_yaml & {"turn_off", "toggle"}:
        err.append("manca l'azione di spegnimento")
    if re.search(r"\b(?:apri|aprire|alza)\w*", q) and not verbi_yaml & {"open_cover", "set_cover_position", "turn_on", "unlock"}:
        err.append("manca l'azione di apertura")
    if re.search(r"\b(?:chiudi|chiudere|abbassa)\w*", q) and not verbi_yaml & {"close_cover", "set_cover_position", "turn_off", "lock", "volume_set"}:
        err.append("manca l'azione di chiusura")
    if "turn_on" in verbi_yaml and not _VUOLE_ACCENDERE.search(q):
        for a in comandi:
            if a["action"].endswith(".turn_on") and a["action"].split(".")[0] not in ("scene", "script"):
                for e in _lista((a.get("target") or {}).get("entity_id")) or ["?"]:
                    err.append("c'e un'accensione che non e stata chiesta: %s" % e)
    if re.search(r"\b(?:apri|chiudi|alza|abbassa)\w*\s+(?:tutte\s+)?(?:le\s+|la\s+)?(?:tend|tapparell|serrand)", q) and "cover" not in dom_az:
        err.append("mancano le tende (cover)")
    if re.search(r"\b(?:tv|televisione|televisore)\b", q) and not any(e.startswith("media_player.") for e in ids):
        err.append("manca la TV (media_player)")
    # stanza nominata nel comando
    for m in re.finditer(cmd + r"\s+(?:tutte\s+)?(?:le\s+|la\s+)?(?:luc[ei]|faretti)\s+(?:del|dello|della|dell'|in|nel|nella)\s*(%s)\b" % "|".join(micro.STANZE), q):
        casa = micro.Casa(states)
        delle = set(casa.luci([m.group(1)]))
        bers = {e for a in comandi for e in _lista((a.get("target") or {}).get("entity_id"))}
        if delle and not delle & bers:
            err.append("le luci di '%s' sono %s e non sono tra i bersagli" % (m.group(1), ", ".join(sorted(delle))))
        elif delle and "tranne" not in q and re.search(r"tutte\s+(?:le\s+)?(?:luc|farett)", m.group(0)) and not delle <= bers:
            err.append("mancano alcune luci di '%s': %s" % (m.group(1), ", ".join(sorted(delle - bers))))
    # solo "luci del X" nella richiesta: nessuna luce di altre stanze fra i bersagli
    stanze_rx = r"\b(?:luc[ei]|faretti)\s+(?:del|dello|della|dell'|in|nel|nella)\s*(%s)\b" % "|".join(micro.STANZE)
    dette = re.findall(stanze_rx, q)
    altro = re.sub(stanze_rx, " ", q)
    if dette and not re.search(r"\b(?:luc[ei]|faretti|lampad\w*|led|striscia|barra|presa|tutto|ingresso|corridoio)\b|tranne|eccetto", altro):
        casa = micro.Casa(states)
        ammesse = {e for r in dette for e in casa.luci([r])}
        if ammesse:
            for a in comandi:
                if a["action"].split(".")[0] in ("light", "switch"):
                    for e in _lista((a.get("target") or {}).get("entity_id")):
                        if e not in ammesse:
                            err.append("%s non e una luce di '%s'" % (e, "' o '".join(dette)))
    # "buio": una condizione sun con un solo lato non copre tutta la notte
    if re.search(r"\bbuio\b", q) and any(n.get("condition") == "sun" and ("after" in n) != ("before" in n) and not any(k.endswith("offset") for k in n) for n in tutti):
        err.append("per 'buio' usa condition: state, entity_id: sun.sun, state: below_horizon (after sunset da solo non vale al mattino)")
    # posizione delle tende solo se chiesta
    if not _VUOLE_POSIZIONE.search(q):
        for a in azioni:
            if a["action"] == "cover.set_cover_position":
                err.append("la posizione delle tende non e stata chiesta: usa cover.open_cover o cover.close_cover")
    casa = micro.Casa(states)
    esclude = re.search(r"\b(?:tranne|eccetto|esclus\w+|ma non)\b", q)
    # tende di ogni stanza nominata nel comando
    if not esclude and re.search(r"\b(?:apri|chiudi|alza|abbassa)", q):
        for m in re.finditer(r"\b(tend[ae]|tapparell[ae]) (?:di|del|della|dell'|in|nel|nella) ?(%s)\b(?! (?:e'|sono|e |si ))" % "|".join(micro.STANZE), q):
            delle = set(casa.tende([m.group(2)]))
            mancano = delle - set(ids)
            if delle and (mancano == delle or (m.group(1) in ("tende", "tapparelle") and mancano)):
                err.append("mancano le tende di '%s': %s" % (m.group(2), ", ".join(sorted(mancano))))
    # ogni dispositivo chiamato per nome deve esserci
    if not esclude:
        nomi = {}
        for s in states:
            nm = " ".join(micro._n((s.get("attributes") or {}).get("friendly_name", "")).split())
            if s["entity_id"].split(".")[0] in ("light", "switch", "fan", "cover") and casa.vivo(s) and len(nm.split()) >= 2:
                nomi.setdefault(nm, []).append(s["entity_id"])
        for nm, es in nomi.items():
            if re.search(r"\b(?:accendi|spegni|apri|chiudi|alza|abbassa)\w* (?:[\w']+ ){0,3}?%s\b" % re.escape(nm), q) and not set(es) & set(ids):
                if not any(o != nm and nm in o and o in q for o in nomi):
                    err.append("manca '%s' (%s)" % (nm, es[0]))
    tc = json.dumps(docs, ensure_ascii=False, default=str)
    for rx, ok, cosa in ((r"\b(?:la sera|di sera|stasera|la notte|di notte|la mattina|di mattina|al mattino)\b", r"\"sun|below_horizon|above_horizon|\"after\"|\"before\"|\"at\"", "il momento della giornata"),
                         (r"\b(?:lunedi|martedi|mercoledi|giovedi|venerdi|sabato|domenica|weekend|fine settimana|feriali|festivi)\b", r"weekday", "i giorni"),
                         (r"\b(?:d'estate|in estate|d'inverno|in inverno|in primavera|in autunno)\b", r"month", "la stagione")):
        if re.search(rx, p) and not re.search(ok, tc):
            err.append("nella richiesta c'e %s (\"%s\") ma l'automazione lo ignora" % (cosa, re.search(rx, p).group(0)))
    return list(dict.fromkeys(err))
