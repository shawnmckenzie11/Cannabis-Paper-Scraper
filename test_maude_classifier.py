"""Tests for Maude rule classifier."""

import unittest

import maude_classifier


class TestMaudeClassifier(unittest.TestCase):
    """Unit tests for Maude classification and disagreement detection."""

    def test_routes_systematic_review(self):
        """Review titles should route to Node 1B/3A with coarse publication_type."""
        result = maude_classifier.classify_paper(
            "A systematic review of cannabis for chronic pain",
            "We searched PubMed and included 42 studies.",
        )
        self.assertEqual(result["publication_type"], "review")
        self.assertIn("systematic review", result["study_type"])
        self.assertIn("node1b_reviews", result["_maude_meta"]["nodes_visited"])
        self.assertIn("node3a", result["_maude_meta"]["nodes_visited"])

    def test_not_cannabis_related_gpr(self):
        """GPR/LPI-only papers should be not_cannabis_related with null publication_type."""
        result = maude_classifier.classify_paper(
            "GPR55 modulates hippocampal synaptic plasticity",
            "We tested LPI and antagonist CID 16020046 in hippocampal slices.",
        )
        self.assertEqual(result["ingestion_status"], "not_cannabis_related")
        self.assertIsNone(result["publication_type"])

    def test_routes_overview_paper_to_review(self):
        """Overview papers (e.g. PMID 34676324) should route to review, not original research."""
        result = maude_classifier.classify_paper(
            "The Trouble with CBD Oil",
            (
                "CBD oil is widely sold for many conditions. This overview paper looks into the known "
                "risks and issues related to the composition of CBD products."
            ),
        )
        self.assertEqual(result["publication_type"], "review")
        self.assertEqual(result["study_type"], ["review"])
        self.assertIn("node1b_reviews", result["_maude_meta"]["nodes_visited"])

    def test_sparse_extraction_fallback_not_cannabis_related(self):
        """Cannabis mention without administration cues and sparse downstream detail → not_cannabis_related."""
        result = maude_classifier.classify_paper(
            "Cannabis policy and public health implications",
            (
                "Cannabis legalization has changed public health discourse. This article discusses "
                "regulatory frameworks without presenting original experimental data."
            ),
            enable_sparse_fallback=True,
        )
        self.assertIsNone(result["publication_type"])
        self.assertEqual(result["ingestion_status"], "not_cannabis_related")
        self.assertTrue(result["_maude_meta"].get("sparse_extraction_fallback"))

    def test_compare_maude_llm_flags_disagreements(self):
        """Disagreement detector should flag publication_type mismatches."""
        maude = {"publication_type": "review", "study_type": ["review"], "ingestion_status": "relevant"}
        llm = {
            "publication_type": "original research",
            "study_type": ["Clinical (RCT)"],
            "ingestion_status": "relevant",
        }
        result = maude_classifier.compare_maude_llm(maude, llm)
        self.assertTrue(result["flagged_for_review"])
        self.assertIn("publication_type", result["fields"])
        self.assertIn("study_type", result["fields"])
        self.assertIn("ingestion_status", result["agreed_fields"])

    def test_routes_pubmed_review_prefix(self):
        """PubMed PublicationType prefix injected at harvest should route to review via metadata rules."""
        result = maude_classifier.classify_paper(
            "Cannabis and chronic pain",
            "Publication Type: Review. This article summarizes evidence on cannabis for pain.",
        )
        self.assertEqual(result["publication_type"], "review")
        self.assertIn("node1b_reviews", result["_maude_meta"]["nodes_visited"])

    def test_routes_pubmed_meta_analysis_prefix(self):
        """PubMed meta-analysis publication type should route to review + meta-analysis subtype."""
        result = maude_classifier.classify_paper(
            "Cannabis dose-response meta-analysis",
            "Publication Type: Meta-Analysis. We pooled estimates from 18 trials.",
        )
        self.assertEqual(result["publication_type"], "review")
        self.assertIn("meta-analysis", result["study_type"])
        self.assertIn("node3b", result["_maude_meta"]["nodes_visited"])

    def test_routes_progress_report_to_review(self):
        """Progress report titles should route to Node 1B."""
        result = maude_classifier.classify_paper(
            "Progress report on new medications for seizures and epilepsy",
            "A summary of the latest conference findings on antiepileptic drugs.",
        )
        self.assertEqual(result["publication_type"], "review")

    def test_chart_review_does_not_route_to_review(self):
        """Methods-section chart review should stay original research."""
        pub, subtype, nodes, _ = maude_classifier.route_publication_type(
            "Prevalence of Cannabinoid Use in Patients With Hip and Knee Osteoarthritis",
            (
                "Chart review provided demographic factors. Descriptive statistics were used "
                "to summarize cannabinoid use in orthopedic patients."
            ),
        )
        self.assertEqual(pub, "original research")
        self.assertNotIn("node1b_reviews", nodes)

    def test_perspective_in_abstract_does_not_route_to_review(self):
        """Harm-reduction perspective in abstract should not route when title lacks review cues."""
        result = maude_classifier.classify_paper(
            "Medical cannabis use in Australia seven years after legalisation",
            (
                "From a harm-reduction perspective there is much to recommend prescribed "
                "medicinal cannabis for eligible patients."
            ),
        )
        self.assertEqual(result["publication_type"], "original research")

    def test_routes_series_of_patients_to_case_study(self):
        """Series-of-patients language should route to Node 1C."""
        pub, subtype, nodes, _ = maude_classifier.route_publication_type(
            "Cannabidiol in children with epilepsy",
            "Here we present a series of patients with refractory epilepsy treated with CBD.",
        )
        self.assertEqual(pub, "case study")
        self.assertIn("node1c_case_report", nodes)

    def test_routes_node2_branches_from_cues(self):
        """Original-research papers should visit Node 2 branches from cue catalog."""
        result = maude_classifier.classify_paper(
            "THC effects in C57BL/6 mice",
            "Male mice received intraperitoneal THC and behavior was assessed.",
        )
        nodes = result["_maude_meta"]["nodes_visited"]
        self.assertIn("node2b_in_vivo", nodes)
        self.assertIn("Animal Models (Mouse)", result["study_type"])

    def test_abstract_only_clears_downstream_fields(self):
        """Abstract-only classification should leave downstream extraction fields empty."""
        result = maude_classifier.classify_paper(
            "Cannabinoid receptor pharmacology in neural tissue",
            "We characterized binding profiles in membrane preparations.",
        )
        self.assertEqual(result["exposure_method"], [])
        self.assertEqual(result["cannabis_type"], [])
        self.assertEqual(result["outcome_domain"], [])
        self.assertIsNone(result["species"])
        self.assertTrue(result["_maude_meta"].get("abstract_only_extraction"))

    def test_abstract_only_allows_preclinical_downstream(self):
        """Preclinical title cues should allow downstream extraction without full text."""
        title = (
            "Cannabidiol Enhances Atezolizumab Efficacy by Upregulating PD-L1 Expression "
            "via the cGAS-STING Pathway in Triple-Negative Breast Cancer Cells"
        )
        result = maude_classifier.classify_paper(
            title,
            "CBD was applied to cultured breast cancer cells for 24 hours.",
        )
        self.assertFalse(result["_maude_meta"].get("abstract_only_extraction"))
        self.assertTrue(result.get("study_type"))

    def test_compare_maude_llm_flags_disagreements(self):
        """Disagreement detector should flag publication_type mismatches."""
        maude = {"publication_type": "review", "study_type": ["review"], "ingestion_status": "relevant"}
        llm = {
            "publication_type": "original research",
            "study_type": ["Clinical (RCT)"],
            "ingestion_status": "relevant",
        }
        result = maude_classifier.compare_maude_llm(maude, llm)
        self.assertTrue(result["flagged_for_review"])
        self.assertIn("publication_type", result["fields"])
        self.assertIn("study_type", result["fields"])
        self.assertIn("ingestion_status", result["agreed_fields"])


if __name__ == "__main__":
    unittest.main()
