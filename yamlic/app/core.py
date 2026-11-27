"""Yamlic core: scelta entita, pseudonimizzazione, validatore e politica di sicurezza.
Solo libreria standard + pyyaml. Nessun accesso alla rete qui."""
import json, os, re
import yaml

SYSTEM = (
    "Sei un assistente per Home Assistant. Scrivi UNA sola automazione in YAML, "
    "nel formato moderno: alias, triggers (con 'trigger:'), conditions, actions (con 'action:'). "
    "Usa SOLO le entita elencate dall'utente, con gli identificatori esatti (anche se sembrano codici). "
    "Rispondi solo con un blocco ```yaml ... ```. "
    "Se la richiesta dice 'esco di casa', 'siamo fuori' o 'non c'e nessuno', usa trigger numeric_state su zone.home con below: 1; "
    "per 'rientro' o 'arriva qualcuno' usa numeric_state su zone.home con above: 0; se nomina una persona usa person.<id> con to: not_home o home. "
    "Per 'spegni le luci' usa solo entita light.* con light.turn_off. "
    "Non aggiungere condizioni se non richieste: scrivi conditions: []. Una sola azione per entita, nessuna ripetizione. "
    "L'alias deve essere in italiano."
)

# ---------- opzioni ----------
def load_options():
    o = {}
    p = os.environ.get("YAMLIC_OPTIONS", "/data/options.json")
    if os.path.exists(p):
        o = json.load(open(p))
    for k, v in os.environ.items():
        if k.startswith("YAMLIC_") and k != "YAMLIC_OPTIONS":
            o[k[7:].lower()] = v
    return o

# ---------- pseudonimizzazione ----------
FIXED = {"zone.home", "sun.sun"}   # non sono privati e servono al modello
GENERIC = set("""luce luci lampada lampade tapparella tapparelle serranda serrande movimento porta porte finestra finestre
temperatura umidita umidità clima condizionatore presa prese ventola tv telefono mobile app scena scene serratura allarme casa
ospiti sensore interruttore switch light cover sensor climate person notify zone sun lock fan media player input boolean
binary script automazione stato batteria energia potenza consumo""".split())
TOK = re.compile(r"[A-Za-zÀ-ÿ0-9]+")
SAFE = set("update home assist satellite assistant voice status ring display screen sun zone mode night enabled reset restart identify mute sound debug kiosk".split())
STOPIT = set("del dello della dei degli delle con per una uno che non tra fra sul sullo sulla sui nel nello nella nei alle alla allo agli dal dallo dalla dai dalle sua suo suoi sue tutte tutti tutto solo anche come quando dopo prima sono stato".split())
SYSWORDS = {w.lower() for w in re.findall(r"[A-Za-zÀ-ÿ0-9]+", SYSTEM)} | SAFE | STOPIT
QUOTED = re.compile(r'"([^"\n]{1,200})"|«([^»\n]{1,200})»')

def _dom(eid):
    return eid.split(".", 1)[0]

def _pretty(eid, name):
    return name if name else eid.split(".", 1)[1].replace("_", " ")

class Anon:
    """Costruisce alias per entita, nomi e testi liberi. Tutto resta in locale."""
    def __init__(self, entities, prompt, all_names=()):
        # entities: lista di (entity_id, friendly_name)
        self.alias, self.back = {}, {}
        self.tok, self.tok_back = {}, {}
        self.texts = []
        self.entities = [(e, _pretty(e, n)) for e, n in entities]
        n = 0
        for eid, _ in self.entities:
            if eid in FIXED:
                continue
            n += 1
            a = "%s.e%d" % (_dom(eid), n)
            self.alias[eid] = a
            self.back[a] = eid
        names = [nm for _, nm in self.entities if _ not in FIXED]
        for eid, nm in self.entities:
            if eid in FIXED:
                continue
            for t in TOK.findall(nm):
                self._add_tok(t)
        # parole del prompt che coincidono con nomi di altre entita della casa
        pool = {t.lower() for nm in all_names for t in TOK.findall(nm or "")}
        for t in TOK.findall(prompt):
            if t.lower() in pool:
                self._add_tok(t)
        self.prompt = self._text(prompt)

    def _add_tok(self, t):
        l = t.lower()
        if l in GENERIC or l in SYSWORDS or len(l) < 3 or l.isdigit() or l in self.tok:
            return
        k = len(self.tok) + 1
        self.tok[l] = "_A%d" % k
        self.tok_back["_A%d" % k] = t

    def _sub(self, s):
        return TOK.sub(lambda m: self.tok.get(m.group(0).lower(), m.group(0)), s)

    def _text(self, prompt):
        def q(m):
            self.texts.append(m.group(1) or m.group(2))
            return "$T%d" % len(self.texts)
        return self._sub(QUOTED.sub(q, prompt))

    def entity_lines(self):
        out = []
        for eid, nm in self.entities:
            if eid in FIXED:
                out.append("- %s (%s)" % (eid, nm))
            else:
                out.append("- %s (%s)" % (self.alias[eid], self._sub(nm)))
        return "\n".join(out)

    def outgoing(self):
        """Il testo che puo uscire da casa."""
        return "Entita disponibili:\n%s\n\nRichiesta: %s" % (self.entity_lines(), self.prompt)

    def leaks(self, text):
        t = text.lower()
        bad = []
        words = set(re.findall(r"[a-z0-9_]+", t))
        for eid in self.alias:
            obj = eid.split(".", 1)[1].lower()
            if eid.lower() in t or (obj in words and not all(x in GENERIC for x in obj.split("_"))):
                bad.append(eid)
        senza_dom = re.sub(r"\b[a-z_]+\.(e\d+|[a-z0-9_]+)", r" \1", text)
        for w in self.tok:
            if w in {x.lower() for x in TOK.findall(senza_dom)}:
                bad.append(w)
        for q in self.texts:
            if q.lower() in t:
                bad.append(q)
        return bad

    def forward_yaml(self, y):
        """Per il dataset: trasforma un YAML reale nella forma con alias (come lo scrive il nodo)."""
        for eid in sorted(self.alias, key=len, reverse=True):
            y = re.sub(r"(?<![\w.])%s(?![\w])" % re.escape(eid), self.alias[eid], y)
        for i, q in enumerate(self.texts, 1):
            y = y.replace(q, "$T%d" % i)
        return self._sub(y)

    def restore(self, y):
        y = re.sub(r"\b([a-z_]+\.e\d+)\b", lambda m: self.back.get(m.group(1), m.group(1)), y)
        y = re.sub(r"_A\d+", lambda m: self.tok_back.get(m.group(0), m.group(0)), y)
        y = re.sub(r"\$T(\d+)", lambda m: self.texts[int(m.group(1)) - 1] if int(m.group(1)) <= len(self.texts) else m.group(0), y)
        return y

    def unresolved(self, y):
        return sorted(set(re.findall(r"\b[a-z_]+\.e\d+\b", y)) | set(re.findall(r"_A\d+|\$T\d+", y)))

# ---------- scelta entita ----------
DOMAINS = {"luc": "light", "lamp": "light", "tappar": "cover", "serrand": "cover", "clima": "climate",
           "esco": "person", "esce": "person", "arriv": "person", "rientr": "person", "uscit": "person",
           " tend": "cover", "serratur": "lock", "bloc": "lock", "sblocc": "lock", "movimento": "binary_sensor", "porta": "binary_sensor", "finestra": "binary_sensor"}

LIGHT_LIKE = re.compile(r"luc|led|farett|barra|lampad|striscia|plafon|abat|veletta|applique")
FIRST = re.compile(r"\b(io|mio|mia|miei|mie|me)\b")
# entita che non sono luci vere: opzioni, indicatori, schermi
NOT_LIGHT = re.compile(r"stuf|forno|bollitor|caffe|frigo|router|stampant|televis|cancell|lavatric|lavastovigli|pompa|boiler|caldai|irrig|garage|portone|ventol|asciug|clima|update|enabled|status.?ring|indicator|night.?mode|display|schermo|screen|backlight|reset|restart|identify|mute|sound|amplifier|internet|debug|kiosk|manutenzione|disturbare|timer|countdown|\\bring\\b|wake|satellite|assist")
ROOMS = [{"soggiorno", "salotto", "living", "salone"}, {"cucina"}, {"bagno"}, {"camera", "letto"}, {"giardino", "esterno", "esterni"}]

def room_words(w):
    for g in ROOMS:
        if w in g:
            return g
    return {w}

def is_light_like(eid, text):
    return not NOT_LIGHT.search(text) and (_dom(eid) == "light" or (_dom(eid) == "switch" and bool(LIGHT_LIKE.search(text))))

def select_entities(states, prompt, io_person="", limit=15, only=None):
    """Sceglie le entita da mostrare al modello. Le persone solo se nominate (o 'io'): per 'esco/rientro'
    senza nome vale zone.home, cioe tutte le persone di casa."""
    words = set(re.findall(r"\w{4,}", prompt.lower()))
    low = prompt.lower()
    wanted = {d for k, d in DOMAINS.items() if k in low and d != "person"}
    if re.search(r"\btutt", low) or only:
        limit = max(limit, 40)
    persons, scored = [], []
    for s in states:
        eid = s["entity_id"]
        name = (s.get("attributes") or {}).get("friendly_name", "")
        text = _t(s)
        if _dom(eid) in ("light", "switch") and NOT_LIGHT.search(text) and not any(m.group(0) in low for m in NOT_LIGHT.finditer(text)):
            continue
        if _dom(eid) == "person":
            obj = eid.split(".", 1)[1].lower().replace("_", " ")
            named = any(t in words for t in re.findall(r"\w{3,}", (obj + " " + name.lower()))) 
            if named or (io_person == eid and FIRST.search(low)):
                persons.append((eid, name))
            continue
        arw = (s.get("attributes") or {}).get("_area", "").lower()
        in_room = bool(arw) and any(len(w) > 3 and any(x in arw for x in room_words(w)) for w in words)
        nome_stanza = any(len(w) > 4 and w not in GENERIC and w not in STOP and w in name.lower() for w in words)
        light_like = "light" in wanted and _dom(eid) == "switch" and (bool(LIGHT_LIKE.search(text)) or in_room or nome_stanza) and not NOT_LIGHT.search(text)
        bonus = 3 if (_dom(eid) in wanted or light_like) else 0
        scored.append((sum(w in text for w in words) + bonus, eid, name, light_like))
    if wanted:
        keep = [x for x in scored if _dom(x[1]) in wanted or x[3] or x[1] == "zone.home"]
        if keep:
            scored = keep
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    top = [(e, n) for _, e, n, _ in scored]
    zone = [x for x in top if x[0] == "zone.home"]
    rest = [x for x in top if x[0] != "zone.home"]
    persons.sort(key=lambda x: x[0] != io_person)
    if only:
        names = {s["entity_id"]: (s.get("attributes") or {}).get("friendly_name", "") for s in states}
        rest = [(e, names.get(e, "")) for e in only if e in names and _dom(e) != "person"]
    return (zone + persons[:3] + rest)[:limit], {s["entity_id"] for s in states}

def prune_unknown(y, known):
    """Toglie dalle liste di entita delle azioni quelle inesistenti (se ne resta almeno una valida).
    Restituisce (yaml, [entita tolte])."""
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y, []
    if not isinstance(cfg, dict) or not isinstance(cfg.get("actions"), list):
        return y, []
    removed = []
    def walk(x):
        if isinstance(x, dict):
            for k, v in list(x.items()):
                if k == "entity_id" and isinstance(v, list):
                    ok = [e for e in v if isinstance(e, str) and e in known]
                    if ok and len(ok) < len(v):
                        removed.extend(str(e) if not isinstance(e, dict) else ": ".join(str(a) for a in next(iter(e.items()))) for e in v if not (isinstance(e, str) and e in known))
                        x[k] = ok
                else:
                    walk(v)
        elif isinstance(x, list):
            for i in x:
                walk(i)
    walk(cfg["actions"])
    if not removed:
        return y, []
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n"), removed


# ---------- domande di chiarimento (regole fisse, non il modello) ----------
ALL_LIGHTS = re.compile(r"\btutt\w*\s+(?:le\s+|i\s+|gli\s+)?(?:luc\w*|lamp\w*|farett\w*|illuminazion\w*)")
STOP = set("quando spegni accendi accendere spegnere chiudi apri alle della delle dello degli sempre ogni dopo prima".split())

