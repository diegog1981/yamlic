"""Micro-frasi: la richiesta viene divisa in pezzi (quando, condizioni, azioni, eccezioni), risolta con regole
e riassemblata qui. Il modello serve solo per le azioni che le regole non capiscono (una micro-frase alla volta).
Regola d'oro: se resta una parola non capita non si indovina (costruisci ritorna None e si usa il metodo classico)."""
import re

import yaml

import core

GIORNI_IT = {"lunedi": "mon", "martedi": "tue", "mercoledi": "wed", "giovedi": "thu", "venerdi": "fri", "sabato": "sat", "domenica": "sun"}
FERIALI, WEEKEND = ["mon", "tue", "wed", "thu", "fri"], ["sat", "sun"]
STANZE = ("soggiorno", "salotto", "salone", "living", "cucina", "bagno", "cameretta", "camera", "giardino", "esterno", "ingresso",
          "corridoio", "studio", "mansarda", "taverna", "lavanderia", "terrazzo", "balcone", "garage", "cantina", "ufficio", "veranda", "isola", "tavolo")
VERBI = {"accend": "turn_on", "riaccend": "turn_on", "spegn": "turn_off", "apr": "open_cover", "riapr": "open_cover", "alz": "open_cover",
         "chiud": "close_cover", "richiud": "close_cover", "abbass": "close_cover"}
_V = r"(?:ri)?(?:accend|spegn|apr|chiud)\w*|alz\w+|abbass\w+"
_NOTIFICA = r"(?:avvisami|avvertimi|notificami|(?:mandami|inviami|manda|invia)\s+(?:una\s+)?notifica(?:\s+(?:sul|al)\s+(?:mio\s+)?(?:telefono|cellulare))?)"
_ANNUNCIO = r"(?:annuncia|annunciami|di'|dillo|fai dire)"
# dove si puo tagliare: virgole/punti, "poi", e "e/ma" solo se dopo viene un verbo o un nuovo quando/se
_INIZIO = r"(?:(?:%s)\b|%s\b|%s\b|quando\b|appena\b|se\b|alle\b|all'|al tramonto|a mezz|dopo\b|poi\b|\d+ minuti (?:prima|dopo))" % (_V, _NOTIFICA, _ANNUNCIO)
_TAGLIO = re.compile(r"(?<!\d)[.;!?]+(?!\d)\s*|,\s*(?:(?:e|ma)\s+)?(?:poi\s+)?|\s+(?:e|ed|ma)\s+poi\s+|\s+poi\s+|\s+(?:e|ed|ma|mentre|invece)\s+(?=%s)" % _INIZIO)
# parole che si possono ignorare senza perdere significato
VUOTE = set("""il lo la i gli le l un uno una di del dello della dei degli delle d in nel nello nella nei nelle a al allo alla ai agli alle
su sul sulla sullo e ed che mi me si ci poi anche pure tutte tutti tutta tutto ogni volta giorno giorni per favore automaticamente
allora quindi inoltre per_favore voglio vorrei con ma sera mattina""".split())


def _n(s):
    s = (s or "").lower().replace("’", "'")
    for a, b in (("ì", "i"), ("à", "a"), ("ò", "o"), ("ù", "u"), ("é", "e")):
        s = s.replace(a, b)
    return s.replace("è", "e'").replace("e''", "e'")


def _togli(testo, m):
    return (testo[:m.start()] + " " + testo[m.end():]).strip()


def _resto(testo):
    return [w for w in re.findall(r"[a-z0-9%]+", testo) if w not in VUOTE]


def _hhmm(h, m, coda=""):
    h, m = int(h), int(m or 0)
    if re.search(r"sera|pomeriggio|notte", coda or "") and 1 <= h < 12 and not ("notte" in coda and h <= 4):
        h += 12
    if h == 24:
        h = 0
    return "%02d:%02d:00" % (h, m) if h < 24 and m < 60 else None


_ORA = r"(\d{1,2})(?:(?:[:.]|\s+e\s+)(\d{2}|mezz[oa]))?(?:\s+(di sera|del pomeriggio|di notte|di mattina))?"


def _ora_gruppi(g):
    mm = g[1]
    return _hhmm(g[0], 30 if mm and mm.startswith("mezz") else mm, g[2])


def _durata(n, u):
    n = 1 if n in ("un", "una") else 30 if n.startswith("mezz") else int(n)
    return {("seconds" if u.startswith("second") else "hours" if u.startswith("or") else "minutes"): n}


_PER = r"(?:per|da)\s+(?:piu\s+di\s+|oltre\s+|almeno\s+)?(\d+|un|una|mezz')\s*(second[oi]|minut[oi]|or[ae]|ora)\b"


