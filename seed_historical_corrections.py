# seed_historical_corrections.py
import sqlite3
import json
from datetime import datetime, timedelta

def main():
    db_path = "cannabis_papers.db"
    print(f"Connecting to database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    try:
        # Get unique audited paper IDs and their earliest audit timestamps
        cursor.execute("""
            SELECT paper_id, MIN(timestamp) as earliest_audit
            FROM feedback_audit
            GROUP BY paper_id
        """)
        audited_papers = [dict(row) for row in cursor.fetchall()]
        print(f"Found {len(audited_papers)} unique corrected papers in feedback_audit.")
        
        inserted_count = 0
        for p in audited_papers:
            paper_id = p["paper_id"]
            earliest_audit_str = p["earliest_audit"]
            
            # Parse the audit timestamp (handling optional fractional seconds)
            try:
                if "." in earliest_audit_str:
                    audit_time = datetime.strptime(earliest_audit_str, "%Y-%m-%dT%H:%M:%S.%f")
                else:
                    audit_time = datetime.strptime(earliest_audit_str, "%Y-%m-%dT%H:%M:%S")
            except Exception as e:
                print(f"Error parsing timestamp {earliest_audit_str}: {e}")
                continue
                
            # Create a classification timestamp 10 minutes BEFORE the expert correction
            call_time = audit_time - timedelta(minutes=10)
            call_time_str = call_time.isoformat()
            
            # Check if an LLM call log already exists for this paper around or before this timestamp
            cursor.execute("""
                SELECT COUNT(*) as cnt 
                FROM llm_calls_log 
                WHERE paper_id = ? AND timestamp < ?
            """, (paper_id, earliest_audit_str))
            
            existing = cursor.fetchone()["cnt"]
            if existing > 0:
                print(f"Paper ID {paper_id} already has {existing} LLM call log(s) before audit. Skipping.")
                continue
                
            # Insert retrospective call log showing classification without few-shot learning
            # (since it was corrected, the initial classification was wrong/low-confidence/without FS)
            model = "claude-3-5-sonnet-20241022"
            input_tokens = 1500
            cache_read_tokens = 0
            cache_write_tokens = 0
            output_tokens = 350
            cost = 0.00975
            few_shot_similarity = 0.0
            few_shot_count = 0
            classification_confidence = 0.60
            classifier_version = "llm-classify-1.0.0"
            batch_id = "historical_pre_few_shot"
            
            cursor.execute("""
                INSERT INTO llm_calls_log (
                    paper_id, timestamp, model, input_tokens, cache_read_tokens, cache_write_tokens,
                    output_tokens, cost, few_shot_similarity, few_shot_count, classification_confidence, classifier_version, batch_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                paper_id, call_time_str, model, input_tokens, cache_read_tokens, cache_write_tokens,
                output_tokens, cost, few_shot_similarity, few_shot_count, classification_confidence, classifier_version, batch_id
            ))
            inserted_count += 1
            print(f"Inserted retrospective call for Paper ID {paper_id} at {call_time_str} (before audit at {earliest_audit_str})")
            
        conn.commit()
        print(f"Successfully inserted {inserted_count} retrospective LLM call records.")
        
    except Exception as e:
        conn.rollback()
        print(f"Error during seeding: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
