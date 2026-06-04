# app.py
import os
import json
import threading
import logging
from datetime import datetime, date
from flask import Flask, render_template, request, jsonify, session, redirect, url_for

from db_manager import DatabaseManager
import classifier
import harvest
from extractor import is_cannabis_related

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "mckenzian-secret-key-12345")
ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "admin123")

# Core global state to track active background harvesting progress
harvest_lock = threading.Lock()
harvest_state = {
    "status": "idle",      # idle, running, success, error
    "progress": "",        # Real-time text output
    "error": None,         # Error message
    "start_time": None
}

def daily_harvest_scheduler():
    """Daily background task scheduler that runs the harvest pipeline for standard terms once a day."""
    import time
    
    sched_logger = logging.getLogger("scheduler")
    sched_logger.info("Daily background scheduler thread starting...")
    db = DatabaseManager()
    
    # Store initial info
    db.set_metadata("scheduler_active", "true")
    if not db.get_metadata("last_daily_harvest_status"):
        db.set_metadata("last_daily_harvest_status", "Never run")
        
    query = "cannabis OR cannabinoid OR marijuana"
    max_results = 200
    
    while True:
        try:
            classify = os.getenv("AUTO_HARVEST_CLASSIFY", "false").lower() == "true"
            today_str = date.today().isoformat()  # YYYY-MM-DD
            last_run_date = db.get_metadata("last_daily_harvest_date")
            
            if last_run_date != today_str:
                sched_logger.info(f"Daily scheduler: starting automated harvest for query '{query}' (today: {today_str}, last run: {last_run_date})")
                db.set_metadata("last_daily_harvest_status", f"Running automated harvest since {datetime.now().strftime('%H:%M:%S')}...")
                
                # Call the unified pipeline
                success_count, skipped_count, filter_skipped = harvest.run_harvest_pipeline(
                    query=query,
                    max_results=max_results,
                    update=True,
                    classify=classify
                )
                
                # Run the purger to clean up any accidentally added unrelated papers
                sched_logger.info("Daily scheduler: Running purge_unrelated to clean up acronym-collision outliers...")
                try:
                    import purge_unrelated
                    purge_unrelated.run_purger(dry_run=False)
                    sched_logger.info("Daily scheduler: Cleanse completed successfully.")
                except Exception as purge_err:
                    sched_logger.error(f"Daily scheduler: Purge process failed: {purge_err}")
                
                # Mark as successful
                date_str = datetime.now().isoformat()
                status_msg = f"Success! Harvest complete. Ingested {success_count} papers (skipped {skipped_count} pre-existing, filtered {filter_skipped} unrelated) at {datetime.now().strftime('%H:%M:%S')}."
                sched_logger.info(f"Daily scheduler status: {status_msg}")
                db.set_metadata("last_daily_harvest_date", today_str)
                db.set_metadata("last_daily_harvest_timestamp", date_str)
                db.set_metadata("last_daily_harvest_status", status_msg)
            else:
                # Already run today
                pass
                
        except Exception as e:
            err_msg = f"Automated harvest failed: {e}"
            sched_logger.error(err_msg)
            db.set_metadata("last_daily_harvest_status", f"Error at {datetime.now().strftime('%H:%M:%S')}: {e}")
            
        # Check every hour
        time.sleep(3600)

def bg_harvest_worker(query: str, max_results: int, update: bool, classify: bool):
    """Asynchronous background worker that runs the harvest pipeline and updates progress state."""
    global harvest_state
    
    with harvest_lock:
        harvest_state["status"] = "running"
        harvest_state["progress"] = "Initializing background harvester..."
        harvest_state["error"] = None
        harvest_state["start_time"] = datetime.now().strftime("%H:%M:%S")

    try:
        def update_progress(msg):
            with harvest_lock:
                harvest_state["progress"] = msg
                
        success_count, skipped_count, filter_skipped = harvest.run_harvest_pipeline(
            query=query,
            max_results=max_results,
            update=update,
            classify=classify,
            progress_callback=update_progress
        )
            
        with harvest_lock:
            harvest_state["status"] = "success"
            harvest_state["progress"] = f"Success! Harvest complete. Ingested {success_count} papers (skipped {skipped_count} pre-existing, filtered {filter_skipped} unrelated)."
            
    except Exception as e:
        with harvest_lock:
            harvest_state["status"] = "error"
            harvest_state["progress"] = "Scraper execution failed."
            harvest_state["error"] = str(e)

