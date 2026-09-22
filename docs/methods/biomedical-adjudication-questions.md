# Biomedical adjudication questions (open)

Owner: Shawn (biomedical lead). Status of every item: open.

This list is input to adjudication. It does not answer the questions, recommend an option, or record a provisional scientific definition. Methods will not close an enum until a decision is written back against the item id.

Related drafts: `docs/methods/field-dictionary.md`, `schemas/methods.schema.json`.

## Corpus boundary

1. Which of the following are inside the Year-1 pilot: plant products, isolated cannabinoids, synthetic cannabinoids, endocannabinoid mechanism papers, medicinal use, observational exposure? Decision needed before inclusion rules change.
2. How should reviews, case reports, ex vivo work, mixed studies, non-English text, and publication corrections be routed for methods extraction? Decision needed before `not_applicable` is used.

## Design, model, product, route

3. What is the allowed value list for `study_type`, including mixed designs? Current `papers.study_type` labels are not that list.
4. What is the allowed value list for `model_population` (human, animal species, cell model, other)? Decision needed before `species` is treated as that field.
5. What is the allowed value list for `product_composition`? The prompt’s current `cannabis_type` bullets are not adopted here.
6. What is the allowed value list for `route`? The `schema.sql` comment on `exposure_method` is not adopted here.
7. Does Year-1 keep a separate strain field, and is chemotype normalization in or out? `strain_normalized` currently maps toward Chemotype I/II/III in code. That mapping is not approved by this draft.

## Quantity, time, comparator, outcome

8. Are product concentration and delivered dose different slots? If a conversion is ever allowed, which pairs and which assumptions? No conversion is specified here.
9. How should exposure duration, treatment duration, inhalation time, and follow-up time be distinguished? When is each `not_applicable`? `duration_days`, `inhaled_exposure_duration`, and `treatment_duration` stay unmerged until this is answered.
10. Do the bins currently commented on `exposure_regimen_bin` (`acute`, `subchronic`, `chronic`) stay, change, or leave the Year-1 contract?
11. What does `sample_size` count (participants, animals, samples, biological replicates, technical replicates, per arm)? Decision needed before gold labels score that field.
12. What counts as a `comparator`?
13. What is an `outcome_measure`, and what is an `outcome_time_point`? Decision needed before `outcome_domain` is treated as either one.
14. What splits one experiment into arms, and may a paper-level dose be stored when arms differ?

## IDEAS / SGBA+

15. What source-defined and, separately, normalized values are allowed for `sex_reported`? Does that list differ for humans, animals, and cell donors? `population_sex` comments (`male`, `female`, `both`) are not that decision.
16. What source-defined and normalized values are allowed for `gender_reported`? Confirm it stays a different field from sex.
17. What may be stored in `age_reported`? The extractor docstring (`pediatric`, `adult`, `geriatric`) is not that decision.
18. Which of these enter Year-1 at all, and what wording is stored versus normalized: `race_ethnicity_reported`, `indigenous_identity_reported`, `disability_reported`, `socioeconomic_reported`, `geographic_context_reported`?
19. What must reviewers refuse to infer, beyond the never-infer rule already stated in the annotation guide? Add cases; do not fill examples as policy here.
20. How should a reporting audit describe missing sex or gender without treating missingness as a biological finding? Wording is a lead decision.

## Legacy columns

21. Which current columns remain public methods fields, which become internal projections, and which are retired after the assertion store exists? Candidates include the unit-baked THC/CBD columns, `population_sex`, `population_age`, `strain_normalized`, and `exposure_regimen_bin`.
22. Should secondary literature carry any of the Year-1 slots, or only routing fields?

## Not in scope for these questions

This list does not ask the lead to approve a deploy, a threshold, or a model. Reliability review of the technical contract is separate and does not close items 1–22.