# ---------- entita ----------
class Casa:
    def __init__(self, states):
        self.states = [s for s in states]
        self.by = {s["entity_id"]: s for s in states}

    def testo(self, s):
        return _n(core._t(s))

    def vivo(self, s):
        return s.get("state") != "unavailable"

    def classe(self, s):
        return (s.get("attributes") or {}).get("device_class")

    def nome(self, eid):
        return (self.by.get(eid, {}).get("attributes") or {}).get("friendly_name") or eid

    def _in_stanza(self, s, stanze, largo=False):
        t = self.testo(s)
        gruppi = set()
        for r in stanze:
            gruppi |= core.room_words(r)
        if not any(re.search(r"\b%s\b" % g, t) for g in gruppi):
            return False
        # "bagno camera" non e la "camera": se il nome ha anche un'altra stanza non citata e l'area non e quella cercata, si scarta
        area = _n((s.get("attributes") or {}).get("_area", ""))
        altre = [r for r in STANZE if r not in gruppi and r not in ("isola", "tavolo", "esterno") and re.search(r"\b%s\b" % r, area)]
        return largo or not altre or any(area == g for g in gruppi)

    def luci(self, stanze=(), largo=False):
        if stanze and list(stanze) in (["giardino"], ["esterno"]):   # "esterni isola" non e il giardino
            lett = [e for e in self.luci() if re.search(r"\bgiardino\b", self.testo(self.by[e]))]
            if lett:
                return lett
        out = []
        for s in self.states:
            e = s["entity_id"]
            if core._dom(e) not in ("light", "switch") or not self.vivo(s):
                continue
            t = core._t(s)
            solo_stanza = _n((s.get("attributes") or {}).get("friendly_name", "")).strip() in stanze   # interruttore chiamato come la stanza
            if core.NOT_LIGHT.search(t) or (core._dom(e) == "switch" and not core.LIGHT_LIKE.search(t) and not solo_stanza):
                continue
            if stanze and not self._in_stanza(s, stanze, largo):
                continue
            out.append(e)
        if stanze and not out and not largo:   # es. nel "bagno" l'unica luce e in "Bagno Camera"
            return self.luci(stanze, True)
        return out

    def tende(self, stanze=()):
        return [s["entity_id"] for s in self.states if core._dom(s["entity_id"]) == "cover" and self.vivo(s)
                and self.classe(s) not in ("garage", "gate", "door") and (not stanze or self._in_stanza(s, stanze))]

    def tv(self, stanze=()):
        best = []
        for s in self.states:
            e, t = s["entity_id"], self.testo(s)
            if core._dom(e) != "media_player" or not self.vivo(s) or not (self.classe(s) == "tv" or re.search(r"\btv\b|televis", t)):
                continue
            if re.search(r"soundbar|spotify|tablet|tab a\d", t):
                continue
            ide = e.split(".")[1].replace("_", " ")
            p = (3 if re.search(r"\btv\b", ide) else 0) + sum(2 for r in stanze if re.search(r"\b%s\b" % r, ide)) \
                + sum(1 for r in stanze if self._in_stanza(s, [r])) - len(ide) / 100.0
            best.append((p, e))
        return [max(best)[1]] if best else []

    def per_nome(self, frase, domini=("light", "switch", "fan", "input_boolean", "cover", "media_player", "vacuum")):
        """Entita il cui nome (almeno 2 parole, o 1 parola lunga) compare per intero nella frase."""
        hit = []
        for s in self.states:
            if core._dom(s["entity_id"]) not in domini or not self.vivo(s):
                continue
            nm = _n((s.get("attributes") or {}).get("friendly_name", "")).strip()
            corto = re.sub(r"\s+(?:%s)$" % "|".join(STANZE), "", nm)
            for n_ in dict.fromkeys([nm, corto]):
                if len(n_) >= 5 and (n_ == nm or (len(n_.split()) >= 2 and not re.match(_COSE + r"\b", n_.split()[-1]))) and re.search(r"\b%s\b" % re.escape(n_), frase):
                    hit.append((len(n_), s["entity_id"], n_))
                    break
        if not hit:
            return [], ""
        top = max(h[0] for h in hit)
        sel = [h for h in hit if h[0] == top]
        return [h[1] for h in sel], sel[0][2]


# ---------- gruppi di bersagli ("le luci del giardino e la tv del soggiorno") ----------
_COSE = r"(luc[ei]|lampade?|lampadina|faretti|illuminazione|tende?|tapparell[ae]|serrand[ae]|tv|tele|televisione|televisore)"
_STANZA = r"(?:\s+(?:del|dello|della|dell'|dei|delle|in|nel|nella|nell'|di)\s*(?:mio\s+|mia\s+)?(%s)\b)" % "|".join(STANZE)


