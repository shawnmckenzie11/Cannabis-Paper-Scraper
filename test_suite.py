# test_suite.py
import unittest
import os
import json
import sqlite3
from db_manager import DatabaseManager
import extractor
import classifier

class TestHeuristicExtractor(unittest.TestCase):
    """Test cases for regex extractions and heuristics in extractor.py."""

    def test_study_type_inference(self):
        title = "Efficacy of Cannabidiol in patients"
        abstract_rct = "This was a double-blind, randomized controlled trial of placebo vs active drug."
        abstract_animal = "We studied Sprague-Dawley rats injected with THC to model anxiety."
        abstract_invitro = "This in vitro cell culture study assessed microglia survival after treatment."
        abstract_casestudy = "This is a clinical case report of an unusual presentation after cannabis ingestion."
        abstract_editorial = "This editorial commentary provides perspective on the new cannabis guidelines."
        
        # Open-source airway epithelial cells manifold paper (observational -> in vitro)
        title_manifold = "Open-source, three-dimensionally printed manifolds for exposure studies using human airway epithelial cells"
        abstract_manifold = "We designed 3D printed manifolds to conduct exposure studies with airway epithelial cells."

        # Review papers
        title_review1 = "Aging circadian rhythms and cannabinoids"
        abstract_review1 = "This review highlights 3 fields-biological aging, circadian rhythms, and endocannabinoid signaling."
        title_review2 = "Cannabinoid therapy for sleep: A review"
        abstract_review2 = "We summarize the current literature on cannabinoid receptor agonists."

        # Publication Type prefixes
        title_pub_review = "Some Cannabis Study"
        abstract_pub_review = "Publication Type: Review. Objectives: We review things. Methods: Check the literature."
        title_pub_meta = "Another Study"
        abstract_pub_meta = "Publication Type: Meta-Analysis. This is a study."

        self.assertEqual(extractor.infer_study_type(title, abstract_rct), ["Clinical (RCT)"])
        self.assertEqual(extractor.infer_study_type(title, abstract_animal), ["Animal Models (rat)"])
        self.assertEqual(extractor.infer_study_type(title, abstract_invitro), ["Cell Culture (cell lines)"])
        self.assertEqual(extractor.infer_study_type(title, abstract_casestudy), ["case study"])
        self.assertEqual(extractor.infer_study_type(title, abstract_editorial), ["editorial"])
        self.assertEqual(extractor.infer_study_type(title_manifold, abstract_manifold), ["Cell Culture (cell lines)"])
        self.assertEqual(extractor.infer_study_type(title_review1, abstract_review1), ["review"])
        self.assertEqual(extractor.infer_study_type(title_review2, abstract_review2), ["review"])
        self.assertEqual(extractor.infer_study_type(title_pub_review, abstract_pub_review), ["review"])
        self.assertEqual(extractor.infer_study_type(title_pub_meta, abstract_pub_meta), ["meta-analysis"])

        # RCT with pre-clinical background in abstract (should filter out Animal Models)
        title_rct_bg = "Signaling-specific inhibition of the CB1 receptor for cannabis use disorder: phase 1 and phase 2a randomized trials"
        abstract_rct_bg = "In mice and non-human primates, AEF0117 decreased cannabinoid self-administration. In a randomized controlled clinical trial, healthy volunteers were randomized..."
        self.assertEqual(extractor.infer_study_type(title_rct_bg, abstract_rct_bg), ["Clinical (RCT)"])


    def test_publication_type_inference(self):
        title = "Study on Cannabis"
        self.assertEqual(extractor.infer_publication_type(title, "This was a double-blind, randomized controlled trial."), "original research")
        self.assertEqual(extractor.infer_publication_type(title, "We conduct a systematic review of the literature."), "systematic review")
        self.assertEqual(extractor.infer_publication_type(title, "This is a case report of an unusual patient."), "case study")
        self.assertEqual(extractor.infer_publication_type("Cannabis for anxiety: a scoping review", "Objectives: To summarize scoping reviews on CBD."), "systematic review")
        self.assertEqual(extractor.infer_publication_type(title, "Publication Type: Meta-Analysis. Results: We pooled clinical trials."), "meta-analysis")
        self.assertEqual(extractor.infer_publication_type(title, "Publication Type: Review. Narrative review of the literature."), "review")
        self.assertEqual(extractor.infer_publication_type(title, "This editorial comment highlights the guidelines."), "editorial")
        self.assertEqual(extractor.infer_publication_type(title, "We present a letter to the editor regarding the recent guidelines."), "letter to the editor")
        self.assertEqual(extractor.infer_publication_type(title, "This perspective viewpoint outlines future directions."), "comment")

    def test_exposure_method(self):
        title = "Inhaled cannabis study"
        abstract_vape = "Subjects vaporized Bedrocan using a Volcano vaporizer."
        abstract_smoke = "We investigated smoked joint combustion outcomes."
        abstract_oral = "Rats were fed with oral edible gummies."
        
        self.assertEqual(extractor.infer_exposure_method(title, abstract_vape, "RCT", "human"), ["inhaled"])
        self.assertEqual(extractor.infer_exposure_method(title, abstract_smoke, "observational", "human"), ["inhaled"])
        self.assertEqual(extractor.infer_exposure_method(title, abstract_oral, "animal", "mouse"), ["oral administration"])

        # In vitro ALI lung epithelial cells paper (dissolved -> exposure to smoke/vapor)
        title_ali = "In vitro Cannabis Exposures of Lung Epithelial Cells at the Air-Liquid Interface"
        abstract_ali = "Lung epithelial cells were exposed to cannabis vapor directly at the air-liquid interface."
        self.assertEqual(extractor.infer_exposure_method(title_ali, abstract_ali, "in vitro", "cell_line"), ["exposure of cells to smoke/vapor"])

    def test_thc_cbd_extraction(self):
        abstract = "The material contained 12.5% THC and CBD (2.5%)."
        self.assertEqual(extractor.extract_thc_pct(abstract), 12.5)
        self.assertEqual(extractor.extract_cbd_pct(abstract), 2.5)
        
        # Test range averaging
        range_abstract = "The variety had 5-10% THC."
        self.assertEqual(extractor.extract_thc_pct(range_abstract), 7.5)

    def test_dose_extraction(self):
        text = "Subjects received a dose of 20 mg of THC, while rats got 5 mg/kg."
        self.assertEqual(extractor.extract_dose_mg(text), 20.0)

    def test_duration_extraction(self):
        text = "Treated for 3 weeks consecutively."
        self.assertEqual(extractor.extract_duration_days(text), 21.0)
        
        text2 = "A duration of 14 days was selected."
        self.assertEqual(extractor.extract_duration_days(text2), 14.0)

        # Hyphenated study duration
        text_hyphen = "A 6-week trial of cannabidiol was performed."
        self.assertEqual(extractor.extract_duration_days(text_hyphen), 42.0)

        # Age exclusions
        text_age = "30-year-old patients were treated with CBD for 5 days."
        self.assertEqual(extractor.extract_duration_days(text_age), 5.0)

        text_age_only = "The baseline mean age of participants was 25 years."
        self.assertEqual(extractor.extract_duration_days(text_age_only), None)

        # Historical exclusion (> 30 years)
        text_history = "Cannabis has been cultivated in the region for 5000 years."
        self.assertEqual(extractor.extract_duration_days(text_history), None)

        # Python duration formatting checks
        self.assertEqual(extractor.format_study_duration(None), "N/A")
        self.assertEqual(extractor.format_study_duration(365.0), "1 year")
        self.assertEqual(extractor.format_study_duration(547.5), "1.5 years")
        self.assertEqual(extractor.format_study_duration(30.0), "1 month")
        self.assertEqual(extractor.format_study_duration(75.0), "2.5 months")
        self.assertEqual(extractor.format_study_duration(14.0), "14 days")
        self.assertEqual(extractor.format_study_duration(0.0), "N/A")

    def test_sample_size(self):
        text = "A sample size of 84 patients was recruited (n = 84)."
        self.assertEqual(extractor.extract_sample_size(text), 84)

    def test_strain_normalization(self):
        text_chem1 = "Studies utilized the OG Kush strain."
        reported1, normalized1 = extractor.extract_strain_info(text_chem1)
        self.assertEqual(reported1, "OG Kush")
        self.assertEqual(normalized1, "Chemotype I")

        text_chem2 = "We selected Bediol for balanced outcomes."
        reported2, normalized2 = extractor.extract_strain_info(text_chem2)
        self.assertEqual(reported2, "Bediol")
        self.assertEqual(normalized2, "Chemotype II")

        text_chem3 = "We tested Charlotte's Web extracts."
        reported3, normalized3 = extractor.extract_strain_info(text_chem3)
        self.assertEqual(reported3, "Charlotte's Web")
        self.assertEqual(normalized3, "Chemotype III")

        # Quoted strain lookup
        quoted = 'The strain "Solodiol" was analyzed.'
        reported_q, normalized_q = extractor.extract_strain_info(quoted)
        self.assertEqual(reported_q, "Solodiol")
        self.assertEqual(normalized_q, "Chemotype III")

    def test_methods_isolation(self):
        title = "Acute Cannabis Administration Transiently Reduces Mitochondrial DNA in Young Adults: Findings from a Secondary Analysis of a Double-Blind, Placebo-Controlled, Randomized Clinical Trial"
        abstract = "Background: Resurgence of research into the effects of plant-derived cannabinoids on mitochondrial health. In particular, a number of studies implicate mitochondrial-Δ9-THC interactions with altered memory, metabolism, and catalepsy in mice. Methods: Blood samples were obtained from a double-blind, placebo-controlled, randomized clinical trial in which adults who regularly use cannabis were randomized. Results: We found that active cannabis was associated with an acute reduction."
        
        study_type = extractor.infer_study_type(title, abstract)
        population = extractor.infer_population(title, abstract, study_type)
        
        self.assertEqual(study_type, ["Clinical (RCT)"])
        self.assertEqual(population, ["human"])

    def test_animal_population_with_adult_keywords(self):
        title = "Effects of cannabis smoke and oral Δ9THC on cognition in young adult and aged rats"
        abstract = "OBJECTIVES: The current study was designed to determine how cannabis influences multiple forms of cognition in young adult and aged rats of both sexes... METHODS: Rats were exposed acutely to cannabis smoke..."
        
        study_type = extractor.infer_study_type(title, abstract)
        population = extractor.infer_population(title, abstract, study_type)
        
        self.assertEqual(study_type, ["Animal Models (rat)"])
        self.assertEqual(population, ["rat"]) # Should NOT include "human")

    def test_get_methods_text_restrictions(self):
        title = "My Cannabis Study"
        
        # Case 1: Structured abstract with METHODS header -> should isolate METHODS
        abstract_structured = "BACKGROUND: Intro text. METHODS: We vaped dry flower. RESULTS: Significant changes in cells. CONCLUSIONS: Vaping is bad."
        methods_text = extractor.get_methods_text(title, abstract_structured)
        self.assertNotIn("BACKGROUND: Intro text", methods_text)
        self.assertIn("METHODS: We vaped dry flower", methods_text)
        self.assertNotIn("RESULTS: Significant changes in cells", methods_text)
        self.assertNotIn("CONCLUSIONS: Vaping is bad", methods_text)
        
        # Case 2: Structured abstract without METHODS header -> should fall back to BACKGROUND and RESULTS
        abstract_no_methods = "BACKGROUND: Intro text. RESULTS: Significant changes in cells. CONCLUSIONS: Vaping is bad."
        methods_text_no_methods = extractor.get_methods_text(title, abstract_no_methods)
        self.assertIn("BACKGROUND: Intro text", methods_text_no_methods)
        self.assertIn("RESULTS: Significant changes in cells", methods_text_no_methods)
        self.assertNotIn("CONCLUSIONS: Vaping is bad", methods_text_no_methods)
        
        # Case 3: Unstructured abstract with conclusion -> should strip conclusion
        abstract_unstructured = "We studied the effects of vaporized cannabinoids in cell cultures. In conclusion, vaping causes damage."
        methods_text_unstructured = extractor.get_methods_text(title, abstract_unstructured)
        self.assertIn("We studied the effects of vaporized cannabinoids in cell cultures", methods_text_unstructured)
        self.assertNotIn("In conclusion, vaping causes damage", methods_text_unstructured)

    def test_heuristic_summary(self):
        data_with_strain = {
            "study_type": "RCT",
            "cannabis_type": "dried flower",
            "exposure_method": "inhaled",
            "population": "human",
            "strain_reported": "Sour Diesel",
        }
        summary_with = extractor.generate_heuristic_summary(data_with_strain)
        self.assertIn("This is an RCT study", summary_with)
        self.assertIn("dried flower cannabis administration", summary_with)
        self.assertIn("via inhaled in human models", summary_with)
        self.assertIn("Reported strain: Sour Diesel.", summary_with)

        data_no_strain = {
            "study_type": "animal",
            "cannabis_type": "pure cannabinoid",
            "exposure_method": "injection cannabinoids",
            "population": "mouse",
            "strain_reported": None,
        }
        summary_no = extractor.generate_heuristic_summary(data_no_strain)
        self.assertIn("This is an animal study", summary_no)
        self.assertIn("pure cannabinoid cannabis administration", summary_no)
        self.assertIn("via injection cannabinoids in mouse models", summary_no)
        self.assertIn("No specific strain was specified.", summary_no)


