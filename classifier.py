# classifier.py
import os
import json
import logging
import re
import math
from collections import Counter
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover - allows unit tests without anthropic installed
    Anthropic = None
from dotenv import load_dotenv

# Import extractor as fallback
import extractor
import classification_schema
import maude_classifier
import maude_confidence

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RULES_CONFIG_FILE = os.path.join(BASE_DIR, "rules_config.json")
RELIABILITY_MANIFEST_FILE = os.path.join(BASE_DIR, "reliability_manifest.json")

def calculate_token_cost(model: str, input_tokens: int, cache_read: int, cache_write: int, output_tokens: int) -> float:
    """Calculates API cost dynamically based on model and token types (input, output, cache)."""
    model = model.lower()
    # Default is Claude 3.5 Sonnet pricing
    input_rate = 3.00
    cache_read_rate = 0.30
    cache_write_rate = 3.75
    output_rate = 15.00
    
    if "haiku" in model:
        if "3-5" in model or "3.5" in model:
            input_rate = 1.00
            cache_read_rate = 0.10
            cache_write_rate = 1.25
            output_rate = 5.00
        else: # Claude 3 Haiku
            input_rate = 0.25
            cache_read_rate = 0.25 # No cache discount
            cache_write_rate = 0.25
            output_rate = 1.25
    elif "opus" in model:
        input_rate = 15.00
        cache_read_rate = 1.50
        cache_write_rate = 18.75
        output_rate = 75.00
        
    cost = (
        (input_tokens * input_rate) +
        (cache_read * cache_read_rate) +
        (cache_write * cache_write_rate) +
        (output_tokens * output_rate)
    ) / 1_000_000.0
    return cost

def load_rules_config() -> Dict[str, Any]:
    """Loads classification rules and configurations dynamically via heuristics_engine."""
    import heuristics_engine
    return heuristics_engine.load_rules_config()

# Order for assembling node-linked prompt sections from rules_config.decision_nodes.
DECISION_NODE_PROMPT_ORDER = (
    "node0_ingestion",
    "node1b_reviews",
    "node1c_case_report",
    "node1a_original",
    "node2a_clinical",
    "node2b_in_vivo",
    "node2c_in_vitro",
    "node2d_mixed",
    "node3a_systematic_review",
    "node3b_meta_analysis",
    "node3c_narrative_editorial",
)


def _resolve_system_prompt_base(config: Dict[str, Any]) -> str:
    """Returns the classifier preamble from split or legacy rules config."""
    base = config.get("system_prompt_base")
    if base:
        return str(base)
    legacy = config.get("system_prompt")
    if isinstance(legacy, str):
        return legacy
    return ""


def _append_node_prompt_sections(config: Dict[str, Any], prompt_blocks: List[str]) -> None:
    """Appends expert decision-tree node sections in routing order."""
    decision_nodes = config.get("decision_nodes") or {}
    if not decision_nodes:
        return

    ordered_ids = [
        node_id for node_id in DECISION_NODE_PROMPT_ORDER if node_id in decision_nodes
    ]
    remaining = [
        node_id
        for node_id in sorted(decision_nodes.keys())
        if node_id not in ordered_ids
    ]
    node_lines: List[str] = []
    for node_id in ordered_ids + remaining:
        node = decision_nodes.get(node_id) or {}
        section = node.get("prompt_section")
        if section:
            prompt_blocks.append(str(section).strip())
            continue
        purpose = node.get("purpose")
        if purpose:
            node_lines.append(f"- {node_id}: {purpose}")

    if node_lines:
        prompt_blocks.append(
            "## Expert Decision Tree Nodes\n"
            "Apply Node 1B (reviews/secondary) routing before Node 1A (original research). "
            "Use these expert node cues for publication_type and study_type routing:\n"
            + "\n".join(node_lines)
        )


