# test_suite.py
import unittest
import os
import json
import sqlite3
from pathlib import Path
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
        title_pub_meta = "Another Cannabis Study"
        abstract_pub_meta = "Publication Type: Meta-Analysis. This is a study."

        self.assertEqual(extractor.infer_study_type(title, abstract_rct), ["Clinical (RCT)"])
        self.assertEqual(extractor.infer_study_type(title, abstract_animal), ["Animal Models (Rat)"])
        self.assertEqual(extractor.infer_study_type(title, abstract_invitro), ["Cell Culture (Other In Vitro)"])
        self.assertEqual(extractor.infer_study_type(title, abstract_casestudy), ["case study"])
        self.assertEqual(extractor.infer_study_type(title, abstract_editorial), ["editorial"])
        self.assertEqual(extractor.infer_study_type(title_manifold, abstract_manifold), ["Cell Culture (Other In Vitro)"])
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
        
        self.assertEqual(extractor.infer_exposure_method(title, abstract_vape, "Clinical (RCT)"), ["inhaled"])
        self.assertEqual(extractor.infer_exposure_method(title, abstract_smoke, "Clinical (observational)"), ["inhaled"])
        self.assertEqual(extractor.infer_exposure_method(title, abstract_oral, "Animal Models (Rat)"), ["oral administration"])

        # In vitro ALI lung epithelial cells paper (dissolved -> exposure to smoke/vapor)
        title_ali = "In vitro Cannabis Exposures of Lung Epithelial Cells at the Air-Liquid Interface"
        abstract_ali = "Lung epithelial cells were exposed to cannabis vapor directly at the air-liquid interface."
        self.assertEqual(extractor.infer_exposure_method(title_ali, abstract_ali, "Cell Culture (Other In Vitro)"), ["exposure of cells to smoke/vapor"])

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
        
        self.assertEqual(study_type, ["Clinical (RCT)"])

    def test_animal_study_with_adult_keywords(self):
        title = "Effects of cannabis smoke and oral Δ9THC on cognition in young adult and aged rats"
        abstract = "OBJECTIVES: The current study was designed to determine how cannabis influences multiple forms of cognition in young adult and aged rats of both sexes... METHODS: Rats were exposed acutely to cannabis smoke..."
        
        study_type = extractor.infer_study_type(title, abstract)
        
        self.assertEqual(study_type, ["Animal Models (Rat)"])

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
            "strain_reported": "Sour Diesel",
        }
        summary_with = extractor.generate_heuristic_summary(data_with_strain)
        self.assertIn("This is an RCT study", summary_with)
        self.assertIn("dried flower cannabis administration", summary_with)
        self.assertIn("Reported strain: Sour Diesel.", summary_with)

        data_no_strain = {
            "study_type": "animal",
            "cannabis_type": "pure cannabinoid",
            "exposure_method": "injection cannabinoids",
            "strain_reported": None,
        }
        summary_no = extractor.generate_heuristic_summary(data_no_strain)
        self.assertIn("This is an animal study", summary_no)
        self.assertIn("pure cannabinoid cannabis administration", summary_no)
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
        if self.db.is_postgres:
            self.db.clear_all_tables()
        
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
            "sample_size": 120,
            "outcome_domain": ["anxiety", "pain"],
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
        
        # 5. Dynamic filter: Study design
        results_filter = self.db.search_papers({
            "study_type": "RCT",
            "thc_min": 10.0,
            "outcome": "anxiety"
        })
        self.assertEqual(len(results_filter), 1)

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

    def test_db_dynamic_cannabinoid_fields(self):
        mock_paper = {
            "pmid": "654321",
            "doi": "10.1001/cannabis.2026.dynamic",
            "title": "Dynamic Cannabinoid Testing Paper",
            "authors": ["Researcher Alice"],
            "journal": "Journal of Dynamic Science",
            "year": 2026,
            "abstract": "This concentrates study evaluates cannabinoid content.",
            "study_type": ["Clinical (RCT)"],
            "exposure_method": ["vaporized"],
            "cannabis_type": ["concentrates", "edibles"],
            "thc_pct": 15.0,
            "cbd_pct": 5.0,
            "puff_count": 8,
            "thc_mg_ml": 25.0,
            "thc_mg_g": 2.0,
            "thc_mg_kg": 5.0,
            "cbd_mg_ml": 10.0,
            "cbd_mg_g": 0.5,
            "cbd_mg_kg": 1.5,
            "summary": "Concentrates and edibles study."
        }
        row_id = self.db.insert_paper(mock_paper)
        self.assertIsNotNone(row_id)
        
        saved = self.db.get_paper(row_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["puff_count"], 8)
        self.assertEqual(saved["thc_mg_ml"], 25.0)
        self.assertEqual(saved["thc_mg_g"], 2.0)
        self.assertEqual(saved["thc_mg_kg"], 5.0)
        self.assertEqual(saved["cbd_mg_ml"], 10.0)
        self.assertEqual(saved["cbd_mg_g"], 0.5)
        self.assertEqual(saved["cbd_mg_kg"], 1.5)
        
        self.db.delete_paper(row_id)

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
            "study_type": ["Clinical (observational)", "Cell Culture (Cell Lines)"],
            "cannabis_type": ["dried flower", "vape pen"],
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
                "study_type": "Clinical (observational),Cell Culture (Cell Lines)",
                "study_logic": "or"
            })
            self.assertEqual(len(res_or_study), 2)
            
            # Study type AND logic
            res_and_study = self.db.search_papers({
                "study_type": "Clinical (observational),Cell Culture (Cell Lines)",
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
            "sample_size": 10,
            "outcome_domain": ["anxiety"],
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
            "sample_size": 200,
            "outcome_domain": ["anxiety"],
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
            "outcome_domain": ["anxiety"],
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
            "outcome_domain": ["anxiety"],
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


class TestExpertEditAndActiveLearning(unittest.TestCase):
    """Test cases for the expert edit endpoint, dynamic few-shot retrieval, locked fields, and confidence estimation."""

    def setUp(self):
        self.test_db_path = "test_rl_papers.db"
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except Exception:
                pass
        self.db = DatabaseManager(self.test_db_path)
        if self.db.is_postgres:
            self.db.clear_all_tables()
        
        # Monkey patch DatabaseManager.__init__ to enforce test_db_path on all instantiations
        from db_manager import DatabaseManager as DBManagerClass
        self.original_init = DBManagerClass.__init__
        test_db_path = self.test_db_path
        
        def patched_init(self, db_path=None):
            self.db_path = db_path if db_path is not None else test_db_path
            # Ensure the parent directory for the database exists
            dir_name = os.path.dirname(self.db_path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            self.init_db()
            
        DBManagerClass.__init__ = patched_init
        
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        from db_manager import DatabaseManager as DBManagerClass
        DBManagerClass.__init__ = self.original_init
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except Exception:
                pass

    def test_audit_logging_and_field_locking(self):
        # Insert a paper
        paper_id = self.db.insert_paper({
            "pmid": "999888",
            "title": "Trial of CBD for Sleep",
            "abstract": "This was a clinical trial evaluating CBD for sleep quality.",
            "study_type": ["review"],
            "exposure_method": ["oral/edible"],
            "classification_confidence": 0.9,
            "classifier_version": "1.0.0"
        })
        
        # Edit the paper classification via API
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["email"] = "shawnmckenzie11.sm@gmail.com"
            
        payload = {
            "study_type": ["Clinical (RCT)"],
            "exposure_method": ["tincture"]
        }
        
        response = self.client.post(
            f"/api/papers/{paper_id}/edit-classification",
            json=payload
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        
        # Assert lock fields list
        self.assertIn("study_type", data["locked_fields"])
        self.assertIn("exposure_method", data["locked_fields"])
        
        # Assert paper updated in DB
        updated_paper = self.db.get_paper(paper_id)
        self.assertEqual(updated_paper["study_type"], ["Clinical (RCT)"])
        self.assertEqual(updated_paper["exposure_method"], ["tincture"])
        self.assertEqual(sorted(updated_paper["expert_locked_fields"]), sorted(["study_type", "exposure_method"]))
        
        # Assert feedback_audit record created
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM feedback_audit WHERE paper_id = ? ORDER BY id ASC", (paper_id,))
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        self.assertEqual(len(rows), 2)
        # Check first audit log entry
        self.assertEqual(rows[0]["field_name"], "study_type")
        self.assertEqual(json.loads(rows[0]["old_value"]), ["review"])
        self.assertEqual(json.loads(rows[0]["new_value"]), ["Clinical (RCT)"])
        
        # Check second audit log entry
        self.assertEqual(rows[1]["field_name"], "exposure_method")
        self.assertEqual(json.loads(rows[1]["old_value"]), ["oral/edible"])
        self.assertEqual(json.loads(rows[1]["new_value"]), ["tincture"])

    def test_reclassify_respects_locked_fields(self):
        # Insert a paper
        paper_id = self.db.insert_paper({
            "pmid": "777666",
            "title": "Randomized controlled trial of vaporized cannabis in humans",
            "abstract": "We conducted a double-blind, randomized controlled trial of smoked vs vaporized cannabis.",
            "study_type": ["review"],
            "expert_locked_fields": json.dumps(["study_type"])
        })
        
        # Run reclassifier
        import reclassify_metadata
        reclassify_metadata.reclassify_all_papers()
        
        # Retrieve updated paper
        updated = self.db.get_paper(paper_id)
        
        # Assert that study_type was NOT updated (remained review) because it is locked
        self.assertEqual(updated["study_type"], ["review"])
        
        # Assert that exposure_method was updated because it was NOT locked (should be populated)
        self.assertTrue(len(updated["exposure_method"]) > 0)

    def test_dynamic_few_shot_retrieval(self):
        # Insert valid paper first to satisfy the FK constraint
        paper_id = self.db.insert_paper({
            "pmid": "123123",
            "title": "In vitro microglial viability after cannabinoid treatment",
            "abstract": "This study used cell lines to evaluate microglial survival.",
            "study_type": ["review"]
        })
        
        # Setup an audit correction manually
        conn = self.db.get_connection()
        conn.execute(
            """
            INSERT INTO feedback_audit (
                paper_id, field_name, old_value, new_value, title, abstract, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                paper_id, "study_type", '["review"]', '["Cell Culture (Cell Lines)"]',
                "In vitro microglial viability after cannabinoid treatment",
                "This study used cell lines to evaluate microglial survival.",
                "2026-06-05T00:00:00"
            )
        )
        conn.commit()
        conn.close()
        
        # Run dynamic retrieval
        few_shot_text, sim = classifier.get_few_shot_examples(
            "Evaluation of cell line microglial cell viability",
            "We treated microglial cell lines with THC in vitro."
        )
        retrieved_text, retrieved_sim, bm25_used, example_count = classifier.retrieve_few_shot_context(
            "Evaluation of cell line microglial cell viability",
            "We treated microglial cell lines with THC in vitro."
        )
        
        self.assertTrue(sim > 0.0)
        self.assertIn("Expert Guidance & Corrections", few_shot_text)
        self.assertIn("Incorrect study_type Classification", few_shot_text)
        self.assertIn('["Cell Culture (Cell Lines)"]', few_shot_text)
        self.assertTrue(bm25_used)
        self.assertEqual(example_count, 1)
        self.assertEqual(retrieved_text, few_shot_text)

    def test_rule_optimizer_field_group_scoring_and_escalation(self):
        import rule_optimizer

        baseline = {"relevance": 0.2, "extraction": 0.4}
        improved = {"relevance": 0.1, "extraction": 0.35}
        regressed = {"relevance": 0.25, "extraction": 0.5}

        improved_eval = rule_optimizer.evaluate_optimization_candidate(baseline, improved)
        self.assertTrue(improved_eval["gate_passed"])
        self.assertTrue(improved_eval["accepted"])

        regressed_eval = rule_optimizer.evaluate_optimization_candidate(baseline, regressed)
        self.assertFalse(regressed_eval["gate_passed"])
        self.assertFalse(regressed_eval["accepted"])

        paper_scores = rule_optimizer.score_field_groups(
            {"publication_type": "review", "study_type": ["review"], "exposure_method": ["unknown"]},
            {"publication_type": "original research", "study_type": ["Animal Models (Rat)"], "exposure_method": ["injection cannabinoids"]},
        )
        self.assertEqual(paper_scores["relevance"], 1.0)
        self.assertEqual(paper_scores["extraction"], 1.0)

        self.db.set_metadata("optimization_failed_attempts", "0")
        first = rule_optimizer.record_optimization_result(self.db, regressed_eval, patch_summary={"cue": "test"})
        second = rule_optimizer.record_optimization_result(self.db, regressed_eval, patch_summary={"cue": "test"})
        third = rule_optimizer.record_optimization_result(self.db, regressed_eval, patch_summary={"cue": "test"})
        self.assertEqual(first["status"], "rejected")
        self.assertEqual(third["status"], "needs_human_review")

        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT status, failed_attempts, field_group_scores FROM optimization_log ORDER BY id DESC LIMIT 1")
        row = dict(cursor.fetchone())
        conn.close()
        self.assertEqual(row["status"], "needs_human_review")
        self.assertGreaterEqual(row["failed_attempts"], 3)
        field_group_scores = json.loads(row["field_group_scores"])
        self.assertIn("relevance", field_group_scores)

    def test_jaccard_similarity_and_confidence(self):
        # Test jaccard similarity
        self.assertEqual(classifier.jaccard_similarity(["a", "b"], ["b", "a"]), 1.0)
        self.assertEqual(classifier.jaccard_similarity(["a"], ["a", "b"]), 0.5)
        self.assertEqual(classifier.jaccard_similarity("review", "review"), 1.0)
        self.assertEqual(classifier.jaccard_similarity("review", "editorial"), 0.0)
        self.assertEqual(classifier.jaccard_similarity(None, None), 1.0)

    def test_compile_system_prompt_injects_expert_cues(self):
        config = {
            "system_prompt": "Base classifier prompt.",
            "cues": {
                "relevance": {
                    "positive_cues": ["THC/CBD measurement"],
                    "negative_cues": ["fiber hemp textiles"]
                },
                "extraction": {
                    "preclinical_cues": ["primary microglia"],
                    "clinical_cues": ["placebo-controlled"]
                }
            },
            "calibration_variants": {
                "decision_checklist": {
                    "prompt_suffix": "Verify active cannabinoid administration."
                }
            },
            "decision_boundaries": {
                "review_vs_original_prenatal_cannabis": {
                    "rule": "Classify broad prenatal cannabis impact summaries as reviews when no new Methods/Results are reported.",
                    "example": "Lasting impacts of prenatal cannabis exposure",
                    "expected": {
                        "publication_type": "review",
                        "study_type": ["review"]
                    }
                }
            }
        }
        
        prompt = classifier.compile_system_prompt(config)
        
        self.assertIn("Base classifier prompt.", prompt)
        self.assertIn("Expert Classification Cues", prompt)
        self.assertIn("THC/CBD measurement", prompt)
        self.assertIn("fiber hemp textiles", prompt)
        self.assertIn("primary microglia", prompt)
        self.assertIn("placebo-controlled", prompt)
        self.assertIn("Learned Decision Boundaries", prompt)
        self.assertIn("review_vs_original_prenatal_cannabis", prompt)
        self.assertIn("Lasting impacts of prenatal cannabis exposure", prompt)
        self.assertNotIn("Verify active cannabinoid administration.", prompt)
        
        original_variant = os.environ.get("CLASSIFIER_PROMPT_VARIANT")
        try:
            os.environ["CLASSIFIER_PROMPT_VARIANT"] = "decision_checklist"
            variant_prompt = classifier.compile_system_prompt(config)
        finally:
            if original_variant is None:
                os.environ.pop("CLASSIFIER_PROMPT_VARIANT", None)
            else:
                os.environ["CLASSIFIER_PROMPT_VARIANT"] = original_variant
                
        self.assertIn("Calibration Variant: decision_checklist", variant_prompt)
        self.assertIn("Verify active cannabinoid administration.", variant_prompt)

    def test_calibration_agent_dry_run_writes_walkthrough(self):
        import argparse
        import calibration_agent
        import tempfile
        
        self.db.insert_paper({
            "pmid": "555666",
            "title": "Preclinical cannabinoid calibration candidate",
            "abstract": "Original research in rat cells tested THC exposure.",
            "study_type": ["Animal Models (Rat)"],
            "publication_type": "original research",
            "classification_confidence": 0.51,
            "classifier_version": "maude-reclassify-1.0.0"
        })
        
        with tempfile.TemporaryDirectory() as temp_dir:
            args = argparse.Namespace(
                max_calls=1,
                fetch_limit=5,
                mode="preclinical_original",
                confidence_max=0.6,
                variants="control,decision_checklist",
                runs=1,
                output_dir=temp_dir,
                dry_run=True,
                abstract_only=True,
                require_full_text=False,
                include_locked=False,
                include_calibrated=False,
            )
            json_path, walkthrough_path = calibration_agent.run_calibration(args)
            
            self.assertTrue(json_path.exists())
            self.assertTrue(walkthrough_path.exists())
            with open(json_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
                
            self.assertEqual(payload["planned_candidates"], 1)
            self.assertEqual(payload["calls_attempted"], 0)
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["updates_applied"], 0)

    def test_calibration_metrics_dashboard_aggregates_manual_batches(self):
        import calibration_metrics

        metrics = calibration_metrics.build_dashboard_metrics(
            output_dir=Path("scratch/calibration_runs"),
            rules_config=classifier.load_rules_config(),
        )

        self.assertGreaterEqual(metrics["summary"]["batch_count"], 2)
        self.assertEqual(metrics["summary"]["total_papers"], 100)
        self.assertIn("control", {v["variant"] for v in metrics["variant_comparison"]["variants"]})
        self.assertIn("decision_checklist", {v["variant"] for v in metrics["variant_comparison"]["variants"]})
        self.assertGreater(metrics["field_change_totals"]["high_level_fields"].get("cannabis_type", 0), 0)
        self.assertFalse(metrics["automation_readiness"]["ready_for_full_automation"])
        self.assertGreater(len(metrics["priority_review"]), 0)
        self.assertGreaterEqual(metrics["summary"]["expert_notes_count"], 1)

    def test_calibration_dashboard_metrics_api(self):
        response = self.client.get("/api/calibration/dashboard-metrics")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data.decode("utf-8"))
        self.assertEqual(data["summary"]["total_papers"], 100)
        self.assertIn("automation_readiness", data)

    def test_agent_queue_status_and_recent_feedback_apis(self):
        paper_id = self.db.insert_paper({
            "pmid": "444555",
            "title": "Low confidence CBD trial",
            "abstract": "This clinical trial evaluated CBD oil in patients.",
            "study_type": ["review"],
            "classification_confidence": 0.42,
            "classifier_version": "llm-reclassify-2.1.0"
        })
        
        queue_response = self.client.get("/api/classification/queue?confidence_max=0.6&limit=5")
        self.assertEqual(queue_response.status_code, 200)
        queue_data = json.loads(queue_response.data.decode("utf-8"))
        queue_ids = {p["id"] for p in queue_data["papers"]}
        self.assertIn(paper_id, queue_ids)
        
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["email"] = "shawnmckenzie11.sm@gmail.com"
            
        edit_response = self.client.post(
            f"/api/papers/{paper_id}/edit-classification",
            json={"study_type": ["Clinical (RCT)"]}
        )
        self.assertEqual(edit_response.status_code, 200)
        
        feedback_response = self.client.get("/api/feedback/recent?limit=5")
        self.assertEqual(feedback_response.status_code, 200)
        feedback_data = json.loads(feedback_response.data.decode("utf-8"))
        self.assertEqual(feedback_data["feedback"][0]["paper_id"], paper_id)
        self.assertEqual(feedback_data["feedback"][0]["field_name"], "study_type")
        
        status_response = self.client.get("/api/agents/automation-status")
        self.assertEqual(status_response.status_code, 200)
        status_data = json.loads(status_response.data.decode("utf-8"))
        self.assertGreaterEqual(status_data["feedback"]["corrections_since_eval"], 1)
        self.assertIn("/api/classification/queue", status_data["agent_automation"]["agent_entrypoints"])


class TestUserAuthentication(unittest.TestCase):
    """Test cases for user registration, verification, password hashing, and Google OAuth methods."""

    def setUp(self):
        self.db = DatabaseManager()
        if self.db.is_postgres:
            self.db.clear_all_tables()
        else:
            self.db.db_path = "test_catalog.db"
            self.db.init_db()
            conn = self.db.get_connection()
            try:
                conn.execute("DELETE FROM users;")
                conn.commit()
            finally:
                conn.close()

    def tearDown(self):
        if os.path.exists("test_catalog.db"):
            try:
                os.remove("test_catalog.db")
            except Exception:
                pass

    def test_password_hashing(self):
        pwd = "secure_password_123"
        hashed = self.db.hash_password(pwd)
        self.assertNotEqual(pwd, hashed)
        self.assertTrue(self.db.check_password(pwd, hashed))
        self.assertFalse(self.db.check_password("wrong_password", hashed))

    def test_user_creation_and_lookup(self):
        username = "testuser"
        email = "testuser@mckenzian.org"
        pwd = "testpassword"
        hashed = self.db.hash_password(pwd)
        
        success = self.db.create_user(
            username=username,
            email=email,
            password_hash=hashed,
            google_id=None,
            is_verified=0,
            verification_code="123456"
        )
        self.assertTrue(success)
        
        duplicate = self.db.create_user(
            username=username,
            email="other@email.com",
            password_hash=hashed
        )
        self.assertFalse(duplicate)
        
        user_by_name = self.db.get_user_by_username_or_email(username)
        self.assertIsNotNone(user_by_name)
        self.assertEqual(user_by_name["email"], email)
        self.assertEqual(user_by_name["is_verified"], 0)
        
        user_by_email = self.db.get_user_by_username_or_email(email)
        self.assertIsNotNone(user_by_email)
        self.assertEqual(user_by_email["username"], username)
        
        verify_success = self.db.verify_user(username)
        self.assertTrue(verify_success)
        
        updated_user = self.db.get_user_by_username_or_email(username)
        self.assertEqual(updated_user["is_verified"], 1)
        self.assertIsNone(updated_user["verification_code"])

    def test_google_user_creation_and_lookup(self):
        username = "Google User"
        email = "google@gmail.com"
        google_id = "google_sub_id_123"
        
        success = self.db.create_user(
            username=username,
            email=email,
            google_id=google_id,
            is_verified=1
        )
        self.assertTrue(success)
        
        google_user = self.db.get_user_by_google_id(google_id)
        self.assertIsNotNone(google_user)
        self.assertEqual(google_user["username"], username)
        self.assertEqual(google_user["email"], email)
        self.assertEqual(google_user["is_verified"], 1)


class TestAnalysesExportAPI(unittest.TestCase):
    """Test cases for the Flask analyses CSV export API."""

    def setUp(self):
        from db_manager import DatabaseManager
        self.db = DatabaseManager()
        if self.db.is_postgres:
            self.db.clear_all_tables()
        self.db.init_analyses_table()
        
        # Create a test user
        self.db.create_user(username="exportuser", email="export@mckenzian.org", is_verified=1)
        user = self.db.get_user_by_username_or_email("exportuser")
        self.user_id = user["id"]
        
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_export_csv_endpoint(self):
        # 1. Insert a test paper
        paper_id = self.db.insert_paper({
            "pmid": "888111",
            "doi": "10.1001/export_test",
            "title": "CSV Export Test Paper Title",
            "authors": ["Author Export"],
            "journal": "JAMA CSV",
            "year": 2026,
            "study_type": ["Clinical (RCT)"]
        })
        
        # 2. Insert a test analysis containing this paper
        chart_data = {
            "paper_count": 1,
            "paper_ids": [paper_id],
            "aggregates": {"avg_thc": 0, "avg_cbd": 0, "large_sample_pct": 0}
        }
        
        analysis_id = self.db.create_analysis(
            name="Test Export Analysis",
            filter_settings=json.dumps({"query": "CSV Export"}),
            paper_count=1,
            chart_data=json.dumps(chart_data),
            user_id=self.user_id
        )
        
        try:
            # 3. Request the export endpoint
            with self.client.session_transaction() as sess:
                sess["logged_in"] = True
                sess["user_id"] = self.user_id
                
            response = self.client.get(f"/api/analyses/{analysis_id}/export-csv")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "text/csv")
            self.assertIn("attachment; filename=analysis_Test_Export_Analysis.csv", response.headers.get("Content-Disposition", ""))
            
            csv_content = response.data.decode("utf-8")
            self.assertIn("CSV Export Test Paper Title", csv_content)
            self.assertIn("Author Export", csv_content)
            self.assertIn("888111", csv_content)
        finally:
            self.db.delete_paper(paper_id)
            self.db.delete_analysis(analysis_id)
            # Cleanup test user
            conn = self.db.get_connection()
            try:
                conn.execute("DELETE FROM users WHERE id = ?;", (self.user_id,))
                conn.commit()
            finally:
                conn.close()


class TestAnalysesUserIsolation(unittest.TestCase):
    """Test cases to verify user-isolation and public analytics access."""

    def setUp(self):
        from db_manager import DatabaseManager
        self.db = DatabaseManager()
        if self.db.is_postgres:
            self.db.clear_all_tables()
        self.db.init_analyses_table()
        
        # Create user A
        self.db.create_user(username="usera", email="usera@test.org", is_verified=1)
        self.user_a = self.db.get_user_by_username_or_email("usera")
        
        # Create user B
        self.db.create_user(username="userb", email="userb@test.org", is_verified=1)
        self.user_b = self.db.get_user_by_username_or_email("userb")
        
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        conn = self.db.get_connection()
        try:
            conn.execute("DELETE FROM users WHERE id IN (?, ?);", (self.user_a["id"], self.user_b["id"]))
            conn.execute("DELETE FROM analyses;")
            conn.commit()
        finally:
            conn.close()

    def test_public_user_behavior(self):
        # 1. Public user runs subset analyses (allowed, no db save)
        response = self.client.post("/api/analyze", json={"filters": {"query": "public_test"}})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data.decode("utf-8"))
        self.assertIsNone(data["id"])  # should be null/None since not saved
        self.assertIn("chart_data", data)

        # 2. Public user lists analyses (returns empty list)
        response = self.client.get("/api/analyses")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data.decode("utf-8")), [])

        # 3. Public user fetching an analysis directly (blocked)
        response = self.client.get("/api/analyses/12345")
        self.assertEqual(response.status_code, 401)

    def test_user_ownership_isolation(self):
        # 1. Create analysis belonging to User A
        analysis_id = self.db.create_analysis(
            name="User A Analysis",
            filter_settings="{}",
            paper_count=0,
            chart_data="{}",
            user_id=self.user_a["id"]
        )

        # 2. User A fetches own analysis (allowed)
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user_id"] = self.user_a["id"]
        response = self.client.get(f"/api/analyses/{analysis_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data.decode("utf-8"))["name"], "User A Analysis")

        # 3. User B fetches User A's analysis (Forbidden 403)
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user_id"] = self.user_b["id"]
        response = self.client.get(f"/api/analyses/{analysis_id}")
        self.assertEqual(response.status_code, 403)

        # 4. User B updates User A's analysis (Forbidden 403)
        response = self.client.put(f"/api/analyses/{analysis_id}", json={"name": "Stolen Analysis"})
        self.assertEqual(response.status_code, 403)

        # 5. User B deletes User A's analysis (Forbidden 403)
        response = self.client.delete(f"/api/analyses/{analysis_id}")
        self.assertEqual(response.status_code, 403)


class TestMvpGatingAPI(unittest.TestCase):
    """Test cases to verify that restricted features are gated in the MVP release."""

    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_gated_endpoints_return_403(self):
        gated_endpoints = [
            ("/api/harvest", "POST", {"query": "test"}),
            ("/api/harvest/status", "GET", None),
            ("/api/learning-dashboard/metrics", "GET", None),
            ("/api/graph/stats", "GET", None),
            ("/api/graph/network", "GET", None)
        ]

        for route, method, payload in gated_endpoints:
            if method == "POST":
                response = self.client.post(route, json=payload or {})
            else:
                response = self.client.get(route)
            
            self.assertEqual(response.status_code, 403, f"Endpoint {route} with method {method} was not gated (status {response.status_code})")
            
            data = json.loads(response.data.decode("utf-8"))
            self.assertEqual(data.get("error"), "This feature is locked in the MVP release.", f"Endpoint {route} returned unexpected error message: {data}")


class TestAdminRequiredEndpoints(unittest.TestCase):
    """Test cases to verify that write/delete/reclassify operations require admin status."""

    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_logged_out_users_get_401(self):
        # 1. Delete papers
        res1 = self.client.post("/api/papers/delete", json={"ids": [1]})
        self.assertEqual(res1.status_code, 401)

        # 2. Edit classification
        res2 = self.client.post("/api/papers/1/edit-classification", json={})
        self.assertEqual(res2.status_code, 401)

        # 3. Reclassify paper
        res3 = self.client.post("/api/papers/1/reclassify-llm", json={})
        self.assertEqual(res3.status_code, 401)

    def test_non_admin_logged_in_users_get_403(self):
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["email"] = "researcher@test.org"

        res1 = self.client.post("/api/papers/delete", json={"ids": [1]})
        self.assertEqual(res1.status_code, 403)

        res2 = self.client.post("/api/papers/1/edit-classification", json={})
        self.assertEqual(res2.status_code, 403)

        res3 = self.client.post("/api/papers/1/reclassify-llm", json={})
        self.assertEqual(res3.status_code, 403)

    def test_admin_logged_in_users_pass_decorator(self):
        with self.client.session_transaction() as sess:
            sess["logged_in"] = True
            sess["email"] = "shawnmckenzie11.sm@gmail.com"

        # Should bypass 401/403 (for delete, we expect 400 'No paper IDs provided' if we send empty/invalid data, or normal execution)
        res1 = self.client.post("/api/papers/delete", json={})
        self.assertEqual(res1.status_code, 400)
        self.assertIn("No paper IDs provided", json.loads(res1.data.decode("utf-8"))["error"])

        # For edit-classification, should return 404 since paper 99999 doesn't exist (proving decorator passed)
        res2 = self.client.post("/api/papers/99999/edit-classification", json={})
        self.assertEqual(res2.status_code, 404)


if __name__ == "__main__":
    unittest.main()


