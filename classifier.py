# classifier.py
import os
import json
import logging
import re
import math
from collections import Counter
from datetime import datetime
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

def load_rules_config() -> Dict[str, Any]:
    """Loads classification rules and configurations, with a fallback default config."""
    default_prompt = (
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
    default_config = {
        "version": "1.0.0",
        "confidence_thresholds": {
            "auto_accept": 0.85,
            "review_recommended": 0.60
        },
        "weights": {
            "self_consistency": 0.5,
            "retrieval_similarity": 0.3,
            "model_agreement": 0.2
        },
        "self_consistency_runs": 1,
        "system_prompt": default_prompt
    }
    
    if os.path.exists("rules_config.json"):
        try:
            with open("rules_config.json", "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load rules_config.json: {e}")
            
    return default_config

def get_historical_corrections() -> List[Dict[str, Any]]:
    """Fetches unique corrected papers from the feedback_audit table."""
    from db_manager import DatabaseManager
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT paper_id, title, abstract
            FROM feedback_audit
            GROUP BY paper_id, title, abstract
            """
        )
        return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Failed to fetch historical corrections: {e}")
        return []
    finally:
        conn.close()

def get_few_shot_examples(new_title: str, new_abstract: str, max_examples: int = 3) -> tuple[str, float]:
    """Retrieves up to max_examples relevant historical expert corrections as few-shot examples.
    
    Returns:
        tuple: (few_shot_string, max_similarity_score)
    """
    corrections = get_historical_corrections()
    if not corrections:
        return "", 1.0  # Default to 1.0 similarity if no corrections exist yet (neutral baseline)
        
    query_text = f"{new_title} {new_abstract}".lower()
    query_tokens = re.findall(r'[a-z0-9]+', query_text)
    if not query_tokens:
        return "", 0.0
        
    # Build corpus of documents (title + abstract)
    documents = []
    doc_tokens_list = []
    for c in corrections:
        doc_text = f"{c.get('title') or ''} {c.get('abstract') or ''}".lower()
        tokens = re.findall(r'[a-z0-9]+', doc_text)
        documents.append(c)
        doc_tokens_list.append(tokens)
        
    # Calculate IDF for all terms in the corpus
    num_docs = len(documents)
    df = Counter()
    for tokens in doc_tokens_list:
        unique_tokens = set(tokens)
        for t in unique_tokens:
            df[t] += 1
            
    idf = {}
    for t, count in df.items():
        idf[t] = math.log(1.0 + (num_docs / (1.0 + count)))
        
    # Calculate TF-IDF vectors
    def get_tfidf_vec(tokens):
        tf = Counter(tokens)
        vec = {}
        for t, f in tf.items():
            if t in idf:
                vec[t] = (1.0 + math.log(f)) * idf[t]
        return vec
        
    def cosine_similarity(v1, v2):
        intersection = set(v1.keys()) & set(v2.keys())
        if not intersection:
            return 0.0
        numerator = sum(v1[t] * v2[t] for t in intersection)
        sum1 = sum(val**2 for val in v1.values())
        sum2 = sum(val**2 for val in v2.values())
        if sum1 == 0 or sum2 == 0:
            return 0.0
        return numerator / (math.sqrt(sum1) * math.sqrt(sum2))
        
    query_vec = get_tfidf_vec(query_tokens)
    scored_docs = []
    for i, doc in enumerate(documents):
        doc_vec = get_tfidf_vec(doc_tokens_list[i])
        sim = cosine_similarity(query_vec, doc_vec)
        scored_docs.append((sim, doc))
        
    # Sort by similarity desc
    scored_docs.sort(key=lambda x: x[0], reverse=True)
    
    max_sim = scored_docs[0][0] if scored_docs else 0.0
    
    # Filter to sim > 0.05 and take top max_examples
    top_docs = [doc for sim, doc in scored_docs if sim > 0.05][:max_examples]
    if not top_docs:
        return "", max_sim
        
    few_shot_str = "\n\nExpert Guidance & Corrections:\n"
    few_shot_str += "Here are examples of how domain experts corrected previous classifications. Adhere strictly to these patterns:\n\n"
    
    for idx, doc in enumerate(top_docs):
        few_shot_str += f"Example {idx + 1}:\n"
        few_shot_str += f"Title: {doc.get('title')}\n"
        few_shot_str += f"Abstract: {doc.get('abstract')}\n"
        
        # Build individual field correction examples
        from db_manager import DatabaseManager
        db = DatabaseManager()
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT field_name, old_value, new_value FROM feedback_audit WHERE paper_id = ?",
            (doc['paper_id'],)
        )
        field_changes = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        for fc in field_changes:
            few_shot_str += f"Incorrect {fc['field_name']} Classification: {fc['old_value']}\n"
            few_shot_str += f"Correct Expert {fc['field_name']} Classification: {fc['new_value']}\n"
        few_shot_str += "\n"
        
    return few_shot_str, max_sim

def jaccard_similarity(a, b) -> float:
    """Computes Jaccard similarity between two elements (lists or single values)."""
    if a is None and b is None:
        return 1.0
    if a is None or b is None:
        return 0.0
        
    # Standardize string representations of lists
    def to_set(val):
        if isinstance(val, list):
            return set(val)
        if isinstance(val, str):
            val = val.strip()
            if val.startswith("[") and val.endswith("]"):
                try:
                    parsed = json.loads(val)
                    if isinstance(parsed, list):
                        return set(parsed)
                except Exception:
                    pass
            return {val}
        return {val}
        
    set_a = to_set(a)
    set_b = to_set(b)
    
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
        
    return len(set_a & set_b) / len(set_a | set_b)

def classify_with_llm(title: str, abstract: str, runs: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Uses Claude 3.5 Sonnet to perform deep extraction of scientific parameters.
    
    Returns:
        Optional[Dict]: Structured fields or None if LLM call fails.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.debug("No ANTHROPIC_API_KEY environment variable set. Skipping LLM pass.")
        return None
        
    config = load_rules_config()
    system_prompt = config.get("system_prompt")
    
    # Retrieve dynamic few-shot templates
    few_shot_text, max_sim = get_few_shot_examples(title, abstract)
    if few_shot_text:
        system_prompt += few_shot_text
        
    num_runs = runs if runs is not None else config.get("self_consistency_runs", 1)
    
    try:
        client = Anthropic(api_key=api_key)
        user_content = f"Title: {title}\n\nAbstract: {abstract}"
        
        parsed_results = []
        
        for run_idx in range(num_runs):
            # Run LLM pass. For multi-run self-consistency, add minor temperature to encourage variation
            temp = 0.5 if num_runs > 1 else 0.0
            
            logger.info(f"Sending paper details to Anthropic API for classification (Run {run_idx+1}/{num_runs})...")
            message = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1000,
                temperature=temp,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": user_content}
                ]
            )
            
            response_text = message.content[0].text.strip()
            
            if response_text.startswith("```"):
                lines = response_text.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                response_text = "\n".join(lines).strip()
                
            parsed_json = json.loads(response_text)
            parsed_results.append(parsed_json)
            
        if not parsed_results:
            return None
            
        # Consensus voting if multiple runs, otherwise take first
        consensus = {}
        consistency_scores = []
        
        if num_runs > 1:
            all_keys = set().union(*(r.keys() for r in parsed_results))
            for key in all_keys:
                # Find most common value for each key
                values = []
                for r in parsed_results:
                    val = r.get(key)
                    if isinstance(val, list):
                        values.append(json.dumps(sorted(val)))
                    elif isinstance(val, dict):
                        values.append(json.dumps(val))
                    else:
                        values.append(json.dumps(val) if val is not None else "null")
                        
                counter = Counter(values)
                most_common_serialized, count = counter.most_common(1)[0]
                agreement_ratio = count / num_runs
                consistency_scores.append(agreement_ratio)
                
                # Unserialize value
                if most_common_serialized == "null":
                    consensus[key] = None
                else:
                    consensus[key] = json.loads(most_common_serialized)
            
            self_consistency_score = sum(consistency_scores) / len(consistency_scores) if consistency_scores else 1.0
        else:
            consensus = parsed_results[0]
            self_consistency_score = 1.0
            
        # Calculate Confidence Signals
        # 1. Model Agreement (LLM vs heuristics fallback)
        heuristic_metadata = extractor.extract_all_heuristics(title, abstract)
        agreements = []
        check_fields = ["study_type", "exposure_method", "population", "cannabis_type", "publication_type"]
        for field in check_fields:
            llm_val = consensus.get(field)
            h_val = heuristic_metadata.get(field)
            agreements.append(jaccard_similarity(llm_val, h_val))
            
        model_agreement_score = sum(agreements) / len(agreements) if agreements else 1.0
        
        # 2. Retrieval Similarity: max_sim from few-shot retrieval is stored
        # If there are no historical expert edits, max_sim is 1.0 (baseline)
        
        # Calculate final combined confidence score
        weights = config.get("weights", {"self_consistency": 0.5, "retrieval_similarity": 0.3, "model_agreement": 0.2})
        w_sc = weights.get("self_consistency", 0.5)
        w_rs = weights.get("retrieval_similarity", 0.3)
        w_ma = weights.get("model_agreement", 0.2)
        
        final_confidence = (
            w_sc * self_consistency_score +
            w_rs * max_sim +
            w_ma * model_agreement_score
        )
        
        # Clamp to 0.0 - 1.0
        final_confidence = max(0.0, min(1.0, final_confidence))
        
        # Inject metadata into result
        consensus["classification_confidence"] = final_confidence
        consensus["classification_timestamp"] = datetime.now().isoformat()
        consensus["classifier_version"] = config.get("version", "1.0.0")
        
        logger.info(f"LLM Classification complete. Confidence: {final_confidence:.2f}")
        return consensus
        
    except Exception as e:
        logger.error(f"Anthropic LLM classification failed: {e}. Falling back to heuristics.")
        return None

def process_paper_metadata(title: str, abstract: str, run_llm: bool = False, runs: Optional[int] = None) -> Dict[str, Any]:
    """Extracts metadata from paper, running LLM if requested/available, else falling back to heuristics.
    
    Args:
        title: Title of the paper
        abstract: Abstract text of the paper
        run_llm: Whether to attempt LLM classification
        runs: Optional count of self-consistency runs override
        
    Returns:
        Dict containing all extracted metadata.
    """
    metadata = None
    
    if run_llm:
        metadata = classify_with_llm(title, abstract, runs=runs)
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
        logger.info("Running standard regex and keyword heuristics extractor.")
        metadata = extractor.extract_all_heuristics(title, abstract)
        # Apply neutral fallback metadata
        metadata["classification_confidence"] = 1.0
        metadata["classification_timestamp"] = datetime.now().isoformat()
        metadata["classifier_version"] = "heuristic-1.0.0"
        
    return metadata
