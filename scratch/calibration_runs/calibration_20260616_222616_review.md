# Calibration High-Level Review

Artifact: `scratch/calibration_runs/calibration_20260616_222616.json`
Batch: `calibration_20260616_222616`
High-level changed papers: `50` / `50`
Low-confidence queue response: `25` papers at confidence <= 0.72; overlap with calibration batch: `1`

## High-Level Field Change Counts

- `cannabis_type`: 48
- `study_type`: 21
- `exposure_method`: 21
- `outcome_domain`: 12
- `publication_type`: 1

## Variant High-Level Change Counts

- `control`: cannabis_type=23, exposure_method=11, study_type=8, outcome_domain=5
- `decision_checklist`: cannabis_type=25, study_type=13, exposure_method=10, outcome_domain=7, publication_type=1

## Highest-Priority Papers For Expert Review

### Paper 31533 | PMID 22229018 | conf 0.608 | queue yes
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `exposure_method`, `publication_type`, `study_type`

Title: Lasting impacts of prenatal cannabis exposure and the role of endogenous cannabinoids in the developing brain

- Expert status: accepted as Maude learning boundary for `review` vs `original research` routing from abstract-level review language.
- `cannabis_type`: "[\"pure cannabinoid\"]" -> ["unknown"]
- `exposure_method`: "[\"inhaled\"]" -> ["unknown"]
- `publication_type`: "original research" -> "review"
- `study_type`: "[\"Clinical (observational)\", \"Animal Models (Other)\"]" -> ["review"]

### Paper 11817 | PMID 38546067 | conf 0.647 | queue no
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `exposure_method`, `outcome_domain`, `study_type`

Title: A Comparative Analysis on the Potential Anticancer Properties of Tetrahydrocannabinol, Cannabidiol, and Tetrahydrocannabivarin Compounds Through In Silico Approach

- `cannabis_type`: "[\"unknown\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"injection cannabinoids\"]" -> ["unknown"]
- `outcome_domain`: "[\"other\"]" -> ["oncology"]
- `study_type`: "[\"Animal Models (Rat)\"]" -> ["Cell Culture (Other In Vitro)"]

### Paper 6192 | PMID 39258755 | conf 0.717 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `outcome_domain`, `study_type`

Title: Cannabinoid combination targets NOTCH1-mutated T-cell acute lymphoblastic leukemia through the integrated stress response pathway

- `cannabis_type`: "[\"concentrates\"]" -> ["pure cannabinoid", "concentrates"]
- `exposure_method`: "[\"injection cannabinoids\"]" -> ["cannabinoids dissolved in media", "injection cannabinoids"]
- `outcome_domain`: "[\"other\"]" -> ["oncology"]
- `study_type`: "[\"Animal Models (Mouse)\"]" -> ["Cell Culture (Cell Lines)", "Animal Models (Mouse)"]

### Paper 10072 | PMID 37330445 | conf 0.646 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: Characterization of cannabis strain-plant-derived extracellular vesicles as potential biomarkers

- `cannabis_type`: "[\"unknown\"]" -> ["dried flower"]
- `exposure_method`: "[\"injection cannabinoids\"]" -> ["unknown"]
- `study_type`: "[\"Animal Models (Other)\"]" -> ["Cell Culture (Other In Vitro)"]

### Paper 30911 | PMID 23373571 | conf 0.673 | queue no
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: The cannabinoid TRPA1 agonist cannabichromene inhibits nitric oxide production in macrophages and ameliorates murine colitis

- `cannabis_type`: "[\"unknown\"]" -> ["pure cannabinoid", "CB receptor agonist", "CB receptor antagonist"]
- `exposure_method`: "[\"cannabinoids dissolved in media\"]" -> ["cannabinoids dissolved in media", "injection cannabinoids"]
- `study_type`: "[\"Animal Models (Mouse)\", \"Cell Culture (Other In Vitro)\"]" -> ["Animal Models (Mouse)", "Cell Culture (Primary Cells)"]

### Paper 16543 | PMID 31437494 | conf 0.674 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: Cannabidiol differentially regulates basal and LPS-induced inflammatory responses in macrophages, lung epithelial cells, and fibroblasts

- `cannabis_type`: "[\"vape pen\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"cannabinoids dissolved in media\"]" -> ["cannabinoids dissolved in media", "exposure of cells to smoke/vapor"]
- `study_type`: "[\"Cell Culture (Other In Vitro)\"]" -> ["Cell Culture (Cell Lines)"]

### Paper 17228 | PMID 30083539 | conf 0.674 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: Pharmacokinetics, Safety, and Clinical Efficacy of Cannabidiol Treatment in Osteoarthritic Dogs

- `cannabis_type`: "[\"dried flower\"]" -> ["pure cannabinoid", "concentrates"]
- `exposure_method`: "[\"inhaled\"]" -> ["oral administration"]
- `study_type`: "[\"Clinical (RCT)\", \"Animal Models (Other)\"]" -> ["Animal Models (Other)"]

