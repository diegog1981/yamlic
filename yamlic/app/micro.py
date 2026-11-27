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
         "chiud": "close_cover", "richiud": "close_cover", "abbass": "close_cover",
         "avvia": "start", "riavvia": "start", "ferma": "stop", "blocca": "lock", "sblocca": "unlock"}
_V = r"(?:ri)?(?:accend|spegn|apr|chiud)\w*|alz\w+|abbass\w+|(?:ri)?avvia\w*|ferma\w*|blocca\w*|sblocca\w*"
_NOTIFICA = r"(?:avvisami|avvertimi|notificami|(?:mandami|inviami|manda|invia)\s+(?:una\s+|un\s+)?(?:notifica|messaggio(?:\s+con\s+scritto)?)(?:\s+(?:sul|al)\s+(?:mio\s+)?(?:telefono|cellulare))?)"
_ANNUNCIO = r"(?:annuncia|annunciami|fai (?:un )?annuncio|di'|dillo|fai dire)"
# dove si puo tagliare: virgole/punti, "poi", e "e/ma" solo se dopo viene un verbo o un nuovo quando/se
_INIZIO = r"(?:(?:metti|imposta|porta|riporta|rimanda)\b|(?:%s)\b|%s\b|%s\b|quando\b|appena\b|se\b|alle\b|all'|al tramonto|a mezz|dopo\b|poi\b|\d+ minuti (?:prima|dopo))" % (_V, _NOTIFICA, _ANNUNCIO)
_TAGLIO = re.compile(r"(?<!\d)[.;!?]+(?!\d)\s*|,\s*(?:(?:e|ma)\s+)?(?:poi\s+)?|\s+(?:e|ed|ma)\s+poi\s+|\s+poi\s+|\s+(?:e|ed|ma|mentre|invece)\s+(?=%s)" % _INIZIO)
# parole che si possono ignorare senza perdere significato
VUOTE = set("""il lo la i gli le l un uno una di del dello della dei degli delle d in nel nello nella nei nelle a al allo alla ai agli alle
su sul sulla sullo e ed che mi me si ci poi anche pure tutte tutti tutta tutto ogni volta giorno giorni per favore automaticamente
allora quindi inoltre per_favore voglio vorrei con ma sera mattina""".split())


def _n(s):
    s = (s or "").lower().replace("’", "'")
    s = re.sub(r"(?<=\w)è", "e", s)
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


# ---------- domande all'utente ----------
class Domanda(Exception):
    """Un pezzo e dubbio: lo chiede Yamlic (non l'AI). La risposta torna in risposte[chiave]."""
    def __init__(self, chiave, testo, opzioni=(), multi=False, riscrivi=False, ricorda=""):
        super().__init__(testo)
        self.d = {"tipo": "micro", "chiave": chiave, "testo": testo, "opzioni": list(opzioni), "multi": multi, "riscrivi": riscrivi, "ricorda": ricorda}


_DOM_VERBO = {"turn_on": ("light", "switch", "fan", "media_player", "input_boolean"), "turn_off": ("light", "switch", "fan", "media_player", "input_boolean"),
              "open_cover": ("cover",), "close_cover": ("cover",), "start": ("vacuum", "switch", "fan"), "stop": ("vacuum", "switch", "fan"),
              "lock": ("lock",), "unlock": ("lock",)}
# parole che cambiano il senso: scegliere solo il dispositivo non basta, il pezzo va riscritto
_PESANTI = re.compile(r"\b(?:non|solo|se|quando|prima|dopo|finche|mentre|altrimenti|oppure|piano|lentamente|gradualmente|meta|volte|ogni|tra|fino)\b|\d")


def _opz(casa, ids):
    return [{"valore": {"entita": [e]}, "nome": casa.nome(e), "dettaglio": e} for e in ids]


def scegli(casa, chiave, testo, ids):
    """Piu dispositivi possibili: torna quelli scelti dall'utente, oppure fa la domanda."""
    r = (casa.risposte.get(chiave) or {}).get("entita")
    r = [e for e in r or [] if e in ids]
    if r:
        return r
    r = ricordata(casa, chiave, ids)
    if r:
        return r
    raise Domanda(chiave, testo, _opz(casa, ids), multi=True, ricorda=chiave)


def ricordata(casa, chiave, ids=None):
    r = [e for e in casa.memoria.get(chiave) or [] if e in casa.by and (ids is None or e in ids)]
    if r and len(r) == len(casa.memoria[chiave]):
        casa.usata.append("uso la scelta che hai gia fatto: %s" % ", ".join(casa.nome(e) for e in r))
        return r
    return []


def candidati(casa, frase, verbo):
    """Dispositivi compatibili col verbo, prima quelli con piu parole in comune col pezzo."""
    parole = set(_resto(frase))
    out = []
    for s in casa.states:
        e = s["entity_id"]
        if core._dom(e) not in _DOM_VERBO.get(verbo, ()) or not casa.vivo(s) or (core._dom(e) == "switch" and core.NOT_LIGHT.search(core._t(s)) and not parole & set(re.findall(r"\w+", casa.testo(s)))):
            continue
        comuni = len(parole & set(re.findall(r"[a-z0-9]+", casa.testo(s))))
        # senza parole in comune propongo solo cose "normali": per accendi/spegni le luci vere
        if not comuni and verbo in ("turn_on", "turn_off") and (not core.is_light_like(e, core._t(s)) or re.search(r"status|display|schermo", casa.testo(s))):
            continue
        out.append((-comuni, casa.nome(e).lower(), e))
    return [x[2] for x in sorted(out)][:8]


def plausibile(azioni, frase):
    """Proposta dell'AI: "spegni la lampada" (uno solo) non puo toccare piu dispositivi."""
    n = sum(len(_lista((a.get("target") or {}).get("entity_id"))) for a in azioni)
    plurale = re.search(r"\b(?:le|i|gli|tutt\w+|e)\b", frase)
    return bool(plurale) or n <= 1


# ---------- entita ----------
class Casa:
    def __init__(self, states):
        aggiungi_stanze(states)
        self.states = [s for s in states]
        self.by = {s["entity_id"]: s for s in states}
        self.risposte, self.domande, self.pezzo = {}, False, ""
        self.memoria, self.usata = {}, []
        self.contesto = []   # stanza citata nel "quando": vale per "accendi i faretti"

    def testo(self, s):
        return _n(core._t(s))

    def vivo(self, s):
        """Disponibile, oppure sistemato dall'utente in "La tua casa" (se e offline per poco resta valido)."""
        a = s.get("attributes") or {}
        return s.get("state") != "unavailable" or bool(a.get("_stanza_utente") or a.get("_funzione"))

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
            detta = (s.get("attributes") or {}).get("_funzione")   # correzione dell'utente: vince sulle regole
            if detta and detta != "luce":
                continue
            if not detta and (core.NOT_LIGHT.search(t) or (core._dom(e) == "switch" and not core.LIGHT_LIKE.search(t) and not solo_stanza)):
                continue
            if stanze and not self._in_stanza(s, stanze, largo):
                continue
            out.append(e)
        if stanze and not out and not largo:   # es. nel "bagno" l'unica luce e in "Bagno Camera"
            return self.luci(stanze, True)
        return out

    def incerti(self, stanze):
        """Interruttori della stanza di cui non si sa cosa comandano (potrebbero essere luci)."""
        out = []
        for s in self.states:
            e, t = s["entity_id"], core._t(s)
            if core._dom(e) != "switch" or not self.vivo(s) or core.NOT_LIGHT.search(t) or core.LIGHT_LIKE.search(t):
                continue
            if (s.get("attributes") or {}).get("_funzione") not in (None, "", "luce"):
                continue
            if self._in_stanza(s, stanze, True):
                out.append(e)
        return out

    def dubbia(self, e, stanze):
        """Il nome cita anche un'altra stanza: "Ingresso bagno" fra le luci dell'ingresso."""
        gruppi = set()
        for r in stanze:
            gruppi |= core.room_words(r)
        a = (self.by.get(e) or {}).get("attributes") or {}
        if a.get("_stanza_utente") and _n(a.get("_area", "")) in gruppi:   # la stanza l'ha scelta l'utente: nessun dubbio
            return False
        nm = _n(self.nome(e))
        return any(re.search(r"\b%s\b" % r, nm) for r in STANZE if r not in gruppi and r not in ("isola", "tavolo", "esterno"))

    def tende(self, stanze=(), largo=False):
        out = [s["entity_id"] for s in self.states if core._dom(s["entity_id"]) == "cover" and self.vivo(s)
               and self.classe(s) not in ("garage", "gate", "door") and (not stanze or self._in_stanza(s, stanze, largo))]
        return self.tende(stanze, True) if stanze and not out and not largo else out

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

    def tv_tutte(self, stanze=()):
        out = []
        for s in self.states:
            e, t = s["entity_id"], self.testo(s)
            if core._dom(e) != "media_player" or not self.vivo(s) or not (self.classe(s) == "tv" or re.search(r"\btv\b|televis", t)):
                continue
            if re.search(r"soundbar|spotify|tablet|tab a\d", t) or (stanze and not self._in_stanza(s, stanze, True)):
                continue
            out.append(e)
        return out

    def unico(self, parola):
        """La parola e nel nome di una sola entita comandabile e non e generica."""
        if len(parola) < 6 or parola in STANZE or parola in core.GENERIC or re.fullmatch(_COSE + "|" + _APPARECCHI, parola):
            return None
        h = [s["entity_id"] for s in self.states if core._dom(s["entity_id"]) in ("light", "switch", "fan", "input_boolean") and self.vivo(s)
             and parola in _n((s.get("attributes") or {}).get("friendly_name", "")).split() and not core.NOT_LIGHT.search(core._t(s).replace("stuf", "").replace("caffe", ""))]
        return h[0] if len(h) == 1 else None

    def per_nome(self, frase, domini=("light", "switch", "fan", "input_boolean", "cover", "media_player", "vacuum")):
        """Entita il cui nome (almeno 2 parole, o 1 parola lunga) compare per intero nella frase."""
        hit = []
        for s in self.states:
            if core._dom(s["entity_id"]) not in domini or not self.vivo(s):
                continue
            nm = _n((s.get("attributes") or {}).get("friendly_name", "")).strip()
            corto = re.sub(r"\s+(?:%s)$" % "|".join(STANZE), "", nm)
            sopr = _n((s.get("attributes") or {}).get("_soprannome", "")).strip()
            if sopr and re.search(r"\b%s\b" % re.escape(sopr), frase):
                hit.append((100 + len(sopr), s["entity_id"], sopr))
                continue
            for n_ in dict.fromkeys([nm, corto]):
                if len(n_) >= 5 and (n_ == nm or (len(n_.split()) >= 2 and not re.match(_COSE + r"\b", n_.split()[-1]))) and re.search(r"\b%s\b" % re.escape(n_), frase):
                    hit.append((len(n_), s["entity_id"], n_))
                    break
        if not hit:
            return [], ""
        top = max(h[0] for h in hit)
        sel = [h for h in hit if h[0] == top]
        return [h[1] for h in sel], sel[0][2]


