# Calibration High-Level Review

Artifact: `scratch/calibration_runs/calibration_20260616_231015.json`
Batch: `calibration_20260616_231015`
High-level changed papers: `48` / `50`
Low-confidence queue response: `25` papers at confidence <= 0.72; overlap with calibration batch: `1`

## High-Level Field Change Counts

- `cannabis_type`: 45
- `exposure_method`: 36
- `study_type`: 35
- `outcome_domain`: 20
- `publication_type`: 1

## Variant High-Level Change Counts

- `control`: cannabis_type=22, exposure_method=17, study_type=17, outcome_domain=12
- `decision_checklist`: cannabis_type=23, exposure_method=19, study_type=18, outcome_domain=8, publication_type=1

## Highest-Priority Papers For Expert Review

### Paper 9877 | PMID 37434213 | conf 0.632 | queue yes
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `exposure_method`, `outcome_domain`, `study_type`

Title: Cannabis sativa demonstrates anti-hepatocellular carcinoma potentials in animal model: in silico and in vivo studies of the involvement of Akt

- `cannabis_type`: "[\"concentrates\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"injection cannabinoids\"]" -> ["oral administration"]
- `outcome_domain`: "[\"oncology\"]" -> ["oncology", "inflammation"]
- `study_type`: "[\"Animal Models (Other)\"]" -> ["Animal Models (Rat)"]

### Paper 30091 | PMID 24911644 | conf 0.661 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `outcome_domain`, `study_type`

Title: The dual FAAH/MAGL inhibitor JZL195 has enhanced effects on endocannabinoid transmission and motor behavior in rats as compared to those of the MAGL inhibitor JZL184

- `cannabis_type`: "[\"unknown\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"cannabinoids dissolved in media\"]" -> ["injection cannabinoids"]
- `outcome_domain`: "[\"other\"]" -> ["addiction", "other"]
- `study_type`: "[\"Animal Models (Rat)\", \"Animal Models (Mouse)\", \"Cell Culture (Other In Vitro)\"]" -> ["Animal Models (Rat)"]

### Paper 26027 | PMID 32913220 | conf 0.662 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `outcome_domain`, `study_type`

Title: Low brain endocannabinoids associated with persistent non-goal directed nighttime hyperactivity after traumatic brain injury in mice

- `cannabis_type`: "[\"dried flower\"]" -> ["unknown"]
- `exposure_method`: "[\"inhaled\"]" -> ["unknown"]
- `outcome_domain`: "[\"cognition\", \"pain\", \"neuroprotection\"]" -> ["neuroprotection", "cognition", "pain", "anxiety"]
- `study_type`: "[\"Clinical (observational)\", \"Animal Models (Mouse)\"]" -> ["Animal Models (Mouse)"]

### Paper 11988 | PMID 38143819 | conf 0.672 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `outcome_domain`, `study_type`

Title: Diclofenac and dexamethasone modulate the effect of cannabidiol on the rat colon motility ex vivo

- `cannabis_type`: "[\"unknown\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"injection cannabinoids\"]" -> ["cannabinoids dissolved in media"]
- `outcome_domain`: "[\"inflammation\"]" -> ["inflammation", "other"]
- `study_type`: "[\"Animal Models (Rat)\"]" -> ["Animal Models (Rat)", "Cell Culture (Other In Vitro)"]

### Paper 6472 | PMID 39112591 | conf 0.676 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `outcome_domain`, `study_type`

Title: Comparing CB1 receptor GIRK channel responses to receptor internalization using a kinetic imaging assay

- `cannabis_type`: "[\"pure cannabinoid\"]" -> ["CB receptor agonist", "pure cannabinoid"]
- `exposure_method`: "[\"injection cannabinoids\"]" -> ["cannabinoids dissolved in media"]
- `outcome_domain`: "[\"other\"]" -> ["addiction", "pain"]
- `study_type`: "[\"Animal Models (Mouse)\"]" -> ["Cell Culture (Cell Lines)"]

### Paper 13134 | PMID 36339540 | conf 0.678 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `outcome_domain`, `study_type`

Title: Cannabidiol markedly alleviates skin and liver fibrosis

- `cannabis_type`: "[\"unknown\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"cannabinoids dissolved in media\"]" -> ["injection cannabinoids", "oral administration", "cannabinoids dissolved in media"]
- `outcome_domain`: "[\"inflammation\", \"other\"]" -> ["inflammation"]
- `study_type`: "[\"Animal Models (Mouse)\", \"Cell Culture (Other In Vitro)\"]" -> ["Animal Models (Mouse)", "Cell Culture (Cell Lines)", "Cell Culture (Primary Cells)"]

### Paper 14933 | PMID 33864076 | conf 0.681 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `outcome_domain`, `study_type`

Title: Cannabidiol converts NF-κB into a tumor suppressor in glioblastoma with defined antioxidative properties

- `cannabis_type`: "[\"unknown\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"injection cannabinoids\"]" -> ["cannabinoids dissolved in media", "injection cannabinoids"]
- `outcome_domain`: "[\"oncology\"]" -> ["oncology", "neuroprotection"]
- `study_type`: "[\"Animal Models (Other)\"]" -> ["Cell Culture (Cell Lines)", "Cell Culture (Primary Cells)", "Animal Models (Mouse)"]

