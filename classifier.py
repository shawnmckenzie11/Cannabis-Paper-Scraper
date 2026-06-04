# classifier.py
import os
import json
import logging
from typing import Dict, Any, List, Optional
from anthropic import Anthropic
from dotenv import load_dotenv

# Import extractor as fallback
import extractor

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

RUBRIC_FILE = "quality_rubric.json"

def load_quality_rubric() -> Dict[str, Any]:
    """Loads the quality scoring rubric from the JSON file."""
    if os.path.exists(RUBRIC_FILE):
        try:
            with open(RUBRIC_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error reading {RUBRIC_FILE}, using fallback defaults. Error: {e}")
            
    # Inline default fallback rubric if file is missing/broken
    return {
        "base_score": 5,
        "deductions": {
            "no_strain_specified": 1,
            "self_report_only": 2,
            "THC_not_quantified": 2,
            "no_control_group": 2,
            "animal_model_only": 1,
            "label_not_verified": 1
        },
        "additions": {
            "quantified_dose": 2,
            "rct_design": 2,
            "peer_reviewed_journal": 1,
            "large_sample_size": 1,
            "multiple_doses": 5,
            "multiple_time_intervals": 5
        },
        "min_score": 0,
        "max_score": 20
    }

def calculate_quality_score(paper: Dict[str, Any]) -> int:
    """Computes the methodological quality score (0-10) using the rubric.
    
    Args:
        paper: Dict containing: study_type, dose_mg, strain_reported, strain_normalized,
               sample_size, journal, methodological_quality_flags
               
    Returns:
        int: Methodological quality score clamped between 0 and 10.
    """
    rubric = load_quality_rubric()
    
    score = rubric.get("base_score", 5)
    
    # 1. Apply Deductions
    flags = paper.get("methodological_quality_flags") or []
    if isinstance(flags, str):
        try:
            flags = json.loads(flags)
        except Exception:
            flags = []
            
    deductions_rubric = rubric.get("deductions", {})
    for flag in flags:
        if flag in deductions_rubric:
            score -= deductions_rubric[flag]
            
    # 2. Apply Additions
    additions_rubric = rubric.get("additions", {})
    
    # quantified_dose: dose_mg is present
    if paper.get("dose_mg") is not None and "quantified_dose" in additions_rubric:
        score += additions_rubric["quantified_dose"]
        
    # rct_design: study_type is RCT or contains RCT
    study_type = paper.get("study_type")
    is_rct = False
    if isinstance(study_type, str):
        is_rct = study_type == "RCT"
    elif isinstance(study_type, list):
        is_rct = "RCT" in study_type
        
    if is_rct and "rct_design" in additions_rubric:
        score += additions_rubric["rct_design"]
        
    # peer_reviewed_journal: check if not in a preprint server
    journal = (paper.get("journal") or "").lower()
    is_preprint = any(k in journal for k in ["biorxiv", "medrxiv", "arxiv", "preprint", "research square"])
    if journal and not is_preprint and "peer_reviewed_journal" in additions_rubric:
        score += additions_rubric["peer_reviewed_journal"]
        
    # large_sample_size: N > 50
    sample_size = paper.get("sample_size")
    if sample_size is not None and int(sample_size) > 50 and "large_sample_size" in additions_rubric:
        score += additions_rubric["large_sample_size"]

    # multiple_doses: true if multiple doses or dose-response parameters present
    if paper.get("multiple_doses") and "multiple_doses" in additions_rubric:
        score += additions_rubric["multiple_doses"]

    # multiple_time_intervals: true if multiple timepoints or repeated measures present
    if paper.get("multiple_time_intervals") and "multiple_time_intervals" in additions_rubric:
        score += additions_rubric["multiple_time_intervals"]
        
    # 3. Clamp score between min and max
    min_val = rubric.get("min_score", 0)
    max_val = rubric.get("max_score", 20)
    
    return max(min_val, min(max_val, int(score)))

def classify_with_llm(title: str, abstract: str) -> Optional[Dict[str, Any]]:
    """Uses Claude 3.5 Sonnet to perform deep extraction of scientific parameters.
    
    Returns:
        Optional[Dict]: Structured fields or None if LLM call fails.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.debug("No ANTHROPIC_API_KEY environment variable set. Skipping LLM pass.")
        return None
        
    try:
        client = Anthropic(api_key=api_key)
        
        system_prompt = (
            "You are an expert pharmacology and clinical research AI assistant specializing in cannabis research. "
            "Your task is to analyze the research paper title and abstract and extract key experimental parameters. "
            "You MUST return a raw JSON object and nothing else. Do not wrap it in markdown block tags like ```json. "
            "Ensure the JSON exactly conforms to the following schema structure:\n"
            "{\n"
            "  \"study_type\": [\"RCT\" | \"observational\" | \"animal\" | \"in vitro\" | \"review\" | \"meta-analysis\" | \"case study\" | \"editorial\"] (multi-label array of matching study designs),\n"
            "  \"publication_type\": \"review\" | \"original research\" | \"case study\" | \"systematic review\" | \"meta-analysis\" | \"editorial\" | \"comment\" | \"letter to the editor\" | \"perspectives paper\" (choose exactly one publication type that best describes the paper),\n"
            "  \"exposure_method\": [\"smoked\" | \"vaporized\" | \"oral/edible\" | \"tincture\" | \"injection\" | \"forced inhalation\" | \"in vitro\" | \"unknown\"] (multi-label array of matching exposure methods),\n"
            "  \"thc_pct\": float or null (numeric percent, e.g. 12.5. Do not include '%' sign),\n"
            "  \"cbd_pct\": float or null (numeric percent, e.g. 0.5. Do not include '%' sign),\n"
            "  \"dose_mg\": float or null (absolute dose in milligrams, e.g., 20.0. Convert from other absolute metric if possible, null if not reported or mg/kg/ml only),\n"
            "  \"strain_reported\": string or null (exact raw strain name as written, e.g., \"Bedrocan\", \"Charlotte's Web\", \"OG Kush\"),\n"
            "  \"strain_normalized\": \"Chemotype I\" | \"Chemotype II\" | \"Chemotype III\" | null (Chemotype I = High THC, Chemotype II = Balanced, Chemotype III = High CBD),\n"
            "  \"duration_days\": float or null (treatment duration converted to days),\n"
            "  \"population\": [\"human\" | \"mouse\" | \"rat\" | \"cell_line\" | \"other\"] (multi-label array of matching populations),\n"
            "  \"sample_size\": integer or null (N value),\n"
            "  \"outcome_domain\": [\"pain\", \"anxiety\", \"cognition\", \"inflammation\", \"addiction\", \"oncology\", \"neuroprotection\", \"sleep\", \"other\"] (multi-label array of matching outcomes),\n"
            "  \"methodological_quality_flags\": [\"no_strain_specified\", \"self_report_only\", \"THC_not_quantified\", \"no_control_group\", \"animal_model_only\", \"label_not_verified\"] (array of matching flags),\n"
            "  \"multiple_doses\": boolean (true if multiple doses, varying dose levels, or dose-response parameters are evaluated in study, false otherwise),\n"
            "  \"multiple_time_intervals\": boolean (true if multiple time intervals, longitudinal timepoints, serial measurements, or repeated administration measures are evaluated, false otherwise),\n"
            "  \"cannabis_type\": [\"dried flower\" | \"concentrates\" | \"vape pen\" | \"pure cannabinoid\" | \"edibles\" | \"hashish/kief\" | \"unknown\"] (multi-label array of matching cannabis product types),\n"
            "  \"summary\": string (a concise 1-2 sentence scientific summary of the study's key objective and findings, explicitly mentioning the strain of cannabis used if reported, or specifying that no strain was reported if none)\n"
            "}"
        )
        
        user_content = f"Title: {title}\n\nAbstract: {abstract}"
        
        logger.info("Sending paper details to Anthropic API for classification...")
        
        # Call Anthropic Claude 3.5 Sonnet
        message = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            temperature=0.0,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_content}
            ]
        )
        
        response_text = message.content[0].text.strip()
        
        # Robust parsing of JSON response
        # Claude might sometimes wrap in code blocks despite instructions, let's clean it up
        if response_text.startswith("```"):
            # Strip first and last lines
            lines = response_text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines[-1].startswith("```"):
                lines = lines[:-1]
            response_text = "\n".join(lines).strip()
            
        parsed_json = json.loads(response_text)
        logger.info("Successfully received and parsed LLM classification response.")
        return parsed_json
        
    except Exception as e:
        logger.error(f"Anthropic LLM classification failed: {e}. Falling back to heuristics.")
        return None

def process_paper_metadata(title: str, abstract: str, run_llm: bool = False) -> Dict[str, Any]:
    """Extracts metadata from paper, running LLM if requested/available, else falling back to heuristics.
    
    Args:
        title: Title of the paper
        abstract: Abstract text of the paper
        run_llm: Whether to attempt LLM classification
        
    Returns:
        Dict containing all extracted metadata + calculated quality score.
    """
    metadata = None
    
    if run_llm:
        metadata = classify_with_llm(title, abstract)
        if metadata:
            if not metadata.get("summary"):
                metadata["summary"] = extractor.generate_heuristic_summary(metadata)
            allowed_pub_types = {
                "review", "original research", "case study", "systematic review",
                "meta-analysis", "editorial", "comment", "letter to the editor", "perspectives paper"
            }
            if not metadata.get("publication_type") or metadata.get("publication_type") not in allowed_pub_types:
                metadata["publication_type"] = extractor.infer_publication_type(title, abstract)
        
    if not metadata:
        # Graceful fallback to heuristics
        logger.info("Running standard regex and keyword heuristics extractor.")
        metadata = extractor.extract_all_heuristics(title, abstract)
        
    # Calculate quality score using the rubric
    metadata["methodological_quality_score"] = calculate_quality_score(metadata)
    
    return metadata