def _t(s):
    """Testo su cui si cercano stanze e parole: id + nome + area di Home Assistant."""
    a = s.get("attributes") or {}
    return (s["entity_id"] + " " + (a.get("friendly_name") or "") + " " + (a.get("_area") or "") + " " + (a.get("_soprannome") or "")).lower().replace("_", " ")

def _nm(s):
    return (s.get("attributes") or {}).get("friendly_name", "") or s["entity_id"]

def ambiguity(states, prompt, io_person=""):
    """Se la richiesta e ambigua restituisce una domanda {tipo, testo, opzioni}, altrimenti None."""
    low = prompt.lower()
    lightlike = []
    for s in states:
        eid = s["entity_id"]
        text = _t(s)
        if is_light_like(eid, text):
            lightlike.append({"id": eid, "nome": _nm(s), "gruppo": _dom(eid), "t": text})
    lightlike.sort(key=lambda o: (o["gruppo"] != "light", o["nome"].lower()))
    opts = lambda l: [{k: v for k, v in o.items() if k != "t"} for o in l]
    # A: "tutte le luci" e in casa ci sono anche interruttori che sembrano luci
    if ALL_LIGHTS.search(low):
        for w in re.findall(r"[a-z\u00e0-\u00ff]{4,}", low):
            if w in GENERIC or w in STOP:
                continue
            syn = room_words(w)
            hit = [o for o in lightlike if any(x in o["t"] for x in syn)]
            if len(syn) > 1 or len(hit):
                if hit:
                    cov = []
                    if re.search(r"tapparell|\btend|serrand", low):
                        cov = [{"id": c["entity_id"], "nome": _nm(c), "gruppo": "cover"} for c in states
                               if _dom(c["entity_id"]) == "cover" and any(x in _t(c) for x in syn)]
                    return {"tipo": "multi", "testo": "Per \u00ab%s\u00bb ci sono %d luci%s. Quali includo?" % (w, len(hit), (" e %d tende o tapparelle" % len(cov)) if cov else ""), "opzioni": opts(hit) + cov}
        nl = sum(o["gruppo"] == "light" for o in lightlike); ns = len(lightlike) - nl
        if ns:
            return {"tipo": "multi", "testo": "Ho trovato %d %s e %d %s che sembrano luci. Quali includo?" % (nl, "luce" if nl == 1 else "luci", ns, "interruttore" if ns == 1 else "interruttori"),
                    "opzioni": opts(lightlike)[:40]}
        return None
    # B: "io", "mio" senza persona impostata e piu persone in casa
    persons = [s for s in states if _dom(s["entity_id"]) == "person"]
    if FIRST.search(low) and not io_person and len(persons) >= 2:
        return {"tipo": "uno", "testo": "Chi sei?", "opzioni": [{"id": s["entity_id"], "nome": _nm(s)} for s in persons]}
    # C: una stanza o un nome che corrisponde a molte entita dello stesso tipo
    wanted = {d for k, d in DOMAINS.items() if k in low and d in ("light", "cover")}
    for w in re.findall(r"[a-zà-ÿ]{4,}", low):
        if w in GENERIC or w in STOP:
            continue
        pool = lightlike if "light" in wanted else [
            {"id": s["entity_id"], "nome": _nm(s), "gruppo": "cover", "t": _t(s)}
            for s in states if "cover" in wanted and _dom(s["entity_id"]) == "cover"]
        syn = room_words(w)
        hit = [o for o in pool if any(x in o["t"] for x in syn)]
        if 3 <= len(hit) <= 15:
            return {"tipo": "multi", "testo": "Per \u00ab%s\u00bb ci sono %d entit\u00e0. Quali includo?" % (w, len(hit)), "opzioni": opts(hit)}
    return None

def fix_domains(y):
    """Se light.turn_* ha tra i target degli interruttori (switch.*), li sposta in una azione switch.turn_*."""
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y
    if not isinstance(cfg, dict) or not isinstance(cfg.get("actions"), list):
        return y
    out, changed = [], False
    for a in cfg["actions"]:
        svc = str(a.get("action", "")) if isinstance(a, dict) else ""
        if svc.startswith("light.") and svc.split(".", 1)[1] in {"turn_on", "turn_off", "toggle"}:
            ents = _target_ents(a)
            sw = [e for e in ents if isinstance(e, str) and e.startswith("switch.")]
            if sw:
                li = [e for e in ents if e not in sw]
                changed = True
                if li:
                    first = {k: v for k, v in a.items() if k not in ("target", "entity_id")}
                    first["target"] = {"entity_id": li}
                    out.append(first)
                out.append({"action": "switch." + svc.split(".", 1)[1], "target": {"entity_id": sw}})
                continue
        out.append(a)
    if not changed:
        return y
    cfg["actions"] = out
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n")

def unknown_entities(y, known):
    """Entita citate ovunque nel YAML (anche dentro choose/conditions) che non esistono in casa."""
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return []
    bad = []
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "entity_id":
                    for e in (v if isinstance(v, list) else [v]):
                        if not isinstance(e, str) or (re.fullmatch(r"[a-z_]+\.[\w]+", e) and e not in known):
                            bad.append(e if isinstance(e, str) else str(e))
                else:
                    walk(v)
        elif isinstance(x, list):
            for i in x:
                walk(i)
    walk(cfg)
    return sorted(set(bad))

TRIGGER_CUE = re.compile(r"quando|\bse\b|ogni|\balle\b|dalle|dopo|prima|appena|tramonto|alba|\bsera\b|mattina|notte|minut|\bore\b|esco|esce|rientr|arriv|uscit|movimento|premo|premuto|pulsante|temperatura|umidit|piove|pioggia|\bsole\b")

COND_CUE = re.compile(r"solo se|soltanto|purche|purché|se non|solo quando|solo di|buio|notte|tramonto|alba|sole|presente|nessuno|qualcuno|feriali|lavorativ|weekend|fine settimana|sabato|domenica|luned|marted|mercoled|gioved|venerd|a casa|in casa|fuori casa|\\bse\\b")
COLORI = {"rosso": (255, 0, 0), "verde": (0, 255, 0), "blu": (0, 0, 255), "azzurro": (0, 160, 255), "celeste": (0, 160, 255),
          "giallo": (255, 220, 0), "arancione": (255, 120, 0), "viola": (150, 0, 255), "rosa": (255, 90, 180)}
MANCANTI = [(r"condizionator|climatizzator|termostat|riscaldament|\\bclima\\b", "climate", "nessun termostato o climatizzatore (climate.*)"),
            (r"allarm", "alarm_control_panel", "nessun allarme (alarm_control_panel.*)")]

def domini_mancanti(states, prompt):
    low = prompt.lower(); doms = {_dom(s["entity_id"]) for s in states}
    out = [m for rx, d, m in MANCANTI if re.search(rx, low) and d not in doms]
    if re.search(r"temperatur", low):
        ok = [s for s in states if _dom(s["entity_id"]) == "sensor" and "temperatur" in _t(s) and not re.search(r"cpu|process|fritz|router|batteri", _t(s))]
        if not ok and "climate" not in doms:
            out.append("nessun sensore di temperatura ambiente")
    if re.search(r"movimento|si muove|qualcuno entra|presenza in", low):
        mov = [s for s in states if _dom(s["entity_id"]) == "binary_sensor" and ((s.get("attributes") or {}).get("device_class") in ("motion", "occupancy", "presence") or re.search(r"movimento|motion|presenza|\bpir\b|occupanc", _t(s)))]
        if not mov:
            out.append("nessun sensore di movimento o presenza")
    return ["in casa non c'e: " + m + ", l'automazione usa entita sbagliate" for m in out]

STANZE_EXTRA = ("ingresso", "corridoio", "studio", "ripostiglio", "cantina", "garage", "terrazzo", "balcone", "lavanderia", "mansarda", "taverna", "soffitta", "cameretta")

def stanze_mancanti(states, prompt):
    """Stanze nominate nel prompt che non compaiono in nessuna entita ne area di casa."""
    low = prompt.lower()
    tutto = " ".join(_t(s) for s in states)
    return [w for w in STANZE_EXTRA if re.search(r"\b%s" % w, low) and w not in tutto]

DAYS = {"luned":"mon","marted":"tue","mercoled":"wed","gioved":"thu","venerd":"fri","sabato":"sat","domenica":"sun"}

def rules(cfg, prompt):
    """Regole deterministiche applicate dopo il modello: tolgono invenzioni e correggono cio che il prompt dice chiaramente."""
    lp = prompt.lower()
    # niente luminosita inventata
    if not re.search(r"%|percent|luminos|intensit|dimm|al \d+|soft|tenue", lp):
        for a in cfg.get("actions") or []:
            if isinstance(a, dict) and isinstance(a.get("data"), dict):
                a["data"].pop("brightness_pct", None); a["data"].pop("brightness", None)
                if not a["data"]:
                    a.pop("data")
    # colore detto a parole -> rgb_color (via color_temp inventata)
    for nome, rgb in COLORI.items():
        if re.search(r"\b%s\b" % nome, lp):
            for a in cfg.get("actions") or []:
                if isinstance(a, dict) and str(a.get("action", "")) == "light.turn_on":
                    d = a.setdefault("data", {}) if isinstance(a.get("data", {}), dict) else {}
                    for k in ("color_temp", "color_temp_kelvin", "kelvin", "color_name", "rgb_color", "xy_color", "hs_color"):
                        d.pop(k, None)
                    d["rgb_color"] = list(rgb); a["data"] = d
            break
    # aspirapolvere: pausa / ritorno alla base
    for a in cfg.get("actions") or []:
        if isinstance(a, dict) and str(a.get("action", "")).startswith("vacuum."):
            if re.search(r"pausa|metti in pausa|sospendi|ferma temporaneamente", lp): a["action"] = "vacuum.pause"
            elif re.search(r"torna.* base|alla base|ricarica", lp): a["action"] = "vacuum.return_to_base"
            elif re.search(r"\bavvia|\bparti|inizia.*puli|\bpulisci|accendi", lp): a["action"] = "vacuum.start"
            elif re.search(r"\bferma|\bstop|spegni", lp): a["action"] = "vacuum.stop"
    # servizi media.* -> media_player.*
    for a in cfg.get("actions") or []:
        if isinstance(a, dict) and str(a.get("action", "")).startswith("media."):
            a["action"] = "media_player." + a["action"].split(".", 1)[1]
    trig = [t for t in (cfg.get("triggers") or []) if isinstance(t, dict)]
    # tramonto / alba
    ev = None
    if re.search(r"al tramonto|quando tramonta|all.?imbrunire", lp): ev = "sunset"
    elif re.search(r"all.?alba|al sorgere|quando sorge", lp): ev = "sunrise"
    if ev and not any(t.get("trigger") == "sun" for t in trig):
        trig = [{"trigger": "sun", "event": ev}]
        cfg["triggers"] = trig
    # orario esplicito
    lp = re.sub(r"\balle (\d{1,2}) e mezz[oa]\b", r"alle \1:30", lp)
    lp = re.sub(r"\balle (\d{1,2}) e un quarto\b", r"alle \1:15", lp)
    lp = re.sub(r"\balle (\d{1,2}) e (\d{1,2})\b(?! ?(?:ore|ora)\b)", lambda m: "alle %s:%02d" % (m[1], int(m[2])) if int(m[2]) < 60 else m[0], lp)
    horas = re.findall(r"\balle (\d{1,2})(?:[:.](\d{2}))?", lp)
    attime = [t for t in trig if t.get("trigger") == "time"]
    if len(horas) == 1 and len(attime) == 1:
        h, m = int(horas[0][0]), int(horas[0][1] or 0)
        if h < 12 and re.search(r"di sera|pomeriggio|di pomeriggio|stasera", lp): h += 12
        if h < 24 and m < 60:
            attime[0]["at"] = "%02d:%02d:00" % (h, m)
    # presenza
    if re.search(r"nessuno (e |è )?(in|a) casa|tutti (sono )?(usciti|fuori)|casa vuota|non c.?e nessuno", lp):
        cond = {"trigger": "numeric_state", "entity_id": "zone.home", "below": 1}
        cfg["triggers"] = [cond]
    # zone.home come trigger di stato: va confrontato come numero
    for t in trig:
        if t.get("trigger") == "state" and t.get("entity_id") == "zone.home":
            to = str(t.get("to", ""))
            if to == "0" or re.search(r"esco|esce|usciti|uscit|nessuno", lp) and not to.isdigit():
                t.clear(); t.update({"trigger": "numeric_state", "entity_id": "zone.home", "below": 1})
            elif to.isdigit():
                t.clear(); t.update({"trigger": "numeric_state", "entity_id": "zone.home", "above": 0})
    # confronti numerici
    for t in trig:
        if t.get("trigger") == "numeric_state":
            if re.search(r"sotto|scend|meno di|inferiore|cala", lp) and "above" in t and "below" not in t:
                t["below"] = t.pop("above")
            elif re.search(r"sopra|sal[ei]|piu di|più di|superiore|supera", lp) and "below" in t and "above" not in t:
                t["above"] = t.pop("below")
        if str(t.get("entity_id", "")).startswith("weather.") and re.search(r"piove|pioggia", lp):
            t["trigger"] = "state"; t["to"] = "rainy"; t.pop("above", None); t.pop("below", None)
    # giorni della settimana
    wd = None
    if re.search(r"feriali|lavorativ|dal luned. al venerd.", lp): wd = ["mon", "tue", "wed", "thu", "fri"]
    elif re.search(r"weekend|fine settimana|nel fine", lp): wd = ["sat", "sun"]
    else:
        f = [v for k, v in DAYS.items() if k in lp]
        if f: wd = f
    conds = [c for c in (cfg.get("conditions") or []) if isinstance(c, dict)]
    if wd:
        conds = [c for c in conds if c.get("condition") != "time" or "weekday" not in c]
        conds.append({"condition": "time", "weekday": wd})
    # pausa e spegnimento inventati: via se il prompt non parla di durata
    acts = cfg.get("actions")
    if isinstance(acts, list) and not re.search(r"minut|second|\bor[ae]\b|dopo|poi\b|per \d|temporizz|timer|finche|finch", lp):
        for i, a in enumerate(acts):
            if isinstance(a, dict) and "delay" in a and i > 0 and i + 1 < len(acts):
                nxt = acts[i + 1]
                if isinstance(nxt, dict) and str(nxt.get("action", "")).endswith(".turn_off") and "turn_off" not in lp and "spegn" not in lp:
                    del acts[i:]
                    break
    # condizioni inventate: via se il prompt non le chiede
    if not COND_CUE.search(lp):
        conds = []
    # condizioni su select/number/text mai citati nel prompt: inventate
    def citata(c):
        e = str(c.get("entity_id", "")) if isinstance(c, dict) else ""
        if e.startswith("zone.") and not re.search(r"casa|nessuno|qualcuno|presen|fuori|usciti|rientr", lp):
            return False
        if e.split(".")[0] not in ("select", "number", "text", "input_select", "input_text"):
            return True
        return any(len(w) > 3 and w not in ("casa", "home", "smart", "sensor", "stato") and w in lp for w in e.split(".", 1)[1].split("_"))
    conds = [c for c in conds if citata(c)]
    # qualcuno a casa
    if re.search(r"se c.?e qualcuno|qualcuno (e |è )?(a|in) casa|solo se (siamo|sono) (a|in) casa", lp) and not any("zone.home" in str(c) for c in conds):
        conds.append({"condition": "numeric_state", "entity_id": "zone.home", "above": 0})
    cfg["conditions"] = conds
    return cfg