def bersagli(frase, casa, avv, verbo=None):
    """Ritorna (lista di entita, frase residua)."""
    ids = []
    esatti, nm = casa.per_nome(frase)
    if esatti and len(nm.split()) >= 2 and not re.fullmatch(r"(?:tv|luc[ei]|tend[ae])\s+(?:%s)" % "|".join(STANZE), nm):
        ids += esatti[:1]
        frase = re.sub(r"\b%s\b" % re.escape(nm), " ", frase)
    rx = re.compile(r"\b%s\b(?:%s)?" % (_COSE, _STANZA))
    while True:
        m = rx.search(frase)
        if not m:
            break
        cosa, stanza = m.group(1), m.group(2)
        st = [stanza] if stanza else []
        if cosa.startswith(("tv", "tele")):
            trovati = casa.tv(st or ["soggiorno", "salotto"])
        elif cosa.startswith(("tend", "tappar", "serrand")):
            trovati = casa.tende(st)
        else:
            trovati = casa.luci(st)
            singolare = cosa in ("luce", "lampada", "lampadina")
            if singolare and len(trovati) > 1:
                veri = [e for e in trovati if e.startswith("light.")]
                avv.append("\"%s%s\" puo essere: %s. Controlla il bersaglio" % (cosa, " " + stanza if stanza else "", ", ".join(casa.nome(e) for e in trovati)))
                trovati = (veri or trovati)[:1]
            if singolare and not stanza:
                trovati = []
        if not trovati:
            return None, frase
        ids += [e for e in trovati if e not in ids]
        frase = _togli(frase, m)
    m = re.search(r"\btutto\b", frase)
    if m and not ids and verbo == "turn_off":
        trovati = casa.luci() + casa.tv(["soggiorno", "salotto"])
        avv.append("\"tutto\" = tutte le luci e la TV: aggiungi o togli a mano il resto")
        ids += trovati; frase = _togli(frase, m)
    if not ids and verbo in ("turn_on", "turn_off"):   # "accendi ingresso e corridoio": interruttori chiamati come la stanza
        nude = [w for w in _resto(frase) if w in STANZE]
        if nude and nude == _resto(frase):
            per_s = [[e for e in casa.luci([w]) if _n(casa.nome(e)) == w] for w in nude]
            if all(len(x) == 1 for x in per_s):
                ids += [x[0] for x in per_s]
                for w in nude:
                    frase = re.sub(r"\b%s\b" % w, " ", frase)
    if not ids and esatti:
        ids = esatti[:1]
        frase = re.sub(r"\b%s\b" % re.escape(nm), " ", frase)
    return ids, frase


def escludi(ids, testo, casa, avv):
    """'tranne X': toglie le entita di X."""
    parole = [w for w in re.findall(r"[a-z]{3,}", testo) if w not in VUOTE and w not in core.GENERIC and w not in ("quelle", "quella", "quello", "quelli", "dell", "nell")]
    if not parole:
        return None
    def corr(e):
        t = casa.testo(casa.by[e])
        return all(any(re.search(r"\b%s\b" % g, t) for g in core.room_words(w)) for w in parole)
    via = [e for e in ids if corr(e)]
    if not via:
        avv.append("\"tranne %s\": in casa non c'e nessuna entita con quel nome fra i bersagli, quindi non ho escluso nulla" % " ".join(parole))
    return [e for e in ids if e not in via] or None


# ---------- quando / condizioni ----------
def stato_entita(testo, casa, avv):
    """'la tv del soggiorno' -> entita singola per condizioni e trigger di stato."""
    ids, resto = bersagli(testo, casa, avv)
    if ids and len(ids) == 1 and not _resto(resto):
        return ids[0]
    return None