def simile(testo, casa):
    """Unica entita comandabile il cui nome+id contiene tutte le parole dette (almeno 3). None se zero o piu di una."""
    parole = {w for w in _resto(testo) if len(w) > 1}
    if len(parole) < 3:
        return None
    hit = [s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) in ("light", "switch", "fan", "cover", "media_player", "input_boolean")
           and casa.vivo(s) and parole <= set(re.findall(r"[a-z0-9]+", casa.testo(s)))]
    if not hit:   # singolare/plurale: "barre" = "barra", "faretti" = "faretto"
        radici = [w[:-1] if len(w) > 4 else w for w in parole]
        hit = [s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) in ("light", "switch", "fan", "cover", "media_player", "input_boolean")
               and casa.vivo(s) and all(re.search(r"\b%s" % re.escape(r_), casa.testo(s)) for r_ in radici)]
    return hit[0] if len(hit) == 1 else None


# ---------- gruppi di bersagli ("le luci del giardino e la tv del soggiorno") ----------
_COSE = r"(luc[ei]|lampade?|lampadina|faretti|illuminazione|tende?|tapparell[ae]|serrand[ae]|tv|tele|televisione|televisore)"
_APPARECCHI = r"(?:ventilatore|ventola|serratura|robot aspirapolvere|robot|aspirapolvere|termostato|climatizzatore|condizionatore|riscaldamento)"
_DOM_APP = {"vent": "fan", "serr": "lock", "robo": "vacuum", "aspi": "vacuum", "term": "climate", "clim": "climate", "cond": "climate", "risc": "climate"}


def apparecchi(frase, casa, avv):
    """'il ventilatore in camera', 'la serratura': entita del tipo giusto, unica (nella stanza se detta)."""
    ids = []
    rx = re.compile(r"\b(%s)\b(?:\s+(?:della porta|del portone))?(?:\s+(?:d'ingresso|di ingresso))?(?:%s)?" % (_APPARECCHI, _STANZA))
    while True:
        m = rx.search(frase)
        if not m:
            return ids, frase
        dom = _DOM_APP[m.group(1)[:4]]
        cand = [s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) == dom and casa.vivo(s)]
        if m.group(2):
            cand = [e for e in cand if casa._in_stanza(casa.by[e], [m.group(2)], True)]
        if len(cand) != 1:
            return None, frase
        ids += cand
        frase = _togli(frase, m)


_STANZA = r"(?:\s+(?:del|dello|della|dell'|dei|delle|in|nel|nella|nell'|di)\s*(?:mio\s+|mia\s+)?(%s)\b)" % "|".join(STANZE)
_BASE = STANZE


def aggiungi_stanze(states):
    """Stanze nuove della casa (aree di HA o scritte dall'utente, es. "sala da pranzo") capite anche nelle frasi."""
    global STANZE, _STANZA
    nuove = []
    for s in states:
        ar = " ".join(_n((s.get("attributes") or {}).get("_area") or "").split())
        if len(ar) >= 4 and re.fullmatch(r"[a-z' ]+", ar) and ar not in STANZE and ar not in nuove and not any(re.search(r"\b%s\b" % r, ar) for r in _BASE):
            nuove.append(ar)
    if nuove:
        STANZE = tuple(sorted(nuove, key=len, reverse=True)) + STANZE
        _STANZA = r"(?:\s+(?:del|dello|della|dell'|dei|delle|in|nel|nella|nell'|di)\s*(?:mio\s+|mia\s+)?(%s)\b)" % "|".join(STANZE)


def bersagli(frase, casa, avv, verbo=None):
    """Ritorna (lista di entita, frase residua)."""
    def _parole(x):
        return sorted(w for w in re.findall(r"[a-z0-9]+", x) if w not in ("il", "lo", "la", "i", "gli", "le", "l", "di", "del", "dello", "della", "dell", "dei", "delle", "in", "nel", "nella"))
    detto = _parole(frase)
    esatti = [s_["entity_id"] for s_ in casa.states if core._dom(s_["entity_id"]) in ("light", "switch", "fan", "cover") and casa.vivo(s_)
              and _parole(" ".join(_n(casa.nome(s_["entity_id"])).split())) == detto]
    if not esatti and len(detto) == 2 and "luce" in detto:   # "la luce ingresso" = il dispositivo "Ingresso"
        esatti = [s_["entity_id"] for s_ in casa.states if core._dom(s_["entity_id"]) in ("light", "switch") and casa.vivo(s_)
                  and _parole(" ".join(_n(casa.nome(s_["entity_id"])).split())) == [w for w in detto if w != "luce"]]
    if len(esatti) == 1 and len(detto) >= 2:   # nome detto per intero
        return esatti, ""
    for s_ in casa.states:   # "la luce ingresso bagno" = il dispositivo "Ingresso bagno": "luce" non e un secondo bersaglio
        nm = " ".join(_n(casa.nome(s_["entity_id"])).split())
        if core._dom(s_["entity_id"]) in ("light", "switch") and len(nm.split()) >= 2 and casa.vivo(s_) and not re.search(r"\bluc[ei]\b", nm):
            frase = re.sub(r"\bluce (?=%s\b(?! (?:camera|salotto|cucina)))" % re.escape(nm), "", frase)
    ids, frase = apparecchi(frase, casa, avv)
    if ids is None:
        return None, frase
    for w in re.findall(r"[a-z]+", frase):   # "il lampadario", "la piantana"
        u = casa.unico(w)
        intero = casa.per_nome(frase)[1]
        if len(intero.split()) >= 2 and w in intero.split():   # parte di un nome detto per intero ("faretti veletta")
            u = None
        if u and not re.search(r"\b%s\s+(?:del|della|dell'|in|nel)" % w, frase) and u not in ids:
            ids.append(u); frase = re.sub(r"\b%s\b" % w, " ", frase, count=1)
    # "stufetta del bagno" -> entita che si chiama "Stufetta bagno"
    for s_ in casa.states:
        e = s_["entity_id"]
        nm_ = _n((s_.get("attributes") or {}).get("friendly_name", "")).split()
        if core._dom(e) in ("light", "switch", "fan") and casa.vivo(s_) and len(nm_) >= 2 and not re.fullmatch(_COSE, nm_[0]):
            rx_ = r"\b" + r"\s+(?:(?:del|della|dello|dell'|di|in|nel|nella)\s*)?".join(re.escape(x) for x in nm_ if x not in ("del", "della", "di")) + r"\b"
            m_ = re.search(rx_, frase)
            if m_ and e not in ids:
                ids.append(e); frase = _togli(frase, m_)
    esatti, nm = casa.per_nome(frase)
    sopr = bool(esatti) and nm == _n((casa.by[esatti[0]].get("attributes") or {}).get("_soprannome", "")).strip()
    if sopr:
        ids += esatti
        frase = re.sub(r"\b%s\b" % re.escape(nm), " ", frase)
    elif esatti and len(nm.split()) >= 2 and (not re.fullmatch(r"(?:tv|luc[ei]|tend[ae])\s+(?:%s)" % "|".join(STANZE), nm) or len(esatti) == 1):
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
            if cosa == "faretti":
                solo = [e for e in trovati if "farett" in casa.testo(casa.by[e])]
                trovati = solo or trovati
            tutte = bool(re.search(r"\btutt\w*\s+(?:le\s+|i\s+)?$", frase[:m.start()]))
            if not st and not tutte and casa.contesto:
                vicine = [e for e in casa.luci(casa.contesto) if e in trovati or cosa != "faretti"]
                if not vicine:
                    return None, frase
                trovati = vicine
                avv.append("\"%s\" senza stanza: uso quelle di '%s' (citata nel quando)" % (cosa, " ".join(casa.contesto)))
            elif not st and not tutte and cosa == "faretti" and len(trovati) > 1:
                if not casa.domande:
                    return None, frase
                trovati = scegli(casa, "ent:" + cosa, "Quali faretti?", trovati)
            singolare = cosa in ("luce", "lampada", "lampadina")
            if st and casa.domande:   # stanza detta: guardo anche i nomi che citano piu stanze ("Ingresso bagno", "Luce bagno camera")
                larghi = [e for e in casa.luci(st, True) + casa.incerti(st) if e not in trovati]
                if singolare:
                    trovati = trovati + larghi
                elif not tutte and any(casa.dubbia(e, st) for e in trovati + larghi):
                    trovati = scegli(casa, "ent:" + cosa + " " + stanza, "\"%s %s\": quali intendi?" % (cosa, stanza), trovati + larghi)
            if singolare and len(trovati) > 1 and casa.domande:
                dove = " " + stanza if stanza else ""
                trovati = scegli(casa, "ent:" + cosa + dove, "\"%s%s\": quale intendi?" % (cosa, dove), trovati)
            elif singolare and len(trovati) > 1:
                veri = [e for e in trovati if e.startswith("light.")]
                avv.append("\"%s%s\" puo essere: %s. Controlla il bersaglio" % (cosa, " " + stanza if stanza else "", ", ".join(casa.nome(e) for e in trovati)))
                trovati = (veri or trovati)[:1]
            if singolare and not stanza and not casa.domande:
                trovati = []
        trovati = decidi(casa, cosa, stanza, trovati, frase[:m.start()], avv)
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


_SINGOLARI = ("luce", "lampada", "lampadina", "tenda", "tapparella", "serranda", "tv", "tele", "televisione", "televisore")


def decidi(casa, cosa, stanza, trovati, prima, avv):
    """Regola unica: uno solo -> quello; plurale/"tutte" -> tutti; singolare con piu candidati -> domanda; nessuno -> "non trovo"."""
    plurale = cosa not in _SINGOLARI or bool(re.search(r"\btutt\w*\s+(?:le\s+|i\s+|gli\s+)?$", prima))
    tv = cosa.startswith(("tv", "tele"))
    if tv:
        tutte = casa.tv_tutte([stanza] if stanza else [])
        if len(tutte) > 1:
            trovati = tutte
    if not trovati and casa.domande:
        altre = casa.tv_tutte() if tv else casa.tende() if cosa.startswith(("tend", "tappar", "serrand")) else casa.luci()
        dove = (" in " + stanza) if stanza else ""
        raise Domanda("ent:" + cosa + (" " + stanza if stanza else ""), "Non trovo %s%s. Quale intendi?" % (cosa, dove), _opz(casa, altre[:8]), multi=True, riscrivi=True)
    if plurale or len(trovati) <= 1:
        return trovati
    if casa.domande:
        return scegli(casa, "ent:" + cosa + (" " + stanza if stanza else ""), "\"%s%s\": quale intendi?" % (cosa, " " + stanza if stanza else ""), trovati)
    avv.append("\"%s%s\" puo essere: %s. Controlla il bersaglio" % (cosa, " " + stanza if stanza else "", ", ".join(casa.nome(e) for e in trovati)))
    return trovati[:1]