_TUTTE_STANZE = {"soggiorno", "salotto", "salone", "cucina", "bagno", "camera", "cameretta", "letto", "giardino", "ingresso", "corridoio",
                 "studio", "mansarda", "taverna", "lavanderia", "terrazzo", "balcone", "garage", "cantina", "ufficio", "isola", "tavolo"}


def _altra_stanza(testo, low, w):
    """Il nome contiene un'altra stanza che il prompt non cita (es. 'bagno ingresso bagno' per 'luce dell'ingresso')."""
    g = room_words(w)
    return any(r not in g and re.search(r"\b%s\b" % r, testo) and r not in low for r in _TUTTE_STANZE)


def nome_esatto(y, states, prompt):
    """Se una parola del prompt coincide col nome esatto di un solo interruttore/luce e il modello ha scelto altro, usa quello."""
    low = prompt.lower()
    if not re.search(r"luc|lamp|farett|accend|spegn", low):
        return y
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y
    if not isinstance(cfg, dict) or not isinstance(cfg.get("actions"), list):
        return y
    words = [w for w in re.findall(r"[a-z\u00e0-\u00ff]{5,}", low) if w not in GENERIC and w not in STOP]
    by = {s_["entity_id"]: s_ for s_ in states}
    changed = False
    for w in words:
        hit = [s_ for s_ in states if _dom(s_["entity_id"]) in ("switch", "light") and (s_.get("attributes") or {}).get("friendly_name", "").strip().lower() == w and not NOT_LIGHT.search(_t(s_))]
        if len(hit) != 1:
            continue
        eid = hit[0]["entity_id"]
        for a in cfg["actions"]:
            if not isinstance(a, dict) or not str(a.get("action", "")).startswith(("light.turn_", "switch.turn_")):
                continue
            tg = (a.get("target") or {}).get("entity_id")
            ids = tg if isinstance(tg, list) else [tg] if tg else []
            if eid in ids or any(w in _t(by[i]) and not _altra_stanza(_t(by[i]), low, w) for i in ids if i in by):
                continue
            a["action"] = _dom(eid) + "." + a["action"].split(".", 1)[1]
            a["target"] = {"entity_id": [eid]}
            changed = True
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n") if changed else y

TRANNE = re.compile(r"\btranne\s+(?:quell\w+\s+|l\w+\s+|il\s+|lo\s+|i\s+|gli\s+|un\s+|una\s+)*(?:d\w+\s+|nel\w*\s+|in\s+)?([a-z\u00e0-\u00ff ]{4,40})")

def tranne(y, states, prompt):
    """'tranne X': toglie dai bersagli le entita di X (se ne resta almeno una)."""
    m = TRANNE.search(prompt.lower())
    if not m:
        return y
    parole = [w for w in re.findall(r"[a-z\u00e0-\u00ff]{4,}", m.group(1)) if w not in GENERIC and w not in STOP]
    if not parole:
        return y
    escl = set()
    for w in parole:
        escl |= room_words(w)
    by = {s_["entity_id"]: _t(s_) for s_ in states}
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y
    if not isinstance(cfg, dict) or not isinstance(cfg.get("actions"), list):
        return y
    changed = False
    out = []
    for a in cfg["actions"]:
        tg = (a.get("target") or {}).get("entity_id") if isinstance(a, dict) else None
        if isinstance(tg, list):
            keep = [e for e in tg if not any(x in by.get(e, "") for x in escl)]
            if len(keep) != len(tg):
                changed = True
                if not keep:
                    continue
                a["target"]["entity_id"] = keep
        out.append(a)
    if not changed or not out:
        return y
    cfg["actions"] = out
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n")

EN_STOP = set("when if turn turns off the and then that this with from sensor light lights switch plug fan door lock open close closes pause resume every each night evening morning midnight sunset sunrise above below over under climbs drops passes tops gets reads goes trips fires catches sees picks detects movement".split())
ABOVE = re.compile(r"\b(?:over|above|exceeds?|climbs?(?: over| above)?|tops?|passes|rises?(?: above)?|higher than|more than|sopra|supera|oltre|piu di|pi\u00f9 di)\s+(\d+(?:[.,]\d+)?)")
BELOW = re.compile(r"\b(?:below|under|drops?(?: below)?|falls?(?: below)?|lower than|less than|sotto|scende(?: sotto)?|meno di)\s+(\d+(?:[.,]\d+)?)")

def _ora(lp):
    """Ora esplicita dal prompt (inglese o italiano): 'HH:MM:00' oppure None."""
    if re.search(r"\bmidnight\b|\bmezzanotte\b", lp): return "00:00:00"
    if re.search(r"\bat noon\b|\bmezzogiorno\b", lp): return "12:00:00"
    m = re.search(r"\b(?:at|alle)\s+(\d{1,2})(?:[:.](\d{2})| e (\d{1,2})\b)?\s*(am|pm)?", lp)
    if not m: return None
    h, mi = int(m.group(1)), int(m.group(2) or m.group(3) or 0)
    ap = m.group(4)
    sera = re.search(r"evening|night|tonight|afternoon|di sera|di notte|pomeriggio|stasera", lp)
    if ap == "pm" and h < 12: h += 12
    elif ap == "am" and h == 12: h = 0
    elif not ap and sera:
        if h < 12 and not (h <= 4 and re.search(r"night|notte", lp) and not re.search(r"evening|di sera", lp)): h += 12
        elif h == 12 and re.search(r"night|notte", lp): h = 0
    if h > 23 or mi > 59: return None
    return "%02d:%02d:00" % (h, mi)

def _overlap(s_, lp):
    words = {w for w in re.findall(r"[a-z\u00e0-\u00ff]{3,}", lp) if w not in EN_STOP and w not in GENERIC and w not in STOP}
    return sum(w in _t(s_) for w in words)

def trigger_da_testo(y, states, prompt):
    """Se il modello ha lasciato il trigger manuale ma il prompt dice quando (orario, alba/tramonto, soglia, sensore), lo costruisce con regole (italiano e inglese)."""
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y
    if not isinstance(cfg, dict):
        return y
    trig = [t for t in (cfg.get("triggers") or []) if isinstance(t, dict)]
    if trig and not all(t.get("trigger") == "event" and t.get("event_type") == "yamlic_manuale" for t in trig):
        return y
    lp = prompt.lower(); new = None
    if re.search(r"\bsunset\b|\bdusk\b", lp): new = {"trigger": "sun", "event": "sunset"}
    elif re.search(r"\bsunrise\b|\bdawn\b", lp): new = {"trigger": "sun", "event": "sunrise"}
    else:
        m_a, m_b = ABOVE.search(lp), BELOW.search(lp)
        m = m_a or m_b
        if m and not re.search(r"\bmin(?:ute|uti)s?\b|\bhours?\b|\bore\b", lp[m.start():m.end() + 12]):
            cand = sorted(((_overlap(s_, lp), s_["entity_id"]) for s_ in states if _dom(s_["entity_id"]) == "sensor"), reverse=True)
            if cand and cand[0][0] > 0 and (len(cand) == 1 or cand[0][0] > cand[1][0]):
                val = float(m.group(1).replace(",", ".")); val = int(val) if val == int(val) else val
                new = {"trigger": "numeric_state", "entity_id": cand[0][1], ("above" if m_a else "below"): val}
        if new is None:
            ora = _ora(lp)
            if ora:
                new = {"trigger": "time", "at": ora}
            elif re.search(r"\b(when|if|quando|se)\b", lp):
                cand = sorted(((_overlap(s_, lp), s_["entity_id"]) for s_ in states if _dom(s_["entity_id"]) == "binary_sensor"), reverse=True)
                if cand and cand[0][0] > 0 and (len(cand) == 1 or cand[0][0] > cand[1][0]):
                    new = {"trigger": "state", "entity_id": cand[0][1], "to": "on"}
    if not new:
        return y
    cfg["triggers"] = [new]
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n")