def evento(testo, casa, io, avv):
    """Testo dopo 'quando/appena': ritorna (trigger, condizioni) oppure None."""
    t = testo.strip()
    per = None
    m = re.search(r"\b" + _PER, t)
    if m:
        per = _durata(m.group(1), m.group(2)); t = _togli(t, m)
    t = " ".join(t.split())

    def fine(tr):
        if per:
            tr["for"] = per
        return tr, []
    if re.fullmatch(r"(?:non (?:c'e'|c'e|ce|e') (?:piu )?(?:nessuno)(?: in casa| a casa)?|(?:in )?casa (?:e' |resta |rimane )?vuota|(?:tutti )?(?:escono|sono usciti)(?: di casa)?)", t):
        return fine({"trigger": "numeric_state", "entity_id": "zone.home", "below": 1})
    if re.fullmatch(r"(?:(?:qualcuno )?(?:torna|rientra|arriva)(?: qualcuno)?(?: a casa| in casa)?)", t):
        return fine({"trigger": "numeric_state", "entity_id": "zone.home", "above": 0})
    if re.fullmatch(r"(?:esco|vado via)(?: di casa| da casa)?|(?:torno|rientro|arrivo)(?: a casa| in casa)?", t):
        if not io:
            esce = bool(re.match(r"esco|vado", t))
            avv.append("non so chi sei (opzione io_person vuota): uso la presenza di casa (%s). Se intendi solo te, imposta io_person" % ("quando esce l'ultima persona" if esce else "quando rientra la prima persona"))
            return fine({"trigger": "numeric_state", "entity_id": "zone.home", **({"below": 1} if esce else {"above": 0})})
        return fine({"trigger": "state", "entity_id": io, "to": "not_home" if re.match(r"esco|vado", t) else "home"})
    if re.fullmatch(r"(?:inizia a piovere|piove|comincia a piovere)", t):
        w = [s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) == "weather"]
        return fine({"trigger": "state", "entity_id": w[0], "to": ["rainy", "pouring"]}) if w else None
    m = re.fullmatch(r"(?:la |il )?(lavatrice|lavastoviglie|asciugatrice) (?:finisce|ha finito|termina|ha terminato)(?: il (?:ciclo|lavaggio))?(?: di lavare| di asciugare)?", t)
    if m:
        pot = [s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) == "sensor" and m.group(1) in casa.testo(s)
               and (casa.classe(s) == "power" or (s.get("attributes") or {}).get("unit_of_measurement") == "W")]
        if len(pot) != 1:
            return None
        avv.append("fine ciclo %s = potenza sotto 5 W per 3 minuti (e prima era sopra): controlla soglia e durata sul tuo elettrodomestico" % m.group(1))
        tr = {"trigger": "numeric_state", "entity_id": pot[0], "below": 5, "for": per or {"minutes": 3}}
        return tr, []
    m = re.fullmatch(r"(?:la )?(temperatura|umidita|potenza) (?:del |della |dell'|in |nel |nella )?(.+?) (supera|va sopra|sale sopra|passa|e' sopra|e' piu' di|scende sotto|va sotto|e' sotto|e' meno di) (?:i |le )?(\d+(?:[.,]\d+)?)\s*(?:gradi|°c?|%|per ?cento|watt|w)?", t)
    if m:
        classe = {"temperatura": "temperature", "umidita": "humidity", "potenza": "power"}[m.group(1)]
        parole = _resto(m.group(2))
        cand = [s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) == "sensor" and casa.vivo(s) and casa.classe(s) == classe
                and parole and all(any(re.search(r"\b%s\b" % g, casa.testo(s)) for g in core.room_words(w)) for w in parole)]
        if len(cand) != 1:
            return None
        v = float(m.group(4).replace(",", ".")); v = int(v) if v == int(v) else v
        return fine({"trigger": "numeric_state", "entity_id": cand[0], ("below" if "sotto" in m.group(3) or "meno" in m.group(3) else "above"): v})
    m = re.fullmatch(r"(?P<s>.+?) (?:resta|rimane|e') (?P<st>acces[ao]|spent[ao]|apert[ao]|chius[ao])", t) or \
        re.fullmatch(r"(?:si (?P<v>accende|spegne|apre|chiude)) (?P<s>.+)", t) or \
        re.fullmatch(r"(?P<s>.+?) (?:si|viene) (?P<v>accende|spegne|apre|chiude|accesa|acceso|spenta|spento|aperta|aperto|chiusa|chiuso)", t)
    if m:
        eid = stato_entita(m.group("s"), casa, avv)
        if not eid:
            return None
        v = m.groupdict().get("st") or m.group("v")
        stato = "on" if v.startswith("acc") else "off" if v.startswith(("spe", "spegn")) else "open" if v.startswith("ap") else "closed"
        if core._dom(eid) != "cover":
            stato = {"open": "on", "closed": "off"}.get(stato, stato)
        if core._dom(eid) == "media_player" and stato in ("on", "off"):   # un media player acceso puo essere playing, idle, paused
            return fine({"trigger": "state", "entity_id": eid, **({"from": ["off", "standby"]} if stato == "on" else {"to": ["off", "standby"]})})
        return fine({"trigger": "state", "entity_id": eid, "to": stato})
    return None


def condizione(testo, casa, avv):
    """'la tv del soggiorno e' accesa' / 'c'e' qualcuno in casa'"""
    t = " ".join(testo.split())
    if re.fullmatch(r"(?:non c'e' nessuno|non c'e nessuno|la casa e' vuota)(?: in casa| a casa)?", t):
        return {"condition": "numeric_state", "entity_id": "zone.home", "below": 1}
    if re.fullmatch(r"(?:c'e' qualcuno|c'e qualcuno|qualcuno e' )(?: in casa| a casa)?", t):
        return {"condition": "numeric_state", "entity_id": "zone.home", "above": 0}
    if re.fullmatch(r"(?:e' |e |fa )?(?:gia )?buio|e' notte", t):
        return {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}
    if re.fullmatch(r"(?:e' |e |c'e' )?(?:ancora )?(?:giorno|chiaro|luce)", t):
        return {"condition": "state", "entity_id": "sun.sun", "state": "above_horizon"}
    m = re.fullmatch(r"(?P<s>.+?) (?:e'|sono|risulta|e) (?:ancora |gia )?(?P<st>acces[aoei]|spent[aoei]|apert[aoei]|chius[aoei])", t)
    if not m:
        return None
    eid = stato_entita(m.group("s"), casa, avv)
    if not eid:
        return None
    v = m.group("st")
    stato = "on" if v.startswith("acc") else "off" if v.startswith("spe") else "open" if v.startswith("ap") else "closed"
    if core._dom(eid) != "cover":
        stato = {"open": "on", "closed": "off"}.get(stato, stato)
    c = {"condition": "state", "entity_id": eid, "state": stato}
    if core._dom(eid) == "media_player" and stato == "on":
        c = {"condition": "not", "conditions": [{"condition": "state", "entity_id": eid, "state": ["off", "standby", "unavailable"]}]}
    return c