class TestDatabaseManager(unittest.TestCase):
    """Test cases for SQLite dynamic operations, FTS5 sync, and CRUD."""

    def setUp(self):
        # Initialize an isolated temporary file-based database for testing
        self.test_db_path = "test_cannabis_papers.db"
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except Exception:
                pass
        self.db = DatabaseManager(self.test_db_path)
        
    def tearDown(self):
        # Clean up temporary database file
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except Exception:
                pass
        
    def test_db_crud_and_fts(self):
        # Build mock paper payload
        mock_paper = {
            "pmid": "123456",
            "doi": "10.1001/cannabis.2026",
            "title": "Impact of THC on Anxiety Outcomes",
            "authors": ["Researcher Alice", "Scientist Bob"],
            "journal": "Journal of Cannabinoid Science",
            "year": 2026,
            "abstract": "This RCT clinical trial evaluated vaporized Bedrocan for acute anxiety pain relief.",
            "study_type": "RCT",
            "exposure_method": "vaporized",
            "thc_pct": 19.0,
            "cbd_pct": 1.0,
            "dose_mg": 10.0,
            "strain_reported": "Bedrocan",
            "strain_normalized": "Chemotype I",
            "duration_days": 1.0,
            "population": "human",
            "sample_size": 120,
            "outcome_domain": ["anxiety", "pain"],
            "methodological_quality_flags": ["self_report_only"],
            "methodological_quality_score": 8,
            "open_access": 1,
            "citation_count": 5,
            "publication_date": "2026-05-20",
            "summary": "This is an RCT study investigating vaporized Bedrocan in human subjects."
        }
        
        # 1. Insert Paper
        row_id = self.db.insert_paper(mock_paper)
        self.assertIsNotNone(row_id)
        
        # 2. Retrieve and Validate Fields
        saved = self.db.get_paper(row_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["pmid"], "123456")
        self.assertEqual(saved["title"], "Impact of THC on Anxiety Outcomes")
        self.assertEqual(saved["study_type"], "RCT")
        self.assertEqual(saved["outcome_domain"], ["anxiety", "pain"])
        self.assertEqual(saved["summary"], "This is an RCT study investigating vaporized Bedrocan in human subjects.")
        
        # 3. Test Conflict Resolution (Update instead of duplicate)
        updated_paper = mock_paper.copy()
        updated_paper["citation_count"] = 12
        updated_row_id = self.db.insert_paper(updated_paper)
        self.assertEqual(row_id, updated_row_id)
        
        saved_updated = self.db.get_paper(row_id)
        self.assertEqual(saved_updated["citation_count"], 12)
        
        # 4. Search and filter - FTS5 Text search
        results_fts = self.db.search_papers({"query": "Bedrocan anxiety"})
        self.assertEqual(len(results_fts), 1)
        self.assertEqual(results_fts[0]["id"], row_id)
        
        # FTS5 search by author
        results_author = self.db.search_papers({"query": "Alice"})
        self.assertEqual(len(results_author), 1)
        self.assertEqual(results_author[0]["id"], row_id)
        
        # 5. Dynamic filter: Study design and population
        results_filter = self.db.search_papers({
            "study_type": "RCT",
            "population": "human",
            "thc_min": 10.0,
            "outcome": "anxiety",
            "flags": "+self_report_only"
        })
        self.assertEqual(len(results_filter), 1)
        
        # Filter against flag
        results_filter_against = self.db.search_papers({
            "flags": "-self_report_only"
        })
        self.assertEqual(len(results_filter_against), 0)

        # Test citation_min filter
        results_cites_match = self.db.search_papers({
            "citations_min": 10
        })
        self.assertEqual(len(results_cites_match), 1)

        results_cites_miss = self.db.search_papers({
            "citations_min": 15
        })
        self.assertEqual(len(results_cites_miss), 0)

        # Test recent filter
        results_recent = self.db.search_papers({
            "recent": True
        })
        self.assertEqual(len(results_recent), 1)

        count_recent = self.db.count_papers({
            "recent": True
        })
        self.assertEqual(count_recent, 1)

        # Test case studies & editorials search routing
        case_study_paper = mock_paper.copy()
        case_study_paper["pmid"] = "222222"
        case_study_paper["doi"] = "10.1001/case"
        case_study_paper["study_type"] = "case study"
        case_study_paper["publication_type"] = "case study"
        case_row_id = self.db.insert_paper(case_study_paper)

        editorial_paper = mock_paper.copy()
        editorial_paper["pmid"] = "333333"
        editorial_paper["doi"] = "10.1001/ed"
        editorial_paper["study_type"] = "editorial"
        editorial_paper["publication_type"] = "editorial"
        ed_row_id = self.db.insert_paper(editorial_paper)

        # tab = "original" should exclude review, meta-analysis, case study, and editorial
        original_tab_results = self.db.search_papers({"tab": "original"})
        self.assertEqual(len(original_tab_results), 1) # Only mock_paper which is "RCT"
        self.assertEqual(original_tab_results[0]["id"], row_id)

        # tab = "review" should include case study and editorial
        review_tab_results = self.db.search_papers({"tab": "review"})
        self.assertEqual(len(review_tab_results), 2)
        review_pids = {p["pmid"] for p in review_tab_results}
        self.assertIn("222222", review_pids)
        self.assertIn("333333", review_pids)

        # clean up case and editorial test papers
        self.db.delete_paper(case_row_id)
        self.db.delete_paper(ed_row_id)
        
        # 6. Delete
        deleted = self.db.delete_paper(row_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.db.get_paper(row_id))

    def test_multiple_classifications_search(self):
        """Test that multiple classifications on a single paper can be searched successfully via OR logic."""
        mock_multi_paper = {
            "pmid": "987654",
            "doi": "10.1001/cannabis.multi",
            "title": "Cannabis vaping elicits transcriptomic and metabolomic changes",
            "authors": ["Scientist Charlie"],
            "journal": "Nature Scientific Reports",
            "year": 2026,
            "abstract": "This study uses both cannabis smoke and cannabis vapor conditioned media.",
            "study_type": ["in vitro"],
            "exposure_method": ["exposure of cells to smoke/vapor", "smoke/vapor conditioned media"],
            "cannabis_type": ["dried flower", "vape pen"],
            "population": ["cell_line"],
            "date_harvested": "2026-06-03"
        }
        row_id = self.db.insert_paper(mock_multi_paper)
        self.assertIsNotNone(row_id)
        
        try:
            # 1. Search by cannabis_type = dried flower
            res1 = self.db.search_papers({"cannabis_type": "dried flower"})
            self.assertEqual(len(res1), 1)
            self.assertEqual(res1[0]["id"], row_id)
            
            # 2. Search by cannabis_type = vape pen
            res2 = self.db.search_papers({"cannabis_type": "vape pen"})
            self.assertEqual(len(res2), 1)
            self.assertEqual(res2[0]["id"], row_id)
            
            # 3. Search by exposure_method = smoke/vapor conditioned media
            res3 = self.db.search_papers({"exposure_method": "smoke/vapor conditioned media"})
            self.assertEqual(len(res3), 1)
            self.assertEqual(res3[0]["id"], row_id)
            
            # 4. Search by study_type = in vitro
            res4 = self.db.search_papers({"study_type": "in vitro"})
            self.assertEqual(len(res4), 1)
            self.assertEqual(res4[0]["id"], row_id)
        finally:
            self.db.delete_paper(row_id)

    def test_and_or_toggles_search(self):
        """Test that AND/OR search logic options work correctly on list filters."""
        # Insert two papers
        paper_a = {
            "pmid": "900001",
            "title": "Study A",
            "authors": ["Author A"],
            "journal": "Journal A",
            "year": 2026,
            "study_type": ["Clinical (observational)", "Cell Culture (cell lines)"],
            "cannabis_type": ["dried flower", "vape pen"],
            "population": ["human", "cell_line"],
            "outcome_domain": ["pain", "anxiety"]
        }
        paper_b = {
            "pmid": "900002",
            "title": "Study B",
            "authors": ["Author B"],
            "journal": "Journal B",
            "year": 2026,
            "study_type": ["Clinical (observational)"],
            "cannabis_type": ["dried flower"],
            "population": ["human"],
            "outcome_domain": ["pain"]
        }
        id_a = self.db.insert_paper(paper_a)
        id_b = self.db.insert_paper(paper_b)
        
        try:
            # OR logic searches (default or logic=or)
            res_or_cannabis = self.db.search_papers({
                "cannabis_type": "dried flower,vape pen",
                "cannabis_logic": "or"
            })
            # Both papers have 'dried flower', so both should match OR search
            self.assertEqual(len(res_or_cannabis), 2)
            
            # AND logic searches
            res_and_cannabis = self.db.search_papers({
                "cannabis_type": "dried flower,vape pen",
                "cannabis_logic": "and"
            })
            # Only paper_a has both, so only paper_a should match AND search
            self.assertEqual(len(res_and_cannabis), 1)
            self.assertEqual(res_and_cannabis[0]["id"], id_a)
            
            # Outcome OR logic
            res_or_outcome = self.db.search_papers({
                "outcome": "pain,anxiety",
                "outcome_logic": "or"
            })
            self.assertEqual(len(res_or_outcome), 2)
            
            # Outcome AND logic
            res_and_outcome = self.db.search_papers({
                "outcome": "pain,anxiety",
                "outcome_logic": "and"
            })
            self.assertEqual(len(res_and_outcome), 1)
            self.assertEqual(res_and_outcome[0]["id"], id_a)

            # Study type OR logic
            res_or_study = self.db.search_papers({
                "study_type": "Clinical (observational),Cell Culture (cell lines)",
                "study_logic": "or"
            })
            self.assertEqual(len(res_or_study), 2)
            
            # Study type AND logic
            res_and_study = self.db.search_papers({
                "study_type": "Clinical (observational),Cell Culture (cell lines)",
                "study_logic": "and"
            })
            self.assertEqual(len(res_and_study), 1)
            self.assertEqual(res_and_study[0]["id"], id_a)
            
        finally:
            self.db.delete_paper(id_a)
            self.db.delete_paper(id_b)

    def test_recents_timeframe_search(self):
        """Test that the recent_range filters (today, week, month) work correctly on date_harvested."""
        from datetime import datetime as dt, timedelta as td
        
        now = dt.now()
        
        # Paper harvested today
        paper_today = {
            "pmid": "950001",
            "title": "Today Study",
            "authors": ["Author A"],
            "journal": "Journal A",
            "year": 2026,
            "date_harvested": now.strftime("%Y-%m-%d") + "T10:00:00"
        }
        # Paper harvested 5 days ago
        paper_week = {
            "pmid": "950002",
            "title": "Week Study",
            "authors": ["Author B"],
            "journal": "Journal B",
            "year": 2026,
            "date_harvested": (now - td(days=5)).strftime("%Y-%m-%d") + "T10:00:00"
        }
        # Paper harvested 15 days ago
        paper_month = {
            "pmid": "950003",
            "title": "Month Study",
            "authors": ["Author C"],
            "journal": "Journal C",
            "year": 2026,
            "date_harvested": (now - td(days=15)).strftime("%Y-%m-%d") + "T10:00:00"
        }
        # Paper harvested 45 days ago
        paper_old = {
            "pmid": "950004",
            "title": "Old Study",
            "authors": ["Author D"],
            "journal": "Journal D",
            "year": 2026,
            "date_harvested": (now - td(days=45)).strftime("%Y-%m-%d") + "T10:00:00"
        }
        
        id_today = self.db.insert_paper(paper_today)
        id_week = self.db.insert_paper(paper_week)
        id_month = self.db.insert_paper(paper_month)
        id_old = self.db.insert_paper(paper_old)
        
        try:
            # 1. Search recent_range = today
            res_today = self.db.search_papers({
                "tab": "recent",
                "recent_range": "today"
            })
            pmids_today = {p["pmid"] for p in res_today}
            self.assertIn("950001", pmids_today)
            self.assertNotIn("950002", pmids_today)
            
            # 2. Search recent_range = week
            res_week = self.db.search_papers({
                "tab": "recent",
                "recent_range": "week"
            })
            pmids_week = {p["pmid"] for p in res_week}
            self.assertIn("950001", pmids_week)
            self.assertIn("950002", pmids_week)
            self.assertNotIn("950003", pmids_week)
            
            # 3. Search recent_range = month
            res_month = self.db.search_papers({
                "tab": "recent",
                "recent_range": "month"
            })
            pmids_month = {p["pmid"] for p in res_month}
            self.assertIn("950001", pmids_month)
            self.assertIn("950002", pmids_month)
            self.assertIn("950003", pmids_month)
            self.assertNotIn("950004", pmids_month)
            
        finally:
            self.db.delete_paper(id_today)
            self.db.delete_paper(id_week)
            self.db.delete_paper(id_month)
            self.db.delete_paper(id_old)

    def test_default_sorting_by_quality(self):
        # Insert papers with different quality scores
        paper_low = {
            "pmid": "1001",
            "doi": "10.1001/1",
            "title": "Low Quality Paper",
            "authors": ["Author A"],
            "journal": "bioRxiv preprint server",
            "year": 2025,
            "abstract": "Anxiety CBD test.",
            "study_type": "observational",
            "exposure_method": "oral/edible",
            "thc_pct": 0.0,
            "cbd_pct": 5.0,
            "dose_mg": None,
            "strain_reported": None,
            "strain_normalized": None,
            "duration_days": 10.0,
            "population": "animal",
            "sample_size": 10,
            "outcome_domain": ["anxiety"],
            "methodological_quality_flags": ["no_strain_specified", "THC_not_quantified", "no_control_group"],
            "methodological_quality_score": 2,
            "open_access": 0,
            "citation_count": 0,
            "publication_date": "2025-01-01"
        }
        paper_high = {
            "pmid": "1002",
            "doi": "10.1001/2",
            "title": "High Quality Paper",
            "authors": ["Author B"],
            "journal": "Nature",
            "year": 2026,
            "abstract": "Anxiety CBD test.",
            "study_type": "RCT",
            "exposure_method": "vaporized",
            "thc_pct": 10.0,
            "cbd_pct": 10.0,
            "dose_mg": 20.0,
            "strain_reported": "Bediol",
            "strain_normalized": "Chemotype II",
            "duration_days": 30.0,
            "population": "human",
            "sample_size": 200,
            "outcome_domain": ["anxiety"],
            "methodological_quality_flags": [],
            "methodological_quality_score": 18,
            "open_access": 1,
            "citation_count": 50,
            "publication_date": "2026-03-15"
        }
        
        self.db.insert_paper(paper_low)
        self.db.insert_paper(paper_high)
        
        # Test 1: Sorting without a search query
        results = self.db.search_papers({})
        self.assertEqual(len(results), 2)
        # Should be ordered by methodological_quality_score DESC first
        self.assertEqual(results[0]["pmid"], "1002") # High quality paper
        self.assertEqual(results[1]["pmid"], "1001") # Low quality paper

        # Test 2: Sorting with a search query
        results_query = self.db.search_papers({"query": "Anxiety"})
        self.assertEqual(len(results_query), 2)
        # Should still be ordered by methodological_quality_score DESC first
        self.assertEqual(results_query[0]["pmid"], "1002")
        self.assertEqual(results_query[1]["pmid"], "1001")

    def test_dynamic_column_sorting(self):
        # Insert papers with different attributes
        paper_1 = {
            "pmid": "2001",
            "doi": "10.1001/p1",
            "title": "Alpha Paper",
            "authors": ["Author A"],
            "journal": "Nature",
            "year": 2020,
            "abstract": "Anxiety CBD test.",
            "study_type": "observational",
            "exposure_method": "oral/edible",
            "thc_pct": 0.0,
            "cbd_pct": 5.0,
            "dose_mg": None,
            "strain_reported": None,
            "strain_normalized": None,
            "duration_days": 5.0,
            "population": "animal",
            "sample_size": 10,
            "outcome_domain": ["anxiety"],
            "methodological_quality_flags": [],
            "methodological_quality_score": 10,
            "open_access": 0,
            "citation_count": 5,
            "publication_date": "2020-01-01"
        }
        paper_2 = {
            "pmid": "2002",
            "doi": "10.1001/p2",
            "title": "Beta Paper",
            "authors": ["Author B"],
            "journal": "Science",
            "year": 2025,
            "abstract": "Anxiety CBD test.",
            "study_type": "RCT",
            "exposure_method": "vaporized",
            "thc_pct": 10.0,
            "cbd_pct": 10.0,
            "dose_mg": 20.0,
            "strain_reported": "Bediol",
            "strain_normalized": "Chemotype II",
            "duration_days": 25.0,
            "population": "human",
            "sample_size": 200,
            "outcome_domain": ["anxiety"],
            "methodological_quality_flags": [],
            "methodological_quality_score": 15,
            "open_access": 1,
            "citation_count": 40,
            "publication_date": "2025-03-15"
        }
        
        self.db.insert_paper(paper_1)
        self.db.insert_paper(paper_2)
        
        # Test 1: Sort by year ASC
        res = self.db.search_papers({"sort_by": "year", "sort_dir": "ASC"})
        self.assertEqual(res[0]["pmid"], "2001") # 2020
        self.assertEqual(res[1]["pmid"], "2002") # 2025
        
        # Test 2: Sort by year DESC
        res = self.db.search_papers({"sort_by": "year", "sort_dir": "DESC"})
        self.assertEqual(res[0]["pmid"], "2002") # 2025
        self.assertEqual(res[1]["pmid"], "2001") # 2020

        # Test 3: Sort by citations ASC
        res = self.db.search_papers({"sort_by": "citations", "sort_dir": "ASC"})
        self.assertEqual(res[0]["pmid"], "2001") # 5 citations
        self.assertEqual(res[1]["pmid"], "2002") # 40 citations

        # Test 4: Sort by citations DESC
        res = self.db.search_papers({"sort_by": "citations", "sort_dir": "DESC"})
        self.assertEqual(res[0]["pmid"], "2002") # 40 citations
        self.assertEqual(res[1]["pmid"], "2001") # 5 citations

        # Test 5: Sort by title ASC
        res = self.db.search_papers({"sort_by": "title", "sort_dir": "ASC"})
        self.assertEqual(res[0]["title"], "Alpha Paper")
        self.assertEqual(res[1]["title"], "Beta Paper")

        # Test 6: Sort by title DESC
        res = self.db.search_papers({"sort_by": "title", "sort_dir": "DESC"})
        self.assertEqual(res[0]["title"], "Beta Paper")
        self.assertEqual(res[1]["title"], "Alpha Paper")

        # Test 7: Sort by duration ASC
        res = self.db.search_papers({"sort_by": "duration", "sort_dir": "ASC"})
        self.assertEqual(res[0]["pmid"], "2001") # 5 days
        self.assertEqual(res[1]["pmid"], "2002") # 25 days

        # Test 8: Sort by study_type DESC
        res = self.db.search_papers({"sort_by": "study_type", "sort_dir": "DESC"})
        # "observational" (paper_1) vs "RCT" (paper_2). "observational" comes first in DESC
        self.assertEqual(res[0]["pmid"], "2001")
        self.assertEqual(res[1]["pmid"], "2002")

    def test_publication_type_tab_routing(self):
        # Insert one original research and one review/case study with publication_type
        paper_original = {
            "pmid": "900001",
            "doi": "10.1001/901",
            "title": "Clinical Trial on CBD",
            "authors": ["Author X"],
            "journal": "JAMA",
            "year": 2026,
            "abstract": "Anxiety CBD test.",
            "publication_type": "original research",
            "study_type": "RCT",
            "exposure_method": "oral/edible",
            "population": "human",
            "outcome_domain": ["anxiety"],
            "methodological_quality_score": 10,
            "open_access": 1,
            "citation_count": 0,
            "publication_date": "2026-01-01"
        }
        paper_review = {
            "pmid": "900002",
            "doi": "10.1001/902",
            "title": "Cannabinoids: A Systematic Review",
            "authors": ["Author Y"],
            "journal": "Lancet",
            "year": 2026,
            "abstract": "This systematic review analyzes anxiety CBD studies.",
            "publication_type": "systematic review",
            "study_type": "review",
            "exposure_method": "oral/edible",
            "population": "human",
            "outcome_domain": ["anxiety"],
            "methodological_quality_score": 5,
            "open_access": 1,
            "citation_count": 0,
            "publication_date": "2026-01-01"
        }
        
        orig_id = self.db.insert_paper(paper_original)
        rev_id = self.db.insert_paper(paper_review)
        
        try:
            # Query original articles tab
            original_tab = self.db.search_papers({"tab": "original"})
            original_pmids = {p["pmid"] for p in original_tab}
            self.assertIn("900001", original_pmids)
            self.assertNotIn("900002", original_pmids)
            
            # Query review articles tab
            review_tab = self.db.search_papers({"tab": "review"})
            review_pmids = {p["pmid"] for p in review_tab}
            self.assertIn("900002", review_pmids)
            self.assertNotIn("900001", review_pmids)

            # Query and sort by publication_type ASC
            res = self.db.search_papers({"sort_by": "publication_type", "sort_dir": "ASC"})
            self.assertEqual(res[0]["pmid"], "900001")
            self.assertEqual(res[1]["pmid"], "900002")

            # Query and sort by publication_type DESC
            res = self.db.search_papers({"sort_by": "publication_type", "sort_dir": "DESC"})
            self.assertEqual(res[0]["pmid"], "900002")
            self.assertEqual(res[1]["pmid"], "900001")
        finally:
            self.db.delete_paper(orig_id)
            self.db.delete_paper(rev_id)

    def test_system_metadata(self):
        """Test system_metadata read/write operations."""
        self.db.set_metadata("test_key", "test_value")
        self.assertEqual(self.db.get_metadata("test_key"), "test_value")
        
        # Test overwrite
        self.db.set_metadata("test_key", "new_value")
        self.assertEqual(self.db.get_metadata("test_key"), "new_value")
        
        # Test default
        self.assertEqual(self.db.get_metadata("non_existent_key", "default_val"), "default_val")
        self.assertIsNone(self.db.get_metadata("non_existent_key"))