def repair(y, known, prompt=""):
    """Ripara id inventati dal modello: media.player.x, persone inesistenti, entita simili o uniche del dominio."""
    import difflib
    y = re.sub(r"\bmedia\.player\.", "media_player.", y)
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y
    if not isinstance(cfg, dict) or not known:
        return y
    lp = prompt.lower()
    uscita = bool(re.search(r"esco|esce|uscit|nessuno|tutti .*(usciti|fuori)|casa vuota", lp))
    ritorno = bool(re.search(r"arriv|torn|rientr", lp))
    trig = []
    for t in cfg.get("triggers") or []:
        e = t.get("entity_id") if isinstance(t, dict) else None
        if isinstance(e, str) and e.startswith("person.") and e not in known and "zone.home" in known:
            if uscita or ritorno:
                t = {"trigger": "numeric_state", "entity_id": "zone.home", **({"below": 1} if uscita else {"above": 0})}
        trig.append(t)
    meteo = next((k for k in sorted(known) if k.startswith("weather.")), None)
    if meteo and re.search(r"piove|pioggia|piovere", lp):
        trig = [{"trigger": "state", "entity_id": meteo, "to": "rainy"} if isinstance(t, dict) and isinstance(t.get("entity_id"), str) and t["entity_id"] not in known else t for t in trig]
    if trig:
        cfg["triggers"] = trig
    def fix(e):
        m = re.fullmatch(r"([a-z_]+)\.([A-Za-z0-9_]+)", e) if isinstance(e, str) else None
        if not m or e in known or m[1] == "person":
            return e
        cand = [k for k in known if k.split(".")[0] == m[1]]
        if not cand:
            return e
        if len(cand) == 1 and m[1] in ("lock", "weather", "alarm_control_panel"):
            return cand[0]
        parole = [w.lower() for w in m[2].split("_") if len(w) > 1 and not re.fullmatch(r"e\d+", w)]
        pp = [w for w in parole if w in lp]
        base = pp or parole
        best = max(cand, key=lambda k: (sum(w in k for w in base), difflib.SequenceMatcher(None, m[2].lower(), k.split(".", 1)[1]).ratio()))
        if sum(w in best for w in base) >= 1 and m[1] in ("light", "switch", "cover", "media_player", "lock"):
            return best
        return e
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "entity_id":
                    x[k] = [fix(i) for i in v] if isinstance(v, list) else fix(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for i in x:
                walk(i)
    walk(cfg)
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n")

def fill_empty(y, states, prompt):
    """Azioni luce/interruttore/tenda con target vuoto: le riempie con le entita che meglio corrispondono alla stanza nominata."""
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y
    if not isinstance(cfg, dict) or not isinstance(cfg.get("actions"), list):
        return y
    low = prompt.lower()
    words = {w for w in re.findall(r"\w{4,}", low) if w not in STOP and w not in GENERIC}
    changed = False
    for a in cfg["actions"]:
        if not isinstance(a, dict):
            continue
        d = str(a.get("action", "")).split(".")[0]
        if d not in ("light", "switch", "cover") or any(e for e in _target_ents(a)):
            continue
        sc = []
        for s_ in states:
            eid = s_["entity_id"]
            t = _t(s_)
            if _dom(eid) != d and not (d == "light" and _dom(eid) == "switch"):
                continue
            if d in ("light",) and not is_light_like(eid, t):
                continue
            if d == "switch" and (_dom(eid) != "switch" or NOT_LIGHT.search(t)):
                continue
            n = sum(any(x in t for x in room_words(w)) for w in words)
            if n:
                sc.append((n, eid))
        if not sc:
            continue
        top = max(n for n, _ in sc)
        ids = sorted(e for n, e in sc if n == top)
        a["target"] = {"entity_id": ids}
        a.pop("entity_id", None)
        changed = True
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n") if changed else y

def room_guard(y, states, prompt):
    """Se la frase nomina UNA stanza e il modello ha scelto entita di un'altra stanza, usa quelle della stanza nominata."""
    low = prompt.lower()
    words = set(re.findall(r"\w+", low))
    groups = [g for g in ROOMS if g & words]
    if len(groups) != 1:
        return y
    G = groups[0]
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y
    if not isinstance(cfg, dict) or not isinstance(cfg.get("actions"), list):
        return y
    smap = {s_["entity_id"]: s_ for s_ in states}
    pw = {w for w in re.findall(r"\w{4,}", low) if w not in STOP and w not in GENERIC and w not in G}
    def inroom(s_):
        t = _t(s_)
        return any(w in t for w in G)
    out, changed = [], False
    for a in cfg["actions"]:
        svc = str(a.get("action", "")) if isinstance(a, dict) else ""
        d, _, verb = svc.partition(".")
        ents = _target_ents(a) if isinstance(a, dict) else []
        altre = set().union(*[g for g in ROOMS if g is not G]) if len(ROOMS) > 1 else set()
        def sbagliata(e):
            if e not in smap or inroom(smap[e]):
                return False
            t = _t(smap[e])
            return any(w in t for w in altre)
        if d not in ("light", "switch", "cover") or not ents or not all(sbagliata(e) for e in ents):
            out.append(a); continue
        cand = []
        for s_ in states:
            eid = s_["entity_id"]
            if not inroom(s_):
                continue
            if d == "cover" and _dom(eid) == "cover":
                cand.append(s_)
            elif d in ("light", "switch") and _dom(eid) in ("light", "switch") and (is_light_like(eid, _t(s_)) or (_dom(eid) == "switch" and not NOT_LIGHT.search(_t(s_)) and (s_.get("attributes") or {}).get("_area"))):
                cand.append(s_)
        if not cand:
            out.append(a); continue
        sc = [(sum(w in _t(s_) for w in pw), s_["entity_id"]) for s_ in cand]
        top = max(n for n, _ in sc)
        ids = sorted(e for n, e in sc if n == top)[:6]
        base = {k: v for k, v in a.items() if k not in ("target", "entity_id", "action")}
        if d == "cover":
            out.append({"action": svc, **base, "target": {"entity_id": ids}})
        else:
            li = [e for e in ids if e.startswith("light.")]
            sw = [e for e in ids if e.startswith("switch.")]
            if li:
                out.append({"action": "light." + verb, **base, "target": {"entity_id": li}})
            if sw:
                out.append({"action": "switch." + verb, **{k: v for k, v in base.items() if k != "data"}, "target": {"entity_id": sw}})
        changed = True
    if not changed:
        return y
    cfg["actions"] = out
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n")


IMPULSO = re.compile(r"cancell|portone|garage|basculante")

DURATA = re.compile(r"minut|second|\bor[ae]\b|dopo|poi\b|per \d|temporizz|timer|finche|finch|ritard|attendi|aspetta")

def via_pausa(y, prompt):
    """Il modello a volte aggiunge 'delay + azione' non richiesti: se il prompt non parla di durata li toglie."""
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y
    acts = cfg.get("actions") if isinstance(cfg, dict) else None
    if not isinstance(acts, list):
        return y
    n0 = len(acts)
    dedup = [a for i, a in enumerate(acts) if not (i > 0 and a == acts[i - 1] and isinstance(a, dict) and "action" in a)]
    if len(dedup) != len(acts):
        cfg["actions"] = acts = dedup
    dump = lambda: yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n")
    if DURATA.search(prompt.lower()):
        return dump() if len(dedup) != n0 else y
    for i, a in enumerate(acts):
        if i > 0 and isinstance(a, dict) and "delay" in a and all(isinstance(b, dict) and str(b.get("action", "")).split(".", 1)[0] in ("light", "switch") for b in acts[i + 1:]):
            del acts[i:]
            return dump()
    return dump() if len(dedup) != n0 else y

def impulsi(y):
    """Cancelli/garage su switch sono quasi sempre a impulso: un solo turn_on. Ritorna (yaml, avvisi)."""
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y, []
    if not isinstance(cfg, dict) or not isinstance(cfg.get("actions"), list):
        return y, []
    avv = []
    for a in cfg["actions"]:
        if not isinstance(a, dict) or not str(a.get("action", "")).startswith("switch."):
            continue
        ents = _target_ents(a)
        if ents and all(IMPULSO.search(e.lower()) for e in ents):
            if a["action"] != "switch.turn_on":
                a["action"] = "switch.turn_on"
            avv.append("cancello/garage a impulso: invia un solo impulso, non sa se e aperto o chiuso (controlla con un sensore di stato)")
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n"), sorted(set(avv))

def force_targets(y, scelte, prompt="", known=None):
    """Dopo una domanda di chiarimento: nelle azioni luce/interruttore mette esattamente le entita scelte
    (luci e interruttori separati), senza fidarsi dell'elenco scritto dal modello. Poi pulisce alias e trigger."""
    if known:
        y = repair(y, known, prompt)
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y
    if not isinstance(cfg, dict) or not isinstance(cfg.get("actions"), list):
        return y
    verbo = None
    out, done = [], False
    for a in cfg["actions"]:
        svc = str(a.get("action", "")) if isinstance(a, dict) else ""
        if scelte and svc.split(".")[0] in ("light", "switch") and svc.split(".", 1)[-1] in {"turn_on", "turn_off", "toggle"}:
            if not done:
                v = svc.split(".", 1)[1]
                lp = prompt.lower()
                if re.search(r"spegn|spenga|spent", lp) and not re.search(r"accend|accens", lp):
                    v = "turn_off"
                elif re.search(r"accend|accens", lp) and not re.search(r"spegn|spenga|spent", lp):
                    v = "turn_on"
                base = {k: val for k, val in a.items() if k not in ("target", "entity_id", "action")}
                li = [e for e in scelte if e.startswith("light.")]
                sw = [e for e in scelte if e.startswith("switch.")]
                if li:
                    out.append({"action": "light." + v, **base, "target": {"entity_id": li}})
                if sw:
                    out.append({"action": "switch." + v, **{k: val for k, val in base.items() if k not in ("data",)}, "target": {"entity_id": sw}})
                done = True
            continue
        out.append(a)
    cov = [e for e in scelte if e.startswith("cover.")]
    if cov:
        o2, fatto = [], False
        for a in out:
            svc = str(a.get("action", "")) if isinstance(a, dict) else ""
            if svc.startswith("cover."):
                if not fatto:
                    b = {k: val for k, val in a.items() if k not in ("target", "entity_id")}
                    b["target"] = {"entity_id": cov}
                    o2.append(b); fatto = True
                continue
            o2.append(a)
        out = o2
    if re.search(r"solo se (?:\u00e8 |e |fa )?buio|quando (?:\u00e8 |e |fa )?buio|solo di notte|dopo il tramonto|solo se (?:\u00e8 |e )?notte|al buio", prompt.lower()):
        out = [a for a in out if not (isinstance(a, dict) and "if" in a and "sun" in str(a.get("if")))]
        cfg["conditions"] = [{"condition": "or", "conditions": [{"condition": "sun", "after": "sunset"}, {"condition": "sun", "before": "sunrise"}]}]
    cfg["actions"] = out
    if prompt:
        cfg = rules(cfg, prompt)
    for t in cfg.get("triggers") or []:
        if isinstance(t, dict) and isinstance(t.get("at"), str):
            m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", t["at"].strip())
            if m:
                t["at"] = "%02d:%s:%s" % (int(m.group(1)), m.group(2), m.group(3) or "00")
    if prompt and not TRIGGER_CUE.search(re.sub(r"(dopo|tra|fra) \d+ \w+", "", prompt.lower())):
        cfg["triggers"] = [{"trigger": "event", "event_type": "yamlic_manuale"}]
        cfg["conditions"] = []
    if prompt:
        al = prompt.strip().rstrip(".")
        al = al[:1].upper() + al[1:]
        if len(al) > 60:
            al = al[:60].rsplit(" ", 1)[0] + "\u2026"
        cfg["alias"] = al
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n")

def clean(y, known):
    """fix_domains + prune_unknown. Restituisce (yaml, entita_tolte)."""
    return prune_unknown(fix_domains(y), known)

# ---------- estrazione YAML ----------
def extract_yaml(text):
    m = re.search(r"```[a-zA-Z]*[ \t]*\n(.*?)(?:```|\Z)", text, re.S)
    return merge_entities((m.group(1) if m else text).strip())

ENT = re.compile(r"^\s+entity_id:\s*[\w.]+\s*$")

def merge_entities(y):
    out, buf = [], []
    ind = lambda l: re.match(r"\s*", l).group(0)
    def flush():
        if len(buf) > 1:
            i = ind(buf[0])
            out.append(i + "entity_id:")
            out.extend(i + "  - " + b.split(":", 1)[1].strip() for b in buf)
        else:
            out.extend(buf)
        buf.clear()
    for line in y.split("\n"):
        if ENT.match(line):
            if buf and ind(line) != ind(buf[0]):
                flush()
            buf.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)