# ---------- pezzo ----------
def tempi(seg):
    """Estrae dal pezzo orari/sole (trigger), fasce e giorni (condizioni). Ritorna (trigger, condizioni, giorni, resto)."""
    tr, cond, giorni = [], [], None
    m = re.search(r"\b(?:dalle|tra le|fra le)\s+%s\s+(?:alle|e le)\s+%s" % (_ORA, _ORA), seg)
    if m:
        a, b = _ora_gruppi(m.groups()[:3]), _ora_gruppi(m.groups()[3:])
        if a and b:
            cond.append({"condition": "time", "after": a, "before": b}); seg = _togli(seg, m)
    for pat, key in ((r"\bdopo le\s+", "after"), (r"\bprima delle\s+", "before")):
        m = re.search(pat + _ORA, seg)
        if m and _ora_gruppi(m.groups()):
            cond.append({"condition": "time", key: _ora_gruppi(m.groups())}); seg = _togli(seg, m)
    for pat, key in ((r"\bdopo (?:il |il calar del )?(tramonto)\b|\bdopo (?:l')(alba)\b", "after"), (r"\bprima (?:del |dell')(tramonto|alba)\b", "before")):
        m = re.search(pat, seg)
        if m and not re.search(r"(?:minut\w*|ora|ore)\s+" + pat, seg):
            ev = next(g for g in m.groups() if g)
            cond.append({"condition": "sun", key: "sunset" if ev == "tramonto" else "sunrise"}); seg = _togli(seg, m)
    seg = re.sub(r"\bogni (?:sera|mattina|giorno|notte)\b(?=.*\balle\b)", " ", seg)
    m = re.search(r"(?:(\d+|mezz'?\s*|un\s*')\s*(minut[oi]|ora|ore)\s+(prima|dopo)\s+(?:del |il |l')?|\b(?:al|all'|il)\s*)\b(tramonto|alba)\b", seg)
    if m:
        t = {"trigger": "sun", "event": "sunset" if m.group(4) == "tramonto" else "sunrise"}
        if m.group(1):
            d = _durata(m.group(1).replace(" ", "").replace("un'", "un"), m.group(2))
            h, mi = d.get("hours", 0), d.get("minutes", 0)
            t["offset"] = "%s%02d:%02d:00" % ("-" if m.group(3) == "prima" else "", h, mi)
        tr.append(t); seg = _togli(seg, m)
    while True:
        m = re.search(r"\balle(?:\s+ore)?\s+" + _ORA, seg)
        if not m:
            break
        o = _ora_gruppi(m.groups())
        if not o:
            break
        tr.append({"trigger": "time", "at": o}); seg = _togli(seg, m)
    m = re.search(r"\ba (mezzanotte|mezzogiorno)\b", seg)
    if m:
        tr.append({"trigger": "time", "at": "00:00:00" if m.group(1) == "mezzanotte" else "12:00:00"}); seg = _togli(seg, m)
    m = re.search(r"\b(?:(?:nei|i|solo nei|solo i)\s+(?:giorni\s+)?(?:feriali|lavorativi)|dal lunedi al venerdi)\b", seg)
    if m:
        giorni, seg = FERIALI, _togli(seg, m)
    m = re.search(r"\b(?:(?:nel|il|solo nel|ogni)\s+(?:weekend|fine settimana)|(?:il )?sabato e (?:la )?domenica)\b", seg)
    if m:
        giorni, seg = WEEKEND, _togli(seg, m)
    m = re.search(r"\b(?:ogni|il|la|solo il|solo la)\s+(%s)\b" % "|".join(GIORNI_IT), seg)
    if m and not giorni:
        giorni, seg = [GIORNI_IT[m.group(1)]], _togli(seg, m)
    return tr, cond, giorni, seg


def messaggio(originale, chiave):
    m = re.search(chiave + r".*?\b(?:che|dicendo(?: che)?|:)\s*[\"«]?(.+?)[\"»]?\s*$", originale, re.I)
    if m:
        t = m.group(1).strip().rstrip(".")
        return t[0].upper() + t[1:]
    m = re.search(r"[\"«]([^\"»]+)[\"»]", originale)
    return m.group(1) if m else None