def escludi(ids, testo, casa, avv):
    """'tranne X e Y': toglie le entita di X e di Y. Se un'eccezione non e chiara ritorna None (non si indovina)."""
    fuori = set()
    for pezzo in re.split(r"\s+(?:e|ed|o)\s+|,", testo.strip()):
        parole = [w for w in re.findall(r"[a-z]+", pezzo) if w not in VUOTE and w not in core.GENERIC and w not in ("quelle", "quella", "quello", "quelli", "dell", "nell")]
        if not parole:
            return None

        def corr(e):
            t = casa.testo(casa.by[e])
            return all(any(re.search(r"\b%s\b" % g, t) for g in core.room_words(w)) for w in parole)
        via = [e for e in ids if corr(e)]
        if parole in (["giardino"],):
            via = [e for e in ids if re.search(r"\bgiardino\b", casa.testo(casa.by[e]))] or via
        if len(parole) == 1:   # "bagno" non deve escludere "Bagno Camera" se esiste il bagno vero
            esatte = [e for e in via if _n((casa.by[e].get("attributes") or {}).get("_area", "")) == parole[0]]
            via = esatte or via
        if not via:
            if len(parole) == 1 and parole[0] in STANZE:   # stanza che fra i bersagli non c'e: non c'e nulla da escludere, ma lo dico
                avv.append("\"tranne %s\": in casa non c'e nessuna entita con quel nome fra i bersagli, quindi non ho escluso nulla" % parole[0])
                continue
            return None
        fuori |= set(via)
    return [e for e in ids if e not in fuori] or None


def binario(casa, classi, dove):
    """Sensore binario unico della classe giusta nella stanza detta."""
    c = [s for s in casa.states if core._dom(s["entity_id"]) == "binary_sensor" and casa.vivo(s) and casa.classe(s) in classi]
    parole = [w for w in _resto(dove or "") if w != "casa"]
    if parole:
        c = [s for s in c if all(any(re.search(r"\b%s\b" % g, casa.testo(s)) for g in core.room_words(w)) for w in parole)]
    return c[0]["entity_id"] if len(c) == 1 else None


def porta_finestra(t, casa):
    """'si apre la finestra della cucina', 'la porta d'ingresso resta aperta' -> (sensore, 'on'/'off')."""
    cosa = r"(?:la |il |una )?(?P<c>porta|finestra|portafinestra)(?P<d>(?: (?:d'|di |del |della |dell'|in |nel )?\s*\w+)?)"
    for pat in (r"(?:si (?P<v>apre|chiude)|viene (?P<w>aperta|chiusa)) " + cosa, cosa + r" (?:si (?P<v>apre|chiude)|(?P<non>non )?(?:viene|resta|rimane|e'|e) (?P<w>aperta|chiusa))"):
        m = re.fullmatch(pat, t)
        if m:
            classi = ("door", "opening") if m.group("c") == "porta" else ("window", "opening")
            dove = re.sub(r"^(?:\s*(?:d'|di |del |della |dell'|in |nel ))", "", m.group("d").strip() + " ").strip()
            e = binario(casa, classi, dove)
            v = m.group("v") or m.group("w")
            aperta = v.startswith("apr") or v.startswith("aper")
            if m.groupdict().get("non"):
                aperta = not aperta
            return (e, "on" if aperta else "off") if e else None
    return None


# ---------- quando / condizioni ----------
def stato_entita(testo, casa, avv):
    """'la tv del soggiorno' -> entita singola per condizioni e trigger di stato."""
    avv2 = []
    e = simile(testo, casa)
    if e:
        return e
    ids, resto = bersagli(testo, casa, avv2)
    if ids and len(ids) == 1 and not _resto(resto):
        avv.extend(avv2)
        return ids[0]
    return None


def nomi_persone(t, casa):
    """"Diego" = la persona "DiegoG" se e l'unica che inizia cosi."""
    for w in set(re.findall(r"[a-z]{4,}", t)):
        cand = [_n(casa.nome(s["entity_id"])).split()[0] for s in casa.states if core._dom(s["entity_id"]) == "person" and _n(casa.nome(s["entity_id"]))]
        hit = [c for c in cand if c.startswith(w) and c != w]
        if len(hit) == 1 and w not in cand:
            t = re.sub(r"\b%s\b" % w, hit[0], t)
    return t


def evento(testo, casa, io, avv):
    """Testo dopo 'quando/appena': ritorna (trigger, condizioni) oppure None."""
    t = nomi_persone(testo.strip(), casa)
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
    m = re.fullmatch(r"(?:vedo (?:del )?movimento|(?:si )?rileva(?:to)?(?: del)? movimento|c'e'? movimento|(?:il sensore )?(?:vede|sente) movimento)(?: (?:in|nel|nella|nell'|all'|al|alla) ?(.+))?", t)
    m2 = re.fullmatch(r"(?:non c'e'? (?:piu )?movimento|non (?:si )?rileva (?:piu )?movimento|non c'e (?:piu )?movimento)(?: (?:in|nel|nella|nell'|all'|al|alla) ?(.+))?", t)
    if m or m2:
        dove = (m or m2).group(1)
        e = binario(casa, ("motion", "occupancy"), dove)
        return fine({"trigger": "state", "entity_id": e, "to": "on" if m else "off"}) if e else None
    if re.fullmatch(r"(?:il )?(?:robot|aspirapolvere)(?: aspirapolvere)? (?:si (?:e' |e )?)?(?:e' |e )?(?:fermato|ferma|bloccato|blocca)", t):
        rb = [s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) == "vacuum" and casa.vivo(s)]
        if len(rb) != 1:
            return None
        avv.append("robot fermo = passa da pulizia a pausa, errore o inattivo (il rientro normale alla base non conta)")
        return fine({"trigger": "state", "entity_id": rb[0], "from": "cleaning", "to": ["paused", "error", "idle"]})
    if re.fullmatch(r"(?:il )?(?:robot|aspirapolvere)(?: aspirapolvere)? (?:ha finito|finisce|ha terminato|termina)(?: di pulire| la pulizia| le pulizie)?", t):
        rb = [s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) == "vacuum" and casa.vivo(s)]
        if len(rb) != 1:
            return None
        avv.append("fine pulizia = il robot passa da pulizia a rientro o base: succede anche se torna per ricaricarsi")
        return fine({"trigger": "state", "entity_id": rb[0], "from": "cleaning", "to": ["returning", "docked"]})
    porta = porta_finestra(t, casa)
    if porta:
        return fine({"trigger": "state", "entity_id": porta[0], "to": porta[1]})
    persone = {_n(casa.nome(s["entity_id"])).split()[0]: s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) == "person"}
    for nome, eid in persone.items():
        if re.fullmatch(r"(?:%s (?:esce|va via)(?: di casa| da casa)?|(?:esce|va via) %s(?: di casa)?)" % (nome, nome), t):
            return fine({"trigger": "state", "entity_id": eid, "to": "not_home"})
        if re.fullmatch(r"(?:%s (?:rientra|torna|arriva)(?: a casa| in casa)?|(?:rientra|torna|arriva) %s(?: a casa| in casa)?)" % (nome, nome), t):
            return fine({"trigger": "state", "entity_id": eid, "to": "home"})
    m = re.fullmatch(r"(accendo|spengo|qualcuno accende|qualcuno spegne) (.+)", t)
    if m:
        eid = stato_entita(m.group(2), casa, avv)
        if not eid:
            return None
        acc = "accen" in m.group(1)
        if core._dom(eid) == "media_player":
            return fine({"trigger": "state", "entity_id": eid, **({"from": ["off", "standby"]} if acc else {"to": ["off", "standby"]})})
        return fine({"trigger": "state", "entity_id": eid, "to": "on" if acc else "off"})
    m = re.fullmatch(r"(?:apro|apre|si apre|viene aperto|qualcuno apre) (?:il |la )?(cancello|portone|garage)|(?:il |la )?(cancello|portone|garage) (?:si apre|viene aperto)", t)
    if m:
        cosa = m.group(1) or m.group(2)
        sw = [s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) == "switch" and casa.vivo(s) and _n(casa.nome(s["entity_id"])) == cosa]
        porta = [s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) in ("cover", "binary_sensor") and casa.vivo(s) and cosa in casa.testo(s)
                 and casa.classe(s) in ("gate", "garage", "garage_door", "door", "opening")]
        if len(porta) == 1:
            return fine({"trigger": "state", "entity_id": porta[0], "to": "open" if porta[0].startswith("cover.") else "on"})
        if len(sw) != 1:
            return None
        avv.append("il %s e un comando a impulso: scatta quando il comando parte da Home Assistant, non sa se si e aperto davvero (non vede il telecomando)" % cosa)
        return fine({"trigger": "state", "entity_id": sw[0], "to": "on"})
    m = re.fullmatch(r"(?:la |il )?(lavatrice|lavastoviglie|asciugatrice) (?:finisce|ha finito|termina|ha terminato)(?: il (?:ciclo|lavaggio))?(?: di lavare| di asciugare)?", t)
    if m:
        pot = [s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) == "sensor" and m.group(1) in casa.testo(s)
               and (casa.classe(s) == "power" or (s.get("attributes") or {}).get("unit_of_measurement") == "W")]
        if len(pot) != 1:
            return None
        avv.append("fine ciclo %s = potenza sotto 5 W per 3 minuti (e prima era sopra): controlla soglia e durata sul tuo elettrodomestico" % m.group(1))
        tr = {"trigger": "numeric_state", "entity_id": pot[0], "below": 5, "for": per or {"minutes": 3}}
        return tr, []
    m = re.fullmatch(r"(?:la |l')?(temperatura|umidita|potenza) (?:del |della |dell'|in |nel |nella )?(.+?) (supera|va sopra|sale sopra|passa|e' sopra|e' piu' di|scende sotto|va sotto|e' sotto|e' meno di) (?:i |il |le )?(\d+(?:[.,]\d+)?)\s*(?:gradi|°c?|%|per ?cento|watt|w)?", t)
    if m:
        classe = {"temperatura": "temperature", "umidita": "humidity", "potenza": "power"}[m.group(1)]
        parole = _resto(m.group(2))
        cand = [s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) == "sensor" and casa.vivo(s) and casa.classe(s) == classe
                and (classe == "power" or not re.search(r"batter|\bcpu\b|chip|processor|\besp\b|dispositivo|device|interna", casa.testo(s)))
                and parole and all(any(re.search(r"\b%s\b" % g, casa.testo(s)) for g in core.room_words(w)) for w in parole)]
        if len(cand) != 1:
            if not cand:
                avv.append("nessun sensore di %s in \"%s\"" % (m.group(1), m.group(2)))
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
    t = nomi_persone(" ".join(testo.split()), casa)
    if re.fullmatch(r"(?:fuori )?(?:sta )?(?:piove(?:ndo)?)(?: fuori)?", t):
        w = [s["entity_id"] for s in casa.states if core._dom(s["entity_id"]) == "weather"]
        if not w:
            avv.append("per la pioggia serve un'entita meteo, in casa non la trovo")
            return None
        avv.append("pioggia presa dal meteo (%s): sono previsioni online, non un sensore" % casa.nome(w[0]))
        return {"condition": "state", "entity_id": w[0], "state": ["rainy", "pouring"]}
    if re.fullmatch(r"(?:non c'e' nessuno|non c'e nessuno|la casa e' vuota|(?:noi )?siamo (?:tutti )?(?:fuori(?: casa| di casa)?|via)|non siamo (?:a|in) casa)(?: in casa| a casa)?", t):
        return {"condition": "numeric_state", "entity_id": "zone.home", "below": 1}
    if re.fullmatch(r"(?:c'e' qualcuno|c'e qualcuno|qualcuno e' )(?: in casa| a casa)?", t):
        return {"condition": "numeric_state", "entity_id": "zone.home", "above": 0}
    if re.fullmatch(r"(?:e' |e |fa )?(?:gia |ancora )?buio|e' notte", t):
        return {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}
    if re.fullmatch(r"(?:e' |e |c'e' )?(?:ancora )?(?:giorno|chiaro|luce)", t):
        return {"condition": "state", "entity_id": "sun.sun", "state": "above_horizon"}
    for s_ in casa.states:
        if core._dom(s_["entity_id"]) == "person":
            nome = _n(casa.nome(s_["entity_id"])).split()[0]
            m_ = re.fullmatch(r"%s (?P<no>non )?(?:e'|e) (?P<d>a casa|in casa|fuori|fuori casa|via)" % nome, t)
            if m_:
                dentro = m_.group("d").endswith("casa") and not m_.group("d").startswith("fuori")
                return {"condition": "state", "entity_id": s_["entity_id"], "state": "home" if dentro != bool(m_.group("no")) else "not_home"}
    if re.fullmatch(r"(?:le )?luci (?:sono )?(?:(?:rimaste|restate|ancora) )?accese", t):
        ids = casa.luci()
        return {"condition": "state", "entity_id": ids, "state": "on", "match": "any"} if ids else None
    pf = porta_finestra(t, casa)
    if pf:
        return {"condition": "state", "entity_id": pf[0], "state": pf[1]}
    m = re.fullmatch(r"(?P<s>.+?) (?P<non>non )?(?:e'|sono|risulta|risultano|e) (?:ancora |gia )?(?P<st>acces[aoei]|spent[aoei]|apert[aoei]|chius[aoei])", t) or \
        re.fullmatch(r"(?P<non>non )?(?:e'|sono|e) (?:ancora |gia )?(?P<st>acces[aoei]|spent[aoei]|apert[aoei]|chius[aoei]) (?P<s>.+)", t)
    if not m:
        return None
    eid = stato_entita(m.group("s"), casa, avv)
    if not eid and re.search(r"\b(?:le|i|gli|tutt\w*)\b", m.group("s")) and re.search(r"[ei]$", m.group("st")):   # plurale: tutte devono essere cosi
        ids, resto = bersagli(m.group("s"), casa, [])
        if ids and not _resto(resto) and len({core._dom(e) for e in ids}) == 1 and core._dom(ids[0]) in ("light", "switch", "cover", "fan"):
            eid = ids
    if not eid:
        return None
    v = m.group("st")
    stato = "on" if v.startswith("acc") else "off" if v.startswith("spe") else "open" if v.startswith("ap") else "closed"
    if m.groupdict().get("non"):   # "non e accesa" = spenta, "non e aperta" = chiusa
        stato = {"on": "off", "off": "on", "open": "closed", "closed": "open"}[stato]
    dom_ = core._dom(eid[0] if isinstance(eid, list) else eid)
    if dom_ != "cover":
        stato = {"open": "on", "closed": "off"}.get(stato, stato)
    c = {"condition": "state", "entity_id": eid, "state": stato}
    if isinstance(eid, list):
        return c
    if core._dom(eid) == "media_player" and stato == "off":
        c = {"condition": "state", "entity_id": eid, "state": ["off", "standby"]}
    if core._dom(eid) == "media_player" and stato == "on":
        c = {"condition": "not", "conditions": [{"condition": "state", "entity_id": eid, "state": ["off", "standby", "unavailable"]}]}
    return c