# ---------- validatore ----------
TRIGGERS = {"state", "numeric_state", "time", "time_pattern", "sun", "homeassistant", "event", "zone", "template", "device"}
SERVICES = {"assist_satellite": {"announce"}, "homeassistant": {"toggle", "turn_on", "turn_off"},
    "light": {"turn_on", "turn_off", "toggle"}, "switch": {"turn_on", "turn_off", "toggle"},
    "cover": {"open_cover", "close_cover", "stop_cover", "set_cover_position", "toggle"},
    "climate": {"set_hvac_mode", "set_temperature", "turn_on", "turn_off"},
    "lock": {"lock", "unlock"}, "fan": {"turn_on", "turn_off", "toggle"},
    "media_player": {"turn_on", "turn_off", "media_pause", "media_play", "media_stop", "play_media", "volume_set", "volume_mute"},
    "vacuum": {"start", "pause", "stop", "return_to_base", "locate"}, "tts": None,
    "alarm_control_panel": {"alarm_arm_away", "alarm_arm_home", "alarm_disarm"},
    "input_boolean": {"turn_on", "turn_off", "toggle"}, "scene": {"turn_on"}, "script": {"turn_on"}, "notify": None,
}
SENSITIVE = {"lock", "alarm_control_panel"}
SENSITIVE_WORDS = ("serratur", "allarm", "blocca", "sblocca", "lock", "disinser", "inser", "arma", "chiud", "apri")
STATE_TO = {"person": {"home", "not_home"}, "binary_sensor": {"on", "off"}, "light": {"on", "off"},
            "switch": {"on", "off"}, "input_boolean": {"on", "off"}, "lock": {"locked", "unlocked"},
            "cover": {"open", "closed"}, "media_player": {"on", "off", "playing", "paused", "idle", "standby"}}
OLD_KEYS = {"platform", "service"}
BLUEPRINTS = {"homeassistant/motion_light.yaml": {"motion_entity", "light_target", "no_motion_wait"}}

def _ents(x):
    return [x] if isinstance(x, str) else list(x) if isinstance(x, (list, tuple)) else []

def _target_ents(step):
    return _ents(step.get("entity_id")) + _ents((step.get("target") or {}).get("entity_id"))

def validate(y, known, prompt="", remote=False):
    """Lista di problemi. remote=True: la risposta arriva da un nodo sconosciuto, regole piu strette."""
    p = []
    try:
        cfg = yaml.safe_load(y)
    except Exception as e:
        return ["YAML non valido: %s" % str(e)[:80]]
    if not isinstance(cfg, dict):
        return ["non e un'automazione"]
    for k in cfg:
        if k not in {"alias", "description", "mode", "triggers", "conditions", "actions", "id", "variables", "use_blueprint"}:
            p.append("chiave non valida: %s" % k)
    if not cfg.get("alias"):
        p.append("manca alias")
    if "use_blueprint" in cfg:
        ub = cfg["use_blueprint"]
        if not isinstance(ub, dict) or ub.get("path") not in BLUEPRINTS:
            p.append("blueprint sconosciuto")
        else:
            for k, val in (ub.get("input") or {}).items():
                if k not in BLUEPRINTS[ub["path"]]:
                    p.append("input blueprint sconosciuto: %s" % k)
                for e in _ents(val if not isinstance(val, dict) else val.get("entity_id")):
                    if isinstance(e, str) and "." in e and e not in known:
                        p.append("entita inesistente: %s" % e)
        return p
    trig, acts = cfg.get("triggers"), cfg.get("actions")
    if not isinstance(trig, list) or not trig:
        p.append("manca triggers (lista)")
    if not isinstance(acts, list) or not acts:
        p.append("manca actions (lista)")
    if not isinstance(cfg.get("conditions", []), list):
        p.append("conditions deve essere una lista")
    for t in trig if isinstance(trig, list) else []:
        if not isinstance(t, dict):
            p.append("trigger non valido"); continue
        if set(t) & OLD_KEYS:
            p.append("sintassi vecchia nel trigger")
        kind = t.get("trigger")
        if kind not in TRIGGERS:
            p.append("tipo trigger sconosciuto: %s" % kind); continue
        if kind in ("state", "numeric_state"):
            es = _ents(t.get("entity_id"))
            if not es:
                p.append("%s senza entity_id" % kind)
            for e in es:
                if e not in known:
                    p.append("entita inesistente: %s" % e)
            if kind == "state":
                to, dom = t.get("to"), _dom(es[0]) if es else ""
                if to is not None and dom in STATE_TO and any(str(v) not in STATE_TO[dom] for v in (to if isinstance(to, list) else [to])):
                    p.append("stato '%s' non valido per %s" % (to, es[0]))
            else:
                if "above" not in t and "below" not in t:
                    p.append("numeric_state senza above/below")
                if "to" in t or "from" in t:
                    p.append("numeric_state non accetta to/from")
        elif kind == "time" and "at" not in t:
            p.append("time senza at")
        elif kind == "sun" and t.get("event") not in {"sunrise", "sunset"}:
            p.append("sun event non valido")
    low = prompt.lower()
    def check_actions(items):
        for a in items:
            if not isinstance(a, dict):
                p.append("azione non valida"); continue
            if set(a) & OLD_KEYS:
                p.append("sintassi vecchia nell'azione")
            if "action" in a:
                svc = str(a["action"]); dom, _, name = svc.partition(".")
                if dom not in SERVICES or not name:
                    p.append("servizio sconosciuto: %s" % svc)
                elif SERVICES[dom] is not None and name not in SERVICES[dom]:
                    p.append("servizio inesistente: %s" % svc)
                for e in _target_ents(a):
                    if "{{" in str(e):
                        continue
                    if e not in known:
                        p.append("entita inesistente: %s" % e)
                    elif dom in SERVICES and dom not in {"notify", "scene", "script", "homeassistant"} and _dom(e) != dom:
                        p.append("entita %s non adatta a %s" % (e, svc))
                if dom != "notify" and not _target_ents(a):
                    p.append("azione senza target: %s" % svc)
                if remote and dom in SENSITIVE and not any(w in low for w in SENSITIVE_WORDS):
                    p.append("azione sensibile (%s) non richiesta: bloccata" % svc)
                if remote and re.search(r"https?://", json.dumps(a.get("data", {}))):
                    p.append("link nel messaggio: bloccato")
            elif not ({"delay", "wait_for_trigger", "if", "choose"} & set(a)):
                p.append("azione senza 'action'")
    check_actions(acts if isinstance(acts, list) else [])
    for c in cfg.get("conditions") or []:
        if not isinstance(c, dict) or "condition" not in c:
            p.append("condizione senza 'condition'"); continue
        if c["condition"] not in {"state", "numeric_state", "time", "sun", "template", "zone", "trigger", "or", "and", "not"}:
            p.append("condizione sconosciuta: %s" % c["condition"])
    return list(dict.fromkeys(p))


# ================= 0.3.28 =================
SERVIZI_OK = {"light", "switch", "cover", "fan", "climate", "media_player", "lock", "vacuum", "scene", "script", "input_boolean", "input_number", "input_select",
              "input_button", "input_datetime", "input_text", "notify", "tts", "alarm_control_panel", "homeassistant", "persistent_notification", "humidifier", "button",
              "number", "select", "automation", "water_heater", "siren", "valve", "remote", "counter", "timer", "event"}
_PIAZZE = ("domain", "state", "attribute")


def _passi(lst, fn):
    """Applica fn(passo)->passo|None a tutti i passi, anche dentro choose/if/sequence/repeat. Ritorna la nuova lista."""
    out = []
    for a in lst or []:
        if not isinstance(a, dict):
            out.append(a); continue
        a = fn(a)
        if a is None:
            continue
        for k in ("sequence", "then", "else", "default"):
            if isinstance(a.get(k), list):
                a[k] = _passi(a[k], fn)
        if isinstance(a.get("choose"), list):
            for o in a["choose"]:
                if isinstance(o, dict) and isinstance(o.get("sequence"), list):
                    o["sequence"] = _passi(o["sequence"], fn)
        if isinstance(a.get("repeat"), dict) and isinstance(a["repeat"].get("sequence"), list):
            a["repeat"]["sequence"] = _passi(a["repeat"]["sequence"], fn)
        out.append(a)
    return out


def _citata(eid, lp):
    """True se il prompt cita l'entita (almeno una parola del suo id/nome)."""
    parole = [p for p in re.split(r"[._]", eid.lower())[1:] if len(p) >= 4 and p not in ("sonoff", "shelly", "light", "switch", "luce")]
    return any(p[:5] in lp for p in parole) if parole else False


_SIN = {"media_player": r"\btv\b|televis|stereo|altoparlante|musica", "light": r"luce|luci|lampad|faretti", "switch": r"presa|interruttore|stufa|caldai|pompa", "fan": r"ventola", "cover": r"tapparell|tend|serrand"}
_STATO = r"acces|spent|apert|chius|in funzione|\battiv"


def regole_028(y, prompt):
    """Dopo il modello: (1) toglie azioni con servizi inventati (camera.*, ...), (2) toglie condizioni su luci/prese non citate,
    (3) avvisa turn_off+turn_on sulla stessa entita, (4) avvisa se il prompt ha due orari e l'automazione uno solo. Ritorna (yaml, avvisi)."""
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y, []
    if not isinstance(cfg, dict):
        return y, []
    lp = prompt.lower()
    avv, mod = [], [False]

    def servizio(a):
        s = str(a.get("action", "")) if "action" in a else ""
        if s and "." in s and s.split(".", 1)[0] not in SERVIZI_OK:
            avv.append("servizio inventato tolto: %s" % s); mod[0] = True
            return None
        return a
    if isinstance(cfg.get("actions"), list):
        cfg["actions"] = _passi(cfg["actions"], servizio)
    conds = cfg.get("conditions")
    if isinstance(conds, list):
        nuove = []
        for c in conds:
            if isinstance(c, dict) and c.get("condition") == "state" and isinstance(c.get("entity_id"), str) \
                    and c["entity_id"].split(".")[0] in ("light", "switch", "fan", "cover", "media_player") \
                    and not _citata(c["entity_id"], lp) and not (re.search(_SIN.get(c["entity_id"].split(".")[0], "$^"), lp) and re.search(_STATO, lp)) and not re.search(r"\bse (e|è|era|sono|la|il)\b.*(spent|acces|apert|chius)|gia (acces|spent)", lp):
                avv.append("condizione non richiesta tolta: %s" % c["entity_id"]); mod[0] = True
                continue
            nuove.append(c)
        if len(nuove) != len(conds):
            cfg["conditions"] = nuove
    # turn_on + turn_off sulla stessa entita
    viste = {}
    for a in cfg.get("actions") or []:
        if isinstance(a, dict) and str(a.get("action", "")).endswith((".turn_on", ".turn_off")):
            t = (a.get("target") or {}).get("entity_id")
            for e in (t if isinstance(t, list) else [t]):
                if isinstance(e, str):
                    viste.setdefault(e, set()).add(a["action"].rsplit(".", 1)[1])
    for e, s in sorted(viste.items()):
        if len(s) > 1 and not re.search(DURATA, lp):
            avv.append("accende e spegne %s nella stessa automazione: controlla" % e)
    # due orari
    ore = set(re.findall(r"\balle\s+(\d{1,2})(?:[:.](\d{2})| e (\d{1,2}))?\b", lp))
    orari = {(h, m or m2 or "0") for h, m, m2 in ore}
    nt = sum(1 for t in (cfg.get("triggers") or []) if isinstance(t, dict) and t.get("trigger") == "time")
    ha_choose = "choose" in y
    if len(orari) >= 2 and nt < 2 and not ha_choose and not re.search(r"\bdalle\b.*\balle\b|\btra le\b", lp):
        avv.append("la richiesta ha due orari ma l'automazione ne ha uno solo: controlla")
    if mod[0]:
        y = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n")
    return y, avv


# ---------- scomposizione ----------
_AZIONE = re.compile(r"\b(accend|spegn|apr[ie]|chiud|avvis|mand|blocc|sblocc|attiv|disattiv|imposta|mett|avvia|ferma|notific|abbass|alza|porta|fai|dimmi|annunci|illumin)\w*")
_QUANDO = re.compile(r"\b(quando|appena|ogni|alle\s+\d|al tramonto|all'alba|al sorgere|se\s+(?:la|il|lo|le|in|c'e|c'è|qualcuno|piove|fa)|dopo le|ogni volta|a mezzanotte|a mezzogiorno|di mattina|di sera|stasera|domani)\b")
_ANAFORA = re.compile(r"\b(?:accend|spegn|apr|chiud|blocc|sblocc|attiv|disattiv|ferm|avvi|alz|abbass|riaccend|riapr|richiud|mett)\w*(?:la|lo|le|li)\b|\b(la stessa|lo stesso|le stesse|stessa luce|stesse luci|poi la|poi lo|di nuovo|ancora|quell[aeoi]|in pi[uù]|se non si)\b")
_TAGLIO = re.compile(r"\s*(?:[.;!?]+\s+|\s+e poi\s+|\s+inoltre\s+|\s+invece\s+|,\s+e\s+(?=quando|appena|ogni|alle|al tramonto|all'alba)|\s+e\s+(?=quando|appena|ogni volta|al tramonto|all'alba)|,\s+(?=quando|appena|ogni volta|al tramonto|all'alba|alle\s+\d)|\s+(?=quando\s))\s*", re.I)


