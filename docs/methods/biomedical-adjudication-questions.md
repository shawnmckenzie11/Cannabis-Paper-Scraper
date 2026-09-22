# Biomedical Adjudication Questions — Shawn

**Role:** CRN Methods (Scientific Data Architect)  
**Date:** 2026-09-22 (ET)  
**Instruction to agents/annotators:** **Do not answer these questions.** Embed for Shawn Biomedical adjudication only.  
**Context:** Year-1 Cannabis Research Navigator methods schema (Faculty of Medicine AI Seed). Technical proposals live in `schema-proposal.md` and `methods.schema.json`; scientific meanings stay open until Shawn decides.

---

## Q1 — Year-1 core fields: mandatory vs deferred

Which proposed Core fields are **mandatory** for Year-1 extraction / Compare filters, and which are **deferred**?

Candidates referenced in the field dictionary: `research_type`, `clinical_subtype`, `product`, `route`, `disease_or_experimental_model`, `outcome_domains`, `species_or_biological_system`, plus dose/regimen cluster, SGBA hooks.

*(No answer here.)*

---

## Q2 — Research-type ontology (in vitro / in vivo / clinical + edge cases)

What is the authoritative **research_type** ontology for cannabis literature in this catalog?

Include how to treat edge cases (mixed designs, ex vivo, organoid, human tissues in vitro, observational without intervention, etc.).

*(No answer here.)*

---

## Q3 — Clinical subtypes

What clinical subtype vocabulary should nest under or sit beside clinical research_type (e.g. RCT, prospective, retrospective, observational, and any others)?

How should case reports / case series relate to clinical subtypes vs `publication_type`?

*(No answer here.)*

---

## Q4 — Product taxonomy

What is the Year-1 **product** taxonomy (replacing/refining legacy `cannabis_type` cue lists)?

How should synthetics, isolates, receptor agonists/antagonists, and plant products be grouped?

*(No answer here.)*

---

## Q5 — Route vs product

How should **route** (administration / exposure modality) be separated from **product**?

Which legacy `exposure_method` / Node 7 path labels survive as routes, which are exposure setups, and which fold into product?

*(No answer here.)*

---

## Q6 — Disease / experimental model

What ontology or coding approach should Year-1 use for **disease or experimental model** (absent as a dedicated column today)?

Mandatory in Year 1 or deferred (ties to Q1)?

*(No answer here.)*

---

## Q7 — Unit of scientific record

What is the unit of scientific record for comparability: **paper**, **paper family**, **experiment**, or **arm**?

If finer than paper, what is the Year-1 interim rule while storage remains paper-scoped?

*(No answer here.)*

---

## Q8 — Dose vs concentration vs regimen + forbidden conversions

Define scientific rules distinguishing **dose**, **concentration**, and **regimen**.

List **forbidden conversions** (and any explicitly allowed ones) for Year-1 extraction — especially across mg, mg/kg, mg/mL, µM, %, and puff-based measures.

*(No answer here.)*

---

## Q9 — Replicates & samples rules

How should `sample_size`, biological replicates, technical replicates, and per-arm N be coded when papers report them inconsistently?

What must never be inferred?

*(No answer here.)*

---

## Q10 — Outcomes domains

What is the Year-1 **outcome_domains** controlled list (refining legacy pain/anxiety/cognition/… cues)?

Policy for `"other"`, multi-label, and excluding background-only mentions?

*(No answer here.)*

---

## Q11 — Species / biological system

What vocabulary covers host **species** and broader **biological system** (cell line vs primary cells vs organoid vs whole animal vs human)?

Where does this sit relative to research_type?

*(No answer here.)*

---

## Q12 — SGBA+ scientific rules

Beyond the technical never-infer rule, what scientific coding is required for sex, gender, and population fields when reported?

Any IDEAS-specific representation requirements for Year-1 gold review?

*(No answer here.)*

---

## Q13 — Source-tier truth (abstract vs full text)

When may an abstract alone assert a Core methods value as `reported`?

What is the truth bar for methods-heavy fields across abstract vs PDF-extracted tiers?

*(No answer here.)*

---

## Q14 — Comparability / gap language bar

What scientific bar must Public Compare / “I wonder…” gap cues meet before claiming thin evidence along a dimension (product × route × model × research_type, etc.)?

*(No answer here. Wonder owns microcopy tone; Shawn owns scientific bar.)*

---

## Q15 — Legacy Maude / decision-tree status

What is the Year-1 status of the legacy Maude decision tree and calibration stack?

Options might include: keep as routing authority, keep as weak prior, freeze, replace after gold gates, or other — **Shawn decides**.

*(No answer here.)*

---

## Q16 — Gold / adjudication policy

What is the gold-set and disagreement adjudication policy (who breaks ties, precision/recall gates before backfill, Label Studio workflow expectations, trainee vs clinician co-review)?

*(No answer here.)*

---

## Sign-off block (for Shawn)

| Question | Decision (Shawn) | Date | Notes |
|----------|------------------|------|-------|
| Q1 | _pending_ | | |
| Q2 | _pending_ | | |
| Q3 | _pending_ | | |
| Q4 | _pending_ | | |
| Q5 | _pending_ | | |
| Q6 | _pending_ | | |
| Q7 | _pending_ | | |
| Q8 | _pending_ | | |
| Q9 | _pending_ | | |
| Q10 | _pending_ | | |
| Q11 | _pending_ | | |
| Q12 | _pending_ | | |
| Q13 | _pending_ | | |
| Q14 | _pending_ | | |
| Q15 | _pending_ | | |
| Q16 | _pending_ | | |

