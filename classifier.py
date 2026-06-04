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
            "  \"study_type\": [\"Clinical (RCT)\" | \"Clinical (prospective)\" | \"Clinical (observational)\" | \"Clinical (retrospective)\" | \"Animal Models (mouse)\" | \"Animal Models (rat)\" | \"Animal Models (non-human primate)\" | \"Animal Models (other)\" | \"Cell Culture (primary cells)\" | \"Cell Culture (cell lines)\" | \"Cell Culture (organoids)\" | \"Cell Culture (co-culture)\" | \"review\" | \"meta-analysis\" | \"case study\" | \"editorial\"] (multi-label array of matching study designs. For original research articles, extract one or more matching designs from the Clinical, Animal Models, or Cell Culture options. For reviews, systematic reviews, meta-analyses, editorials, or case studies, extract 'review', 'meta-analysis', 'editorial', or 'case study' as appropriate),\n"
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
            "  \"multiple_doses\": boolean (true if multiple doses, varying dose levels, or dose-response parameters are evaluated in study, false otherwise),\n"
            "  \"multiple_time_intervals\": boolean (true if multiple time intervals, longitudinal timepoints, serial measurements, or repeated administration measures are evaluated, false otherwise),\n"
            "  \"cannabis_type\": [\"dried flower\" | \"concentrates\" | \"vape pen\" | \"pure cannabinoid\" | \"edibles\" | \"hashish/kief\" | \"CB receptor agonist\" | \"CB receptor antagonist\" | \"unknown\"] (multi-label array of matching cannabis product types),\n"
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
        Dict containing all extracted metadata.
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
        
    return metadata