def scomponi(prompt):
    """Divide una richiesta lunga in richieste indipendenti (ognuna con un proprio 'quando' e una propria azione).
    Non divide se la seconda parte si riferisce alla prima (spegnila, la stessa, ...) o non ha un proprio trigger.
    Ritorna una lista (1 elemento = niente da dividere)."""
    p = prompt.strip()
    if len(p) < 40:
        return [p]
    pezzi = [re.sub(r"[\s,]+(e\s+)?poi$", "", x.strip(" ,"), flags=re.I) for x in _TAGLIO.split(p) if x and x.strip(" ,")]
    if len(pezzi) < 2:
        return [p]
    out = [pezzi[0]]
    for x in pezzi[1:]:
        lx = x.lower()
        ok = _AZIONE.search(lx) and _QUANDO.search(lx) and not _ANAFORA.search(lx) and len(lx) >= 12
        prec = out[-1].lower()
        if ok and _AZIONE.search(prec) and _QUANDO.search(prec):
            out.append(x)
        else:
            out[-1] = out[-1] + (" " if out[-1][-1:] in ".;!?" else ", ") + x
    return out[:4] if len(out) > 1 else [p]



_CODA = re.compile(r"[\s,]+e\s+se\s+(?P<sogg>.+?)\s+(?:\u00e8|e'|e)\s+(?P<st>accesa|acceso|spenta|spento|aperta|aperto|chiusa|chiuso)\s*,?\s+(?P<v>spegn|accend|apr|chiud)(?:ila|ilo|ili|ile|i)\b[\s.!]*$", re.I)
_STATO_SERV = {"media_player": ("turn_on", "turn_off"), "light": ("turn_on", "turn_off"), "switch": ("turn_on", "turn_off"), "fan": ("turn_on", "turn_off"), "cover": ("open_cover", "close_cover")}


def coda_condizionale(prompt, states):
    """'..., e se la TV del soggiorno e accesa spegnila': separa la coda dalla frase e la costruisce con regole.
    Ritorna (prompt_ridotto, azione_choose, avviso) oppure None."""
    m = _CODA.search(prompt)
    if not m:
        return None
    parole = [w for w in re.findall(r"[a-z\u00e0-\u00ff]{2,}", m.group("sogg").lower()) if w not in STOP and w not in ("del", "della", "dello", "dei", "delle", "il", "lo", "la", "le", "un", "una")]
    if not parole:
        return None
    best, bs = None, 0
    for s_ in states:
        eid = s_["entity_id"]
        d = eid.split(".")[0]
        if d not in _STATO_SERV:
            continue
        t = _t(s_)
        n = sum(w in t for w in parole)
        if "tv" in parole and d == "media_player":
            n += 2
        if "solo audio" in t or "luminosita" in t:
            n -= 3
        if n > bs:
            best, bs = eid, n
    if not best or bs < max(1, len(parole) - 1):
        return None
    d = best.split(".")[0]
    st = m.group("st").lower()
    v = m.group("v").lower()
    on, off = _STATO_SERV[d]
    apre = v.startswith(("accend", "apr"))
    srv = d + "." + (on if apre else off)
    if d == "cover":
        stato = "open" if st in ("aperta", "aperto") else "closed"
    else:
        stato = "on" if st in ("accesa", "acceso", "aperta", "aperto") else "off"
    azione = {"if": [{"condition": "state", "entity_id": best, "state": stato}], "then": [{"action": srv, "target": {"entity_id": best}}]}
    return prompt[:m.start()].rstrip(" ,") + ".", azione, "'se %s' costruito con regole: se %s e %s, %s" % (m.group("sogg").strip(), best, stato, srv)


def aggiungi_azione(y, azione):
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y
    if not isinstance(cfg, dict):
        return y
    k = "actions" if "actions" in cfg or "triggers" in cfg else "action"
    if not isinstance(cfg.get(k), list):
        cfg[k] = []
    cfg[k].append(azione)
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n")

def unisci(risultati):
    """risultati: lista di dict di generate(). Ritorna il YAML unito (automazioni separate da ---) e i problemi con prefisso [n]."""
    ys, pr = [], []
    for i, r in enumerate(risultati, 1):
        if r.get("error"):
            pr.append("[%d] non riuscita: %s" % (i, r["error"]))
            ys.append("# Automazione %d: non riuscita" % i)
            continue
        ys.append("# Automazione %d\n%s" % (i, r.get("yaml", "")))
        pr += ["[%d] %s" % (i, x) for x in r.get("problems", [])]
    return "\n---\n".join(ys), pr


# ---------- modifica di un'automazione esistente ----------
SYSTEM_MOD = SYSTEM + " Se ricevi un'automazione attuale e una modifica, restituisci l'automazione completa modificata, cambiando solo cio che e chiesto."
_GIORNI = [(r"feriali|dal lunedi al venerdi|nei giorni lavorativi", ["mon", "tue", "wed", "thu", "fri"]), (r"weekend|fine settimana|sabato e domenica", ["sat", "sun"]),
           (r"tranne la domenica|non la domenica", ["mon", "tue", "wed", "thu", "fri", "sat"]), (r"solo il lunedi", ["mon"]), (r"solo il martedi", ["tue"]),
           (r"solo il mercoledi", ["wed"]), (r"solo il giovedi", ["thu"]), (r"solo il venerdi", ["fri"]), (r"solo il sabato", ["sat"]), (r"solo la domenica", ["sun"])]


def modifica_regole(y, istr):
    """Modifiche semplici senza modello: orario, giorni, luminosita, togli condizioni. Ritorna (yaml, descrizione) o None."""
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return None
    if not isinstance(cfg, dict):
        return None
    lp = istr.lower().replace("ì", "i").replace("è", "e")
    novita = []
    # togli le condizioni
    if re.search(r"\b(togli|elimina|rimuovi|cancella)\b.*\bcondizion|senza condizion|deve scattare sempre|parta sempre", lp) and cfg.get("conditions"):
        cfg["conditions"] = []; novita.append("condizioni tolte")
    else:
        # giorni
        for pat, g in _GIORNI:
            if re.search(pat, lp) and re.search(r"solo|tranne|feriali|weekend|giorni|aggiungi|limita", lp):
                cond = [c for c in (cfg.get("conditions") or []) if not (isinstance(c, dict) and c.get("condition") == "time" and "weekday" in c)]
                cond.append({"condition": "time", "weekday": g}); cfg["conditions"] = cond; novita.append("giorni"); break
        # luminosita
        m = re.search(r"(?:luminosit\w*|intensit\w*|al|a)\s*(?:del|al|a)?\s*(\d{1,3})\s*(?:%|per ?cento|percento)", lp)
        if m and 0 < int(m.group(1)) <= 100:
            n = [0]

            def lum(a):
                if a.get("action") == "light.turn_on":
                    a.setdefault("data", {})["brightness_pct"] = int(m.group(1)); n[0] += 1
                return a
            cfg["actions"] = _passi(cfg.get("actions"), lum)
            if n[0]: novita.append("luminosita")
        # orario
        ora = _ora(lp)
        tr = [t for t in (cfg.get("triggers") or []) if isinstance(t, dict) and t.get("trigger") == "time"]
        if ora and len(tr) == 1 and re.search(r"orario|alle|sposta|anticip|posticip|cambia|fallo|metti", lp) and str(tr[0].get("at")) != ora:
            tr[0]["at"] = ora; novita.append("orario")
    if not novita:
        return None
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n"), ", ".join(novita)


# ---------- firma strutturale (per il voto tra nodi) e controlli finali ----------
def firma(y):
    """Struttura normalizzata di un'automazione: insieme di trigger e di (servizio, bersagli). Serve a confrontare risposte di nodi diversi."""
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return None
    if not isinstance(cfg, dict):
        return None

    def ents(t):
        e = (t or {}).get("entity_id") if isinstance(t, dict) else None
        return tuple(sorted(e if isinstance(e, list) else [e])) if e else ()
    trig = sorted((str(t.get("trigger")), ents(t), str(t.get("at") or t.get("event") or t.get("to") or t.get("above") or t.get("below") or ""))
                  for t in (cfg.get("triggers") or []) if isinstance(t, dict))
    acts = []
    _passi(cfg.get("actions") or [], lambda a: (acts.append((str(a.get("action", "")), ents(a.get("target")), json.dumps(a.get("data", {}), sort_keys=True))) if "action" in a else None, a)[1])
    return json.dumps([trig, sorted(acts), len(cfg.get("conditions") or [])], sort_keys=True)


def controlla_jinja(y):
    """Se l'automazione contiene template {{ }} / {% %}, ne controlla la sintassi (se jinja2 c'e). Ritorna una lista di problemi."""
    if "{{" not in y and "{%" not in y:
        return []
    try:
        import jinja2
    except Exception:
        return []
    p = []
    for blocco in re.findall(r"(\{\{.*?\}\}|\{%.*?%\})", y, re.S):
        try:
            jinja2.Environment().parse(blocco)
        except Exception as e:
            p.append("template non valido %s: %s" % (blocco[:40], str(e)[:60]))
    return p


def unisci_stesso_trigger(risultati):
    """Se due parti producono automazioni con gli stessi trigger e nessuna condizione diversa, le fonde in una (azioni in sequenza).
    risultati: lista di dict di generate(). Ritorna la lista con i dict fusi (YAML dump deterministico)."""
    out, visti = [], {}
    for r in risultati:
        if r.get("error") or not r.get("yaml"):
            out.append(r); continue
        try:
            cfg = yaml.safe_load(r["yaml"])
            chiave = json.dumps([cfg.get("triggers"), cfg.get("conditions") or []], sort_keys=True, default=str)
        except Exception:
            out.append(r); continue
        if chiave in visti and isinstance(cfg.get("actions"), list):
            base = visti[chiave]
            bc = yaml.safe_load(base["yaml"])
            bc["actions"] = (bc.get("actions") or []) + [a for a in cfg["actions"] if a not in (bc.get("actions") or [])]
            base["yaml"] = yaml.safe_dump(bc, allow_unicode=True, sort_keys=False).rstrip("\n")
            base["problems"] = list(dict.fromkeys(base.get("problems", []) + r.get("problems", [])))
        else:
            visti[chiave] = r; out.append(r)
    return out


# ---------- grammatica (llama.cpp GBNF): il modello puo scrivere solo entita e servizi dell'elenco ----------
SERVIZI_BASE = ["light.turn_on", "light.turn_off", "light.toggle", "switch.turn_on", "switch.turn_off", "switch.toggle", "cover.open_cover", "cover.close_cover", "cover.stop_cover",
                "cover.set_cover_position", "fan.turn_on", "fan.turn_off", "climate.set_temperature", "climate.set_hvac_mode", "climate.turn_on", "climate.turn_off",
                "media_player.turn_on", "media_player.turn_off", "media_player.media_play", "media_player.media_pause", "media_player.volume_set", "lock.lock", "lock.unlock",
                "vacuum.start", "vacuum.pause", "vacuum.stop", "vacuum.return_to_base", "scene.turn_on", "script.turn_on", "input_boolean.turn_on", "input_boolean.turn_off",
                "alarm_control_panel.alarm_arm_away", "alarm_control_panel.alarm_arm_home", "alarm_control_panel.alarm_disarm", "tts.speak", "persistent_notification.create",
                "humidifier.turn_on", "humidifier.turn_off", "automation.trigger", "homeassistant.turn_on", "homeassistant.turn_off"]