### Paper 28277 | PMID 28439004 | conf 0.676 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: Endocannabinoid system acts as a regulator of immune homeostasis in the gut

- `cannabis_type`: "[\"edibles\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"oral administration\"]" -> ["oral administration", "injection cannabinoids", "cannabinoids dissolved in media"]
- `study_type`: "[\"Animal Models (Mouse)\", \"Cell Culture (Other In Vitro)\"]" -> ["Animal Models (Mouse)", "Cell Culture (Primary Cells)"]

### Paper 29912 | PMID 25269802 | conf 0.686 | queue no
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: Colon carcinogenesis is inhibited by the TRPM8 antagonist cannabigerol, a Cannabis-derived non-psychotropic cannabinoid

- `cannabis_type`: "[\"unknown\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"injection cannabinoids\"]" -> ["cannabinoids dissolved in media", "injection cannabinoids"]
- `study_type`: "[\"Animal Models (Mouse)\"]" -> ["Cell Culture (Cell Lines)", "Animal Models (Mouse)"]

### Paper 23523 | PMID 35007072 | conf 0.691 | queue no
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `outcome_domain`, `study_type`

Title: Cannabinoids Block Cellular Entry of SARS-CoV-2 and the Emerging Variants

- `cannabis_type`: "[\"concentrates\"]" -> ["pure cannabinoid"]
- `outcome_domain`: "[\"inflammation\", \"other\"]" -> ["inflammation"]
- `study_type`: "[\"Cell Culture (Other In Vitro)\"]" -> ["Cell Culture (Cell Lines)"]

### Paper 15976 | PMID 32345916 | conf 0.693 | queue no
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: A randomized, double-blind, placebo-controlled study of daily cannabidiol for the treatment of canine osteoarthritis pain

- `cannabis_type`: "[\"dried flower\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"inhaled\"]" -> ["oral administration", "cannabinoids dissolved in media"]
- `study_type`: "[\"Animal Models (Mouse)\", \"Clinical (RCT)\"]" -> ["Clinical (RCT)", "Animal Models (Mouse)", "Cell Culture (Cell Lines)"]

### Paper 11539 | PMID 39122786 | conf 0.699 | queue no
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: Dental pulp stem cells-derived cannabidiol-treated organoid-like microspheroids show robust osteogenic potential via upregulation of WNT6

- `cannabis_type`: "[\"unknown\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"cannabinoids dissolved in media\"]" -> ["cannabinoids dissolved in media", "injection cannabinoids"]
- `study_type`: "[\"Cell Culture (Organoids)\", \"Animal Models (Mouse)\"]" -> ["Cell Culture (Organoids)", "Cell Culture (Primary Cells)", "Animal Models (Mouse)"]

### Paper 31491 | PMID 22300105 | conf 0.706 | queue no
Variant: `control` | Changed high-level fields: `cannabis_type`, `exposure_method`, `outcome_domain`

Title: Inhibitory effect of cannabichromene, a major non-psychotropic cannabinoid extracted from Cannabis sativa, on inflammation-induced hypermotility in mice

- `cannabis_type`: "[\"unknown\"]" -> ["pure cannabinoid", "CB receptor antagonist"]
- `exposure_method`: "[\"cannabinoids dissolved in media\"]" -> ["injection cannabinoids", "cannabinoids dissolved in media"]
- `outcome_domain`: "[\"inflammation\"]" -> ["inflammation", "other"]

### Paper 16492 | PMID 31518892 | conf 0.708 | queue no
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `exposure_method`, `study_type`

Title: Cannabidiol induces antioxidant pathways in keratinocytes by targeting BACH1

- `cannabis_type`: "[\"unknown\"]" -> ["pure cannabinoid"]
- `exposure_method`: "[\"injection cannabinoids\"]" -> ["cannabinoids dissolved in media", "injection cannabinoids"]
- `study_type`: "[\"Animal Models (Mouse)\"]" -> ["Cell Culture (Primary Cells)", "Animal Models (Mouse)"]

### Paper 13089 | PMID 36385633 | conf 0.720 | queue no
Variant: `decision_checklist` | Changed high-level fields: `cannabis_type`, `outcome_domain`, `study_type`

Title: Effects of cannabidiol on vacuous chewing movements, plasma glucose and oxidative stress indices in rats administered high dose risperidone

- `cannabis_type`: "[\"edibles\"]" -> ["pure cannabinoid"]
- `outcome_domain`: "[\"other\"]" -> ["inflammation", "cognition", "other"]
- `study_type`: "[\"Animal Models (Rat)\", \"Animal Models (Other)\"]" -> ["Animal Models (Rat)"]

## Low-Confidence Queue Overlap

- `31533` conf `0.607634734061274`: Lasting impacts of prenatal cannabis exposure and the role of endogenous cannabinoids in the developing brain

## Correction Status

No expert-approved corrections were applied in this pass. The candidates above are queued for expert review before any `/api/papers/<paper_id>/edit-classification` calls should be made.
