"""Deterministic, rule-based extractor: paper main text -> contract v0.7 methods record.

No LLM, no network, no paid API. Records only what the main text says: never fills gaps,
never uses product labels/monographs or cited papers, never computes tiers (that is
methods_completeness.compute, owned by Methods). Every vocabulary value comes from
schemas/normalization_rules.json; every enum from schemas/methods.schema.json.
"""
import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_PATH = pathlib.Path(__file__).with_name("extraction_config.json")
SCHEMA_PATH = ROOT / "schemas/methods.schema.json"
NORM_PATH = ROOT / "schemas/normalization_rules.json"
RULES_PATH = ROOT / "schemas/completeness_rules.json"

EXTRACTOR_ID = "crn_rule_extractor"
SCHEMA_VERSION = "wp1.8.0-draft"
CONTRACT_VERSION = "0.8"
# Paper-level failures -> every assessed item is could_not_check with this contract reason.
PAPER_FAILURES = ("fetch_failed", "parse_failed", "source_not_available", "budget_cap")
_F = re.I


def load_config(path=CONFIG_PATH):
    return json.loads(pathlib.Path(path).read_text())


def config_hash(cfg=None):
    raw = json.dumps(cfg or load_config(), sort_keys=True).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:16]


def _vocab():
    v = json.loads(NORM_PATH.read_text())["vocabularies"]
    return {
        "route": {x["value"] for x in v["route"]["values"]},
        "form": {x["value"] for x in v["product_form"]["values"]},
        "cannabinoid": [(x["value"], x["synonyms"] + [x["full_name"]]) for x in v["cannabinoid"]["values"]],
        "duration_category": [(x["value"], x["synonyms"]) for x in v["duration_category"]["values"]],
    }


def _enum(name):
    return json.loads(SCHEMA_PATH.read_text())["$defs"][name]["enum"]


def _nrr_enum():
    fr = json.loads(SCHEMA_PATH.read_text())["$defs"]["field_reporting"]["properties"]
    return fr["not_reported_reason"]["anyOf"][0]["enum"]