class TestFlaskSchedulerAPI(unittest.TestCase):
    """Test cases for the Flask web application routes related to the scheduler."""

    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_scheduler_status_endpoint(self):
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
        response = self.client.get("/api/scheduler/status")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        
        self.assertIn("active", data)
        self.assertIn("last_run_date", data)
        self.assertIn("last_run_timestamp", data)
        self.assertIn("last_run_status", data)
        self.assertEqual(data["query"], "cannabis OR cannabinoid OR marijuana")


class TestQualityScorer(unittest.TestCase):
    """Test cases for the transparent quality scoring logic."""

    def test_score_calculation(self):
        # 1. Base Score = 5
        # Additions: quantified_dose (+2), RCT (+2), large sample (+1), Peer-reviewed journal (+1)
        # Deductions: self_report_only (-2)
        # Total expected: 5 + 2 + 2 + 1 + 1 - 2 = 9
        paper1 = {
            "study_type": "RCT",
            "dose_mg": 25.0,
            "strain_normalized": "Chemotype I",
            "sample_size": 150,
            "journal": "British Journal of Pharmacology",
            "methodological_quality_flags": ["self_report_only"]
        }
        score1 = classifier.calculate_quality_score(paper1)
        self.assertEqual(score1, 9)
        
        # 2. Minimal features
        # Base Score = 5
        # Deductions: no_strain_specified (-1), THC_not_quantified (-2), no_control_group (-2)
        # Total expected: 5 - 1 - 2 - 2 = 0
        paper2 = {
            "study_type": "observational",
            "dose_mg": None,
            "strain_normalized": None,
            "sample_size": 20,
            "journal": "bioRxiv preprint server",
            "methodological_quality_flags": ["no_strain_specified", "THC_not_quantified", "no_control_group"]
        }
        score2 = classifier.calculate_quality_score(paper2)
        self.assertEqual(score2, 0)

        # 3. New additions: multiple_doses (+5) and multiple_time_intervals (+5)
        # Base Score = 5
        # Additions: quantified_dose (+2), RCT (+2), large sample (+1), Peer-reviewed journal (+1), multiple_doses (+5), multiple_time_intervals (+5)
        # Deductions: self_report_only (-2)
        # Total expected: 5 + 2 + 2 + 1 + 1 + 5 + 5 - 2 = 19
        paper3 = {
            "study_type": "RCT",
            "dose_mg": 25.0,
            "strain_normalized": "Chemotype I",
            "sample_size": 150,
            "journal": "British Journal of Pharmacology",
            "methodological_quality_flags": ["self_report_only"],
            "multiple_doses": True,
            "multiple_time_intervals": True
        }
        score3 = classifier.calculate_quality_score(paper3)
        self.assertEqual(score3, 19)

        # 4. Score clamping at 20 max
        # Base Score = 5
        # Additions: quantified_dose (+2), RCT (+2), large sample (+1), Peer-reviewed journal (+1), multiple_doses (+5), multiple_time_intervals (+5)
        # Deductions: None
        # Total expected: 5 + 2 + 2 + 1 + 1 + 5 + 5 = 21 -> Clamped to 20
        paper4 = {
            "study_type": "RCT",
            "dose_mg": 25.0,
            "strain_normalized": "Chemotype I",
            "sample_size": 150,
            "journal": "British Journal of Pharmacology",
            "methodological_quality_flags": [],
            "multiple_doses": True,
            "multiple_time_intervals": True
        }
        score4 = classifier.calculate_quality_score(paper4)
        self.assertEqual(score4, 20)