CHIAVI_OK = ("alias description mode id triggers trigger event event_type at to from for hours minutes seconds above below offset value_template conditions condition state after before "
             "weekday zone actions data target brightness_pct brightness rgb_color color_temp_kelvin position temperature hvac_mode message title volume_level media_content_id "
             "delay choose conditions sequence default if then else repeat count while until wait_template wait_for_trigger timeout continue_on_timeout variables type "
             "attribute entity_ids device_class area_id floor_id label_id use_blueprint path input platform domain service fan_mode preset_mode transition effect").split()


def _esc(x):
    return x.replace("\\", "\\\\").replace('"', '\\"')


def grammatica(entita, servizi_extra=()):
    """GBNF per llama.cpp: entity_id solo dall'elenco, action solo da servizi noti (+ notify.* presenti), chiavi note, valori senza identificatori inventati (lettera.lettera)."""
    ent = " | ".join('"%s"' % _esc(e) for e in sorted(set(entita))) or '"zone.home"'
    notify = ["%s" % e for e in entita if e.startswith("notify.")]
    svc = " | ".join('"%s"' % _esc(s) for s in sorted(set(SERVIZI_BASE) | set(notify) | set(servizi_extra)))
    chiavi = " | ".join('"%s"' % k for k in sorted(set(CHIAVI_OK)))
    return "\n".join([
        'root ::= "```yaml\\n" line+ "```"',
        'line ::= ind (entkv | entitem | actkv | kv | item) "\\n"',
        'ind ::= " "*',
        'entkv ::= ("- ")? "entity_id:" (" " (ent | "[" ent (", " ent)* "]"))?',
        'entitem ::= "- " ent',
        'actkv ::= ("- ")? "action: " svc',
        'kv ::= ("- ")? key ":" (" " val)?',
        'item ::= "- " val',
        "ent ::= " + ent,
        "svc ::= " + svc,
        "key ::= " + chiavi,
        'val ::= vchar+ "."?',
        'vchar ::= [^\\n.] | "." [0-9 ]',
        ""])


_VAL = re.compile(r"^(?:[^\n.]|\.[0-9 ])+\.?$")


def grammatica_accetta(y, entita, servizi_extra=()):
    """Stesso criterio della grammatica, in Python (per provarla sui dati senza llama.cpp)."""
    ents, svc = set(entita), set(SERVIZI_BASE) | {e for e in entita if e.startswith("notify.")} | set(servizi_extra)
    for riga in y.strip("\n").split("\n"):
        t = riga.lstrip(" ")
        if not t:
            return False
        if t.startswith("- "):
            r = t[2:]
            if r in ents:
                continue
        else:
            r = t
        m = re.match(r"^([A-Za-z_]+):(?: (.*))?$", r)
        if m:
            k, v = m.group(1), m.group(2)
            if k == "entity_id":
                if v is None or v in ents or (v.startswith("[") and v.endswith("]") and all(x in ents for x in v[1:-1].split(", "))):
                    continue
                return False
            if k == "action":
                if v in svc:
                    continue
                return False
            if k not in CHIAVI_OK:
                return False
            if v is None or _VAL.match(v):
                continue
            return False
        if t.startswith("- ") and _VAL.match(r):
            continue
        return False
    return True


# ---------- 0.3.28: riparazioni dopo il modello ----------
SERVIZI_DOMINIO = {
    "number": {"set_value", "increment", "decrement"},
    "select": {"select_option", "select_first", "select_last", "select_next", "select_previous"},
    "light": {"turn_on", "turn_off", "toggle"}, "switch": {"turn_on", "turn_off", "toggle"},
    "cover": {"open_cover", "close_cover", "stop_cover", "set_cover_position", "toggle", "open_cover_tilt", "close_cover_tilt", "set_cover_tilt_position"},
    "fan": {"turn_on", "turn_off", "toggle", "set_percentage", "oscillate", "set_preset_mode", "set_direction"},
    "tts": {"speak", "cloud_say", "google_translate_say"},
    "input_boolean": {"turn_on", "turn_off", "toggle"}, "scene": {"turn_on", "apply"},
    "script": {"turn_on", "turn_off", "toggle", "reload"}, "button": {"press"}, "input_button": {"press"},
    "climate": {"set_hvac_mode", "set_temperature", "turn_on", "turn_off", "set_preset_mode", "set_fan_mode", "set_humidity", "toggle"},
    "humidifier": {"turn_on", "turn_off", "toggle", "set_humidity", "set_mode"},
}
_LUCE_DATA = ("brightness_pct", "brightness", "color_name", "rgb_color", "color_temp", "color_temp_kelvin", "kelvin", "transition")


def _luce_simile(eid, states):
    """light.* con l'object_id piu simile (prefisso comune piu lungo) a un'entita di altro dominio."""
    oid = eid.split(".", 1)[1] if "." in eid else eid
    best, lb = None, 0
    for s_ in states:
        e = s_["entity_id"]
        if not e.startswith("light."):
            continue
        o = e.split(".", 1)[1]
        n = len(os.path.commonprefix([o, oid]))
        if n > lb and n >= min(len(o), 8):
            best, lb = e, n
    return best


def _luci_stanza(states, lp):
    out = []
    for g in ROOMS + [{"ingresso"}, {"corridoio"}]:
        parole = [w for w in g if re.search(r"\b%s\b" % w, lp)]
        if not parole:
            continue
        for s_ in states:
            t = _t(s_)
            if any(w in t for w in g) and is_light_like(s_["entity_id"], t) and not _altra_stanza(t, lp, parole[0]):
                out.append(s_["entity_id"])
    out.sort(key=lambda e: not e.startswith("light."))
    return out


def ripara(y, states, prompt, notify_ok=()):
    """Corregge errori tipici del modello senza richiamarlo. Ritorna (yaml, avvisi)."""
    try:
        cfg = yaml.safe_load(y)
    except Exception:
        return y, []
    if not isinstance(cfg, dict):
        return y, []
    lp = prompt.lower()
    avv, mod = [], [False]
    noti = [n for n in notify_ok if n.startswith("notify.")]
    mobili = [n for n in noti if n.startswith("notify.mobile_app_")]

    def passo(a):
        s = str(a.get("action", "")) if isinstance(a, dict) else ""
        if "." not in s:
            return a
        dom, srv = s.split(".", 1)
        # notify con alias anonimo o servizio che non esiste in casa
        if dom == "notify" and noti and s not in noti:
            nuovo = mobili[0] if len(mobili) == 1 and re.search(r"telefon|cellular|smartphone|avvisami|notific", lp) else ("notify.notify" if "notify.notify" in noti else (mobili or noti)[0])
            avv.append("servizio di notifica %s non esiste in casa: messo %s, controlla" % (s, nuovo))
            a["action"] = nuovo; mod[0] = True
            return a
        if dom == "notify" and re.match(r"e\d+$", srv):
            avv.append("servizio di notifica %s non risolto: messo notify.notify, scegli quello del tuo telefono" % s)
            a["action"] = "notify.notify"; mod[0] = True
            return a
        ok = SERVIZI_DOMINIO.get(dom)
        if ok is None or srv in ok:
            return a
        data = a.get("data") or {}
        tg = (a.get("target") or {}).get("entity_id")
        tgl = tg if isinstance(tg, list) else [tg] if tg else []
        if any(k in data for k in _LUCE_DATA):
            luci = [l for l in (_luce_simile(e, states) for e in tgl if isinstance(e, str)) if l]
            if luci:
                avv.append("servizio %s non esiste: uso light.turn_on su %s" % (s, ", ".join(luci)))
                a["action"] = "light.turn_on"; a["target"] = {"entity_id": luci if len(luci) > 1 else luci[0]}; mod[0] = True
                return a
        avv.append("servizio inesistente tolto: %s" % s); mod[0] = True
        return None
    if isinstance(cfg.get("actions"), list):
        cfg["actions"] = _passi(cfg["actions"], passo)
        if not cfg["actions"]:
            avv.append("nessuna azione valida rimasta: riformula la richiesta")
    # pioggia: il trigger deve essere il meteo
    meteo = [s_["entity_id"] for s_ in states if s_["entity_id"].startswith("weather.")]
    trig = cfg.get("triggers") if isinstance(cfg.get("triggers"), list) else []
    if meteo and re.search(r"\b(se|quando|appena)\s+(inizia a\s+)?piov|\bse c'?\s?e pioggia|\bquando c'?\s?e pioggia", lp) \
            and not any("weather." in str(t) for t in trig) and not re.search(r"\balle\s+\d|tramonto|alba|\b(esco|torno|arriv)", lp):
        cfg["triggers"] = [{"trigger": "state", "entity_id": meteo[0], "to": "rainy"}]
        avv.append("trigger messo sul meteo (%s diventa pioggia)" % meteo[0]); mod[0] = True
    # chiede una luce ma il modello ha usato la TV
    if re.search(r"\bluc[ei]\b|\blampad", lp) and not re.search(r"\btv\b|televis", lp):
        luci = _luci_stanza(states, lp)
        if luci:
            def via_tv(x):
                return isinstance(x, str) and re.search(r"(^|[._])tv([._]|$)|televis", x)
            for t in trig:
                if isinstance(t, dict) and via_tv(t.get("entity_id")):
                    avv.append("il trigger usava %s ma chiedi una luce: messo %s" % (t["entity_id"], luci[0])); t["entity_id"] = luci[0]; mod[0] = True
            for a in cfg.get("actions") or []:
                if isinstance(a, dict) and isinstance(a.get("target"), dict) and via_tv(a["target"].get("entity_id")):
                    e = luci[0]
                    a["action"] = _dom(e) + "." + str(a.get("action", ".turn_off")).split(".", 1)[1]
                    a["target"] = {"entity_id": e}; mod[0] = True
                    avv.append("l'azione usava la TV ma chiedi una luce: messo %s" % e)
    # "le luci del salotto" (plurale): tutte le luci della stanza, non una sola
    stanze_citate = sum(1 for g in ROOMS + [{"ingresso"}, {"corridoio"}] if any(re.search(r"\b%s\b" % w, lp) for w in g))
    mt = re.search(r"\b(?:tranne|eccetto)\s+(.+?)(?:\s+e\s+se\b|[,.]|$)", lp)
    if re.search(r"\b(le|tutte le) luci (del|della|dello|dell'|in)\s*\w+", lp) and not re.search(r"tutte le luci\b(?! (del|della|dell))", lp) \
            and stanze_citate == 1 and (mt or not re.search(r"tranne|eccetto", lp)) and not re.search(r"\be (i|il|la|le|lo|gli|l')\s*\w+", lp.replace(mt.group(0), "") if mt else lp):
        luci = _luci_stanza(states, lp)
        if mt:
            esc = [w for w in re.findall(r"[a-z\u00e0-\u00ff]{4,}", mt.group(1)) if w not in GENERIC and w not in STOP and w not in STOPIT]
            fuori = [e for e in luci if any(w in _t(next(s_ for s_ in states if s_["entity_id"] == e)) for w in esc)] if esc else []
            if esc and not fuori:
                avv.append("\"tranne %s\": non ho trovato nessuna luce con quel nome, nessuna esclusa" % " ".join(esc))
            luci = [e for e in luci if e not in fuori]
        if len(luci) > 1:
            for a in cfg.get("actions") or []:
                if not isinstance(a, dict) or not re.match(r"(light|switch)\.turn_(on|off)$", str(a.get("action", ""))):
                    continue
                tg = (a.get("target") or {}).get("entity_id")
                tgl = tg if isinstance(tg, list) else [tg] if tg else []
                if tgl and set(tgl) < set(luci):
                    verbo = a["action"].split(".")[1]
                    sw = [e for e in luci if e.startswith("switch.")]
                    li = [e for e in luci if e.startswith("light.")]
                    nuove = []
                    if li:
                        nuove.append({"action": "light." + verbo, "target": {"entity_id": li if len(li) > 1 else li[0]}})
                    if sw:
                        nuove.append({"action": "switch." + verbo, "target": {"entity_id": sw if len(sw) > 1 else sw[0]}})
                    i = cfg["actions"].index(a)
                    cfg["actions"][i:i + 1] = nuove
                    avv.append("\"le luci\" della stanza: messe tutte (%d)" % len(luci)); mod[0] = True
                    break
    if mod[0]:
        y = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False).rstrip("\n")
    return y, avv