### Paper 5988 | PMID 39375818 | conf 0.689 | queue no
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `exposure_method`, `outcome_domain`, `study_type`

Title: Preparation of a nanoemulsion containing active ingredients of cannabis extract and its application for glioblastoma: in vitro and in vivo studies

- `cannabis_type`: "[\"concentrates\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"cannabinoids dissolved in media\"]" -> ["injection cannabinoids", "cannabinoids dissolved in media"]
- `outcome_domain`: "[\"oncology\"]" -> ["oncology", "neuroprotection"]
- `study_type`: "[\"Animal Models (Rat)\", \"Cell Culture (Other In Vitro)\"]" -> ["Animal Models (Rat)", "Cell Culture (Cell Lines)"]

### Paper 7100 | PMID 38830102 | conf 0.690 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `outcome_domain`, `study_type`

Title: Structure-based identification of a G protein-biased allosteric modulator of cannabinoid receptor CB1

- `cannabis_type`: "[\"unknown\"]" -> ["pure cannabinoid", "CB receptor agonist"]
- `exposure_method`: "[\"injection cannabinoids\"]" -> ["injection cannabinoids", "cannabinoids dissolved in media"]
- `outcome_domain`: "[\"pain\", \"addiction\"]" -> ["pain", "addiction", "neuroprotection"]
- `study_type`: "[\"Animal Models (Mouse)\"]" -> ["Cell Culture (Cell Lines)", "Animal Models (Mouse)"]

### Paper 8442 | PMID 38151493 | conf 0.714 | queue no
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `exposure_method`, `outcome_domain`, `study_type`

Title: Cannabis Sativa targets mediobasal hypothalamic neurons to stimulate appetite

- `cannabis_type`: "[\"vape pen\"]" -> ["dried flower", "CB receptor agonist"]
- `exposure_method`: "[\"whole body. smoke/vapor\"]" -> ["whole body. smoke/vapor", "injection cannabinoids"]
- `outcome_domain`: "[\"other\"]" -> ["addiction", "inflammation"]
- `study_type`: "[\"Animal Models (Rat)\", \"Animal Models (Mouse)\"]" -> ["Animal Models (Rat)", "Animal Models (Mouse)", "Cell Culture (Primary Cells)"]

### Paper 17333 | PMID 34676320 | conf 0.644 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: Human Pharmacokinetics and Adverse Effects of Pulmonary and Intravenous THC-CBD Formulations

- `cannabis_type`: "[\"vape pen\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"cannabinoids dissolved in media\"]" -> ["inhaled", "injected"]
- `study_type`: "[\"Cell Culture (Other In Vitro)\"]" -> ["Clinical (prospective)"]

### Paper 31303 | PMID 22646533 | conf 0.650 | queue no
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: The antinociceptive triterpene β-amyrin inhibits 2-arachidonoylglycerol (2-AG) hydrolysis without directly targeting cannabinoid receptors

- `cannabis_type`: "[\"unknown\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"injection cannabinoids\"]" -> ["cannabinoids dissolved in media"]
- `study_type`: "[\"Animal Models (Mouse)\", \"Animal Models (Other)\"]" -> ["Cell Culture (Cell Lines)"]

### Paper 13668 | PMID 35644533 | conf 0.652 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: The effect of a mixed cannabidiol and cannabidiolic acid based oil on client-owned dogs with atopic dermatitis

- `cannabis_type`: "[\"dried flower\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"inhaled\"]" -> ["oral administration"]
- `study_type`: "[\"Clinical (prospective)\", \"Clinical (RCT)\", \"Animal Models (Other)\"]" -> ["Animal Models (Other)"]

### Paper 18128 | PMID 29588939 | conf 0.655 | queue no
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: Cannabis in epilepsy: From clinical practice to basic research focusing on the possible role of cannabidivarin

- `cannabis_type`: "[\"unknown\"]" -> ["pure cannabinoid", "dried flower"]
- `exposure_method`: "[\"injection cannabinoids\"]" -> ["oral", "cannabinoids dissolved in media"]
- `study_type`: "[\"Animal Models (Other)\"]" -> ["case study", "Cell Culture (Other In Vitro)"]

### Paper 10107 | PMID 37313955 | conf 0.656 | queue no
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: Evaluation of Cytochrome P450-Mediated Cannabinoid-Drug Interactions in Healthy Adult Participants

- `cannabis_type`: "[\"concentrates\", \"edibles\"]" -> ["pure cannabinoid", "edibles"]
- `exposure_method`: "[\"cannabinoids dissolved in media\"]" -> ["oral"]
- `study_type`: "[\"Cell Culture (Other In Vitro)\"]" -> ["Clinical (RCT)"]

## Low-Confidence Queue Overlap

- `9877` conf `0.6320886036951789`: Cannabis sativa demonstrates anti-hepatocellular carcinoma potentials in animal model: in silico and in vivo studies of the involvement of Akt

## Correction Status

No additional expert-approved corrections were applied in this pass. The candidates above are queued for expert review before any `/api/papers/<paper_id>/edit-classification` calls should be made.
