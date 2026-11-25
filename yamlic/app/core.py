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
        if l in GENERIC or len(l) < 3 or l.isdigit() or l in self.tok:
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
        for w in self.tok:
            if w in {x.lower() for x in TOK.findall(text)}:
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
           " tend": "cover", "movimento": "binary_sensor", "porta": "binary_sensor", "finestra": "binary_sensor"}

LIGHT_LIKE = re.compile(r"luc|led|farett|barra|lampad|striscia|plafon|abat|veletta|applique")
FIRST = re.compile(r"\b(io|mio|mia|miei|mie|me)\b")
# entita che non sono luci vere: opzioni, indicatori, schermi
NOT_LIGHT = re.compile(r"update|enabled|status.?ring|indicator|night.?mode|display|schermo|screen|backlight|reset|restart|identify|mute|sound|amplifier|internet|debug|kiosk|manutenzione|disturbare")
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
        if _dom(eid) == "person":
            obj = eid.split(".", 1)[1].lower().replace("_", " ")
            named = any(t in words for t in re.findall(r"\w{3,}", (obj + " " + name.lower()))) 
            if named or (io_person == eid and FIRST.search(low)):
                persons.append((eid, name))
            continue
        light_like = "light" in wanted and _dom(eid) == "switch" and bool(LIGHT_LIKE.search(text)) and not NOT_LIGHT.search(text)
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
    return (s["entity_id"] + " " + (a.get("friendly_name") or "") + " " + (a.get("_area") or "")).lower().replace("_", " ")

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

TRIGGER_CUE = re.compile(r"quando|\bse\b|ogni|\balle\b|dalle|dopo|prima|appena|tramonto|alba|\bsera\b|mattina|notte|minut|\bore\b|esco|esce|rientr|arriv|uscit|movimento|\bapr|chiud|premo|premuto|pulsante|temperatura|umidit|piove|pioggia|\bsole\b")

def force_targets(y, scelte, prompt=""):
    """Dopo una domanda di chiarimento: nelle azioni luce/interruttore mette esattamente le entita scelte
    (luci e interruttori separati), senza fidarsi dell'elenco scritto dal modello. Poi pulisce alias e trigger."""
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
    for t in cfg.get("triggers") or []:
        if isinstance(t, dict) and isinstance(t.get("at"), str):
            m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", t["at"].strip())
            if m:
                t["at"] = "%02d:%s:%s" % (int(m.group(1)), m.group(2), m.group(3) or "00")
    if prompt and not TRIGGER_CUE.search(prompt.lower()):
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
SERVICES = {
    "light": {"turn_on", "turn_off", "toggle"}, "switch": {"turn_on", "turn_off", "toggle"},
    "cover": {"open_cover", "close_cover", "stop_cover", "set_cover_position", "toggle"},
    "climate": {"set_hvac_mode", "set_temperature", "turn_on", "turn_off"},
    "lock": {"lock", "unlock"}, "fan": {"turn_on", "turn_off", "toggle"},
    "media_player": {"turn_on", "turn_off", "media_pause", "media_play"},
    "alarm_control_panel": {"alarm_arm_away", "alarm_arm_home", "alarm_disarm"},
    "input_boolean": {"turn_on", "turn_off", "toggle"}, "scene": {"turn_on"}, "script": {"turn_on"}, "notify": None,
}
SENSITIVE = {"lock", "alarm_control_panel"}
SENSITIVE_WORDS = ("serratur", "allarm", "blocca", "sblocca", "lock", "disinser", "inser", "arma", "chiud", "apri")
STATE_TO = {"person": {"home", "not_home"}, "binary_sensor": {"on", "off"}, "light": {"on", "off"},
            "switch": {"on", "off"}, "input_boolean": {"on", "off"}, "lock": {"locked", "unlocked"},
            "cover": {"open", "closed"}, "media_player": {"on", "off", "playing", "paused", "idle"}}
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
                if to is not None and dom in STATE_TO and str(to) not in STATE_TO[dom]:
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
                    if e not in known:
                        p.append("entita inesistente: %s" % e)
                    elif dom in SERVICES and dom not in {"notify", "scene", "script"} and _dom(e) != dom:
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