class TestIntelligentHarvest(unittest.TestCase):
    """Test cases for biology-driven context-aware relevance pre-filtering heuristics."""

    def test_valid_cannabis_paper(self):
        title = "Effects of Cannabidiol (CBD) on Sleep Quality"
        abstract = "We administered CBD oil to healthy volunteers and monitored actigraphy outcomes."
        is_relevant, reason = extractor.is_cannabis_related(title, abstract)
        self.assertTrue(is_relevant)
        self.assertEqual(reason, "Valid cannabis context.")

    def test_unrelated_acronym_collisions(self):
        # Bile duct collision
        title1 = "Diameter of the common bile duct (CBD)"
        abstract1 = "We measured CBD size under ultrasound in 150 patients undergoing cholecystectomy."
        is_relevant1, reason1 = extractor.is_cannabis_related(title1, abstract1)
        self.assertFalse(is_relevant1)
        self.assertIn("Common Bile Duct", reason1)

        # Corticobasal Degeneration collision
        title2 = "Clinical progression in Corticobasal Degeneration (CBD)"
        abstract2 = "CBD is a rare tauopathy characterized by asymmetric parkinsonism."
        is_relevant2, reason2 = extractor.is_cannabis_related(title2, abstract2)
        self.assertFalse(is_relevant2)
        self.assertIn("Corticobasal Degeneration", reason2)

        # Central Business District collision
        title3 = "Air quality in the central business district (CBD) of Toronto"
        abstract3 = "High concentration of metropolitan vehicle exhaust in the urban city center."
        is_relevant3, reason3 = extractor.is_cannabis_related(title3, abstract3)
        self.assertFalse(is_relevant3)
        self.assertIn("Central Business District", reason3)

    def test_positive_override(self):
        # A paper mentioning CBD (common bile duct) but also containing cannabinoid / cannabis positive context
        title = "Expression of CB1 and CB2 Cannabinoid Receptors in the Common Bile Duct"
        abstract = "This study analyzed cannabinoid receptor CB1/CB2 distribution in human common bile duct tissues."
        is_relevant, reason = extractor.is_cannabis_related(title, abstract)
        self.assertTrue(is_relevant)
        self.assertEqual(reason, "Valid cannabis context.")


if __name__ == "__main__":
    unittest.main()

