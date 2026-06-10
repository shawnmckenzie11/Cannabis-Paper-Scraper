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
    default_prompt = 'Classify the attached cannabis/cannabinoid research paper by extracting the following characteristics from its full text. Use the full PDF text (title, abstract, introduction, methods, results, discussion) to inform your judgments. Return a JSON object with ONLY the fields listed below.\n## Fields to extract\n### 1. publication_type (single string)\nWhat kind of publication is this at the article level?\n- "original research" — primary research presenting new data/experiments\n- "review" — narrative or general review (not systematic)\n- "systematic review" — structured, replicable literature review\n- "meta-analysis" — statistical pooling of multiple studies\n- "case study" — detailed report of one or a few cases\n- "editorial" — opinion/editorial piece\n- "comment" — commentary or correspondence\n- "letter to the editor"\n- "perspectives paper"\n### 2. study_type (list of strings, multi-label)\nWhat study design(s) does this paper use? Can have multiple.\n**Clinical/Human:**\n- "Clinical (RCT)" — randomized controlled trial, double-blind, placebo-controlled\n- "Clinical (prospective)" — prospective cohort or longitudinal\n- "Clinical (retrospective)" — retrospective chart review or historical cohort\n- "Clinical (observational)" — cross-sectional, survey, registry, case-control, epidemiological, GWAS\n**Animal Models (in vivo):**\n- "Animal Models (Mouse)" — mouse, murine, C57BL/6\n- "Animal Models (Rat)" — rat, Wistar, Sprague-Dawley\n- "Animal Models (Other Rodents)" — hamster, gerbil, guinea pig, vole\n- "Animal Models (Non-Human Primates)" — macaque, rhesus, monkey, baboon\n- "Animal Models (Other)" — dog, cat, pig, rabbit, zebrafish, drosophila\n**Cell Culture (in vitro):**\n- "Cell Culture (Primary Cells)" — primary cells, splenocytes, primary microglia, primary hepatocytes\n- "Cell Culture (Cell Lines)" — HeLa, HepG2, PC12, RAW 264.7, SH-SY5Y, Jurkat, CHO\n- "Cell Culture (Organoids)" — organoid, spheroid, 3D culture\n- "Cell Culture (Co-Culture)" — co-culture of multiple cell types\n- "Cell Culture (PCLS)" — precision-cut lung slices\n- "Cell Culture (Other In Vitro)" — in vitro, cultured cells, epithelial cells, airway epithelial\n**Review types:**\n- "review" — narrative/systematic/scoping review\n- "meta-analysis"\n- "case study"\n- "editorial"\n### 3. exposure_method (list of strings, multi-label)\nHow was cannabis/cannabinoids administered?\n**Clinical/Human routes:**\n- "inhaled" — smoking, vaping, vaporization, inhalation\n- "oral" — edible, capsule, gummy, ingestion, gavage\n- "sublingual" — under tongue, drops, tincture\n- "injected" — IV, IM, SC (in human subjects)\n**In vitro methods:**\n- "exposure of cells to smoke/vapor" — direct chamber/ALI exposure of cells\n- "smoke/vapor conditioned media" — cells treated with smoke/vapor extract or CSE\n- "cannabinoids dissolved in media" — cannabinoids added directly to culture media\n**In vivo (animal) methods:**\n- "nose only smoke/vapor" — nose-only or snout-only exposure\n- "whole body. smoke/vapor" — whole-body chamber exposure (note: includes period)\n- "injection cannabinoids" — IP, IV, SC, IM injection of cannabinoids\n- "oral administration" — gavage, diet, feeding\n- "sub-lingual"\n- "intranasal" — nasal instillation/drops\n- "intratracheal" — intratracheal instillation\nIf none apply: "unknown"\n### 4. cannabis_type (list of strings, multi-label)\nWhat form of cannabis product was administered?\n- "dried flower" — cannabis flower, bud, joint, combusted herb, marijuana cigarette\n- "concentrates" — shatter, wax, resin, hash oil, BHO, rosin, tincture\n- "vape pen" — vape cartridge, e-cigarette, distillate vape, vaporizer, aerosol\n- "pure cannabinoid" — synthetic cannabinoid, dronabinol, nabilone, isolate\n- "edibles" — gummy, chocolate, brownie, cookie, drink, beverage, capsule\n- "hashish/kief" — hashish, hash, kief, charas\n- "CB receptor agonist" — synthetic agonist (e.g., WIN 55,212-2, CP 55,940, HU-210, JWH-018)\n- "CB receptor antagonist" — rimonabant, SR141716, AM251, AM630, SR144528\nIf none: "unknown"\n### 5. outcome_domain (list of strings, multi-label)\nWhat biological/clinical domain(s) does the study investigate as its primary or secondary outcome? Focus on the stated aims and key findings. Exclude domains that are only mentioned in passing background context.\n- "pain" — pain, analgesic, nociception, hyperalgesia, allodynia, neuropathic\n- "anxiety" — anxiety, anxiolytic, fear, panic, PTSD\n- "cognition" — cognition, memory, learning, attention, executive function, dementia, Alzheimer\'s\n- "inflammation" — inflammation, cytokine, TNF, interleukin, anti-inflammatory, arthritis\n- "addiction" — addiction, dependence, withdrawal, craving, substance use, relapse\n- "oncology" — cancer, tumor, chemotherapy, glioblastoma, carcinoma, antineoplastic (only if a primary focus, not background mention)\n- "neuroprotection" — neuroprotection, stroke, ischemia, brain injury, sclerosis, epilepsy, seizure\n- "sleep" — sleep, insomnia, sleep quality, melatonin\nIf none clearly identified: "other"\n### 6. population (list of strings, multi-label)\nWhat experimental subjects were used?\n- "human"\n- "mouse"\n- "rat"\n- "cell_line"\n- "other" (dog, pig, monkey, rabbit, feline, canine)\n### 7. Cannabinoid concentrations\n**thc_pct** (float or null): THC percentage reported in the cannabis product (e.g., 12.5 for "12.5% THC"). Look in Methods or Results or Product sections.\n**cbd_pct** (float or null): CBD percentage reported in the cannabis product (e.g., 1.0 for "1% CBD").\n**dose_mg** (float or null): Numeric dose in mg (e.g., 10 for "10 mg THC"). Extract dose values associated with cannabis/cannabinoids only (not other drugs).\n### 8. Strain/Chemotype\n**strain_reported** (string or null): Exact strain/cultivar name as written (e.g., "Bedrocan", "OG Kush", "Charlotte\'s Web").\n**strain_normalized** (string or null): One of:\n- "Chemotype I" — High THC, low CBD\n- "Chemotype II" — Balanced THC:CBD (~1:1)\n- "Chemotype III" — High CBD, low THC\n- Or null if not identifiable\n### 9. Timing parameters\n**duration_days** (float or null): Total study duration in days (e.g., 14 for "2 weeks", 30 for "30 days"). Extract from Methods.\n**inhaled_exposure_duration** (string or null): For inhalation studies, the per-session exposure duration (e.g., "30 minutes", "10 puffs", "5 minutes"). Extract from Methods.\n**administration_frequency** (string or null): How often the substance was administered (e.g., "daily", "twice daily", "once weekly", "5 days/week"). Extract from Methods.\n**treatment_duration** (string or null): For in vitro studies, how long cells were exposed (e.g., "24 hours", "48 hours"). Extract from Methods.\n### 10. Sample size and design flags\n**sample_size** (int or null): N value — number of subjects per group or total (e.g., 10 for "n = 10"). Extract from Methods.\n**multiple_doses** (boolean): true if multiple dose levels were tested (dose-response), or multiple concentrations in vitro.\n**multiple_time_intervals** (boolean): true if measurements were taken at multiple timepoints (longitudinal, repeated measures, time course).\n### 11. Puff count and THC/CBD concentrations (per-unit)\n**puff_count** (int or null): Number of puffs administered (relevant to inhaled studies). Null if not reported.\n**thc_mg_ml** (float or null): THC concentration in mg/mL of product volume. Null if not reported.\n**thc_mg_g** (float or null): THC dose in mg/g of product weight or body weight. Null if not reported.\n**thc_mg_kg** (float or null): THC dose in mg/kg of body weight. Null if not reported.\n**cbd_mg_ml** (float or null): CBD concentration in mg/mL of product volume. Null if not reported.\n**cbd_mg_g** (float or null): CBD dose in mg/g of product weight. Null if not reported.\n**cbd_mg_kg** (float or null): CBD dose in mg/kg of body weight. Null if not reported.\n### 12. Methodological quality flags\n**methodological_quality_flags** (list of strings): Any notable quality indicators or concerns. Examples: "randomized", "blinded", "placebo-controlled", "small sample size", "no control group", "confounding factors". Can be empty list.\n## Output format\nReturn ONLY valid JSON matching this schema:\n{\n  "publication_type": "string",\n  "study_type": ["string", ...],\n  "exposure_method": ["string", ...],\n  "cannabis_type": ["string", ...],\n  "outcome_domain": ["string", ...],\n  "population": ["string", ...],\n  "thc_pct": null or float,\n  "cbd_pct": null or float,\n  "dose_mg": null or float,\n  "strain_reported": null or string,\n  "strain_normalized": null or string,\n  "duration_days": null or float,\n  "inhaled_exposure_duration": null or string,\n  "administration_frequency": null or string,\n  "treatment_duration": null or string,\n  "sample_size": null or int,\n  "multiple_doses": false,\n  "multiple_time_intervals": false,\n  "puff_count": null or int,\n  "thc_mg_ml": null or float,\n  "thc_mg_g": null or float,\n  "thc_mg_kg": null or float,\n  "cbd_mg_ml": null or float,\n  "cbd_mg_g": null or float,\n  "cbd_mg_kg": null or float,\n  "methodological_quality_flags": []\n}\n## Important rules\n- Be conservative: only assign values you are confident about. Use null/empty where uncertain.\n- For multi-label fields (study_type, exposure_method, cannabis_type, outcome_domain, population), include ALL that apply.\n- Distinguish between primary research (study has original experiments) and reviews (summarizes other studies).\n- For exposure_method, focus on how cannabis/cannabinoids were administered, not other drugs or procedures.\n- For outcome_domain, exclude domains mentioned only in background/introduction context — focus on what the study actually measured.\n- For cannabinoid concentrations and doses, extract only values that pertain to cannabis/cannabinoids (not other drugs).'
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
        check_fields = ["study_type", "exposure_method", "cannabis_type", "publication_type"]
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