def condizioni(testo, casa, avv):
    """Una o piu condizioni unite da "e" / "o". Lista di condizioni da mettere insieme, oppure None."""
    c = condizione(testo, casa, avv)
    if c:
        return [c]
    for rx, tipo in ((r"\s+(?:o|oppure)\s+(?:se\s+)?", "or"), (r"\s+(?:e|ed|ma)\s+(?:se\s+|solo se\s+)?", "and")):
        pezzi = [z for z in re.split(rx, testo.strip()) if z]
        if len(pezzi) < 2:
            continue
        fatte = [condizioni(z, casa, avv) for z in pezzi]
        if all(fatte):
            if tipo == "or":
                return [{"condition": "or", "conditions": [x[0] if len(x) == 1 else {"condition": "and", "conditions": x} for x in fatte]}]
            return [x for gruppo in fatte for x in gruppo]
    return None


_MODI = ("piano", "lentamente", "gradualmente", "dolcemente", "delicatamente", "velocemente")


def significato(p, casa):
    """Parole con piu significati: si chiede, non si sceglie in silenzio."""
    m = re.fullmatch(r"(?:lascia|tieni) (.+?) acces[aoei] (?:di sera|la sera|tutta la sera|stasera)", p)
    if m:
        cosa = m.group(1)
        raise Domanda("riscrivi:" + p, "\"La sera\": da quando a quando?",
                      [{"valore": {"testo": "al tramonto accendi %s e %s spegni %s" % (cosa, quando_, cosa)}, "nome": nome_, "dettaglio": ""}
                       for quando_, nome_ in (("alle 23", "Dal tramonto alle 23"), ("alle 0", "Dal tramonto a mezzanotte"), ("all'alba", "Dal tramonto all'alba"))], riscrivi=True)
    if not re.search(r"\d|tramonto|\balba\b|mezzanotte|mezzogiorno|quando|appena|\bbuio\b", p):
        for rx, testo, scelte in ((r"\b(?:tutte le sere|la sera|di sera|stasera)\b", "la sera", (("al tramonto", "al tramonto"), ("alle 20", "alle 20"), ("alle 22", "alle 22"))),
                                  (r"\b(?:tutte le mattine|la mattina|di mattina)\b", "la mattina", (("all'alba", "all'alba"), ("alle 7", "alle 7"), ("alle 8", "alle 8")))):
            m = re.search(rx, p)
            if m:
                raise Domanda("riscrivi:" + m.group(0), "\"%s\": a che ora?" % testo,
                              [{"valore": {"testo": v}, "nome": n, "dettaglio": ""} for v, n in scelte], riscrivi=True)
    if re.search(r"\bvento\b|ventoso", p):
        raise Domanda("riscrivi:" + p, "Il vento non lo so gestire: servirebbe un sensore o una soglia sul meteo. Scrivi un altro evento.", riscrivi=True)
    if re.search(r"cancello.*\b(?:rimasto|ancora|e') apert|cancello apert", p) and not any(casa.classe(s) in ("gate", "garage_door", "door", "opening") and "cancell" in casa.testo(s) for s in casa.states):
        raise Domanda("riscrivi:" + p, "Non posso sapere se il cancello e aperto: in casa non trovo un sensore sul cancello.", riscrivi=True)
    m = re.fullmatch(r"(?:lascia|tieni) (.+?) acces([ao]) (?:di notte|la notte|tutta la notte|durante la notte)", p)
    if m:
        cosa, o = m.group(1), m.group(2)
        raise Domanda("riscrivi:" + p, "\"Di notte\": da quando a quando?",
                      [{"valore": {"testo": "al tramonto accendi %s e all'alba spegni %s" % (cosa, cosa)}, "nome": "Dal tramonto all'alba", "dettaglio": ""},
                       {"valore": {"testo": "alle 22 accendi %s e alle 7 spegni %s" % (cosa, cosa)}, "nome": "Dalle 22 alle 7", "dettaglio": ""},
                       {"valore": {"testo": "alle 0 accendi %s e alle 6 spegni %s" % (cosa, cosa)}, "nome": "Da mezzanotte alle 6", "dettaglio": ""}], riscrivi=True)
    m = re.search(r"\bfa (freddo|caldo)\b", p)
    if m:
        ha = any(casa.classe(s) == "temperature" and casa.vivo(s) and not re.search(r"batter|cpu", casa.testo(s)) for s in casa.states)
        raise Domanda("riscrivi:" + m.group(0), ("\"%s\": scrivi la soglia, per esempio \"la temperatura scende sotto 18 gradi\"." % m.group(0)) if ha
                      else "\"%s\": in casa non trovo un sensore di temperatura. Scrivi un altro evento." % m.group(0), riscrivi=True)


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
    m = re.search(r"\bdi notte\b", seg)
    if m and not re.search(r"\balle\b", seg):
        cond.append({"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}); seg = _togli(seg, m)
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
    mm = re.search(r"\b(?:il|ogni) (%s) e (?:il |la )?(%s)\b" % ("|".join(GIORNI_IT), "|".join(GIORNI_IT)), seg)
    if mm and not giorni:
        giorni, seg = [GIORNI_IT[mm.group(1)], GIORNI_IT[mm.group(2)]], _togli(seg, mm)
    m = re.search(r"\b(?:ogni|il|la|solo il|solo la)\s+(%s)\b" % "|".join(GIORNI_IT), seg)
    if m and not giorni:
        giorni, seg = [GIORNI_IT[m.group(1)]], _togli(seg, m)
    return tr, cond, giorni, seg


def messaggio(originale, chiave):
    m = re.search(chiave + r".*?(?:\b(?:che|dicendo(?: che)?|dimmi)\b|:|\bdi')\s*[\"«]?(.+?)[\"»]?\s*$", originale, re.I)
    if m:
        t = m.group(1).strip().rstrip(".")
        return t[0].upper() + t[1:]
    m = re.search(r"[\"«]([^\"»]+)[\"»]", originale)
    return m.group(1) if m else None


def parlato(p):
    """Forme parlate -> forme che le regole capiscono (usata anche dal controllo finale)."""
    for da, a_ in ((r"\bun quarto d'ora\b", "15 minuti"), (r"\b(alle \d{1,2}) e un quarto\b", r"\1:15"), (r"\b(alle \d{1,2}) e tre quarti\b", r"\1:45"),
                   (r"\ball'una\b(?! e)", "alle 1:00"), (r"\bal calar del sole\b", "al tramonto"), (r"\bmentre\b", "solo se"), (r"\be (?:sono )?(?=dopo le \d)", " "), (r"\bcon scritto\b", "che"), (r"\bmezz'ora\b", "30 minuti"), (r"\ba mezzanotte e mezz[ao]\b", "alle 0:30"),
                   (r"\b(?:quando )?(?:fa|si fa) buio\b(?= (?:accend|spegn|apr|chiud|alz|abbass))", "al tramonto"),
                   (r"\b(?:tranne|escluso|eccetto) (?:il |nel )?(?:weekend|fine settimana)\b", "nei giorni feriali"),
                   (r"\bfa(?:i|llo|lla) ripartire\b", "riavvialo"), (r"\bfai partire\b", "avvia"),
                   (r"\b(?:la mattina|di mattina|tutte le mattine)\b(?= alle)", " "),
                   (r"\b(?:tutte le sere|la sera|tutti i giorni|ogni giorno)\b(?= alle (?:1[3-9]|2[0-3])\b)", " "),
                   (r"\b(?:ogni giorno|tutti i giorni)\b", " "), (r"\b(?:pero|comunque)\b", " "),
                   (r"\b(?:tutte le sere|tutti i giorni|ogni giorno)(?= *$)", " ")):
        p = re.sub(da, a_, p)
    p = re.sub(r"\b(alle \d{1,2}(?:[:.]\d\d)?) (?:tutte le sere|tutti i giorni|ogni giorno)\b", r"\1", p)
    p = re.sub(r"\b(luce|luci)( (?:del|della|dell') ?\w+) e (?=(?:del|della|dell')\b)", r"\1\2 e le luci ", p)
    p = re.sub(r"\b((?:ri)?(?:accendi|spegni|apri|chiudi|avvia|ferma)) (?=(?:luci|tende|tapparelle|faretti)\b)", lambda m: m.group(1) + (" i " if p[m.end():].startswith("faretti") else " le "), p)
    p = re.sub(r"\b((?:ri)?(?:avvia|ferma)) (?=(?:robot|aspirapolvere)\b)", r"\1 il ", p)
    p = " ".join(p.split())
    # "se sono le 22 (e sono ancora aperte)": e un orario; chiudere cio che e gia chiuso non fa nulla
    ms = re.search(r"\bse sono le (\d{1,2}(?:[:.]\d\d)?)\b(?: e (?:le tende |le tapparelle )?sono (?:ancora )?(?:aperte|accese|accesi))?", p)
    if ms and re.search(r"\b(?:chiudi|spegni)\b", p):
        p = "alle %s %s" % (ms.group(1), (p[:ms.start()] + p[ms.end():]).strip(" ,"))
    p = re.sub(r"\bse (?:e'|e) mezzanotte\b", "a mezzanotte", p)
    if re.search(r"\balle\b|\ba mezzanotte\b", p):   # "spegni X a mezzanotte se e ancora accesa": inutile controllarlo
        for verbo_, stato_ in (("chiudi", "apert"), ("spegni", "acces")):
            if re.search(r"\b%s\b" % verbo_, p):
                p = re.sub(r"\s*\b(?:e |se )+(?:le tende |le tapparelle )?(?:e'|e|sono) (?:ancora )?%s[aoei]\b" % stato_, "", p)
        m_ = re.match(r"^(.*?) (a mezzanotte|alle \d{1,2}(?:[:.]\d\d)?)$", p)
        if m_ and " e " not in m_.group(1) and "quando" not in p:
            p = "%s %s" % (m_.group(2), m_.group(1))
    p = re.sub(r"\bun'ora\b", "1 ora", p)
    p = re.sub(r"\b(resta|rimane) (acces[ao]|apert[ao]) ((?:il |la |l').+?) per (?:piu' di |piu di |oltre |almeno )?(\d+) (second\w|minut\w|ora|ore)\b", r"\3 \1 \2 per \4 \5", p)
    p = re.sub(r"\bfa(?:i)? (?:tornare|rientrare) (il robot|l'aspirapolvere) (?:alla|in) base\b", r"riporta \1 alla base", p)
    p = re.sub(r"\bguardo la (tv|televisione)\b", r"accendo la \1", p)
    p = re.sub(r"\bescono tutti\b", "tutti escono", p)
    if re.search(r"\b(?:quando|appena)\b|\bse (?:rimane|resta)\b", p):
        p = re.sub(r"(?<!\d )\bdi sera\b", "di notte", p)
    chi_ = r"(?:torna qualcuno|rientra qualcuno|qualcuno (?:torna|rientra)|tutti escono)(?: a casa| in casa| di casa)?"
    m_ = re.match(r"^se (.+?) e (%s) (.+)$" % chi_, p)
    if m_:
        p = "quando %s %s se %s" % (m_.group(2), m_.group(3), m_.group(1))
    m_ = re.match(r"^(avvisami|avvertimi|mandami una notifica) se (%s) e (.+)$" % chi_, p)
    if m_:
        p = "quando %s %s se %s" % (m_.group(2), m_.group(1), m_.group(3))
    m_ = re.match(r"^(?!se\b|quando\b)(.+?) se (%s)$" % chi_, p)
    if m_:
        p = "quando %s %s" % (m_.group(2), m_.group(1))
    p = re.sub(r"^(avvisami|avvertimi|mandami una notifica) se (.*(?:fermat\w+|si ferma|bloccat\w+|ha finito.*|finisce.*|resta .*|rimane .*|%s))$" % chi_, r"quando \2 \1", p)
    p = re.sub(r"^(di notte )?se (.+? (?:resta|rimane) (?:acces|apert)[ao] per \d+ \w+) (.+)$", lambda m: "quando %s %s%s" % (m.group(2), m.group(1) or "", m.group(3)), p)
    p = " ".join(p.split())
    # "le tende di cucina e soggiorno" -> una frase completa per stanza
    r_ = "|".join(STANZE)
    prep = r"(?:(?:di|del|dello|della|dell'|in|nel|nella|nell') ?)"
    rx = r"\b(tende|tapparelle|serrande|luci|faretti)( %s(?:%s))(?:,| e) (%s)?(?=(?:%s)\b)" % (prep, r_, prep, r_)
    prima = None
    while prima != p:
        prima = p
        p = re.sub(rx, lambda m: "%s%s e %s %s %s" % (m.group(1), m.group(2), "i" if m.group(1) == "faretti" else "le", m.group(1), m.group(3) or "di "), p)
    return p


def ripeti_verbo(p):
    """"accendi X e le Y" -> "accendi X e accendi le Y": ogni micro-frase e completa (per le regole e per l'AI)."""
    if re.search(r"\b(?:tranne|eccetto|esclus\w+|ma non|volume|per \d+|per un|dopo \d+)\b|%|per ?cento|gradi", p):
        return p
    pezzi = re.split(r"([.;,]| poi )", p)
    for i, z in enumerate(pezzi):
        m = re.search(r"\b((?:ri)?(?:accendi|spegni|apri|chiudi)|alza|abbassa)\s", z)
        if m:
            coda = re.split(r"(\b(?:quando|appena|se)\b)", z[m.end():], maxsplit=1)
            if not re.search(r"\b(?:alle|all'|tramonto|alba|mezzanotte|mezzogiorno)\b", coda[0]):   # l'orario in fondo vale per tutto l'elenco
                testa = z[:m.end()]

                def _verbo(mm, testa=testa, coda0=coda[0]):   # il verbo piu vicino prima di "e la/le ..."
                    vs = re.findall(r"\b((?:ri)?(?:accendi|spegni|apri|chiudi)|alza|abbassa)\s", testa + coda0[:mm.start()])
                    return " e %s " % (vs[-1] if vs else m.group(1))
                coda[0] = re.sub(r" e (?=(?:il |lo |la |i |gli |le |l'))", _verbo, coda[0])
            pezzi[i] = z[:m.end()] + "".join(coda)
    return "".join(pezzi)


_AZIONE_IT = {"turn_on": "accendo", "turn_off": "spengo", "open_cover": "apro", "close_cover": "chiudo", "start": "avvio", "stop": "fermo",
              "lock": "blocco", "unlock": "sblocco", "set_temperature": "imposto la temperatura di", "volume_set": "regolo il volume di",
              "set_cover_position": "sposto", "toggle": "inverto", "return_to_base": "rimando alla base"}


def descrivi(azioni, casa):
    """Azioni in italiano semplice, per le proposte."""
    out = []
    for a in azioni:
        svc = str(a.get("action", "")).split(".")[-1]
        nomi = ", ".join(casa.nome(e) for e in _lista((a.get("target") or {}).get("entity_id")))
        dati = ", ".join("%s %s" % (k, v) for k, v in (a.get("data") or {}).items())
        out.append(" ".join(x for x in (_AZIONE_IT.get(svc, svc), nomi, "(%s)" % dati if dati else "") if x))
    return " + ".join(out)


_ESEMPI = {"evento": "\"la luce del corridoio resta accesa per 10 minuti\", \"il robot torna alla base\", \"Martina esce di casa\"",
           "condizione": "\"la TV e accesa\", \"c'e qualcuno in casa\", \"e buio\""}


def pezzo_ai(casa, tipo, testo, avv):
    """Evento o condizione non capiti dalle regole: li chiedo all'AI da soli; se non regge, chiedo all'utente solo questo pezzo."""
    testo = " ".join(testo.split()).strip(" ,")
    if not testo:
        return None
    out = casa.chiedi_pezzo(tipo, testo.replace("e'", "è")) if getattr(casa, "chiedi_pezzo", None) else None
    if out:
        out = rifai_entita(casa, testo, out, avv)
    if out:
        avv.append("\"%s\": capito con l'AI, controlla il riassunto" % testo.replace("e'", "è"))
        return out
    if casa.domande:
        raise Domanda("riscrivi:" + testo, "Questo pezzo non lo capisco: \"%s%s\". Riscrivi solo questo, per esempio %s." % ("quando " if tipo == "evento" else "se ", testo.replace("e'", "è"), _ESEMPI[tipo]), riscrivi=True)
    return None


def rifai_entita(casa, testo, out, avv):
    """Dell'entita proposta dall'AI tengo il tipo; la scelta la rifa Yamlic con le parole del pezzo.
    Uguale -> ok; diversa dello stesso tipo -> uso la mia; di un altro tipo -> scarto; dubbia -> domanda (dalla regola unica)."""
    import json as _j
    testo_out = _j.dumps(out)
    for e in sorted(set(re.findall(r'"([a-z_]+\.[a-z0-9_]+)"', testo_out))):
        if e in ("zone.home", "sun.sun") or e.startswith(("person.", "weather.")) or e not in casa.by:
            continue
        ids, _ = bersagli(testo, casa, [])   # puo fare domanda se la scelta e dubbia
        if not ids or e in ids:
            continue
        if len(ids) == 1 and core._dom(ids[0]) == core._dom(e):
            avv.append("\"%s\": l'AI aveva scelto %s, uso %s" % (testo, casa.nome(e), casa.nome(ids[0])))
            testo_out = testo_out.replace('"%s"' % e, '"%s"' % ids[0])
        else:
            return None
    return _j.loads(testo_out)


_PASSATI = ((r"\bse (.+?) (?:e'|e) (?:finit|terminat)[oa]\b", r"quando \1 finisce"), (r"\bse (.+?) ha (?:finito|terminato)\b", r"quando \1 finisce"),
            (r"\bse sono (?:tornat|rientrat)[oa]\b", "quando torno"), (r"\bse sono arrivat[oa]\b", "quando arrivo"), (r"\bse sono uscit[oa]\b", "quando esco"),
            (r"\bse ([a-z]+) (?:e'|e) (?:tornat|rientrat)[oa]\b", r"quando \1 torna"), (r"\bse ([a-z]+) (?:e'|e) arrivat[oa]\b", r"quando \1 arriva"),
            (r"\bse ([a-z]+) (?:e'|e) uscit[oa]\b", r"quando \1 esce"),
            (r"\bse si (?:e'|e) (apert|chius|aces|acces|spent)[oa] (.+?)(?= (?:%s)\b|$)" % _V, lambda m: "quando si %s %s" % ({"apert": "apre", "chius": "chiude", "acces": "accende", "aces": "accende", "spent": "spegne"}[m.group(1)], m.group(2))),
            (r"\bse (.+?) si (?:e'|e) (apert|chius|acces|spent|fermat|bloccat)[oa]\b", lambda m: "quando %s si %s" % (m.group(1), {"apert": "apre", "chius": "chiude", "acces": "accende", "spent": "spegne", "fermat": "ferma", "bloccat": "blocca"}[m.group(2)])))


def passati(p):
    """"se la lavatrice e finita" = "quando la lavatrice finisce": vale solo se non c'e gia un quando o un orario."""
    if re.search(r"\b(?:quando|appena|ogni volta|alle|dalle|dopo le|prima delle)\b|tramonto|\balba\b|mezzanotte|mezzogiorno", p):
        return p
    for rx, a in _PASSATI:
        p = re.sub(rx, a, p, count=1)
    return p


def telefono(casa, chi, mob):
    """Il servizio notify del telefono di una persona (dal suo device_tracker), oppure domanda."""
    pers = [s for s in casa.states if core._dom(s["entity_id"]) == "person" and (s["entity_id"] == chi or _n(casa.nome(s["entity_id"])).split()[:1] == [chi]
            or _n(casa.nome(s["entity_id"])).startswith(chi))]
    trov = []
    for s in pers[:1]:
        for dt in (s.get("attributes") or {}).get("device_trackers") or []:
            obj = str(dt).split(".", 1)[-1]
            trov += [m for m in mob if m == "mobile_app_" + obj or obj in m]
        if not trov:
            nome = _n(casa.nome(s["entity_id"])).split()[0]
            trov = [m for m in mob if nome in m]
    trov = list(dict.fromkeys(trov))
    r = (casa.risposte.get("tel:" + chi) or {}).get("entita")
    if r:
        return str(r[0]).replace("notify.", "")
    if len(trov) == 1:
        return trov[0]
    if casa.domande:
        raise Domanda("tel:" + chi, "Su quale telefono avviso %s?" % chi.capitalize(), [{"valore": {"entita": ["notify." + m]}, "nome": m.replace("mobile_app_", "").replace("_", " "), "dettaglio": "notify." + m} for m in mob])
    return None


_SCENA = re.compile(r"^(?:crea(?:mi)? |fai(?:mi)? |imposta )?(?:la |una )?scena\s*(?P<nome>[a-z' ]*?)\s*(?::|,|\bche\b|\bcon\b|\bdove\b)\s*(?P<corpo>.+)$")
_TERZA = {"chiude": "chiudi", "spegne": "spegni", "accende": "accendi", "apre": "apri", "abbassa": "abbassa", "alza": "alza", "avvia": "avvia"}
_STATO_SCENA = {"turn_off": "off", "turn_on": "on", "close_cover": "closed", "open_cover": "open"}


def scena(prompt, casa, notify, io, chiedi):
    """"Scena cinema: chiudi le tende, spegni le luci del salotto e lascia accesa solo la striscia led" -> scena di HA."""
    p = " ".join(_n(prompt).split()).rstrip(" .!")
    m = _SCENA.match(p)
    if not m:
        return None
    nome = " ".join(m.group("nome").split()) or "nuova"
    corpo = re.sub(r"\b(%s)\b" % "|".join(_TERZA), lambda x: _TERZA[x.group(1)], m.group("corpo"))
    tieni = None
    mt = re.search(r"\s*(?:,|\be\b)?\s*lascia(?:ndo)? acces[aoei] (?:solo |soltanto )?(.+?)\s*$", corpo)
    if mt:
        tieni, corpo = mt.group(1), corpo[:mt.start()]
    r = _costruisci("alle 12:00 " + corpo, casa, notify, io, chiedi)
    if not r:
        if casa.domande:
            raise Domanda("riscrivi:" + corpo.strip(), "Scena %s: non ho capito cosa deve fare \"%s\". Riscrivi solo questo pezzo." % (nome, corpo.strip()), riscrivi=True)
        return None
    stati = {}
    for a in core._azioni_tutte(yaml.safe_load(r["yaml"]).get("actions")):
        dom, _, srv = a["action"].partition(".")
        st = _STATO_SCENA.get(srv)
        if not st or dom not in ("light", "switch", "cover", "media_player", "fan"):
            return None
        for e in _lista((a.get("target") or {}).get("entity_id")):
            stati[e] = st
    if tieni:
        ids, resto = bersagli(tieni, casa, [])
        if not ids or _resto(resto):
            if not casa.domande:
                return None
            ids = scegli(casa, "ent:" + tieni, "Scena %s: quale luce resta accesa (\"%s\")?" % (nome, tieni), sorted(casa.luci(), key=casa.nome))
        for e in ids:
            stati[e] = "on"
    if not stati:
        return None
    cfg = {"name": "Scena " + nome.capitalize() if not nome.startswith("scena") else nome.capitalize(), "entities": stati}
    avv = ["e una SCENA: la attivi a mano o da un'automazione (scene.turn_on)"] + r["avvisi"]
    return {"yaml": yaml.dump(cfg, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=1000).rstrip("\n"), "avvisi": avv, "modello": r["modello"], "frase": prompt, "fisso": True, "scena": True}


def da_condizioni(cs):
    """"se A e B fai X": un trigger per ogni condizione che diventa vera. None se una non si sa trasformare."""
    out = []
    for c in cs:
        k = c.get("condition")
        if k == "numeric_state":
            out.append({"trigger": "numeric_state", **{x: c[x] for x in ("entity_id", "above", "below") if x in c}})
        elif k == "state" and c.get("entity_id") == "sun.sun":
            out.append({"trigger": "sun", "event": "sunset" if c["state"] == "below_horizon" else "sunrise"})
        elif k == "state":
            out.append({"trigger": "state", "entity_id": c["entity_id"], "to": c["state"]})
        else:
            return None
    return out


_PRESENZA = r"\b(?:simul\w+\b.*\bpresenza|presenza simulata|(?:random|a caso|casual\w*)\b.*\b(?:luc[ei])|luc[ei]\b.*\b(?:random|a caso|casual\w*))"


def presenza(prompt, casa):
    """Simulazione di presenza: le luci le sceglie sempre l'utente."""
    p = " ".join(_n(prompt).split())
    if not re.search(_PRESENZA, p) or not re.search(r"fuori|\bvia\b|vacanza|non (?:c'e|ci siamo|siamo)|nessuno|vuota|presenza", p):
        return None
    if re.search(r"\balle\b|\bdalle\b|tranne|ogni \d", p):
        return None
    scelta = (casa.risposte.get("presenza:luci") or {}).get("entita")
    if not scelta:
        if not casa.domande:
            return None
        raise Domanda("presenza:luci", "Quali luci accendo e spengo a caso quando non c'e nessuno?", _opz(casa, sorted(casa.luci(), key=casa.nome)), multi=True)
    scelta = [e for e in scelta if re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", str(e))]
    cfg = {"alias": "Simulazione presenza", "mode": "single",
           "triggers": [{"trigger": "time_pattern", "minutes": "/20"}],
           "conditions": [{"condition": "numeric_state", "entity_id": "zone.home", "below": 1}, {"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}],
           "actions": [{"delay": {"minutes": "{{ range(0, 10) | random }}"}},
                       {"action": "homeassistant.toggle", "target": {"entity_id": "{{ [%s] | random }}" % ", ".join("'%s'" % e for e in scelta)}}]}
    avv = ["ogni 20-30 minuti circa cambio stato a una luce a caso tra: %s" % ", ".join(casa.nome(e) for e in scelta),
           "solo con casa vuota e quando e buio; al rientro le luci restano come sono"]
    return {"yaml": yaml.dump(cfg, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=1000).rstrip("\n"), "avvisi": avv, "modello": 0, "frase": prompt, "fisso": True}


def costruisci(prompt, states, notify=(), io="", chiedi=None, risposte=None, domande=False, memoria=None, pezzo=None):
    """Come _costruisci; con domande=True un pezzo dubbio diventa {"domanda": ...} invece di None."""
    casa = Casa(states)
    casa.risposte, casa.domande, casa.memoria = dict(risposte or {}), domande, dict(memoria or {})
    casa.chiedi_pezzo = pezzo
    try:
        r = presenza(prompt, casa) or scena(prompt, casa, notify, io, chiedi) or _costruisci(prompt, casa, notify, io, chiedi)
    except Domanda as d:
        return {"domanda": d.d}
    if r is None and domande and casa.pezzo:
        return {"domanda": Domanda("riscrivi:" + casa.pezzo, "Non ho capito \"%s\". Riscrivi questo pezzo con altre parole." % casa.pezzo.replace("e'", "è"), riscrivi=True).d}
    if r and r.get("yaml"):
        for e in sorted(set(re.findall(r"\b[a-z_]+\.[a-z0-9_]+\b", r["yaml"])) & set(casa.by)):
            if casa.by[e].get("state") == "unavailable":
                r["avvisi"].append("%s adesso e offline: l'automazione funzionera quando torna disponibile" % casa.nome(e))
    if r and casa.usata:
        r["avvisi"] += list(dict.fromkeys(casa.usata))
    return r


def _costruisci(prompt, casa, notify, io, chiedi):
    """Ritorna dict {yaml, avvisi, modello(n. micro-frasi chieste)} oppure None se la richiesta non e capita del tutto."""
    states, avv = casa.states, []
    originale = prompt.strip()
    p = " ".join(_n(originale).split()).rstrip(" .!")
    if not p or len(p) > 400:
        return None
    # il testo di notifiche/annunci non va spezzato: lo metto da parte
    testo_msg = None
    m = re.search(r"\b(?:%s|%s)\b[^,.;]*?\b(che|dicendo che|dicendo|:|di'|dimmi)\s+" % (_NOTIFICA, _ANNUNCIO), p)
    if m:
        coda = p[m.end():]
        stop = re.search(r"[.;]| e poi |, poi |,? e (?=(?:%s)\b|alle |quando )" % _V, coda)
        fine = m.end() + (stop.start() if stop else len(coda))
        om = messaggio(originale if not stop else originale[:len(originale)], r"(?:avvis|avvert|notific|annunc|manda|invia|dire)")
        grezzo = p[m.end():fine]
        if om and _n(om).startswith(grezzo[:12]):
            testo_msg = om[:len(grezzo)].rstrip(" .")
        testo_msg = testo_msg or (grezzo[:1].upper() + grezzo[1:])
        p = p[:m.start(1)] + " __msg__ " + p[fine:]
    m = re.search(r"\b(luce|tapparella|tenda)\b(?=.*\bquell[ae] (?:del|della|dell'|in))", p)
    if m:
        p = re.sub(r"\bquella (?=del|della|dell'|in )", m.group(1) + " ", p)
    p = ripeti_verbo(parlato(p))
    riscritta, applicate = False, set()
    for _ in range(4):   # pezzi riscritti dall'utente (ognuno una volta sola)
        for k, v in casa.risposte.items():
            if k in applicate:
                continue
            vecchio = k[9:] if k.startswith("riscrivi:") else k[6:] if k.startswith("pezzo:") else None
            nuovo = " ".join(_n(str((v or {}).get("testo") or "")).split()).rstrip(" .!")
            if vecchio and nuovo and k.startswith("riscrivi:") and re.search(r"\b(?:%s)\b" % _V, vecchio) and not re.search(r"\b(?:%s)\b|%s|%s" % (_V, _NOTIFICA, _ANNUNCIO), nuovo):
                nuovo = nuovo + " " + vecchio   # ha scritto solo quando: lo aggiungo alla frase
            if vecchio and nuovo and nuovo != vecchio and vecchio in p:
                p, riscritta = p.replace(vecchio, ripeti_verbo(parlato(nuovo)), 1), True
                applicate.add(k)
    p = passati(p)
    casa.dest = None
    md_ = re.search(r"\s*\b(?:sul|al) (?:telefono|cellulare) (?:di|del|della) ([a-z]+)\b", p)
    if md_:
        casa.dest, p = md_.group(1), _togli(p, md_)
    md_ = re.search(r"\bavvisa(?:re)? ([a-z]{3,})\b", p)
    if md_ and md_.group(1) not in ("mi", "anche", "tutti"):
        casa.dest, p = md_.group(1), p[:md_.start()] + "avvisami" + p[md_.end():]
    frase_vera = p
    p = re.sub(r"\s*[,;.]?\s*\b(?:(?:e |ma )?altrimenti|se no|in caso contrario)\b\s*,?\s*", ". __else__ ", p)
    ultimo_if = ramo_else = None
    if casa.domande:
        significato(p, casa)
        persone = [s_["entity_id"] for s_ in casa.states if core._dom(s_["entity_id"]) == "person"]
        if not io and len(persone) >= 2 and re.search(r"\b(?:esco|rientro|torno|arrivo|me ne vado)\b", p) and not re.search(r"\b(?:qualcuno|tutti|nessuno|di [a-z]+o)\b", p):
            chi = (casa.risposte.get("chi:io") or {}).get("entita")
            if not chi:
                raise Domanda("chi:io", "Chi intendi con \"io\"?", _opz(casa, persone) + [{"valore": {"entita": ["zone.home"]}, "nome": "Tutti: quando la casa resta vuota o torna qualcuno", "dettaglio": ""}])
            io = chi[0] if chi[0] in persone else ""
    segs = [s.strip(" ,") for s in _TAGLIO.split(p) if s and s.strip(" ,")]
    rami, cur = [], {"tr": [], "cond": [], "az": []}
    giorni_tutti, sospese, ultimi, chieste = None, [], [], 0
    verbo_prec = None
    comandate = False
    apri_blocco = False
    blocco = None   # "se X, spegni ... e annuncia ...": le azioni che seguono restano dentro lo stesso if

    def chiudi():
        if cur["tr"] or cur["az"]:
            rami.append(dict(cur))

    for seg in segs:
        if seg.startswith("__else__"):
            seg = seg[8:].strip()
            if ultimo_if is None or "else" in ultimo_if or re.search(r"\b(?:se|quando|appena|alle)\b", seg):
                return None
            ramo_else = ultimo_if["else"] = []
            blocco, verbo_prec = None, verbo_prec
        casa.pezzo = seg if "__msg__" not in seg else ""
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
        va = re.search(r"\baccendi\w*", seg)
        if m and va and not (m.start() < va.start() and re.search(r"\b(?:quando|appena|se)\b", seg[:m.start()])) and not re.search(r"\b(?:quando|se|appena)\b[^,]*\bper\b(?![^,]*accendi)", seg[:m.start()] if False else ""):
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
            if cond and re.search(r"\b(?:resta|rimane)\b", seg):
                avv.append("\"resta acceso dopo le...\": scatta solo quando la durata si completa nella fascia oraria. Se era gia acceso da prima non parte")
        if not m and not cur["tr"] and not trig and not rami and re.match(r"se\b", seg) and not re.search(r"\balle\b|tramonto|\balba\b|quando|mezzanotte|mezzogiorno", re.sub(r"\bdalle \d+ alle \d+", " ", p)):
            ms_ = re.match(r"se\s+((?:piove|rileva movimento.*?|(?:non )?c'e'? (?:piu )?movimento.*?))(?=\s+(?:accendi|spegni|apri|chiudi|alza|abbassa|avvia|avvisami|mandami)|$)", seg)
            if ms_:
                m = re.match(r"se\b\s*(.*)$", seg)
        if m:
            corpo = m.group(1)
            cmd = re.search(r"(?<!si )\b(?!(?:fermat[oa]|bloccat[oa]|apro|apre|chiudo|chiude|accendo|accende|spengo|spegne|avvio|avvia il ciclo)\b)(?:(?:%s|metti|imposta|riporta|rimanda)\b|%s|%s)" % (_V, _NOTIFICA, _ANNUNCIO), corpo)
            if cmd and re.match(r"si (?:accende|spegne|apre|chiude)", corpo):
                cmd = re.search(r"\b(?:(?:accendi|spegni|apri|chiudi|alza|abbassa|avvia|ferma|blocca|sblocca|metti|imposta)\w*|%s|%s)" % (_NOTIFICA, _ANNUNCIO), corpo)
            testo_ev = corpo[:cmd.start()] if cmd else corpo
            if "__msg__" in testo_ev:
                testo_ev = re.sub(r"\s*(?:,|\be\b)?\s*__msg__\s*", " ", testo_ev).strip()
                if not cmd:
                    corpo = corpo + " __msg__"
                    cmd = re.search(r"__msg__", corpo)
            ms = re.fullmatch(r"\s*si (accende|spegne|apre|chiude)\s*", testo_ev)
            prima_tr = [t_ for r_ in rami + [cur] for t_ in r_["tr"] if str(t_.get("entity_id", "")).startswith(("light.", "switch.", "media_player.", "cover.", "fan.", "binary_sensor."))]
            if ms and prima_tr:
                testo_ev = "si %s %s" % (ms.group(1), _n(casa.nome(prima_tr[-1]["entity_id"])))
                soggetto = prima_tr[-1]["entity_id"]
                stato = {"accende": "on", "spegne": "off", "apre": "open", "chiude": "closed"}[ms.group(1)]
                r = ({"trigger": "state", "entity_id": soggetto, "to": stato}, [])
            else:
                r = evento(testo_ev, casa, io, avv) or pezzo_ai(casa, "evento", testo_ev, avv)
            if not r:
                return None
            trs_ = r[0] if isinstance(r[0], list) else [r[0]]
            trig += trs_; ev_cond = r[1]
            ee = str(trs_[0].get("entity_id", "")) if trs_ else ""
            if ee.startswith(("light.", "switch.", "cover.", "media_player.", "fan.")) and not comandate:
                ultimi = [ee]
            seg = (seg[:m.start()] + " " + (corpo[cmd.start():] if cmd else "")).strip()
        if m:
            casa.contesto = [r_ for r_ in STANZE if re.search(r"\b%s\b" % r_, testo_ev)] or casa.contesto
        # se ... (condizione sui pezzi che seguono)
        m = re.search(r"\b(?:solo se|se|ma solo se|purche)\b\s+(.*)$", seg)
        if m:
            corpo = m.group(1)
            cmd = re.search(r"\b(?:(?:accendi|spegni|apri|chiudi|alza|abbassa|avvia|ferma|blocca|sblocca|metti|imposta)\w*|%s|%s)" % (_NOTIFICA, _ANNUNCIO), corpo)
            cs = condizioni(corpo[:cmd.start()] if cmd else corpo, casa, avv) or pezzo_ai(casa, "condizione", corpo[:cmd.start()] if cmd else corpo, avv)
            if not cs:
                return None
            prima = seg[:m.start()].strip()
            resto_seg = corpo[cmd.start():] if cmd else ""
            if not _resto(resto_seg) and not _resto(prima) and re.search(r"\bsolo se\b|\bpurche\b", m.group(0)) and cur["az"] and not trig:
                cur["cond"] += cs   # "accendi X, ma solo se Y, e poi...": la condizione riguarda quello gia detto
                continue
            prefisso = bool(_resto(resto_seg) or not _resto(prima))
            if prefisso:
                sospese += cs; seg = (prima + " " + resto_seg).strip()
                c = cs[0]
                if len(cs) == 1 and (str(c.get("entity_id", "")).startswith(("light.", "switch.", "cover.", "media_player.", "fan.")) or c["condition"] == "not"):
                    ultimi = [(c.get("entity_id") or c["conditions"][0]["entity_id"])]
            else:   # "spegni X se Y": la condizione vale per questa azione
                sospese += cs; seg = prima
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
            mob = sorted({str(n).replace("notify.", "", 1) for n in notify if str(n).replace("notify.", "", 1).startswith("mobile_app_")})
            srv = mob[0] if len(mob) == 1 else "notify"
            chi_ = casa.dest or (io if io.startswith("person.") and len(mob) > 1 else None)
            if chi_ and len(mob) > 1:
                srv = telefono(casa, chi_, mob) or srv
            if len(mob) != 1 and srv == "notify":
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
            nominati = []
            for s_ in sat:   # "su Casa Voce": il nome del dispositivo senza "assist satellite"
                corto = re.sub(r"\s*assist(?:ente)?\s*satellite\s*$", "", " ".join(_n(casa.nome(s_["entity_id"])).split()))
                mm = re.search(r"\b(?:su|sul|sulla|con|da)?\s*(?:il |la |l')?%s\b" % re.escape(corto), seg) if corto else None
                if mm:
                    nominati.append(s_); seg = _togli(seg, mm)
            if not nominati:
                nominati = [s for s in sat if all(w in casa.testo(s) for w in _resto(seg)[:3])]
            if not txt and len(nominati) == 1:   # "annuncia su Casa Voce buongiorno a tutti": il resto e il testo
                resto_ = " ".join(seg.replace("__msg__", " ").split()).strip(" ,")
                resto_ = re.sub(r"^(?:e|ed)\s+", "", resto_)
                if resto_:
                    i_ = _n(originale).find(resto_)
                    txt = originale[i_:i_ + len(resto_)] if i_ >= 0 else resto_
                    txt, seg = txt[:1].upper() + txt[1:], ""
            if not txt or len(nominati) != 1:
                return None
            azioni.append({"action": "assist_satellite.announce", "target": {"entity_id": nominati[0]["entity_id"]}, "data": {"message": txt}})
        elif re.search(r"\bmetti in pausa (?:il |l')?(?:robot|aspirapolvere)", seg):
            rb = [s_["entity_id"] for s_ in casa.states if core._dom(s_["entity_id"]) == "vacuum" and casa.vivo(s_)]
            if len(rb) != 1 or _resto(re.sub(r"\b(?:metti|in|pausa|robot|aspirapolvere)\b", " ", seg)):
                return None
            azioni.append({"action": "vacuum.pause", "target": {"entity_id": rb[0]}})
        elif re.search(r"\b(?:riporta|rimanda|manda|torna)\b.*\b(?:robot|aspirapolvere)\b.*\bbase\b", seg):
            rb = [s_["entity_id"] for s_ in casa.states if core._dom(s_["entity_id"]) == "vacuum" and casa.vivo(s_)]
            if len(rb) != 1 or _resto(re.sub(r"\b(?:riporta|rimanda|manda|robot|aspirapolvere|base)\b", " ", seg)):
                return None
            azioni.append({"action": "vacuum.return_to_base", "target": {"entity_id": rb[0]}})
        elif re.search(r"\bvolume\b", seg) and re.search(r"\d{1,3}", seg):
            mvol = re.search(r"\b(?:metti|imposta|porta|abbassa|alza|regola)\b", seg)
            mn_ = re.search(r"\b(?:al|a)\s+(\d{1,3})\s*(?:%|per ?cento)?", seg) or re.search(r"(\d{1,3})\s*(?:%|per ?cento)", seg)
            if not mvol or not mn_ or int(mn_.group(1)) > 100:
                return None
            resto = _togli(_togli(seg, mn_), mvol) if mn_.start() > mvol.start() else _togli(_togli(seg, mvol), mn_)
            parole = [w for w in _resto(resto) if w not in ("volume",)]
            cand = [s_["entity_id"] for s_ in casa.states if core._dom(s_["entity_id"]) == "media_player" and casa.vivo(s_)
                    and parole and all(re.search(r"\b%s\b" % w, casa.testo(s_)) for w in parole)]
            if len(cand) > 1 and casa.domande:
                cand = scegli(casa, "vol:" + " ".join(parole), "Di quale dispositivo regolo il volume?", cand)[:1]
            if len(cand) > 1:   # nome piu corto = quello detto
                avv.append("piu dispositivi per il volume (%s): controlla il bersaglio" % ", ".join(casa.nome(e) for e in cand))
                cand = sorted(cand, key=len)[:1]
            if not cand:
                return None
            azioni.append({"action": "media_player.volume_set", "target": {"entity_id": cand[0]}, "data": {"volume_level": int(mn_.group(1)) / 100}})
        elif re.search(r"\b(?:termostato|climatizzatore|condizionatore|riscaldamento)\b", seg) and re.search(r"\b(?:a|ai|su)\s+\d{1,2}(?:[.,]5)?\s*(?:gradi|°)", seg):
            mg = re.search(r"\b(?:a|ai|su)\s+(\d{1,2}(?:[.,]5)?)\s*(?:gradi|°c?)", seg)
            resto = _togli(seg, mg)
            mi_ = re.search(r"\b(?:imposta|metti|porta|regola)\b", resto)
            if not mi_:
                return None
            ids, resto = apparecchi(_togli(resto, mi_), casa, avv)
            if not ids or _resto(resto):
                return None
            gr = float(mg.group(1).replace(",", ".")); gr = int(gr) if gr == int(gr) else gr
            azioni.append({"action": "climate.set_temperature", "target": {"entity_id": ids[0]}, "data": {"temperature": gr}})
        else:
            if re.search(r"\b(?:metti|imposta)\b.*\b(?:luc\w*|lamp\w*|farett\w*|piantana)\b.*\bal\s+\d{1,3}\s*(?:%|per ?cento)", seg):
                seg = re.sub(r"\b(?:metti|imposta)\b", "accendi", seg)
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
                frase = frase.replace("e'", "è")
                modi = _resto(resto or "")
                if casa.domande and modi and all(w in _MODI for w in modi):
                    senza = " ".join(w for w in casa.pezzo.split() if w not in _MODI)
                    raise Domanda("riscrivi:" + casa.pezzo, "Non so fare \"%s\". Continuo senza?" % " ".join(modi),
                                  [{"valore": {"testo": senza}, "nome": "Si, senza \"%s\"" % " ".join(modi), "dettaglio": ""}], riscrivi=True)
                cosa_ = "pezzo:" + " ".join(_resto(resto or frase))
                risp = casa.risposte.get("pezzo:" + frase) or {}
                if not risp and casa.domande and eccez is None and not anafora and not _PESANTI.search(" ".join(_resto(resto or ""))):
                    ric = ricordata(casa, cosa_, set(e_ for e_ in casa.by if core._dom(e_) in _DOM_VERBO.get(verbo, ())))
                    if ric:
                        risp = {"entita": ric}
                if risp.get("azioni"):
                    fatte, ids = risp["azioni"], None
                elif risp.get("entita"):
                    ids = [e for e in risp["entita"] if e in casa.by]
                    if not ids:
                        return None
                    avv.append("\"%s\": bersaglio scelto da te%s" % (frase, "; parole ignorate: " + " ".join(_resto(resto)) if _resto(resto) else ""))
                    fatte, resto = None, ""
                else:
                    fatte = chiedi(frase) if chiedi and not anafora and eccez is None else None
                    if casa.domande:   # non decido io: propongo
                        opz = []
                        if eccez is None and not anafora and not _PESANTI.search(" ".join(_resto(resto or ""))):
                            opz += _opz(casa, candidati(casa, frase, verbo))
                        if fatte and plausibile(fatte, frase):
                            opz.append({"valore": {"azioni": fatte}, "nome": "Proposta AI: " + descrivi(fatte, casa), "dettaglio": ""})
                        if opz:
                            raise Domanda("pezzo:" + frase, "Non ho capito \"%s\". Cosa intendi?" % frase, opz, multi=True, riscrivi=True, ricorda=cosa_ if _resto(resto or "") else "")
                        return None
                    if not fatte:
                        return None
                    chieste += 1
                    avv.append("\"%s\": azione scritta dall'AI, controllala" % frase)
                    ids = None
                if ids is None:
                    azioni += fatte
                    ultimi = [e for a in fatte for e in _lista((a.get("target") or {}).get("entity_id"))]
            if ids is not None:
                if eccez is not None:
                    ids = escludi(ids, eccez, casa, avv)
                    if not ids:
                        return None
                    eccez = None
                ultimi = ids
                comandate = True
                per_dom = {}
                for e in ids:
                    per_dom.setdefault(core._dom(e), []).append(e)
                for d, es in per_dom.items():
                    if d in ("switch", "fan") and verbo in ("start", "stop"):
                        srv = "turn_on" if verbo == "start" else "turn_off"
                    elif verbo in ("start", "stop"):
                        if d != "vacuum":
                            return None
                        srv = verbo
                    elif verbo in ("lock", "unlock"):
                        if d != "lock":
                            return None
                        srv = verbo
                    elif d == "lock":
                        if verbo != "close_cover":   # "apri la serratura" resta da scrivere a mano: sblocco solo se detto "sblocca"
                            return None
                        srv = "lock"
                    elif d == "vacuum":
                        if verbo != "turn_on":
                            return None
                        srv = "start"
                    elif d == "cover":
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
        if ramo_else is not None:
            if sospese:
                return None
            ramo_else += azioni
        elif sospese:
            nuovo = {"if": sospese, "then": azioni}
            ultimo_if = nuovo
            cur["az"].append(nuovo); sospese = []
            blocco = nuovo if apri_blocco else None
            apri_blocco = False
        elif blocco is not None:
            blocco["then"] += azioni
        else:
            cur["az"] += azioni
    casa.pezzo = ""
    chiudi()
    if ramo_else is not None and not ramo_else:
        return None
    if not sospese and len(rami) == 1 and not rami[0]["tr"] and len(rami[0]["az"]) == 1 and set(rami[0]["az"][0]) == {"if", "then"}:
        blocco_se = rami[0]["az"][0]
        trs = da_condizioni(blocco_se["if"])
        if trs:
            rami[0] = {"tr": trs, "cond": rami[0]["cond"] + (blocco_se["if"] if len(trs) > 1 else []), "az": blocco_se["then"]}
            avv.append("non hai detto quando controllare: parte appena la condizione diventa vera")
    if casa.domande and rami and not sospese and all(not r["tr"] and r["az"] for r in rami):
        raise Domanda("riscrivi:" + frase_vera, "Quando deve succedere? (puoi anche scrivere l'orario, es. \"alle 20\")",
                      [{"valore": {"testo": v + frase_vera}, "nome": n, "dettaglio": ""} for v, n in
                       (("al tramonto ", "Al tramonto"), ("all'alba ", "All'alba"), ("quando torna qualcuno a casa ", "Quando qualcuno torna a casa"), ("scena: ", "Crea una scena da attivare a mano"))], riscrivi=True)
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
    return {"yaml": y, "avvisi": avv, "modello": chieste, "frase": frase_vera if riscritta else originale}


def _testo_evento(tr, casa, originale):
    t = tr[0] if tr else {}
    e = t.get("entity_id", "")
    if "lavatrice" in e or "lavastoviglie" in e or "asciugatrice" in e:
        return "%s: ciclo finito" % casa.nome(e).rsplit(" ", 1)[0]
    if e == "zone.home":
        return "Casa vuota" if "below" in t else "Qualcuno e tornato a casa"
    if t.get("trigger") == "state" and isinstance(t.get("to"), str) and t.get("for"):
        d = t["for"]
        return "%s: %s da %s" % (casa.nome(e), {"on": "stato acceso", "off": "stato spento", "open": "stato aperto", "closed": "stato chiuso"}.get(t["to"], t["to"]),
                               " ".join("%s %s" % (v, {"minutes": "minuti", "seconds": "secondi", "hours": "ore"}[k]) for k, v in d.items()))
    return "Yamlic: " + originale[:100]


def _lista(x):
    return x if isinstance(x, list) else [x] if x else []


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True
