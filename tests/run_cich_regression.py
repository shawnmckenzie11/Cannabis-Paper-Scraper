import os
import json
import unittest
import logging
from typing import List, Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("run_cich_regression")

# Helper matching functions
def list_match(extracted: List[str], ground_truth: List[str]) -> bool:
    """True if sets of elements match exactly (order-independent, case-insensitive)."""
    ext_set = {str(x).strip().lower() for x in (extracted or [])}
    gt_set = {str(x).strip().lower() for x in (ground_truth or [])}
    return ext_set == gt_set

def val_match(extracted: str, ground_truth: str, is_pub_type: bool = False) -> bool:
    """True if values match exactly (case-insensitive, normalized)."""
    ext_str = str(extracted).strip().lower() if extracted else ""
    gt_str = str(ground_truth).strip().lower() if ground_truth else ""
    
    if is_pub_type:
        # Coarse publication type normalization
        reviews = ["review", "meta-analysis", "systematic review", "scoping review"]
        if gt_str in reviews:
            return ext_str == "review"
    return ext_str == gt_str

class HeuristicsRegressionTests(unittest.TestCase):
    """Regression test suite for Continuous Integration of Heuristics (CI/CH)."""

    # Baseline scores representing the current best Maude model
    BASELINE_ROUTING = 0.5850
    BASELINE_EXTRACTION = 0.4764
    BASELINE_OVERALL = 0.5291

    @classmethod
    def setUpClass(cls):
        # Load the golden dataset
        dataset_path = os.path.join(os.path.dirname(__file__), "golden_dataset.json")
        if not os.path.exists(dataset_path):
            raise FileNotFoundError(f"Golden dataset not found at {dataset_path}")
            
        with open(dataset_path, "r", encoding="utf-8") as f:
            cls.papers = json.load(f)
            
        import extractor
        cls.extractor = extractor

    def test_run_regression(self):
        """Run the heuristics engine against all 100 golden papers and score alignment."""
        total_papers = len(self.papers)
        logger.info(f"Running regression evaluation on {total_papers} golden papers...")

        # Initialize score counters
        routing_correct = 0
        routing_total = 0
        
        extraction_correct = 0
        extraction_total = 0

        detailed_results = []

        for p in self.papers:
            title = p["title"]
            text = p["text"]
            gt = p["ground_truth"]
            pmid = p["pmid"]

            # Run extraction pipeline
            res = self.extractor.extract_all_heuristics(title=title, abstract=text, full_text=None)

            # --- Tier 1: Routing Metrics ---
            pub_ok = val_match(res.get("publication_type"), gt.get("publication_type"), is_pub_type=True)
            
            # study_type check (set match)
            study_ok = list_match(res.get("study_type"), gt.get("study_type"))
            
            routing_total += 2
            if pub_ok:
                routing_correct += 1
            if study_ok:
                routing_correct += 1

            # --- Tier 2: Sub-Tier Extraction Metrics ---
            pub_coarse = str(res.get("publication_type")).strip().lower()
            gt_pub_coarse = "review" if str(gt.get("publication_type")).strip().lower() in ["review", "meta-analysis", "systematic review"] else "original research"

            # Sub-tier extraction only applies if both routed to original research and in-scope
            is_original = pub_coarse == "original research" and gt_pub_coarse == "original research"
            
            exposure_ok = True
            cannabis_ok = True
            age_ok = True
            sex_ok = True

            if is_original:
                # 1. Exposure Method (set match)
                exposure_ok = list_match(res.get("exposure_method"), gt.get("exposure_method"))
                extraction_total += 1
                if exposure_ok:
                    extraction_correct += 1

                # 2. Cannabis Type (set match)
                cannabis_ok = list_match(res.get("cannabis_type"), gt.get("cannabis_type"))
                extraction_total += 1
                if cannabis_ok:
                    extraction_correct += 1

                # 3. Clinical Study Population (if clinical)
                study_types = {str(s).lower() for s in (gt.get("study_type") or [])}
                is_clinical = any("clinical" in s for s in study_types)
                if is_clinical:
                    age_ok = val_match(res.get("population_age"), gt.get("population_age"))
                    sex_ok = val_match(res.get("population_sex"), gt.get("population_sex"))
                    extraction_total += 2
                    if age_ok:
                        extraction_correct += 1
                    if sex_ok:
                        extraction_correct += 1

            detailed_results.append({
                "pmid": pmid,
                "title": title,
                "pub_type": {"extracted": res.get("publication_type"), "gt": gt.get("publication_type"), "ok": pub_ok},
                "study_type": {"extracted": res.get("study_type"), "gt": gt.get("study_type"), "ok": study_ok},
                "exposure_method": {"extracted": res.get("exposure_method"), "gt": gt.get("exposure_method"), "ok": exposure_ok} if is_original else None,
                "cannabis_type": {"extracted": res.get("cannabis_type"), "gt": gt.get("cannabis_type"), "ok": cannabis_ok} if is_original else None,
                "population_age": {"extracted": res.get("population_age"), "gt": gt.get("population_age"), "ok": age_ok} if (is_original and is_clinical) else None,
                "population_sex": {"extracted": res.get("population_sex"), "gt": gt.get("population_sex"), "ok": sex_ok} if (is_original and is_clinical) else None,
            })

        # Calculate alignment rates
        routing_score = routing_correct / routing_total if routing_total > 0 else 0.0
        extraction_score = extraction_correct / extraction_total if extraction_total > 0 else 0.0
        overall_score = (routing_correct + extraction_correct) / (routing_total + extraction_total) if (routing_total + extraction_total) > 0 else 0.0

        print("\n" + "="*50)
        print("           CI/CH REGRESSION RESULTS           ")
        print("="*50)
        print(f"Total Papers Evaluated: {total_papers}")
        print(f"Tier 1 Routing Alignment:    {routing_score * 100:.2f}% ({routing_correct}/{routing_total})")
        print(f"Tier 2 Extraction Alignment: {extraction_score * 100:.2f}% ({extraction_correct}/{extraction_total})")
        print(f"Overall Scoped Alignment:    {overall_score * 100:.2f}% ({routing_correct + extraction_correct}/{routing_total + extraction_total})")
        print("-"*50)
        print(f"Baseline Overall Target:     {self.BASELINE_OVERALL * 100:.2f}%")
        print("="*50 + "\n")

        # Zero-regression check
        self.assertGreaterEqual(
            overall_score, 
            self.BASELINE_OVERALL, 
            f"Regression detected! Overall score {overall_score * 100:.2f}% is below baseline {self.BASELINE_OVERALL * 100:.2f}%"
        )
        
        # Log status regarding the 85% production target
        if overall_score < 0.85:
            logger.warning(
                f"Production Target Warning: Overall score {overall_score * 100:.2f}% is below the "
                f"85.00% production release target. Additional RL calibration cycles are required."
            )
        else:
            logger.info(f"Production Target Met: Overall score {overall_score * 100:.2f}% exceeds the 85.00% target!")

if __name__ == "__main__":
    unittest.main()