@app.before_request
def require_login():
    # Allow access to login route and static assets without session
    if request.path == '/login' or request.path.startswith('/static/'):
        return
    if session.get("logged_in"):
        return
    # If not logged in and requesting API, return JSON 401
    if request.path.startswith('/api/'):
        return jsonify({"error": "Unauthorized. Please log in."}), 401
    # If not logged in and requesting page, redirect to login
    return redirect(url_for('login'))

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        password = request.form.get("password")
        if password == ACCESS_PASSWORD:
            session["logged_in"] = True
            session.permanent = True
            return redirect(url_for("index"))
        else:
            error = "Invalid credentials. Please try again."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
def index():
    """Serves the Single-Page Application dynamic research dashboard."""
    return render_template("index.html")

@app.route("/api/search", methods=["GET"])
def api_search():
    """API endpoint to query, dynamic filter, and sort papers."""
    db = DatabaseManager()
    
    # Retrieve dynamic filters from HTTP query args
    query = request.args.get("query")
    year_min = request.args.get("year_min")
    year_max = request.args.get("year_max")
    study_type = request.args.get("study_type")
    exposure_method = request.args.get("method")
    thc_min = request.args.get("thc_min")
    thc_max = request.args.get("thc_max")
    population = request.args.get("population")
    outcome = request.args.get("outcome")
    flags = request.args.get("flags")
    open_access = request.args.get("open_access")
    sort_by = request.args.get("sort_by")
    quality_min = request.args.get("quality_min")
    citations_min = request.args.get("citations_min")
    recent = request.args.get("recent")
    recent_range = request.args.get("recent_range")
    tab = request.args.get("tab")
    cannabis_type = request.args.get("cannabis_type")
    cannabis_logic = request.args.get("cannabis_logic", "or")
    method_logic = request.args.get("method_logic", "or")
    population_logic = request.args.get("population_logic", "or")
    outcome_logic = request.args.get("outcome_logic", "or")
    
    page = request.args.get("page", 1)
    limit = request.args.get("limit", 50)
    try:
        page = int(page)
    except (ValueError, TypeError):
        page = 1
    try:
        limit = int(limit)
    except (ValueError, TypeError):
        limit = 50

    # Clean filters
    clean_filters = {}
    if query:
        clean_filters["query"] = query
    if year_min:
        clean_filters["year_min"] = int(year_min)
    if year_max:
        clean_filters["year_max"] = int(year_max)
    if study_type and study_type != "ALL":
        clean_filters["study_type"] = study_type
    if exposure_method and exposure_method != "ALL":
        clean_filters["exposure_method"] = exposure_method
    if thc_min:
        clean_filters["thc_min"] = float(thc_min)
    if thc_max:
        clean_filters["thc_max"] = float(thc_max)
    if population and population != "ALL":
        clean_filters["population"] = population
    if outcome:
        clean_filters["outcome"] = outcome
    if flags:
        clean_filters["flags"] = flags
    if open_access and open_access != "ALL":
        clean_filters["open_access"] = open_access == "true"
    if sort_by and sort_by != "DEFAULT":
        clean_filters["sort_by"] = sort_by
    if quality_min is not None and quality_min != "":
        clean_filters["quality_min"] = int(quality_min)
    if citations_min is not None and citations_min != "":
        clean_filters["citations_min"] = int(citations_min)
    if tab:
        clean_filters["tab"] = tab
    elif recent and recent.lower() == "true":
        clean_filters["tab"] = "recent"
    if recent_range:
        clean_filters["recent_range"] = recent_range
    if cannabis_type and cannabis_type != "ALL":
        clean_filters["cannabis_type"] = cannabis_type
        
    clean_filters["cannabis_logic"] = cannabis_logic
    clean_filters["exposure_logic"] = method_logic
    clean_filters["population_logic"] = population_logic
    clean_filters["outcome_logic"] = outcome_logic
        
    try:
        total_count = db.count_papers(clean_filters)
        
        # Apply pagination params
        clean_filters["limit"] = limit
        clean_filters["offset"] = (page - 1) * limit
        
        results = db.search_papers(clean_filters)
        
        # Calculate newly_harvested boolean based on last auto-harvest timestamp
        last_harvest_ts = db.get_metadata("last_daily_harvest_timestamp")
        for paper in results:
            paper_harvested = paper.get("date_harvested")
            if last_harvest_ts and paper_harvested:
                paper["newly_harvested"] = paper_harvested >= last_harvest_ts
            else:
                paper["newly_harvested"] = False
                
        return jsonify({
            "papers": results,
            "total_count": total_count,
            "page": page,
            "limit": limit
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/papers/delete", methods=["POST"])
def api_delete_papers():
    """Deletes multiple papers by their database IDs."""
    data = request.get_json() or {}
    ids = data.get("ids", [])
    if not ids:
        return jsonify({"error": "No paper IDs provided."}), 400
        
    db = DatabaseManager()
    success_count = 0
    try:
        for pid in ids:
            if db.delete_paper(int(pid)):
                success_count += 1
        return jsonify({"message": f"Successfully deleted {success_count} papers."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/harvest", methods=["POST"])
def api_harvest():
    """Triggers an asynchronous background search & ingest run."""
    global harvest_state
    
    with harvest_lock:
        if harvest_state["status"] == "running":
            return jsonify({"error": "A harvesting process is already actively running."}), 400
            
    # Read payload parameters
    data = request.get_json() or {}
    query = data.get("query")
    update = bool(data.get("update", True))
    classify = bool(data.get("classify", False))
    force = bool(data.get("force", False))
    max_results = data.get("max_results")
    
    if not query:
        return jsonify({"error": "Search query is required."}), 400
        
    if not force:
        # Fetch the total number of matching papers on PubMed
        total_count = harvest.get_pubmed_count(query)
        if total_count > 500:
            return jsonify({
                "status": "prompt",
                "total_count": total_count
            })
        else:
            # If total_count is under 500, ingest them all automatically
            max_results = total_count if total_count > 0 else 50
    else:
        try:
            max_results = int(max_results)
        except (ValueError, TypeError):
            max_results = 500
        
    # Start thread
    thread = threading.Thread(
        target=bg_harvest_worker,
        args=(query, max_results, update, classify)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({"message": "Background harvest started successfully.", "status": "success"})

@app.route("/api/harvest/status", methods=["GET"])
def api_harvest_status():
    """Returns the real-time status and logs of the background harvest worker."""
    with harvest_lock:
        return jsonify(harvest_state)

@app.route("/api/scheduler/status", methods=["GET"])
def api_scheduler_status():
    """Returns the daily background scheduler status from the metadata table."""
    db = DatabaseManager()
    active = db.get_metadata("scheduler_active", "false")
    last_date = db.get_metadata("last_daily_harvest_date", "Never")
    last_timestamp = db.get_metadata("last_daily_harvest_timestamp", "Never")
    last_status = db.get_metadata("last_daily_harvest_status", "Never run")
    return jsonify({
        "active": active == "true",
        "last_run_date": last_date,
        "last_run_timestamp": last_timestamp,
        "last_run_status": last_status,
        "query": "cannabis OR cannabinoid OR marijuana"
    })

# Start the background daily scheduler thread, protected against debug reloader double-runs
if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    logging.getLogger("scheduler").info("Launching daily automatic harvest scheduler thread...")
    threading.Thread(target=daily_harvest_scheduler, daemon=True).start()

if __name__ == "__main__":
    # Start server on local network port 5001 to bypass macOS default AirPlay port conflict (5000)
    app.run(debug=True, port=5001)