def compile_system_prompt(config: Dict[str, Any]) -> str:
    """Compiles the classifier prompt from base, node sections, and expert cue blocks."""
    prompt_blocks: List[str] = []
    base_prompt = _resolve_system_prompt_base(config)
    if base_prompt:
        prompt_blocks.append(base_prompt)

    prompt_sections = config.get("prompt_sections") or {}
    for section_key in ("shared_fields", "output_format", "global_rules"):
        section_text = prompt_sections.get(section_key)
        if section_text:
            prompt_blocks.append(str(section_text).strip())

    _append_node_prompt_sections(config, prompt_blocks)

    cues = config.get("cues") or {}
    relevance_cues = cues.get("relevance") or {}
    extraction_cues = cues.get("extraction") or {}
    cue_lines = []
    
    positive = relevance_cues.get("positive_cues") or []
    negative = relevance_cues.get("negative_cues") or []
    preclinical = extraction_cues.get("preclinical_cues") or []
    clinical = extraction_cues.get("clinical_cues") or []
    
    if positive:
        cue_lines.append(f"- Positive relevance cues: {', '.join(positive)}")
    if negative:
        cue_lines.append(f"- Negative relevance cues: {', '.join(negative)}")
    if preclinical:
        cue_lines.append(f"- Preclinical extraction cues: {', '.join(preclinical)}")
    if clinical:
        cue_lines.append(f"- Clinical extraction cues: {', '.join(clinical)}")
        
    if cue_lines:
        prompt_blocks.append(
            "## Expert Classification Cues\n"
            "Use these domain expert cues as routing evidence, while still grounding every extracted value in the paper text.\n"
            + "\n".join(cue_lines)
        )
    
    boundary_lines = []
    decision_boundaries = config.get("decision_boundaries") or {}
    for boundary_name, boundary in decision_boundaries.items():
        rule = boundary.get("rule")
        example = boundary.get("example")
        expected = boundary.get("expected")
        if rule:
            boundary_lines.append(f"- {boundary_name}: {rule}")
        if example:
            boundary_lines.append(f"  Example: {example}")
        if expected:
            boundary_lines.append(f"  Expected routing: {json.dumps(expected)}")
    
    if boundary_lines:
        prompt_blocks.append(
            "## Learned Decision Boundaries\n"
            "Apply these Maude calibration lessons before extracting detailed fields:\n"
            + "\n".join(boundary_lines)
        )

    prompt_variant = os.getenv("CLASSIFIER_PROMPT_VARIANT", "control")
    variants = config.get("calibration_variants") or {}
    variant_config = variants.get(prompt_variant) or {}
    variant_suffix = variant_config.get("prompt_suffix")
    if variant_suffix:
        prompt_blocks.append(
            f"## Calibration Variant: {prompt_variant}\n"
            f"{variant_suffix}"
        )
        
    return "\n\n".join(block for block in prompt_blocks if block)

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


def _format_few_shot_examples(paper_examples: List[Dict[str, Any]]) -> str:
    """Formats retrieved correction papers into a few-shot prompt block."""
    if not paper_examples:
        return ""

    from db_manager import DatabaseManager
    db = DatabaseManager()
    few_shot_str = "\n\nExpert Guidance & Corrections:\n"
    few_shot_str += "Here are examples of how domain experts corrected previous classifications. Adhere strictly to these patterns:\n\n"

    for idx, doc in enumerate(paper_examples):
        few_shot_str += f"Example {idx + 1}:\n"
        few_shot_str += f"Title: {doc.get('title')}\n"
        few_shot_str += f"Abstract: {doc.get('abstract')}\n"
        for fc in doc.get("field_changes") or []:
            few_shot_str += f"Incorrect {fc['field_name']} Classification: {fc['old_value']}\n"
            few_shot_str += f"Correct Expert {fc['field_name']} Classification: {fc['new_value']}\n"
        few_shot_str += "\n"
    return few_shot_str


def retrieve_few_shot_context(
    title: str,
    abstract: str,
    max_examples: int = 1,
) -> tuple[str, float, bool, int]:
    """Retrieves per-paper few-shot corrections via BM25 over feedback_audit_fts.

    Returns:
        tuple: (few_shot_string, max_similarity_score, bm25_retrieval_used, example_count)
    """
    from db_manager import DatabaseManager

    db = DatabaseManager()
    query_text = f"{title or ''} {abstract or ''}".strip()
    if not query_text:
        return "", 1.0, False, 0

    ranked_rows = db.search_feedback_corrections_bm25(query_text, limit=max(10, max_examples * 4))
    if not ranked_rows:
        return "", 1.0, False, 0

    selected_papers: List[Dict[str, Any]] = []
    seen_paper_ids = set()
    max_sim = 0.0

    for row in ranked_rows:
        paper_id = row.get("paper_id")
        if paper_id in seen_paper_ids:
            continue
        seen_paper_ids.add(paper_id)
        max_sim = max(max_sim, float(row.get("retrieval_similarity") or 0.0))
        selected_papers.append({
            "paper_id": paper_id,
            "title": row.get("title"),
            "abstract": row.get("abstract"),
            "field_changes": db.get_feedback_audit_for_paper(paper_id),
        })
        if len(selected_papers) >= max_examples:
            break

    few_shot_text = _format_few_shot_examples(selected_papers)
    if not few_shot_text:
        return "", max_sim, False, 0

    return few_shot_text, max_sim, True, len(selected_papers)