# ---------- 0.3.29: ibrido (YAML del modello -> piano -> controlli -> YAML compilato) ----------
_PAROLA = re.compile(r"[a-z0-9à-ÿ]+")
_VUOTE = {"the", "di", "del", "della", "dello", "dei", "delle", "la", "il", "lo", "le", "e", "a", "in", "on", "off", "sensor", "binary", "switch", "light", "cover", "fan", "lock"}
_MISURA = {"temperatura": ("temp", "thermo", "termo"), "umidita": ("humid", "hygro", "umid", "moisture"), "luminosita": ("lux", "illum", "light_level"),
           "potenza": ("power", "potenza", "watt"), "energia": ("energy", "energia")}


def _tok(s):
    return {w for w in _PAROLA.findall(str(s).lower().replace("_", " ")) if w not in _VUOTE and len(w) > 1}


def _simile(eid, states, dominio=None, contesto="", escludi=()):
    """Entita esistente piu simile a un id inventato (stesso dominio se possibile)."""
    dom = dominio or _dom(eid)
    base = _tok(eid.split(".", 1)[-1])
    best, bs = None, 0.0
    for s_ in states:
        e = s_["entity_id"]
        if _dom(e) != dom or e in escludi:
            continue
        t = _tok(_t(s_))
        if not t:
            continue
        sc = len(base & t) * 2 + len(_tok(contesto) & t) * 0.5
        if base and base <= t:
            sc += 3
        if sc > bs:
            best, bs = e, sc
    return best if bs >= 2 else None


def _sensore_per(prompt, states, attuale=""):
    lp = prompt.lower()
    tipo = next((k for k, v in _MISURA.items() if re.search(k[:5] + r"|" + "|".join(v), lp)), None)
    if not tipo and re.search(r"grad|zero|cald|fredd|°", lp):
        tipo = "temperatura"
    if not tipo:
        return None
    radici = _MISURA[tipo] + (tipo[:5],)
    cands = [s_ for s_ in states if _dom(s_["entity_id"]) == "sensor" and any(r in _t(s_) for r in radici)]
    if not cands:
        return None
    pt = _tok(prompt)
    if re.search(r"\bfuori\b|esterno|outdoor|outside", lp):
        pt |= {"outdoor", "outside", "esterno", "esterna", "fuori", "exterior"}
    cands.sort(key=lambda s_: -len(pt & _tok(_t(s_))))
    return cands[0]["entity_id"]


def _per_nome(prompt, states, escludi=(), domini=None):
    """Entita il cui nome compare nella frase (la piu lunga), esclusa quella del trigger."""
    lp = " " + re.sub(r"[^a-z0-9à-ÿ ]", " ", prompt.lower()) + " "
    best, bl = None, 0
    for s_ in states:
        e = s_["entity_id"]
        if e in escludi or (domini and _dom(e) not in domini):
            continue
        n = re.sub(r"[^a-z0-9à-ÿ ]", " ", ((s_.get("attributes") or {}).get("friendly_name") or "").lower()).strip()
        if len(n) > 3 and (" %s " % n) in lp and len(n) > bl:
            best, bl = e, len(n)
    return best


_VERBO = [(r"\b(spegn|turn off|switch off|stop)", "turn_off"), (r"\b(accend|turn on|switch on|attiv|avvia|fai partire)", "turn_on"),
          (r"\b(apri|open)", "open"), (r"\b(chiudi|close)", "close")]


def ibrido(y, states, prompt):
    """Converte lo YAML del modello in piano, corregge entita/trigger/azioni e ricompila. Ritorna (yaml, avvisi)."""
    import piano as P
    try:
        p = P.da_yaml(y)
    except Exception:
        return y, []
    if not p or not p.get("a"):
        return y, []
    ids = {s_["entity_id"] for s_ in states}
    lp = prompt.lower()
    avv = []

    def ripara_id(e, dom=None, ctx=""):
        if not isinstance(e, str) or e in ids or e.startswith(("zone.", "sun.")):
            return e
        oid = e.split(".", 1)[-1]
        altro = next((x for x in ids if x.split(".", 1)[-1] == oid), None)   # stesso nome, dominio diverso
        nuovo = _simile(e, states, dom, ctx) or altro or (_per_nome(prompt, states, domini={_dom(e)}) if re.fullmatch(r"e\d+", oid) else None)
        if nuovo:
            avv.append("%s non esiste: usato %s" % (e, nuovo))
            return nuovo
        return e
    trig_ids = set()
    for t in p.get("t") or []:
        for k in ("stato", "soglia"):
            if k in t:
                v = t[k] if isinstance(t[k], list) else [t[k]]
                v = [ripara_id(e, ctx=prompt) for e in v]
                if k == "soglia" and not t.get("attr"):
                    v2 = []
                    for e in v:
                        sbagliato_tipo = False
                        if _dom(e) == "sensor":
                            st_ = next((x for x in states if x["entity_id"] == e), None)
                            tt = _t(st_) if st_ else e
                            chiede = next((k for k, v in _MISURA.items() if re.search(k[:5] + r"|" + "|".join(v), lp)), None)
                            ha = next((k for k, v in _MISURA.items() if any(r in tt for r in v + (k[:5],))), None)
                            sbagliato_tipo = bool(chiede and ha and chiede != ha)
                        if sbagliato_tipo or _dom(e) not in ("sensor", "input_number", "number", "zone") or (_dom(e) == "zone" and re.search(r"grad|zero|temperat|umid|cald|fredd", lp)):
                            s = _sensore_per(prompt, states, e)
                            if s:
                                avv.append("la soglia era su %s: messa sul sensore %s" % (e, s)); e = s
                        v2.append(e)
                    v = v2
                t[k] = v[0] if len(v) == 1 else v
                trig_ids |= set(v)
    for c in p.get("c") or []:
        for k in ("stato", "soglia"):
            if k in c and isinstance(c[k], str):
                c[k] = ripara_id(c[k], ctx=prompt)

    def az(lst):
        out = []
        for a in lst:
            if "scegli" in a:
                for r in a["scegli"]:
                    r["fai"] = az(r.get("fai") or [])
                if a.get("altrimenti"):
                    a["altrimenti"] = az(a["altrimenti"])
            if "fai" in a and a.get("su") and isinstance(a["su"], list):
                srv = a["fai"].split(".", 1)
                su = []
                for e in a["su"]:
                    e2 = ripara_id(e, dom=srv[0] if srv[0] not in ("homeassistant",) else None, ctx=prompt)
                    if e2 != e and _dom(e2) != srv[0] and srv[0] in ("light", "switch", "fan", "cover"):
                        a["fai"] = _dom(e2) + "." + srv[1]
                    su.append(e2)
                # azione sulla stessa entita del trigger (es. "quando la porta si sblocca, accendi la luce" -> sblocca la porta)
                if any(e in trig_ids for e in su) and not re.search(r"\b(spegnila|spegnilo|chiudila|chiudilo|riaccend|di nuovo)\b", lp):
                    nuovo = _per_nome(prompt, states, escludi=trig_ids, domini={"light", "switch", "fan", "cover", "media_player", "climate", "valve"})
                    verbo = next((v for r, v in _VERBO if re.search(r, lp)), None)
                    if nuovo and verbo:
                        d = _dom(nuovo)
                        servizio = {"open": "open_cover" if d == "cover" else "open_valve" if d == "valve" else "turn_on",
                                    "close": "close_cover" if d == "cover" else "close_valve" if d == "valve" else "turn_off"}.get(verbo, verbo)
                        avv.append("l'azione ripeteva il trigger (%s): messa %s.%s su %s" % (", ".join(su), d, servizio, nuovo))
                        a["fai"], su = d + "." + servizio, [nuovo]
                    elif _dom(su[0]) in ("lock", "alarm_control_panel"):
                        avv.append("azione su %s tolta: ripeteva il trigger" % su[0])
                        continue
                a["su"] = su
            out.append(a)
        return out
    p["a"] = az(p["a"])
    if not avv:
        return y, []
    try:
        return P.compila(p, ids), avv
    except Exception as ex:
        return y, ["controllo del piano non riuscito: %s" % str(ex)[:80]]


# ================= 0.3.41: blocco di sicurezza =================
_VUOTE = {"casa", "quando", "appena", "accendi", "spegni", "apri", "chiudi", "avvia", "ferma", "partire", "lascia", "accesa", "acceso", "spenta", "spento", "della", "delle", "degli", "dello", "nella", "nelle", "alle", "sono", "siamo", "fuori", "nessuno", "qualcuno", "notte", "giorno", "sera", "mattina", "tutte", "tutti", "dopo", "prima", "ancora", "invece", "altrimenti", "solo", "come", "avvisami"}
_CATEGORIE = ((r"luc[ei]\b|lampad|farett|illumin|\bled\b", ("light", "switch")), (r"\btend|tapparell|serrand|garage|cancell", ("cover",)),
              (r"\btv\b|\btele\b|televis|musica|radio|volume|cassa|speaker", ("media_player",)), (r"robot|aspirapolvere", ("vacuum",)),
              (r"ventil", ("fan",)), (r"termostat|clima|condizionat|riscaldament|gradi", ("climate",)), (r"serratur|blocca", ("lock",)), (r"annunci|\bdi'", ("assist_satellite",)))


def _azioni_tutte(n):
    if isinstance(n, dict):
        if "action" in n:
            yield n
        for v in n.values():
            yield from _azioni_tutte(v)
    elif isinstance(n, list):
        for v in n:
            yield from _azioni_tutte(v)


def blocco(y, prompt, states, scelte=()):
    """Motivi per NON consegnare lo YAML: comandi o entita inventati, oppure bersagli che non c'entrano con la richiesta."""
    try:
        docs = [d for d in yaml.safe_load_all(y) if isinstance(d, dict)]
    except Exception:
        return ["lo YAML non e leggibile"]
    note = {s["entity_id"]: s for s in states}
    p = " ".join(prompt.lower().replace("’", "'").split())
    parole = {w[:-1] for w in re.findall(r"[a-z]{4,}", p) if w not in _VUOTE}
    cat = {d for rx, ds in _CATEGORIE if re.search(rx, p) for d in ds}
    motivi = []
    for a in _azioni_tutte(docs):
        dom, _, nome = str(a["action"]).partition(".")
        if dom not in SERVICES or not nome or (SERVICES[dom] is not None and nome not in SERVICES[dom]):
            motivi.append("comando inesistente: %s" % a["action"])
        for e in _target_ents(a):
            if "{{" in str(e) or dom == "notify":
                continue
            if e not in note:
                motivi.append("entita inesistente: %s" % e)
                continue
            if e in scelte or re.search(r"\btutt[oaie]\b", p) or _dom(e) in cat - {"switch", "light"}:
                continue
            testo = _t(note[e]).lower()
            if any(w in testo for w in parole):
                continue
            if _dom(e) in cat and (_dom(e) != "switch" or is_light_like(e, testo)):
                continue
            motivi.append("\"%s\" non c'entra con la richiesta" % ((note[e].get("attributes") or {}).get("friendly_name") or e))
    return list(dict.fromkeys(motivi))


def coerente(y, prompt):
    """La proposta dell'AI deve parlare di quello che hai chiesto (e non aggiungere eventi mai detti)."""
    p = " ".join(prompt.lower().replace("’", "'").split())
    t = y[y.find("triggers:"):]
    for rx, ok in ((r"robot|aspirapolvere", r"vacuum\."), (r"\btv\b|televis", r"media_player\."), (r"\btend|tapparell|serrand", r"cover\."),
                   (r"\bluc[ei]\b|farett|lampad", r"(?:light|switch)\."), (r"avvis|notific", r"notify"),
                   (r"\d+ ?(?:minut|second|or[ae])|un'ora|mezz'ora", r"\bfor:|delay|offset|time_pattern"), (r"cancell", r"cancell"),
                   (r"piov|pioggia|vento|meteo", r"weather\.|rain|pioggia"), (r"tramonto|\balba\b|\bbuio\b|\bnotte\b", r"sun|after:|before:|at:")):
        if re.search(rx, p) and not re.search(ok, t):
            return False
    if "zone.home" in t and not re.search(r"nessuno|qualcuno|tutti|casa|\besco|escono|torn|rientr|arriv|via\b|vacanza|presenza", p):
        return False
    if re.search(r"robot|aspirapolvere", p) and re.search(r"finit|fermat|bloccat", p) and not re.search(r"trigger: state\n\s*entity_id: vacuum", t):
        return False
    return True
