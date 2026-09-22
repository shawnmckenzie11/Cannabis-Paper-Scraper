# Annotation Guide Draft — Methods Extraction (WP1)

**Role:** CRN Methods (Scientific Data Architect)  
**Date:** 2026-09-22 (ET)  
**Status:** Draft for gold-set / HITL training. Scientific coding rules that invent ontology are **out of scope** — see Shawn Q12–Q16.  
**Audience:** Trainee annotators, clinician co-reviewers, future Label Studio config (Eng).

---

## 1. Purpose

Annotate (or review AI extractions of) **method and context properties** so papers can be compared honestly in Public Compare — without inventing citations, methods, or SGBA+ attributes.

You are labeling **what the available source says**, not what you believe the authors meant.

---

## 2. Provenance rules (technical — always apply)

Every extraction record must carry a **provenance envelope**:

| Field | Annotator action |
|-------|------------------|
| `schema_version` | Use current contract id (e.g. `wp1.0.0-draft`) |
| `extractor_id` | System-filled for AI; for hand entry use `human-annotator` |
| `model_id` | System-filled or null for pure hand tags |
| `prompt_id` | System-filled or null |
| `confidence` | Optional 0–1 for AI; humans may leave null or set review confidence per local SOP |
| `source_tier` | Record what you actually read: abstract_only, pdf_extracted, etc. |
| `source_span` | Prefer a short verbatim quote or section locator when asserting `reported` |
| `human_override` | Set when correcting AI; list fields changed |
| `reviewed_at` | Timestamp when review completed |

**Honesty chips (UI):** Prefer `human_reviewed` only after a human has checked the fields in scope. Do not mark human-reviewed solely because confidence is high.

---

## 3. Missingness rules (technical — distinct codes)

Do **not** use blank/null to mean everything. Choose exactly one:

| Code | Use when |
|------|----------|
| `reported` | Value is explicitly present in the **available** source you are allowed to use for this tier |
| `not_in_available_source` | You had the expected source text, searched appropriately, and the fact is simply not stated |
| `source_unavailable` | The needed source (e.g. full Methods PDF) was not available; absence is about access, not about the paper’s content |
| `not_applicable` | The field cannot apply given other adjudicated structure (e.g. clinical_subtype when research_type is not clinical — **after** Shawn locks that rule) |
| `uncertain` | Source language is ambiguous; you refuse to force a label |
| `extraction_failed` | Pipeline/annotator process failed (timeout, unreadable PDF, tool error) — not a scientific claim |

### Quick tests

- Abstract only, dose only in full Methods PDF you do not have → `source_unavailable` (not `not_in_available_source`).  
- Full text in hand, no dose stated → `not_in_available_source`.  
- Cell study, asking for human clinical subtype → `not_applicable` **only if** adjudication says so; until then prefer `uncertain` rather than inventing N/A policy.  
- Two conflicting doses with no primary identified → `uncertain` (+ note), not an average.

---

## 4. Source-tier truth (interim technical; final bar = Shawn Q13)

Until Q13 is answered, follow Seed honesty:

1. Prefer values grounded in Methods/Results when full text is available.  
2. Do not treat background/introduction mentions as study methods.  
3. Methods-heavy numerics (doses, concentrations, puff counts, regimen bins, sample size, etc.) should not be forced from abstracts when the abstract lacks them — use `not_in_available_source` or `source_unavailable` as appropriate.  
4. Never upgrade an abstract-only guess to look like PDF truth.

---

## 5. Field annotation posture (scientific values PENDING)

For Core fields (`research_type`, `product`, `route`, `disease_or_experimental_model`, `outcome_domains`, `species_or_biological_system`, clinical subtypes, regimen bins, SGBA codes):

- If Shawn has **not** locked an enum, mark `pending_biomedical: true` and either leave value null with `uncertain`, or capture **verbatim reported phrase** in `source_span` / notes — **do not invent a closed ontology**.  
- Multi-label fields: include all that the source clearly supports; do not “helpfully” add likely domains.  
- Reviews summarizing other studies: extract review methodology only; do not copy cited studies’ doses into the review’s method card (aligns with existing Node 1B spirit).

Dose / concentration:

- Copy the **reported unit family** into the matching slot.  
- **Forbidden (interim):** converting µg/mL → mg/mL, µM → mg/kg, % → mg, or any cross-family “normalization” unless/until Shawn Q8 explicitly allows a named conversion.  
- If only a non-matching unit appears, use `uncertain` or `not_in_available_source` for the target slot and quote the original in `source_span`.

---

## 6. SGBA+ / IDEAS — never infer

Seed requirement: capture sex, gender, and population **only when papers report them**.

### Never-infer list (non-exhaustive)

- Do **not** infer sex/gender from author names, pronouns in unrelated text, given names, photos, or country of study.  
- Do **not** assume “both” sexes because a disease affects all sexes.  
- Do **not** fill gender from sex or sex from gender.  
- Do **not** invent Indigenous / racial / socioeconomic descriptors from geography.  
- Do **not** treat animal sex as human SGBA+ population fields without clear reporting and adjudication rules (Q11/Q12).

### Positive rules

- If the paper states sex distribution or gender identity categories, capture under the matching SGBA hook and set `sgba_reporting_present: true`.  
- If the paper is silent, use `not_in_available_source` (or `source_unavailable` if you lacked full text) and `sgba_reporting_present: false`.  
- Gold co-review should check whether SGBA+-relevant reporting was **captured or correctly marked absent** — not whether the study “should have” reported it.

`ideas_representation_notes` is for human reviewers only (capture quality, accessibility of reporting). Models must not free-write policy judgments there.

---

## 7. Comparability / gap language (annotators)

Annotators do **not** write Public Compare gap copy. Wonder owns calm “I wonder…” language.  
Do not mark fields to force a dramatic gap. Prefer honest missingness. Final bar for gap claims = Shawn Q14.

---

## 8. Conflict with AI extraction

When AI and human disagree:

1. Prefer the human after review (`human_override`).  
2. Keep the AI value in audit elsewhere if the platform supports it (feedback_audit spirit).  
3. Do not silently blend values.  
4. Gold policy for adjudication disagreements = Shawn Q16 (unanswered here).

---

## 9. Out of scope for annotators

- Inventing scientific definitions or closed enums  
- Fly/deploy decisions  
- Replacing Maude tree (Q15)  
- Chatbot answers about papers  
- Unit conversions across families (Q8)

---

## 10. Versioning

Update this guide when Shawn closes Q1–Q16. Until then, treat all scientific vocabularies as **`pending_biomedical`**.