def _sentences(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    parts = re.split(r"(?<=[.;!?])\s+(?=[A-Z(\[])", text)
    return [p for p in parts if p]


class Paper:
    """Main text only. Sections of type 'supplement' are never read."""

    def __init__(self, paper, cfg):
        self.cfg = cfg
        prio = cfg["section_priority"]
        main = set(cfg["main_text_sections"])
        secs = [s for s in paper.get("sections") or [] if s.get("type") in main]
        secs.sort(key=lambda s: prio.index(s["type"]) if s["type"] in prio else len(prio))
        self.sents = [(s["type"], t) for s in secs for t in _sentences(s.get("text"))]

    def first(self, pattern, sections=None):
        rx = re.compile(pattern, _F)
        for sec, s in self.sents:
            if sections and sec not in sections:
                continue
            m = rx.search(s)
            if m:
                return sec, s, m
        return None

    def all(self, pattern):
        rx = re.compile(pattern, _F)
        return [(sec, s, m) for sec, s in self.sents for m in rx.finditer(s)]

    def cue_with(self, cue, other):
        """First sentence matching both an item cue and another pattern."""
        a, b = re.compile(cue, _F), re.compile(other, _F)
        for sec, s in self.sents:
            if a.search(s) and b.search(s):
                return sec, s
        return None

    def loc(self, sec):
        return self.cfg["section_location"].get(sec, "unknown")


def _reported(p, hit, **extra):
    sec, s = hit[0], hit[1]
    r = {"status": "reported", "location": p.loc(sec), "evidence_quote": s[:500], "value_source": "paper"}
    r.update(extra)
    return r


def _missing(p, key, special=None, quote=None, **extra):
    """Not found in main text. Order: item-specific not_reported_reason, supplement reference
    (could_not_check source_not_fetched), figure reference (could_not_check figure_not_read),
    'as previously described' (cited_elsewhere), else absent."""
    cfg = p.cfg
    if special:
        r = {"status": "not_reported", "not_reported_reason": special, "location": None,
             "evidence_quote": (quote or None) and quote[:500], "value_source": "paper"}
        r.update(extra)
        return r
    cue = cfg["item_cues"].get(key)
    if cue:
        hit = p.cue_with(cue, cfg["supplement_ref"])
        if hit:
            return dict({"status": "could_not_check", "check_failure_reason": "source_not_fetched",
                         "location": None, "evidence_quote": hit[1][:500]}, **extra)
        hit = p.cue_with(cue, cfg["figure_ref"])
        if hit and hit[0] != "figure_caption":
            return dict({"status": "could_not_check", "check_failure_reason": "figure_not_read",
                         "location": None, "evidence_quote": hit[1][:500]}, **extra)
        hit = p.cue_with(cue, cfg["cited_elsewhere"])
        if hit:
            return dict({"status": "not_reported", "not_reported_reason": "cited_elsewhere", "location": None,
                         "evidence_quote": hit[1][:500], "value_source": "paper"}, **extra)
    return dict({"status": "not_reported", "not_reported_reason": "absent", "location": None,
                 "evidence_quote": None, "value_source": "paper"}, **extra)


def _sf(value, span):
    return {"value": value, "missingness": "reported", "source_span": span[:500], "pending_biomedical": True}


def classify(p, cfg):
    """Return (excluded_reason_or_None, research_type_or_None, evidence)."""
    for kind, pat in cfg["excluded_study_designs"].items():
        if kind == "note":
            continue
        hit = p.first(pat, sections=("abstract", "methods", "body"))
        if hit:
            return "not_original_research_" + kind, None, hit
    scores = {}
    for rt, pat in cfg["research_type"].items():
        hits = p.all(pat)
        if hits:
            scores[rt] = (len(hits), hits[0])
    if not scores:
        return None, None, None
    # clinical beats in_vivo when humans are the subjects; in_vitro only if no animals/humans dominate
    rt = max(scores, key=lambda k: scores[k][0])
    return None, rt, scores[rt][1]


def _study_type_for_rules(rt, randomized):
    return {"in_vivo": "in_vivo", "in_vitro": "in_vitro", "clinical": "clinical"}.get(rt)


def _extract_dose(p, cfg, key, rt, fields):
    """C1: a range counts as reported. TOP3: number without unit -> missing_units.
    'per protocol' with no number -> protocol_without_number."""
    vrx = cfg["dose"]["value"]
    units = ("µM", "μM", "uM", "mg/ml", "mg/mL") if key == "concentration" else None
    for sec, s, m in p.all(vrx):
        unit = m.group(3)
        if units and unit not in units:
            continue
        if key == "dose" and rt == "in_vitro":
            continue
        if sec == "table":
            # T2: a flattened table row we cannot attribute to one arm/unit is not a confident read
            table_units = {u.group(3).lower() for u in re.finditer(vrx, s, _F)}
            if len(table_units) > 1:
                return {"status": "could_not_check", "check_failure_reason": "low_confidence_read",
                        "location": None, "evidence_quote": s[:500]}
        _fill_dose_fields(fields, s, m)
        return _reported(p, (sec, s))
    hit = p.cue_with(cfg["item_cues"]["dose"] if key == "dose" else cfg["item_cues"]["concentration"],
                     cfg["dose"]["unitless"])
    if hit:
        return _missing(p, key, "missing_units", hit[1])
    hit = p.first(cfg["protocol_without_number"])
    if hit:
        return _missing(p, key, "protocol_without_number", hit[1])
    return _missing(p, key)


def _fill_dose_fields(fields, s, m):
    lo, hi, unit = m.group(1), m.group(2), m.group(3)
    u = unit.replace("μ", "µ").replace("uM", "µM").lower()
    sl = s.lower()
    canna = "thc" if "thc" in sl and "cbd" not in sl else "cbd" if "cbd" in sl and "thc" not in sl else None
    name = {"mg/kg": "dose_{c}_mg_kg", "mg/g": "dose_{c}_mg_g", "mg/ml": "conc_{c}_mg_ml", "µm": "conc_{c}_uM"}.get(u)
    if u == "mg":
        fname = "dose_mg"
    elif name and canna:
        fname = name.format(c=canna)
    else:
        return
    fields.setdefault(fname, {"value": None if hi else float(lo), "unit_as_reported": unit,
                              "missingness": "reported", "source_span": m.group(0) if not hi else s[:500],
                              "pending_biomedical": True})


def _duration(p, cfg, key, vocab, fields):
    pat = cfg["duration"]["exposure"] if key == "exposure_time" else cfg["duration"]["value"]
    hit = p.first(pat)
    cat = None
    for value, syns in vocab["duration_category"]:
        for syn in syns:
            h = p.first(r"\b" + re.escape(syn) + r"(?:ly)?\s+(?:\w+\s+)?(?:treatment|administration|exposure|dosing|regimen|injections?|use)\b|\b(?:treated|administered|exposed|dosed)\s+" + re.escape(syn) + r"ly\b",
                        sections=("methods", "abstract", "body", "results"))
            if h:
                cat = (value, re.search(re.escape(syn), h[2].group(0), _F).group(0), h)
                break
        if cat:
            break
    extra = {}
    if cat and key == "duration":
        extra = {"duration_category": cat[0], "duration_category_verbatim": cat[1]}
    if hit:
        m = hit[2]
        fields.setdefault("treatment_duration", _sf(m.group(0), hit[1]))
        if key == "duration" and m.group(2).lower().startswith("day") or (key == "duration" and "consecutive" in m.group(2).lower()):
            fields.setdefault("duration_days", {"value": float(m.group(1)), "unit_as_reported": m.group(2),
                                                "missingness": "reported", "source_span": m.group(0), "pending_biomedical": True})
        return _reported(p, hit, **extra)
    if cat and key == "duration":  # C2: vague term only
        return _missing(p, key, "vague_term_only", cat[2][1], **extra)
    return _missing(p, key, **extra)


def _simple(p, cfg, key, pattern, field=None, fields=None, group=0, sections=None):
    hit = p.first(pattern, sections=sections)
    if not hit:
        return _missing(p, key)
    if field and fields is not None:
        val = hit[2].group(group) if hit[2].group(group) else hit[2].group(0)
        fields.setdefault(field, _sf(val.strip(), hit[1]))
    return _reported(p, hit)


_HYPHENS = "\\-\u2010\u2011\u2012\u2013\u2212"


def _cannabinoid_patterns(vocab):
    """One regex per locked vocabulary value (synonyms + full_name from normalization_rules.json).
    PDF/XML text often uses Unicode hyphens (U+2010/2011/2013/2212) and a space after the delta
    sign, so a hyphen in a synonym matches any of those and 'Δ9' also matches 'Δ 9'."""
    out = []
    for value, syns in vocab["cannabinoid"]:
        alts = []
        for syn in sorted(set(syns), key=len, reverse=True):
            e = re.escape(syn).replace("\\-", "[" + _HYPHENS + "]")
            e = re.sub(r"(Δ|delta|D)(\d)", r"\1\\s?\2", e)
            alts.append(e)
        rx = re.compile(r"(?<![\w" + _HYPHENS + r"])(" + "|".join(alts) + r")(?![\w" + _HYPHENS + r"])", _F)
        out.append((value, rx))
    return out


_UNIT = r"(mg/kg|mg/g|mg/ml|mg/mL|µM|μM|uM|nM|mg|%)"
_AMT_WINDOW = 40  # chars looked at on each side of a mention (keeps matching linear in sentence length)
_MAX_SENT = 5000  # longer 'sentences' (tables / broken PDF text) are only read up to this length
_AFTER_RX = re.compile(r"\s*[(,]?\s*(?:at\s+)?(\d+(?:\.\d+)?)\s*" + _UNIT + r"(?![\w/])(?!\s*(?:-|–|to)\s*\d)")
_BEFORE_RX = re.compile(r"(?<![\d.\-–])(\d+(?:\.\d+)?)\s*" + _UNIT + r"\s*(?:of\s+)?$")
# A name written directly before an amount owns it ('Morphine 10 mg/kg THC' -> morphine's dose).
_DRUGLIKE = re.compile(r"(?:[A-Z][\w\-]*|[a-z][\w\-]*(?:ine|ol|am|ide|one|ate|azole|pine|done|fen|nil|cin))", 0)
_PREV_WORD = re.compile(r"([^\s(),;:]+)[\s(,]*$")


def _adjacent_amount(s, m, cfg, cann_rx=()):
    """(amount, unit) written directly after ('CBD (10 mg/kg)', 'THC, 5 mg') or before
    ('5.9% THC', '10 mg/kg CBD') a cannabinoid mention; ranges are not collapsed to one number.
    An amount before the mention is used only when no drug/compound name directly precedes it
    (that name claims it). Only a small window around the mention is searched."""
    after = _AFTER_RX.match(s, m.end(), min(len(s), m.end() + _AMT_WINDOW))
    if after:
        return float(after.group(1)), after.group(2)
    lo = max(0, m.start() - _AMT_WINDOW)
    win = s[lo:m.start()]
    before = _BEFORE_RX.search(win)
    if before:
        prev = _PREV_WORD.search(s[max(0, lo + before.start() - _AMT_WINDOW):lo + before.start()])
        if prev:
            w = prev.group(1)
            first_word = s[:lo + before.start()].strip() == w
            if any(rx.fullmatch(w) for _, rx in cann_rx) or (_DRUGLIKE.fullmatch(w) and not
                    (first_word and not re.search(r"(?:ine|ol|am|ide|one|ate|azole|pine|done|fen|nil|cin)$", w, re.I))):
                return None
        return float(before.group(1)), before.group(2)
    return None


_VERB = r"(?:obtained|purchased|provided|supplied|donated|procured|sourced)"
_FROM_RX = re.compile(r"\b(?:" + _VERB + r"\s+(?:by|from)|from)\s+(?:the\s+)?([^,.;()]{1,120}?)(?=\s+(?:and|while|whereas)\s|[,.;()]|$)", re.I)
_VERB_RX = re.compile(r"\b" + _VERB + r"\s+(?:by|from)\s", re.I)
_CONJ_RX = re.compile(r"^\s*(?:and|while|whereas)\s", re.I)
_SEG_MAX = 200


def _supplier(p, cfg, cann_rx):
    """Supplier of the test material. Each 'from X' / 'obtained by X' clause is linked to the
    compounds written in the stretch of text just before it (back to the previous clause, at most
    _SEG_MAX chars), so 'CBD and THC were purchased from A and CBG from B' links CBD and THC to A
    and CBG to B. A bare 'from' counts only as a continuation ('and CBG from B'). Clauses about
    animals, colonies or animal vendors never count. A sentence with a cannabinoid-specific clause
    is preferred over an earlier generic one ('All chemicals ... from Sigma').
    Returns (section, sentence, [(compounds, supplier)])."""
    sc = cfg["supplier"]
    animal = re.compile(sc["animal_source"], _F)
    compound = re.compile(sc["test_compound"], _F)
    generic = None
    for sec, s in p.sents:
        s_ = s[:_MAX_SENT]
        links, pos, specific = [], 0, False
        for m in _FROM_RX.finditer(s_):
            seg = s_[max(pos, m.start() - _SEG_MAX):m.end()]
            pre = s_[max(pos, m.start() - _SEG_MAX):m.start()]
            pos = m.end()
            if not _VERB_RX.search(seg) and not (links and _CONJ_RX.search(pre)):
                continue
            name = m.group(1).strip()
            if not name or animal.search(pre) or animal.search(name):
                continue
            found = [mm.group(1) for _, rx in cann_rx for mm in [rx.search(pre)] if mm]
            found.sort(key=lambda c: pre.find(c))
            if found:
                specific = True
            else:
                g = compound.search(pre)
                if not g:
                    continue
                found = [g.group(1)]
            links.append((found, name))
        if links:
            if specific:
                return sec, s, links
            generic = generic or (sec, s, links)
    return generic


def _supplier_value(links):
    """One supplier -> its name. Several -> 'CBD: A; THC: A; CBG: B' (one pair per compound) (fields.supplier is a string_field)."""
    names = list(dict.fromkeys(n for _, n in links))
    if len(names) == 1:
        return names[0]
    return "; ".join(c + ": " + n for cs, n in links for c in cs)


def extract(paper, cfg=None):
    """paper: {paper_id:int, pmid:str, source_tier, sections:[{type,text}], mesh:[D-ids],
    status: ok|fetch_failed|parse_failed|source_not_available|budget_cap}.

    Returns {"record": dict|None, "excluded": reason|None, "study_type": str|None}.
    """
    cfg = cfg or load_config()
    vocab = _vocab()
    rules = json.loads(RULES_PATH.read_text())
    prov = {"schema_version": CONTRACT_VERSION, "extractor_id": f"{EXTRACTOR_ID}/{cfg['version']}",
            "model_id": None, "prompt_id": config_hash(cfg), "confidence": None,
            "source_tier": paper.get("source_tier") or "unknown", "source_span": None,
            "human_override": False, "reviewed_at": None}
    rec = {"schema_version": SCHEMA_VERSION, "paper_id": int(paper["paper_id"]),
           "record_id": paper.get("record_id") or f"rec-{EXTRACTOR_ID}-{paper['pmid']}",
           "provenance": prov, "honesty_label": "rule_based", "fields": {}, "reporting": {}}
    mesh = [d for d in paper.get("mesh") or [] if re.fullmatch(r"D[0-9]{6,9}", d)]
    if mesh:
        rec["mesh_from_pubmed"] = mesh  # cross-check only; never copied into reporting.conditions (A11)
    status = paper.get("status", "ok")
    universal = [i["key"] for i in rules["tiers"]["universal"]["items"]]
    if status in PAPER_FAILURES:
        for k in ["research_type"] + universal:
            rec["reporting"][k] = {"status": "could_not_check", "check_failure_reason": status, "location": None}
        return {"record": rec, "excluded": None, "study_type": None}

    p = Paper(paper, cfg)
    excl, rt, ev = classify(p, cfg)
    if excl:  # Q7: not original research -> no record
        return {"record": None, "excluded": excl, "study_type": None}
    F, R = rec["fields"], rec["reporting"]
    cann_rx = _cannabinoid_patterns(vocab)
    if rt:
        F["research_type"] = _sf(rt, ev[1])
        R["research_type"] = _reported(p, ev)
    else:
        R["research_type"] = _missing(p, "research_type")

    # universal
    prod = p.first(cfg["product_named"])
    strain = p.first(r"\b(?:strain|cultivar|chemovar)\s+(?:named\s+)?[\"'“]?([A-Z][\w'-]+(?:\s[A-Z][\w'-]+)?)")
    if prod or strain:
        hit = prod or strain
        # v0.7 fields.product needs normalized origin+preparation (pending vocab); we never infer
        # them, so a named product is kept only as the evidence quote (contract gap, see docs).
        if strain:
            F["strain_reported"] = _sf(strain[2].group(1), strain[1])
        R["strain_or_product_name"] = _reported(p, hit)
    else:
        R["strain_or_product_name"] = _missing(p, "strain_or_product_name")

    if rt == "clinical":
        R["supplier"] = {"status": "not_applicable"}
    else:
        hit = _supplier(p, cfg, cann_rx)
        if hit:
            sec, s, links = hit
            gov = re.compile(cfg["supplier"]["government_unnamed"], _F)
            named = [(c, n) for c, n in links if not gov.match(n)]
            if not named:  # P2
                F["supplier_program_verbatim"] = _sf(links[0][1], s)
                R["supplier"] = _missing(p, "supplier", "government_program_unnamed", s)
            else:
                F["supplier"] = _sf(_supplier_value(named), s)
                R["supplier"] = _reported(p, (sec, s))
        else:
            R["supplier"] = _missing(p, "supplier")

    forms = [(v, p.first(pat)) for v, pat in cfg["form"] if v in vocab["form"]]
    forms = [(v, h) for v, h in forms if h]
    if forms:
        F["form"] = {"value": [v for v, _ in forms], "missingness": "reported", "source_span": forms[0][1][1][:500], "pending_biomedical": True}
        R["form"] = _reported(p, forms[0][1])
    else:
        R["form"] = _missing(p, "form")

    cann = []
    first_hit = None
    for value, rx in cann_rx:
        hits = [(sec, s, m) for sec, s in p.sents for m in rx.finditer(s, 0, _MAX_SENT)]
        if hits:
            first_hit = first_hit or hits[0]
            entry = {"name": value, "source_span": hits[0][1][:300]}
            for sec, s, m in hits:  # amount + unit only when written right next to the name
                amt = _adjacent_amount(s, m, cfg, cann_rx)
                if amt:
                    entry.update(amount=amt[0], unit=amt[1], source_span=s[:300])
                    break
            cann.append(entry)
    if cann:
        F["cannabinoids"] = {"value": cann, "missingness": "reported", "source_span": first_hit[1][:500], "pending_biomedical": True}
        R["major_cannabinoids"] = _reported(p, first_hit)
    elif prod:  # product named, content not given; never filled from labels/monographs
        R["major_cannabinoids"] = _missing(p, "major_cannabinoids", "product_named_content_missing", prod[1])
    else:
        R["major_cannabinoids"] = _missing(p, "major_cannabinoids")

    routes = []
    rhit = None
    for v, pat in cfg["route"]:
        if v not in vocab["route"]:
            continue
        h = p.first(pat)
        if h:
            routes.append(v)
            rhit = rhit or h
    if routes:
        F["route"] = {"value": routes, "missingness": "reported", "source_span": rhit[1][:500], "pending_biomedical": True}
        R["route"] = _reported(p, rhit)
    else:
        R["route"] = _missing(p, "route")

    # study-type items
    if rt == "in_vivo" or rt == "clinical":
        R["dose"] = _extract_dose(p, cfg, "dose", rt, F)
        R["frequency"] = _simple(p, cfg, "frequency", cfg["frequency"], "administration_frequency", F)
        R["duration"] = _duration(p, cfg, "duration", vocab, F)
    if rt == "in_vivo":
        n = p.first(cfg["sample_size"]["animals"]) or p.first(cfg["sample_size"]["total_n"])
        if n:
            F["sample_size"] = {"value": int(n[2].group(1)), "missingness": "reported", "source_span": n[1][:500], "pending_biomedical": True}
            R["number_of_animals"] = _reported(p, n)
        else:
            R["number_of_animals"] = _missing(p, "number_of_animals")
        R["species"] = _simple(p, cfg, "species", cfg["species"], "species_or_biological_system", F)
        R["animal_strain"] = _simple(p, cfg, "animal_strain", cfg["animal_strain"])
        R["sex"] = _simple(p, cfg, "sex", cfg["sex"], "sgba_sex_reported", F)
        R["age_or_weight"] = _simple(p, cfg, "age_or_weight", cfg["age_or_weight"], "sgba_population_age", F)
        R["vehicle_solvent"] = _simple(p, cfg, "vehicle_solvent", cfg["vehicle_solvent"], "vehicle_solvent", F)
    if rt == "in_vitro":
        R["cell_line_or_tissue_source"] = _simple(p, cfg, "cell_line_or_tissue_source", cfg["cell_line"], "cell_line_or_tissue_source", F)
        R["species_of_origin"] = _simple(p, cfg, "species_of_origin", cfg["species"], "species_or_biological_system", F)
        R["concentration"] = _extract_dose(p, cfg, "concentration", rt, F)
        R["exposure_time"] = _duration(p, cfg, "exposure_time", vocab, F)
        R["vehicle_solvent"] = _simple(p, cfg, "vehicle_solvent", cfg["vehicle_solvent"], "vehicle_solvent", F)
        hit = p.first(cfg["replicates"])
        if hit:
            F["replicates_notes"] = _sf(hit[2].group(0), hit[1])
            R["replicates"] = _reported(p, hit)
        else:
            R["replicates"] = _missing(p, "replicates")
    if rt == "clinical":
        rnd = "no" if p.first(cfg["randomized"]["no"]) else "yes" if p.first(cfg["randomized"]["yes"]) else "unknown"
        F["randomized"] = {"value": rnd, "missingness": "reported" if rnd != "unknown" else "uncertain"}
        n = p.first(cfg["sample_size"]["participants"]) or p.first(cfg["sample_size"]["total_n"])
        if n:
            F["sample_size"] = {"value": int(n[2].group(1)), "missingness": "reported", "source_span": n[1][:500], "pending_biomedical": True}
            R["number_of_participants"] = _reported(p, n)
        else:
            R["number_of_participants"] = _missing(p, "number_of_participants")
        age = p.first(cfg["age_or_weight"])
        sex = p.first(cfg["sex"])
        if age:
            F["sgba_population_age"] = _sf(age[2].group(0), age[1])
        if sex:
            F["sgba_sex_reported"] = _sf(sex[2].group(0), sex[1])
        R["participant_age_or_sex"] = _reported(p, age or sex) if (age or sex) else _missing(p, "participant_age_or_sex")
        R["diagnosis_or_condition"] = _simple(p, cfg, "diagnosis_or_condition", cfg["diagnosis"], "disease_or_experimental_model", F, group=1)
        R["comparison_group"] = _comparator(p, cfg, F)

    gs = _group_sizes(p, cfg, rt)
    if gs:
        R["group_sample_sizes"] = gs

    # good practice (recorded only)
    hit = p.first(cfg["storage_temperature"])
    if hit:
        F["storage_temperature"] = _sf(hit[2].group(1), hit[1])
        R["storage_temperature"] = _reported(p, hit)
    else:
        R["storage_temperature"] = _missing(p, "storage_temperature")
    for key in ("milled_ground", "protected_from_light"):
        hit = p.first(cfg[key])
        if hit:
            F[key] = {"value": "yes", "missingness": "reported", "source_span": hit[1][:500], "pending_biomedical": True}
            R[key] = _reported(p, hit)
        else:
            R[key] = _missing(p, key)
    return {"record": rec, "excluded": None, "study_type": _study_type_for_rules(rt, None)}


def _comparator(p, cfg, F):
    """comparison_group + comparator confidence (extraction_confidence). comparator_validated is
    always False here: only CRN Evaluation's sign-off may set it (A13)."""
    named = p.first(cfg["comparison_group"]["named"])
    base = {"comparator_validated": False}
    if named:
        F["comparison_group"] = _sf(named[2].group(0), named[1])
        if named[0] in ("table", "figure_caption"):
            return _reported(p, named, extraction_confidence="medium", confidence=0.6, needs_review=True,
                             review_reason="arm_only_in_table_or_figure", **base)
        return _reported(p, named, extraction_confidence="high", confidence=0.9, needs_review=False, **base)
    weak = p.first(cfg["comparison_group"]["weak"])
    if weak:
        F["comparison_group"] = _sf(weak[2].group(0), weak[1])
        return _reported(p, weak, extraction_confidence="low", confidence=0.4, needs_review=True,
                         review_reason="no_named_control_arm", **base)
    return _missing(p, "comparison_group", **base)


def _group_sizes(p, cfg, rt):
    """A15: only numeric per-group n; unit enum is animals|replicates (no clinical unit)."""
    unit = {"in_vivo": "animals", "in_vitro": "replicates"}.get(rt)
    if not unit:
        return []
    out, seen = [], set()
    for _, _, m in p.all(cfg["group_sample_size"]):
        label = re.sub(r"^(?:(?:and|or|the|a|in|of)\s+)+", "", m.group(1).strip(), flags=_F)
        if label.lower() in seen:
            continue
        seen.add(label.lower())
        out.append({"group_label": label, "n": int(m.group(2)), "unit": unit})
    return out


def validate(record):
    import jsonschema
    schema = json.loads(SCHEMA_PATH.read_text())
    return sorted(e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(record))