def get_few_shot_examples(new_title: str, new_abstract: str, max_examples: int = 1) -> tuple[str, float]:
    """Retrieves relevant historical expert corrections as few-shot examples.

    Returns:
        tuple: (few_shot_string, max_similarity_score)
    """
    few_shot_text, max_sim, _, _ = retrieve_few_shot_context(new_title, new_abstract, max_examples=max_examples)
    return few_shot_text, max_sim


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

def classify_with_llm(title: str, abstract: str, runs: Optional[int] = None, full_text: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Uses Claude 3.5 Sonnet to perform deep extraction of scientific parameters.
    
    Returns:
        Optional[Dict]: Structured fields or None if LLM call fails.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or Anthropic is None:
        logger.debug("No ANTHROPIC_API_KEY environment variable set. Skipping LLM pass.")
        return None
        
    config = load_rules_config()
    static_prompt = compile_system_prompt(config)
    
    # Retrieve dynamic few-shot corrections via BM25 over feedback_audit_fts.
    few_shot_text, max_sim, bm25_retrieval_used, few_shot_count = retrieve_few_shot_context(title, abstract)
    
    # 1. Run Tier 1 native abstract classification via heuristics
    h = extractor.extract_all_heuristics(title, abstract)
    h_study = set(h.get("study_type") or [])
    h_exposure = set(h.get("exposure_method") or [])
    pub_type = h.get("publication_type")
    
    # Load reliability manifest
    manifest = {}
    if os.path.exists(RELIABILITY_MANIFEST_FILE):
        try:
            with open(RELIABILITY_MANIFEST_FILE, "r") as f:
                manifest = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load reliability manifest: {e}")
            
    # Default fallback is study_type is reliable, exposure/cannabis type are unreliable
    preclinical_study_reliable = True
    preclinical_exposure_reliable = False
    preclinical_cannabis_reliable = False
    clinical_study_reliable = True
    clinical_exposure_reliable = False
    clinical_cannabis_reliable = False
    
    if manifest:
        metrics = manifest.get("metrics", {})
        preclinical_study_reliable = metrics.get("preclinical", {}).get("study_type", {}).get("reliable", True)
        preclinical_exposure_reliable = metrics.get("preclinical", {}).get("exposure_method", {}).get("reliable", False)
        preclinical_cannabis_reliable = metrics.get("preclinical", {}).get("cannabis_type", {}).get("reliable", False)
        clinical_study_reliable = metrics.get("clinical", {}).get("study_type", {}).get("reliable", True)
        clinical_exposure_reliable = metrics.get("clinical", {}).get("exposure_method", {}).get("reliable", False)
        clinical_cannabis_reliable = metrics.get("clinical", {}).get("cannabis_type", {}).get("reliable", False)

    dynamic_rules_list = []
    
    # 2. Build dynamic rules list
    if pub_type == "original research":
        dynamic_rules_list.append("## Dynamic Context-Specific Extraction Rules (Original Research)")
        
        is_preclinical = any("animal" in s.lower() or "cell" in s.lower() or "vitro" in s.lower() for s in h_study)
        is_clinical = any("clinical" in s.lower() or "rct" in s.lower() or "observational" in s.lower() for s in h_study)
        
        if is_preclinical and preclinical_study_reliable:
            dynamic_rules_list.append("### Preclinical (Animal / In Vitro) Guidelines:")
            dynamic_rules_list.append("- Prioritize extracting details from the **Materials & Methods** section.")
            dynamic_rules_list.append("- Extract chemical manufacturers, suppliers, and compound/ligand details into `strain_reported` (e.g. 'Sigma-Aldrich', 'THC Pharm', 'Cayman Chemical').")
            dynamic_rules_list.append("- **CRITICAL**: Do NOT extract host animal strains (e.g. Sprague-Dawley, Wistar, C57BL/6, Wistar rats) into `strain_reported`.")
            
            is_invitro = any("cell" in s.lower() or "vitro" in s.lower() for s in h_study)
            if is_invitro:
                dynamic_rules_list.append("- For Cell Culture models: Extract treatment duration into `treatment_duration`. Extract micromolar concentrations (e.g., 5 µM) as numeric values into `thc_uM` or `cbd_uM`.")
                
            is_invivo = any("animal" in s.lower() for s in h_study)
            if is_invivo:
                dynamic_rules_list.append("- For Animal Models: Extract in vivo doses in mg/kg or mg/g into `thc_mg_kg` / `cbd_mg_kg` / `thc_mg_g` / `cbd_mg_g`. Extract study duration in days into `duration_days` and frequency into `administration_frequency`.")
                
            if preclinical_exposure_reliable:
                dynamic_rules_list.append("- **Preclinical Exposure Specific Instructions**: Since Tier 1 exposure classification is highly reliable, pay extra attention to extraction details matching the detected preclinical route.")
                
        if is_clinical and clinical_study_reliable:
            dynamic_rules_list.append("### Clinical (Human Study) Guidelines:")
            dynamic_rules_list.append("- Focus strictly on clinical parameters. Do NOT extract cell line names, animal strains, or chemical suppliers/manufacturers into `strain_reported`.")
            dynamic_rules_list.append("- Extract study duration (days/weeks/months) into `duration_days` and sample size (number of patients) into `sample_size`.")
            
            is_inhaled = any("inhaled" in e.lower() or "smoke" in e.lower() or "vapor" in e.lower() or "vape" in e.lower() for e in h_exposure)
            if is_inhaled and clinical_exposure_reliable:
                dynamic_rules_list.append("- For Inhaled Studies: Extract exposure duration per session (e.g., '30 minutes') into `inhaled_exposure_duration` and puff counts into `puff_count`.")
                
    elif pub_type in ("review",):
        dynamic_rules_list.append("## Dynamic Context-Specific Extraction Rules (Review Paper)")
        dynamic_rules_list.append("- This is a review paper. Do NOT extract individual animal strains, cell lines, supplier details, or dose values from reviewed papers. Leave these fields null/empty unless they describe the review methodology itself.")

    dynamic_rules_str = "\n".join(dynamic_rules_list) if dynamic_rules_list else ""
    
    if dynamic_rules_str:
        logger.info(f"Dynamically injected context-specific rules for {pub_type} study type: {list(h_study)} (manifest flags: preclinical_study={preclinical_study_reliable}, clinical_study={clinical_study_reliable})")

    # Build system prompt blocks utilizing prompt caching for the large static ruleset
    system_blocks = [
        {
            "type": "text",
            "text": static_prompt,
            "cache_control": {"type": "ephemeral"}
        }
    ]
    if dynamic_rules_str:
        system_blocks.append({
            "type": "text",
            "text": dynamic_rules_str
        })
    if few_shot_text:
        system_blocks.append({
            "type": "text",
            "text": few_shot_text
        })
        
    num_runs = runs if runs is not None else config.get("self_consistency_runs", 1)
    
    total_input_tokens = 0
    total_cache_read = 0
    total_cache_write = 0
    total_output_tokens = 0
    actual_model_used = "unknown"
 
    try:
        client = Anthropic(api_key=api_key)
        if full_text:
            # Truncate full text to first 100,000 characters to prevent excessive tokens
            truncated_text = full_text[:100000]
            user_content = f"Title: {title}\n\nAbstract: {abstract}\n\nFull Paper Text (PDF):\n{truncated_text}"
        else:
            user_content = f"Title: {title}\n\nAbstract: {abstract}"
        
        parsed_results = []
        
        for run_idx in range(num_runs):
            # Run LLM pass. For multi-run self-consistency, add minor temperature to encourage variation
            temp = 0.5 if num_runs > 1 else 0.0
            
            logger.info(f"Sending paper details to Anthropic API for classification (Run {run_idx+1}/{num_runs})...")
            models_to_try = [
                "claude-sonnet-4-6",
                "claude-haiku-4-5-20251001",
                "claude-sonnet-4-5-20250929",
                "claude-3-5-sonnet-20241022",
                "claude-3-5-sonnet-20240620",
                "claude-3-5-haiku-20241022",
                "claude-3-haiku-20240307"
            ]
            message = None
            for model_name in models_to_try:
                try:
                    message = client.messages.create(
                        model=model_name,
                        max_tokens=1000,
                        temperature=temp,
                        system=system_blocks,
                        messages=[
                            {"role": "user", "content": user_content}
                        ]
                    )
                    
                    actual_model_used = model_name
                    # Log cache metrics if available
                    if message and hasattr(message, "usage"):
                        usage = message.usage
                        creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
                        read = getattr(usage, "cache_read_input_tokens", 0) or 0
                        if creation == 0 and hasattr(usage, "cache_creation") and usage.cache_creation:
                            creation = getattr(usage.cache_creation, "ephemeral_5m_input_tokens", 0) or 0
                        
                        total_input_tokens += usage.input_tokens
                        total_cache_read += read
                        total_cache_write += creation
                        total_output_tokens += usage.output_tokens
                        
                        logger.info(f"Anthropic API call complete. Input: {usage.input_tokens} tokens (Cached read: {read} tokens, Cached write: {creation} tokens), Output: {usage.output_tokens} tokens")
                    
                    break
                except Exception as api_err:
                    if "not_found_error" in str(api_err) or "404" in str(api_err):
                        logger.warning(f"Model {model_name} not found. Trying next fallback...")
                        continue
                    else:
                        raise api_err
            
            if not message:
                raise RuntimeError("All configured Anthropic models returned 404 Not Found. Please verify your API key privileges.")
            
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
        
        # Calculate cost
        cost = calculate_token_cost(
            actual_model_used,
            total_input_tokens,
            total_cache_read,
            total_cache_write,
            total_output_tokens
        )
        
        # Inject metadata into result
        consensus["classification_confidence"] = final_confidence
        consensus["classification_timestamp"] = datetime.now().isoformat()
        consensus["classifier_version"] = config.get("version", "1.0.0")
        
        consensus["_llm_call_metrics"] = {
            "model": actual_model_used,
            "input_tokens": total_input_tokens,
            "cache_read_tokens": total_cache_read,
            "cache_write_tokens": total_cache_write,
            "output_tokens": total_output_tokens,
            "cost": cost,
            "few_shot_similarity": max_sim,
            "few_shot_count": few_shot_count,
            "bm25_retrieval_used": 1 if bm25_retrieval_used else 0,
            "classification_confidence": final_confidence,
            "classifier_version": config.get("version", "1.0.0")
        }
        
        logger.info(f"LLM Classification complete. Confidence: {final_confidence:.2f}")
        return consensus
        
    except Exception as e:
        logger.error(f"Anthropic LLM classification failed: {e}. Falling back to heuristics.")
        return None


def get_rules_version() -> str:
    """Returns the active rules configuration version string."""
    return load_rules_config().get("version", "1.0.0")


def classify_with_maude(
    title: str,
    abstract: str,
    *,
    full_text: Optional[str] = None,
    full_text_link: Optional[str] = None,
    pmid: Optional[str] = None,
    doi: Optional[str] = None,
    cache: Optional[Dict[str, Optional[str]]] = None,
    abstract_only: bool = False,
    text_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Runs the RL-tuned Maude classifier using PDF, article full text, or abstract."""
    import calibration_build
    import calibration_pdf

    rules_version = get_rules_version()
    source = calibration_pdf.CLASSIFICATION_SOURCE_ABSTRACT
    resolved_text = full_text
    if abstract_only:
        resolved_text = None
        source = calibration_pdf.CLASSIFICATION_SOURCE_ABSTRACT
    elif resolved_text is not None:
        if text_source is not None:
            source = calibration_pdf.normalize_classification_source(text_source)
        elif not str(resolved_text).strip():
            resolved_text = None
            source = calibration_pdf.CLASSIFICATION_SOURCE_ABSTRACT
        else:
            source = calibration_pdf.CLASSIFICATION_SOURCE_FULLTEXT
    elif resolved_text is None:
        resolved_text, source = calibration_pdf.resolve_classification_full_text(
            full_text_link=full_text_link,
            pmid=pmid,
            doi=doi,
            cache=cache,
        )

    result = maude_classifier.classify_paper(
        title,
        abstract,
        full_text=resolved_text,
        rules_version=rules_version,
        abstract_only_extraction=False if resolved_text is not None else None,
    )
    if not result.get("summary"):
        result["summary"] = extractor.generate_heuristic_summary(result)
    result["classification_timestamp"] = datetime.now().isoformat()
    row_index: Optional[int] = None
    golden_row = os.getenv("GOLDEN_ROW_INDEX")
    if golden_row is not None:
        try:
            row_index = int(golden_row)
        except ValueError:
            row_index = None
    result["classifier_version"] = calibration_pdf.maude_classifier_version(
        source, rules_version, row_index=row_index,
    )
    # Apply confidence before dropping meta so cue_score / methods_used can adjust it.
    maude_confidence.apply_maude_confidence(result)
    result.pop("_maude_meta", None)
    logger.info(
        "Maude classification complete (build=%s rules=v%s source=%s version=%s).",
        calibration_build.MAUDE_CLASSIFIER_BUILD_ID,
        rules_version,
        source,
        result["classifier_version"],
    )
    return result


def _legacy_heuristic_metadata(title: str, abstract: str) -> Dict[str, Any]:
    """Last-resort abstract heuristics when Maude classification raises unexpectedly."""
    metadata = extractor.extract_all_heuristics(title, abstract)
    metadata["classification_confidence"] = 0.6
    metadata["classification_timestamp"] = datetime.now().isoformat()
    metadata["classifier_version"] = "heuristic-1.0.0"
    return metadata


def process_paper_metadata(
    title: str,
    abstract: str,
    run_llm: bool = False,
    runs: Optional[int] = None,
    full_text: Optional[str] = None,
    full_text_link: Optional[str] = None,
    pmid: Optional[str] = None,
    doi: Optional[str] = None,
    pdf_cache: Optional[Dict[str, Optional[str]]] = None,
    abstract_only: bool = False,
    text_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Extracts metadata from paper via Claude LLM or the Maude rule/cue classifier.

    Harvest and daily ingest use Maude by default (``run_llm=False``). Maude resolves text in
    priority order: PDF link → article full text (PMC/HTML) → abstract.

    Args:
        title: Title of the paper
        abstract: Abstract text of the paper
        run_llm: Whether to attempt LLM classification
        runs: Optional count of self-consistency runs override
        full_text: Optional pre-resolved full text (skips PDF/PMC fetch)
        full_text_link: Optional PDF or publisher URL for text resolution
        pmid: Optional PubMed ID for Europe PMC full-text lookup
        doi: Optional DOI for Europe PMC full-text lookup
        pdf_cache: Optional per-run cache for PDF/PMC/HTML fetches
        abstract_only: When True, Maude uses abstract only (no PDF/PMC/HTML fetch).
        text_source: Optional resolved tier label (pdf/fulltext/abstract) when full_text is preset.

    Returns:
        Dict containing all extracted metadata.
    """
    metadata = None

    if run_llm:
        if full_text is None and full_text_link and not pdf_cache:
            import calibration_pdf
            full_text, _ = calibration_pdf.resolve_classification_full_text(
                full_text_link=full_text_link,
                pmid=pmid,
                doi=doi,
            )
        metadata = classify_with_llm(title, abstract, runs=runs, full_text=full_text)
        if metadata and not metadata.get("summary"):
            metadata["summary"] = extractor.generate_heuristic_summary(metadata)

    if not metadata:
        rules_version = get_rules_version()
        logger.info("Running Maude rule/cue classifier (rules v%s).", rules_version)
        try:
            metadata = classify_with_maude(
                title,
                abstract,
                full_text=full_text,
                full_text_link=full_text_link,
                pmid=pmid,
                doi=doi,
                cache=pdf_cache,
                abstract_only=abstract_only,
                text_source=text_source,
            )
        except Exception as exc:
            logger.error("Maude classification failed: %s. Falling back to legacy heuristics.", exc)
            metadata = _legacy_heuristic_metadata(title, abstract)

    return classification_schema.normalize_classification_record(metadata, title, abstract)