def costruisci(prompt, states, notify=(), io="", chiedi=None):
    """Ritorna dict {yaml, avvisi, modello(n. micro-frasi chieste)} oppure None se la richiesta non e capita del tutto."""
    casa, avv = Casa(states), []
    originale = prompt.strip()
    p = " ".join(_n(originale).split()).rstrip(" .!")
    if not p or len(p) > 400:
        return None
    # il testo di notifiche/annunci non va spezzato: lo metto da parte
    testo_msg = None
    m = re.search(r"\b(?:%s|%s)\b[^,.;]*?\b(che|dicendo che|dicendo|:)\s+" % (_NOTIFICA, _ANNUNCIO), p)
    if m:
        coda = p[m.end():]
        stop = re.search(r"[.;]| e poi |, poi |,? e (?=(?:%s)\b|alle |quando )" % _V, coda)
        fine = m.end() + (stop.start() if stop else len(coda))
        om = messaggio(originale if not stop else originale[:len(originale)], r"(?:avvis|avvert|notific|annunc|di'|manda|invia|dire)")
        grezzo = p[m.end():fine]
        if om and _n(om).startswith(grezzo[:12]):
            testo_msg = om[:len(grezzo)].rstrip(" .")
        testo_msg = testo_msg or (grezzo[:1].upper() + grezzo[1:])
        p = p[:m.start(1)] + " __msg__ " + p[fine:]
    segs = [s.strip(" ,") for s in _TAGLIO.split(p) if s and s.strip(" ,")]
    rami, cur = [], {"tr": [], "cond": [], "az": []}
    giorni_tutti, sospese, ultimi, chieste = None, [], [], 0
    verbo_prec = None
    apri_blocco = False
    blocco = None   # "se X, spegni ... e annuncia ...": le azioni che seguono restano dentro lo stesso if

    def chiudi():
        if cur["tr"] or cur["az"]:
            rami.append(dict(cur))

    for seg in segs:
        eccez = None
        m = re.search(r"\b(?:tranne|eccetto|escluse?|escluso|esclusi|ma non)\b(.*)$", seg)
        if m:
            eccez, seg = m.group(1), seg[:m.start()].strip()
            if re.search(r"\b(%s)\b" % _V, eccez):
                return None
        attesa = durata_azione = None
        m = re.search(r"\b(?:dopo|(?:aspetta|attendi))\s+(\d+|un|una|mezz')\s*(second[oi]|minut[oi]|or[ae]|ora)\b(?!\s+(?:prima|dopo)\b)", seg)
        if m:
            attesa, seg = _durata(m.group(1), m.group(2)), _togli(seg, m)
        m = re.search(r"\bper\s+(\d+|un|una|mezz')\s*(second[oi]|minut[oi]|or[ae]|ora)\b", seg)
        if m and re.search(r"\baccendi\w*", seg) and not re.search(r"\b(?:quando|se|appena)\b[^,]*\bper\b(?![^,]*accendi)", seg[:m.start()] if False else ""):
            if not re.search(r"\b(?:resta|rimane)\b", seg):
                durata_azione, seg = _durata(m.group(1), m.group(2)), _togli(seg, m)
        trig, cond, giorni, seg = tempi(seg)
        if giorni:
            giorni_tutti = giorni
        ev_cond = []
        # quando ...  (l'evento va fino al primo verbo di comando)
        m = re.search(r"\b(?:quando|appena|ogni volta che|non appena)\b\s*(.*)$", seg)
        if not m and not cur["tr"] and not trig and re.match(r"se\b", seg) and re.search(r"\b(?:resta|rimane|si (?:accende|spegne|apre|chiude))\b", seg):
            m = re.match(r"se\b\s*(.*)$", seg)
            if cond:
                avv.append("\"resta acceso dopo le...\": scatta solo quando la durata si completa nella fascia oraria. Se era gia acceso da prima non parte")
        if m:
            corpo = m.group(1)
            cmd = re.search(r"(?<!si )\b(?:(?:%s)\b|%s|%s)" % (_V, _NOTIFICA, _ANNUNCIO), corpo)
            if cmd and re.match(r"si (?:accende|spegne|apre|chiude)", corpo):
                cmd = re.search(r"\b(?:(?:accendi|spegni|apri|chiudi|alza|abbassa)\w*|%s|%s)" % (_NOTIFICA, _ANNUNCIO), corpo)
            testo_ev = corpo[:cmd.start()] if cmd else corpo
            ms = re.fullmatch(r"\s*si (accende|spegne|apre|chiude)\s*", testo_ev)
            prima_tr = [t_ for r_ in rami + [cur] for t_ in r_["tr"] if str(t_.get("entity_id", "")).startswith(("light.", "switch.", "media_player.", "cover.", "fan.", "binary_sensor."))]
            if ms and prima_tr:
                testo_ev = "si %s %s" % (ms.group(1), _n(casa.nome(prima_tr[-1]["entity_id"])))
                soggetto = prima_tr[-1]["entity_id"]
                stato = {"accende": "on", "spegne": "off", "apre": "open", "chiude": "closed"}[ms.group(1)]
                r = ({"trigger": "state", "entity_id": soggetto, "to": stato}, [])
            else:
                r = evento(testo_ev, casa, io, avv)
            if not r:
                return None
            trig.append(r[0]); ev_cond = r[1]
            ee = r[0].get("entity_id", "")
            if ee.startswith(("light.", "switch.", "cover.", "media_player.", "fan.")):
                ultimi = [ee]
            seg = (seg[:m.start()] + " " + (corpo[cmd.start():] if cmd else "")).strip()
        # se ... (condizione sui pezzi che seguono)
        m = re.search(r"\b(?:solo se|se|ma solo se|purche)\b\s+(.*)$", seg)
        if m:
            corpo = m.group(1)
            cmd = re.search(r"\b(?:(?:accendi|spegni|apri|chiudi|alza|abbassa)\w*|%s|%s)" % (_NOTIFICA, _ANNUNCIO), corpo)
            c = condizione(corpo[:cmd.start()] if cmd else corpo, casa, avv)
            if not c:
                return None
            prima = seg[:m.start()].strip()
            resto_seg = corpo[cmd.start():] if cmd else ""
            prefisso = bool(_resto(resto_seg) or not _resto(prima))
            if prefisso:
                sospese.append(c); seg = (prima + " " + resto_seg).strip()
                if c.get("entity_id", "").startswith(("light.", "switch.", "cover.", "media_player.", "fan.")) or c["condition"] == "not":
                    ultimi = [(c.get("entity_id") or c["conditions"][0]["entity_id"])]
            else:   # "spegni X se Y": la condizione vale per questa azione
                sospese.append(c); seg = prima
            apri_blocco = apri_blocco or prefisso
        # nuovo quando => nuovo ramo
        if trig:
            blocco = None
            if cur["az"]:
                chiudi(); cur = {"tr": [], "cond": [], "az": []}
                sospese = sospese if not cur["az"] else []
            cur["tr"] += trig
        cur["cond"] += cond + ev_cond
        if not _resto(seg.replace("__msg__", "")) and eccez is None:
            if attesa:
                (blocco["then"] if blocco is not None else cur["az"]).append({"delay": attesa})
            if durata_azione:
                return None
            continue
        # azioni
        azioni = []
        mn = re.search(r"\b%s" % _NOTIFICA, seg)
        ma = re.search(r"\b%s\b" % _ANNUNCIO, seg)
        if mn:
            seg = _togli(seg, mn)
            txt = testo_msg if "__msg__" in seg else None
            seg = seg.replace("__msg__", " ")
            mob = sorted(n for n in notify if n.startswith("mobile_app_"))
            srv = mob[0] if len(mob) == 1 else "notify"
            if len(mob) != 1:
                avv.append("notifica: ho messo notify.notify, scegli il tuo telefono (notify.mobile_app_...)")
            if not txt:
                avv.append("testo della notifica scritto da Yamlic: cambialo se vuoi")
                txt = _testo_evento(cur["tr"], casa, originale)
            azioni.append({"action": "notify." + srv, "data": {"message": txt}})
            if _resto(seg):
                return None
        elif ma:
            seg = _togli(seg, ma)
            txt = testo_msg if "__msg__" in seg else None
            seg = seg.replace("__msg__", " ")
            sat = [s for s in casa.states if core._dom(s["entity_id"]) == "assist_satellite" and casa.vivo(s)]
            nominati = [s for s in sat if all(w in casa.testo(s) for w in _resto(seg)[:3])]
            if not txt or len(nominati) != 1:
                return None
            azioni.append({"action": "assist_satellite.announce", "target": {"entity_id": nominati[0]["entity_id"]}, "data": {"message": txt}})
        else:
            mv = re.search(r"\b(%s)\b" % _V, seg)
            if mv:
                parola = mv.group(1)
                base = next((k for k in sorted(VERBI, key=len, reverse=True) if parola.startswith(k)), None)
                verbo = VERBI[base]
                verbo_prec = verbo
                resto = _togli(seg, mv)
                anafora = bool(re.search(r"(?:la|lo|le|li)$", parola)) and parola not in ("apri",)
            elif verbo_prec and eccez is None:
                verbo, resto, anafora = verbo_prec, seg, False
            elif eccez is not None and not _resto(seg) and cur["az"]:
                return None
            else:
                verbo = None
            if verbo is None:
                return None
            lum = None
            ml = re.search(r"\bal\s+(\d{1,3})\s*(?:%|per ?cento)", resto)
            if ml:
                lum, resto = int(ml.group(1)), _togli(resto, ml)
            ids, resto = bersagli(resto, casa, avv, verbo)
            if ids == [] and anafora and ultimi:
                ids = list(ultimi)
            if not ids or _resto(resto):
                # il pezzo non e capito: lo chiedo al modello, da solo
                frase = " ".join((parola if mv else "") .split() + [seg if not mv else _togli(seg, mv)]).strip()
                fatte = chiedi(frase) if chiedi and not anafora and eccez is None else None
                if not fatte:
                    return None
                chieste += 1
                avv.append("\"%s\": azione scritta dall'AI, controllala" % frase)
                azioni += fatte
                ultimi = [e for a in fatte for e in _lista((a.get("target") or {}).get("entity_id"))]
            else:
                if eccez is not None:
                    ids = escludi(ids, eccez, casa, avv)
                    if not ids:
                        return None
                    eccez = None
                ultimi = ids
                per_dom = {}
                for e in ids:
                    per_dom.setdefault(core._dom(e), []).append(e)
                for d, es in per_dom.items():
                    if d == "cover":
                        srv = {"turn_on": "open_cover", "turn_off": "close_cover"}.get(verbo, verbo)
                    elif verbo in ("turn_on", "turn_off"):
                        srv = verbo
                    else:
                        return None
                    a = {"action": "%s.%s" % (d, srv), "target": {"entity_id": es[0] if len(es) == 1 else es}}
                    if lum is not None:
                        if d != "light" or srv != "turn_on":
                            avv.append("luminosita %d%% non applicabile a %s (e un interruttore)" % (lum, ", ".join(casa.nome(e) for e in es)))
                        else:
                            a["data"] = {"brightness_pct": lum}
                    azioni.append(a)
        if eccez is not None:
            return None
        if attesa:
            azioni = [{"delay": attesa}] + azioni
        if durata_azione:
            accese = [a for a in azioni if a.get("action", "").endswith(".turn_on")]
            if not accese:
                return None
            azioni += [{"delay": durata_azione}] + [{"action": a["action"].replace("turn_on", "turn_off"), "target": a["target"]} for a in accese]
        md = re.search(r"\bper\s+(\d+)\s*(second|minut|or)", seg)
        if md:
            return None
        if sospese:
            nuovo = {"if": sospese, "then": azioni}
            cur["az"].append(nuovo); sospese = []
            blocco = nuovo if apri_blocco else None
            apri_blocco = False
        elif blocco is not None:
            blocco["then"] += azioni
        else:
            cur["az"] += azioni
    chiudi()
    if sospese or not rami or any(not r["tr"] or not r["az"] for r in rami):
        return None
    cfg = {"alias": originale if len(originale) <= 60 else originale[:59].rsplit(" ", 1)[0] + "…", "mode": "single"}
    if len(rami) == 1:
        cfg["triggers"] = rami[0]["tr"]; cfg["conditions"] = list(rami[0]["cond"]); cfg["actions"] = rami[0]["az"]
    else:
        trs, scelte = [], []
        for i, r in enumerate(rami, 1):
            for t in r["tr"]:
                trs.append(dict(t, id="ramo%d" % i))
            scelte.append({"conditions": [{"condition": "trigger", "id": "ramo%d" % i}] + r["cond"], "sequence": r["az"]})
        cfg["triggers"] = trs; cfg["conditions"] = []; cfg["actions"] = [{"choose": scelte}]
        cfg["mode"] = "queued"
    if any("delay" in n for r in rami for n in r["az"] + [x for a in r["az"] for x in a.get("then", [])]):
        cfg["mode"] = "restart"
    if giorni_tutti:
        cfg["conditions"].append({"condition": "time", "weekday": giorni_tutti})
        if len(rami) > 1:
            avv.append("i giorni indicati valgono per tutta l'automazione, anche per gli altri orari")
    y = yaml.dump(cfg, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=1000).rstrip("\n")
    return {"yaml": y, "avvisi": avv, "modello": chieste}


def _testo_evento(tr, casa, originale):
    t = tr[0] if tr else {}
    e = t.get("entity_id", "")
    if "lavatrice" in e or "lavastoviglie" in e or "asciugatrice" in e:
        return "%s: ciclo finito" % casa.nome(e).rsplit(" ", 1)[0]
    if e == "zone.home":
        return "Casa vuota" if "below" in t else "Qualcuno e tornato a casa"
    if t.get("trigger") == "state" and isinstance(t.get("to"), str) and t.get("for"):
        d = t["for"]
        return "%s %s da %s" % (casa.nome(e), {"on": "acceso", "off": "spento", "open": "aperto", "closed": "chiuso"}.get(t["to"], t["to"]),
                               " ".join("%s %s" % (v, {"minutes": "minuti", "seconds": "secondi", "hours": "ore"}[k]) for k, v in d.items()))
    return "Yamlic: " + originale[:100]


def _lista(x):
    return x if isinstance(x, list) else [x] if x else []


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True
