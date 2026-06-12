# app.py
import os
import json
import threading
import logging
from datetime import datetime, date
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import random
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from db_manager import DatabaseManager
import classifier
import harvest
from extractor import is_cannabis_related
from citation_graph import CitationGraph

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

def send_verification_email(recipient_email, username, code):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = os.getenv("SMTP_PORT", "587")
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_sender = os.getenv("SMTP_SENDER", smtp_username)

    if not smtp_server or not smtp_username or not smtp_password:
        print(f"[SMTP WARNING] SMTP environment variables are not fully configured. Cannot send real email to {recipient_email}.")
        print(f"[SMTP VERIFICATION CODE] Code for {username}: {code}")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Email Verification | Cannabis Research Catalog"
        msg["From"] = smtp_sender
        msg["To"] = recipient_email

        text = f"Hello {username},\n\nYour email verification code is: {code}\n\nPlease enter this code in the Cannabis Research Catalog portal to complete your registration."
        html = f"""
        <html>
          <body style="font-family: Arial, sans-serif; background-color: #0b0f19; color: #f8fafc; padding: 20px;">
            <div style="max-width: 500px; margin: 0 auto; background-color: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 30px;">
              <h2 style="color: #6366f1; font-family: 'Outfit', sans-serif;">Verify Your Email Address</h2>
              <p style="color: #94a3b8;">Hello <strong>{username}</strong>,</p>
              <p style="color: #94a3b8;">Thank you for registering. Please use the following code to verify your email address and activate your account:</p>
              <div style="background-color: rgba(99, 102, 241, 0.1); border: 1px dashed #6366f1; padding: 15px; border-radius: 8px; font-size: 24px; font-weight: bold; letter-spacing: 4px; text-align: center; color: #f8fafc; margin: 25px 0;">
                {code}
              </div>
              <p style="color: #94a3b8; font-size: 12px;">If you did not request this code, you can safely ignore this email.</p>
            </div>
          </body>
        </html>
        """
        part1 = MIMEText(text, "plain")
        part2 = MIMEText(html, "html")
        msg.attach(part1)
        msg.attach(part2)

        server = smtplib.SMTP(smtp_server, int(smtp_port))
        server.starttls()
        server.login(smtp_username, smtp_password)
        server.sendmail(smtp_sender, recipient_email, msg.as_string())
        server.quit()
        print(f"[SMTP INFO] Sent real verification email to {recipient_email}")
        return True
    except Exception as e:
        print(f"[SMTP ERROR] Failed to send email to {recipient_email}: {e}")
        return False

@app.before_request
def require_login():
    allowed_paths = [
        '/',
        '/login',
        '/signup',
        '/verify-email',
        '/auth/google',
        '/auth/google/callback',
        '/logout'
    ]
    if request.path in allowed_paths or request.path.startswith('/static/'):
        return
        
    # REMOVED: auth requirement — all buttons unlocked

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("index"))
        
    error = None
    if request.method == "POST":
        identifier = request.form.get("username_or_email", "").strip()
        password = request.form.get("password", "")
        
        db = DatabaseManager()
        user = db.get_user_by_username_or_email(identifier)
        
        if user and user["password_hash"] and db.check_password(password, user["password_hash"]):
            if user["is_verified"] == 0:
                return redirect(url_for("verify_email", username=user["username"]))
            else:
                session["logged_in"] = True
                session["username"] = user["username"]
                session["email"] = user["email"]
                session["is_google"] = False
                session.permanent = True
                return redirect(url_for("index"))
        else:
            error = "Invalid username/email or password."
            
    return render_template("login.html", error=error)

@app.route("/signup", methods=["POST"])
def signup():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    
    if not username or not email or not password:
        return render_template("login.html", signup_error="All fields are required.", active_tab="signup")
        
    db = DatabaseManager()
    
    existing = db.get_user_by_username_or_email(username)
    if existing:
        return render_template("login.html", signup_error="Username already exists.", active_tab="signup")
    existing_email = db.get_user_by_username_or_email(email)
    if existing_email:
        return render_template("login.html", signup_error="Email already registered.", active_tab="signup")
        
    code = f"{random.randint(100000, 999999)}"
    password_hash = db.hash_password(password)
    
    success = db.create_user(
        username=username,
        email=email,
        password_hash=password_hash,
        google_id=None,
        is_verified=0,
        verification_code=code
    )
    
    if success:
        send_verification_email(email, username, code)
        return redirect(url_for("verify_email", username=username))
    else:
        return render_template("login.html", signup_error="Registration failed. Please try again.", active_tab="signup")

@app.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    username = request.args.get("username") or request.form.get("username")
    if not username:
        return redirect(url_for("login"))
        
    db = DatabaseManager()
    user = db.get_user_by_username_or_email(username)
    if not user:
        return redirect(url_for("login"))
        
    error = None
    if request.method == "POST":
        entered_code = request.form.get("code", "").strip()
        if entered_code == user["verification_code"]:
            db.verify_user(username)
            session["logged_in"] = True
            session["username"] = user["username"]
            session["email"] = user["email"]
            session["is_google"] = False
            session.permanent = True
            return redirect(url_for("index"))
        else:
            error = "Invalid verification code. Please try again."
            
    smtp_configured = bool(os.getenv("SMTP_SERVER") and os.getenv("SMTP_USERNAME") and os.getenv("SMTP_PASSWORD"))
    dev_code = user["verification_code"] if not smtp_configured else None
    
    return render_template("verify.html", username=username, email=user["email"], error=error, dev_code=dev_code)

@app.route("/auth/google")
def auth_google():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
    if not redirect_uri:
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        redirect_uri = f"{scheme}://{request.host}/auth/google/callback"
        
    if not client_id:
        print("[GOOGLE AUTH WARNING] GOOGLE_CLIENT_ID environment variable not configured. Falling back to mock Google login.")
        return render_template("google_auth.html")
        
    import urllib.parse
    state = f"{random.randint(100000, 999999)}"
    session["google_oauth_state"] = state
    
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state
    }
    
    google_auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return redirect(google_auth_url)

@app.route("/auth/google/callback")
def auth_google_callback():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
    if not redirect_uri:
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        redirect_uri = f"{scheme}://{request.host}/auth/google/callback"
        
    if not client_id or not client_secret:
        email = request.args.get("email")
        name = request.args.get("name") or email.split("@")[0]
        if not email:
            return redirect(url_for("login"))
            
        db = DatabaseManager()
        google_id = f"mock_google_{email}"
        user = db.get_user_by_google_id(google_id)
        if not user:
            user_by_email = db.get_user_by_username_or_email(email)
            if user_by_email:
                user = user_by_email
                conn = db.get_connection()
                try:
                    conn.execute("UPDATE users SET google_id = ?, is_verified = 1 WHERE id = ?;", (google_id, user["id"]))
                    conn.commit()
                finally:
                    conn.close()
                user = db.get_user_by_google_id(google_id)
            else:
                db.create_user(username=name, email=email, google_id=google_id, is_verified=1)
                user = db.get_user_by_google_id(google_id)
                
        session["logged_in"] = True
        session["username"] = user["username"]
        session["email"] = user["email"]
        session["is_google"] = True
        session.permanent = True
        return redirect(url_for("index"))
        
    code = request.args.get("code")
    state = request.args.get("state")
    
    stored_state = session.pop("google_oauth_state", None)
    if not code or (stored_state and state != stored_state):
        return render_template("login.html", error="Google authentication failed: state mismatch.")
        
    try:
        token_url = "https://oauth2.googleapis.com/token"
        data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code"
        }
        token_resp = requests.post(token_url, data=data, timeout=10)
        token_resp.raise_for_status()
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        
        if not access_token:
            return render_template("login.html", error="Failed to fetch access token from Google.")
            
        userinfo_url = "https://www.googleapis.com/oauth2/v3/userinfo"
        headers = {"Authorization": f"Bearer {access_token}"}
        userinfo_resp = requests.get(userinfo_url, headers=headers, timeout=10)
        userinfo_resp.raise_for_status()
        userinfo_data = userinfo_resp.json()
        
        email = userinfo_data.get("email")
        google_id = userinfo_data.get("sub")
        name = userinfo_data.get("name") or userinfo_data.get("given_name") or email.split("@")[0]
        
        if not email or not google_id:
            return render_template("login.html", error="Google profile info missing required fields.")
            
        db = DatabaseManager()
        user = db.get_user_by_google_id(google_id)
        if not user:
            user_by_email = db.get_user_by_username_or_email(email)
            if user_by_email:
                user = user_by_email
                conn = db.get_connection()
                try:
                    conn.execute("UPDATE users SET google_id = ?, is_verified = 1 WHERE id = ?;", (google_id, user["id"]))
                    conn.commit()
                finally:
                    conn.close()
                user = db.get_user_by_google_id(google_id)
            else:
                db.create_user(username=name, email=email, google_id=google_id, is_verified=1)
                user = db.get_user_by_google_id(google_id)
                
        session["logged_in"] = True
        session["username"] = user["username"]
        session["email"] = user["email"]
        session["is_google"] = True
        session.permanent = True
        return redirect(url_for("index"))
        
    except Exception as e:
        print(f"[GOOGLE AUTH ERROR] Failed to exchange token/fetch info: {e}")
        return render_template("login.html", error=f"Google authentication error: {e}")

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
    outcome = request.args.get("outcome")
    open_access = request.args.get("open_access")
    sort_by = request.args.get("sort_by")
    citations_min = request.args.get("citations_min")
    recent = request.args.get("recent")
    recent_range = request.args.get("recent_range")
    tab = request.args.get("tab")
    cannabis_type = request.args.get("cannabis_type")
    claude_classified = request.args.get("claude_classified")
    classification_level = request.args.get("classification_level")
    cannabis_logic = request.args.get("cannabis_logic", "or")
    method_logic = request.args.get("method_logic", "or")
    outcome_logic = request.args.get("outcome_logic", "or")
    study_logic = request.args.get("study_logic", "or")
    
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
    if outcome:
        clean_filters["outcome"] = outcome
    if open_access and open_access != "ALL":
        clean_filters["open_access"] = open_access == "true"
    if sort_by and sort_by != "DEFAULT":
        clean_filters["sort_by"] = sort_by
    sort_dir = request.args.get("sort_dir")
    if sort_dir:
        clean_filters["sort_dir"] = sort_dir
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
    if claude_classified:
        clean_filters["claude_classified"] = claude_classified == "true"
    if classification_level and classification_level != "ALL":
        clean_filters["classification_level"] = classification_level
        
    clean_filters["cannabis_logic"] = cannabis_logic
    clean_filters["exposure_logic"] = method_logic
    clean_filters["outcome_logic"] = outcome_logic
    clean_filters["study_logic"] = study_logic
        
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

@app.route("/api/papers/<int:paper_id>/edit-classification", methods=["POST"])
def api_edit_classification(paper_id):
    """Updates the classification fields of a paper, marks them as expert locked, and logs changes to feedback_audit."""
    db = DatabaseManager()
    paper = db.get_paper(paper_id)
    if not paper:
        return jsonify({"error": "Paper not found"}), 404
        
    data = request.get_json() or {}
    
    editable_fields = [
        "study_type", "publication_type", "exposure_method", "thc_pct", "cbd_pct",
        "dose_mg", "puff_count", "thc_mg_ml", "thc_mg_g", "thc_mg_kg",
        "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg", "thc_uM", "cbd_uM", "strain_reported", "strain_normalized", "duration_days",
        "inhaled_exposure_duration", "administration_frequency", "treatment_duration",
        "sample_size", "outcome_domain", "cannabis_type", "summary", "abstract"
    ]
    
    conn = db.get_connection()
    cursor = conn.cursor()
    
    try:
        current_locked = paper.get("expert_locked_fields") or []
        if isinstance(current_locked, str):
            try:
                current_locked = json.loads(current_locked)
            except Exception:
                current_locked = []
        if not isinstance(current_locked, list):
            current_locked = []
            
        updated_fields = {}
        changes_logged = 0
        now_str = datetime.now().isoformat()
        
        # Load rules config version for metadata logging
        rules_version = "1.0.0"
        if os.path.exists("rules_config.json"):
            try:
                with open("rules_config.json", "r") as f:
                    rules_version = json.load(f).get("version", "1.0.0")
            except Exception:
                pass

        for field in editable_fields:
            if field not in data:
                continue
                
            new_val = data[field]
            old_val = paper.get(field)
            
            # Helper to normalize values for comparison
            def normalize(val):
                if val is None or val == "":
                    return None
                if isinstance(val, (list, dict)):
                    return sorted(val) if isinstance(val, list) else val
                if isinstance(val, str):
                    val = val.strip()
                    # Check if it represents JSON array
                    if val.startswith("[") and val.endswith("]"):
                        try:
                            parsed = json.loads(val)
                            return sorted(parsed) if isinstance(parsed, list) else parsed
                        except Exception:
                            pass
                    # Check if numeric
                    try:
                        if "." in val:
                            return float(val)
                        return int(val)
                    except ValueError:
                        pass
                    return val
                if isinstance(val, (int, float)):
                    return val
                return val

            norm_old = normalize(old_val)
            norm_new = normalize(new_val)
            
            if norm_old != norm_new:
                # Value has changed, log correction
                old_str = json.dumps(old_val) if isinstance(old_val, (list, dict)) else str(old_val) if old_val is not None else None
                new_str = json.dumps(new_val) if isinstance(new_val, (list, dict)) else str(new_val) if new_val is not None else None
                
                # Insert into feedback_audit
                cursor.execute(
                    """
                    INSERT INTO feedback_audit (
                        paper_id, field_name, old_value, new_value, title, abstract,
                        timestamp, confidence_before_review, classifier_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        paper_id,
                        field,
                        old_str,
                        new_str,
                        paper.get("title"),
                        paper.get("abstract"),
                        now_str,
                        paper.get("classification_confidence"),
                        paper.get("classifier_version") or rules_version
                    )
                )
                
                # Add to locked fields
                if field not in current_locked:
                    current_locked.append(field)
                    
                updated_fields[field] = new_val
                changes_logged += 1
                
        if changes_logged > 0:
            # Prepare updates dictionary
            update_sql_parts = []
            update_params = []
            
            # Add updated fields
            for field, val in updated_fields.items():
                update_sql_parts.append(f"{field} = ?")
                if isinstance(val, list):
                    update_params.append(json.dumps(val))
                elif val == "":
                    update_params.append(None)
                else:
                    update_params.append(val)
                    
            # Add expert_locked_fields update
            update_sql_parts.append("expert_locked_fields = ?")
            update_params.append(json.dumps(current_locked))
            
            update_params.append(paper_id)
            
            sql = f"UPDATE papers SET {', '.join(update_sql_parts)} WHERE id = ?"
            cursor.execute(sql, update_params)
            conn.commit()
            
        return jsonify({
            "message": f"Successfully updated paper and logged {changes_logged} corrections.",
            "locked_fields": current_locked
        })
        
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route("/api/papers/<int:paper_id>/sync-metadata", methods=["POST"])
def api_sync_metadata(paper_id):
    """Queries external APIs (Semantic Scholar / PubMed) using DOI or PMID to fetch the abstract,
    updates it in the database, and runs the classification pipeline to update the paper's metadata.
    """
    import requests
    from Bio import Entrez
    from harvest import parse_pubmed_xml
    
    db = DatabaseManager()
    paper = db.get_paper(paper_id)
    if not paper:
        return jsonify({"error": "Paper not found."}), 404

    doi = paper.get("doi")
    pmid = paper.get("pmid")
    if not doi and not pmid:
        return jsonify({"error": "Paper must have a DOI or PMID to sync metadata."}), 400

    # Query Semantic Scholar
    s2_data = {}
    s2_id = None
    if doi:
        s2_id = f"DOI:{doi}"
    elif pmid:
        s2_id = f"PMID:{pmid}"

    if s2_id:
        try:
            url = f"https://api.semanticscholar.org/graph/v1/paper/{s2_id}"
            params = {"fields": "abstract,citationCount,isOpenAccess,openAccessPdf"}
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                s2_data = response.json()
        except Exception as e:
            app.logger.error(f"Semantic Scholar lookup failed in sync: {e}")

    # Fallback to PubMed if PMID is available and S2 didn't have abstract
    pubmed_abstract = None
    if pmid and (not s2_data.get("abstract")):
        try:
            # Set default Entrez email if not set
            Entrez.email = os.getenv("ENTREZ_EMAIL", "miladn1@mcmaster.ca")
            handle = Entrez.efetch(db="pubmed", id=pmid, retmode="xml")
            xml_data = handle.read()
            handle.close()
            if isinstance(xml_data, bytes):
                xml_data = xml_data.decode("utf-8")
            fetched_papers = parse_pubmed_xml(xml_data)
            if fetched_papers:
                pubmed_abstract = fetched_papers[0].get("abstract")
        except Exception as e:
            app.logger.error(f"PubMed efetch failed in sync: {e}")

    abstract = s2_data.get("abstract") or pubmed_abstract
    if not abstract:
        return jsonify({"error": "Could not retrieve abstract from public APIs."}), 404

    # Update database record
    conn = db.get_connection()
    try:
        # Update abstract
        conn.execute("UPDATE papers SET abstract = ? WHERE id = ?", (abstract, paper_id))
        conn.commit()
        
        # Reload paper with new abstract
        paper = db.get_paper(paper_id)
        
        # Run classification pipeline to update metadata based on new abstract
        api_key = os.getenv("ANTHROPIC_API_KEY")
        run_llm = True if api_key else False
        
        extracted = classifier.process_paper_metadata(
            title=paper["title"],
            abstract=paper["abstract"],
            run_llm=run_llm
        )
        
        # Do not overwrite expert locked fields
        locked = paper.get("expert_locked_fields") or []
        if isinstance(locked, str):
            try:
                locked = json.loads(locked)
            except Exception:
                locked = []
                
        valid_columns = {
            "study_type", "exposure_method", "thc_pct", "cbd_pct", "dose_mg", "puff_count",
            "thc_mg_ml", "thc_mg_g", "thc_mg_kg", "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg",
            "thc_uM", "cbd_uM", "strain_reported", "strain_normalized", "duration_days",
            "inhaled_exposure_duration", "administration_frequency", "treatment_duration",
            "sample_size", "outcome_domain", "cannabis_type", "summary", "publication_type",
            "classification_confidence", "classification_timestamp", "classifier_version"
        }
        
        update_data = {}
        for k, v in extracted.items():
            if k in valid_columns and k not in locked:
                update_data[k] = v
                
        # Build UPDATE query for metadata fields
        if update_data:
            set_clauses = []
            update_params = []
            for k, v in update_data.items():
                set_clauses.append(f"{k} = ?")
                # Handle lists/JSON fields
                if isinstance(v, list):
                    update_params.append(json.dumps(v))
                else:
                    update_params.append(v)
            update_params.append(paper_id)
            
            conn.execute(f"UPDATE papers SET {', '.join(set_clauses)} WHERE id = ?", update_params)
            conn.commit()
            
            if "_llm_call_metrics" in extracted:
                db.log_llm_call(
                    paper_id=paper_id,
                    metrics=extracted["_llm_call_metrics"],
                    batch_id="sync_metadata"
                )
            
        # Return updated paper
        updated_paper = db.get_paper(paper_id)
        return jsonify({
            "message": "Metadata synced and classification updated successfully.",
            "paper": updated_paper
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route("/api/papers/<int:paper_id>/reclassify-llm", methods=["POST"])
def api_reclassify_llm(paper_id):
    """Runs the Claude LLM classifier directly on the paper's title and abstract,
    respecting expert locked fields.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "Anthropic API key is not configured."}), 400

    db = DatabaseManager()
    paper = db.get_paper(paper_id)
    if not paper:
        return jsonify({"error": "Paper not found."}), 404

    # Load rules config version
    rules_version = "1.0.0"
    if os.path.exists("rules_config.json"):
        try:
            with open("rules_config.json", "r") as f:
                rules_version = json.load(f).get("version", "1.0.0")
        except Exception:
            pass

    try:
        # Run Claude LLM classification on current title and abstract
        extracted = classifier.process_paper_metadata(
            title=paper.get("title") or "",
            abstract=paper.get("abstract") or "",
            run_llm=True
        )

        if not extracted:
            return jsonify({"error": "No metadata was extracted by the classifier."}), 500

        # Do not overwrite expert locked fields
        locked = paper.get("expert_locked_fields") or []
        if isinstance(locked, str):
            try:
                locked = json.loads(locked)
            except Exception:
                locked = []

        valid_columns = {
            "study_type", "exposure_method", "thc_pct", "cbd_pct", "dose_mg", "puff_count",
            "thc_mg_ml", "thc_mg_g", "thc_mg_kg", "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg",
            "thc_uM", "cbd_uM", "strain_reported", "strain_normalized", "duration_days",
            "inhaled_exposure_duration", "administration_frequency", "treatment_duration",
            "sample_size", "outcome_domain", "cannabis_type", "summary", "publication_type"
        }

        update_data = {}
        for k, v in extracted.items():
            if k in valid_columns and k not in locked:
                update_data[k] = v

        # Add metadata fields
        update_data["classifier_version"] = f"llm-reclassify-{rules_version}"
        update_data["classification_timestamp"] = datetime.now().isoformat()
        if "classification_confidence" in extracted:
            update_data["classification_confidence"] = extracted["classification_confidence"]

        # Build UPDATE query
        conn = db.get_connection()
        try:
            set_clauses = []
            update_params = []
            for k, v in update_data.items():
                set_clauses.append(f"{k} = ?")
                if isinstance(v, (list, dict)):
                    update_params.append(json.dumps(v))
                else:
                    update_params.append(v)
            update_params.append(paper_id)

            conn.execute(f"UPDATE papers SET {', '.join(set_clauses)} WHERE id = ?", update_params)
            conn.commit()

            if "_llm_call_metrics" in extracted:
                db.log_llm_call(
                    paper_id=paper_id,
                    metrics=extracted["_llm_call_metrics"],
                    batch_id="single_reclassify"
                )

            # Return updated paper
            updated_paper = db.get_paper(paper_id)
            return jsonify({
                "message": "Paper reclassified with Claude successfully.",
                "paper": updated_paper
            })
        finally:
            conn.close()

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

# ─── Analyses Endpoints ─────────────────────────────────────────

def _compute_analysis_chart_data(papers):
    """Server-side computation mirroring client-side renderVisualAnalysis logic."""
    thc_values = [p["thc_pct"] for p in papers if p.get("thc_pct") is not None]
    cbd_values = [p["cbd_pct"] for p in papers if p.get("cbd_pct") is not None]
    sample_sizes = [p["sample_size"] for p in papers if p.get("sample_size") is not None]

    aggregates = {
        "avg_thc": round(sum(thc_values) / len(thc_values), 1) if thc_values else None,
        "avg_cbd": round(sum(cbd_values) / len(cbd_values), 1) if cbd_values else None,
        "large_sample_pct": round(sum(1 for s in sample_sizes if s and s > 50) / len(sample_sizes) * 100) if sample_sizes else None
    }

    study_design = {}
    timeline = {}
    thc_bins = {"zero": 0, "low": 0, "medLow": 0, "med": 0, "medHigh": 0, "high": 0, "veryHigh": 0, "ultraHigh": 0, "notReported": 0}
    clinical_exp = {"inhaled": 0, "oral": 0, "sublingual": 0, "injected": 0}
    vitro_exp = {"exposure of cells to smoke/vapor": 0, "cannabinoids dissolved in media": 0, "smoke/vapor conditioned media": 0}
    vivo_exp = {"whole body. smoke/vapor": 0, "nose only smoke/vapor": 0, "injection cannabinoids": 0, "oral administration": 0, "sub-lingual": 0, "intranasal": 0, "intratracheal": 0}
    cannabis_type = {}
    outcome = {"pain": 0, "anxiety": 0, "cognition": 0, "inflammation": 0, "addiction": 0, "oncology": 0, "neuroprotection": 0, "sleep": 0}

    for p in papers:
        # Study design
        designs = p.get("study_type")
        if isinstance(designs, list):
            for d in designs:
                study_design[d] = study_design.get(d, 0) + 1
        elif designs:
            study_design[designs] = study_design.get(designs, 0) + 1

        # Timeline
        yr = p.get("year") or "N/A"
        timeline[yr] = timeline.get(yr, 0) + 1

        # THC bins
        thc = p.get("thc_pct")
        if thc is None:
            thc_bins["notReported"] += 1
        elif thc == 0:
            thc_bins["zero"] += 1
        elif thc <= 5:
            thc_bins["low"] += 1
        elif thc <= 10:
            thc_bins["medLow"] += 1
        elif thc <= 15:
            thc_bins["med"] += 1
        elif thc <= 20:
            thc_bins["medHigh"] += 1
        elif thc <= 25:
            thc_bins["high"] += 1
        elif thc <= 30:
            thc_bins["veryHigh"] += 1
        else:
            thc_bins["ultraHigh"] += 1

        # Exposure methods
        exps = p.get("exposure_method")
        if isinstance(exps, list):
            for e in exps:
                if e in clinical_exp: clinical_exp[e] += 1
                elif e in vitro_exp: vitro_exp[e] += 1
                elif e in vivo_exp: vivo_exp[e] += 1
        elif exps:
            if exps in clinical_exp: clinical_exp[exps] += 1
            elif exps in vitro_exp: vitro_exp[exps] += 1
            elif exps in vivo_exp: vivo_exp[exps] += 1

        # Cannabis type
        ctypes = p.get("cannabis_type")
        if isinstance(ctypes, list):
            for ct in ctypes:
                cannabis_type[ct] = cannabis_type.get(ct, 0) + 1
        elif ctypes:
            cannabis_type[ctypes] = cannabis_type.get(ctypes, 0) + 1

        # Outcomes
        outcomes = p.get("outcome_domain") or []
        for o in outcomes:
            if o in outcome:
                outcome[o] += 1

    return {
        "paper_count": len(papers),
        "aggregates": aggregates,
        "study_design": study_design,
        "thc_bins": thc_bins,
        "timeline": timeline,
        "clinical_exposure": clinical_exp,
        "vitro_exposure": vitro_exp,
        "vivo_exposure": vivo_exp,
        "cannabis_type": cannabis_type,
        "outcome": outcome
    }


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """Accepts filter params, fetches matching papers, computes chart data, saves analysis to DB."""
    data = request.get_json() or {}
    filters = data.get("filters", {})
    name = data.get("name", f"Analysis {datetime.now().strftime('%b %d %Y %H:%M')}")

    db = DatabaseManager()

    # Fetch ALL matching papers (no pagination limit)
    filters["limit"] = 100000
    filters["offset"] = 0
    papers = db.search_papers(filters)

    chart_data = _compute_analysis_chart_data(papers)
    chart_data["paper_ids"] = [p["id"] for p in papers]

    analysis_id = db.create_analysis(
        name=name,
        filter_settings=json.dumps(filters, default=str),
        paper_count=chart_data["paper_count"],
        chart_data=json.dumps(chart_data, default=str)
    )

    return jsonify({
        "id": analysis_id,
        "name": name,
        "paper_count": chart_data["paper_count"],
        "filter_settings": filters,
        "chart_data": chart_data,
        "created_at": datetime.now().isoformat()
    })


@app.route("/api/analyses", methods=["GET"])
def api_list_analyses():
    """Returns all saved analyses."""
    db = DatabaseManager()
    analyses = db.list_analyses()
    # Parse JSON fields for the frontend
    for a in analyses:
        try:
            a["filter_settings"] = json.loads(a["filter_settings"])
        except (json.JSONDecodeError, TypeError):
            a["filter_settings"] = {}
    return jsonify(analyses)


@app.route("/api/analyses/<int:analysis_id>", methods=["GET"])
def api_get_analysis(analysis_id):
    """Returns full analysis data including chart_data."""
    db = DatabaseManager()
    analysis = db.get_analysis(analysis_id)
    if not analysis:
        return jsonify({"error": "Analysis not found"}), 404
    try:
        analysis["filter_settings"] = json.loads(analysis["filter_settings"])
    except (json.JSONDecodeError, TypeError):
        analysis["filter_settings"] = {}
    try:
        analysis["chart_data"] = json.loads(analysis["chart_data"])
    except (json.JSONDecodeError, TypeError):
        analysis["chart_data"] = {}
    return jsonify(analysis)


@app.route("/api/analyses/<int:analysis_id>", methods=["PUT"])
def api_update_analysis(analysis_id):
    """Updates an analysis (e.g. rename)."""
    data = request.get_json(silent=True) or {}
    db = DatabaseManager()
    name = data.get("name")
    if name is not None and (not isinstance(name, str) or len(name.strip()) == 0):
        return jsonify({"error": "Name cannot be empty"}), 400
    if name is not None:
        name = name.strip()
    if db.update_analysis(analysis_id, name=name):
        return jsonify({"success": True})
    return jsonify({"error": "Analysis not found"}), 404


@app.route("/api/analyses/<int:analysis_id>", methods=["DELETE"])
def api_delete_analysis(analysis_id):
    """Deletes an analysis."""
    db = DatabaseManager()
    if db.delete_analysis(analysis_id):
        return jsonify({"success": True})
    return jsonify({"error": "Analysis not found"}), 404


@app.route("/api/analyses/<int:analysis_id>/export-csv", methods=["GET"])
def api_export_analysis_csv(analysis_id):
    """Generates and downloads a CSV export of all papers associated with a saved analysis."""
    db = DatabaseManager()
    analysis = db.get_analysis(analysis_id)
    if not analysis:
        return jsonify({"error": "Analysis not found"}), 404

    try:
        filter_settings = json.loads(analysis["filter_settings"])
    except Exception:
        filter_settings = {}

    try:
        chart_data = json.loads(analysis["chart_data"])
    except Exception:
        chart_data = {}

    # Get paper IDs from chart_data or filter_settings
    paper_ids = chart_data.get("paper_ids") or filter_settings.get("paper_ids")
    papers = []

    if paper_ids:
        import sqlite3
        conn = db.get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            placeholders = ",".join(["?"] * len(paper_ids))
            sql = f"SELECT * FROM papers WHERE id IN ({placeholders})"
            cursor.execute(sql, paper_ids)
            rows = cursor.fetchall()
            for row in rows:
                res = dict(row)
                for json_field in ["authors", "outcome_domain"]:
                    if res.get(json_field):
                        try:
                            res[json_field] = json.loads(res[json_field])
                        except Exception:
                            res[json_field] = []
                    else:
                        res[json_field] = []
                for json_field in ["study_type", "exposure_method", "cannabis_type", "expert_locked_fields"]:
                    if res.get(json_field):
                        try:
                            val = res[json_field].strip()
                            if val.startswith("[") and val.endswith("]"):
                                res[json_field] = json.loads(res[json_field])
                        except Exception:
                            pass
                papers.append(res)
        except Exception as e:
            app.logger.error(f"Error querying papers by IDs: {e}")
        finally:
            conn.close()
    else:
        # Fallback to querying using filter settings
        filter_settings["limit"] = 100000
        filter_settings["offset"] = 0
        try:
            papers = db.search_papers(filter_settings)
        except Exception as e:
            app.logger.error(f"Error searching papers with filters: {e}")

    if not papers:
        return jsonify({"error": "No papers found for this analysis"}), 404

    import io
    import csv

    output = io.StringIO()
    fields = [
        "id", "pmid", "doi", "semantic_scholar_id", "title", "authors", "journal", "year",
        "abstract", "full_text_link", "study_type", "publication_type", "exposure_method",
        "thc_pct", "cbd_pct", "dose_mg", "puff_count", "thc_mg_ml", "thc_mg_g", "thc_mg_kg",
        "cbd_mg_ml", "cbd_mg_g", "cbd_mg_kg", "thc_uM", "cbd_uM", "strain_reported", "strain_normalized", "duration_days",
        "inhaled_exposure_duration", "administration_frequency", "treatment_duration",
        "sample_size", "outcome_domain", "open_access", "citation_count",
        "date_harvested", "publication_date", "summary", "expert_locked_fields",
        "classification_confidence", "classification_timestamp", "classifier_version", "cannabis_type"
    ]

    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for p in papers:
        p_copy = {}
        for f in fields:
            val = p.get(f)
            if isinstance(val, list):
                p_copy[f] = json.dumps(val)
            else:
                p_copy[f] = val
        writer.writerow(p_copy)

    csv_data = output.getvalue()
    output.close()

    filename = f"analysis_{analysis_id}_export.csv"
    if analysis.get("name"):
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', analysis["name"])
        filename = f"analysis_{safe_name}.csv"

    from flask import Response
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.route("/api/learning-dashboard/metrics", methods=["GET"])
def api_learning_dashboard_metrics():
    """Returns aggregated metadata and metrics for the Learning Dashboard."""
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    try:
        # 1. Summary Statistics
        cursor.execute("SELECT COUNT(*) as count FROM papers WHERE classifier_version LIKE 'llm-%'")
        row = cursor.fetchone()
        total_llm_classified = row["count"] if row else 0
        
        cursor.execute("SELECT COUNT(*) as count FROM papers WHERE expert_locked_fields IS NOT NULL AND expert_locked_fields != '[]' AND expert_locked_fields != ''")
        row = cursor.fetchone()
        total_locked_papers = row["count"] if row else 0
        
        # Calculate total count of individual locked fields and their distribution
        cursor.execute("SELECT expert_locked_fields FROM papers WHERE expert_locked_fields IS NOT NULL AND expert_locked_fields != '[]' AND expert_locked_fields != ''")
        rows = cursor.fetchall()
        total_locked_fields_count = 0
        active_locks_dist = {}
        for row in rows:
            try:
                fields = json.loads(row["expert_locked_fields"])
                if isinstance(fields, list):
                    total_locked_fields_count += len(fields)
                    for f in fields:
                        active_locks_dist[f] = active_locks_dist.get(f, 0) + 1
            except Exception:
                pass
                
        cursor.execute("SELECT AVG(classification_confidence) as avg_confidence FROM llm_calls_log WHERE classification_confidence IS NOT NULL")
        row = cursor.fetchone()
        avg_confidence = row["avg_confidence"] if row else 0.0
        
        cursor.execute("SELECT SUM(cost) as total_cost, SUM(input_tokens + cache_read_tokens + cache_write_tokens + output_tokens) as total_tokens FROM llm_calls_log")
        cost_tokens_row = cursor.fetchone()
        total_cost = (cost_tokens_row["total_cost"] if cost_tokens_row else 0.0) or 0.0
        total_tokens = (cost_tokens_row["total_tokens"] if cost_tokens_row else 0) or 0
        
        # 2. Token & Cost Trends by Batch
        cursor.execute("""
            SELECT 
                batch_id,
                MIN(timestamp) as batch_time,
                MAX(model) as model,
                COUNT(*) as paper_count,
                AVG(input_tokens) as avg_input,
                AVG(cache_read_tokens) as avg_cache_read,
                AVG(cache_write_tokens) as avg_cache_write,
                AVG(output_tokens) as avg_output,
                AVG(cost) as avg_cost,
                SUM(cost) as total_cost,
                AVG(classification_confidence) as avg_confidence
            FROM llm_calls_log
            GROUP BY batch_id
            ORDER BY batch_time ASC
        """)
        batches_rows = cursor.fetchall()
        batches = []
        for r in batches_rows:
            batches.append({
                "batch_id": r["batch_id"] or "manual/single",
                "batch_time": r["batch_time"],
                "model": r["model"],
                "paper_count": r["paper_count"],
                "avg_input": round(r["avg_input"] or 0, 1),
                "avg_cache_read": round(r["avg_cache_read"] or 0, 1),
                "avg_cache_write": round(r["avg_cache_write"] or 0, 1),
                "avg_output": round(r["avg_output"] or 0, 1),
                "avg_cost": round(r["avg_cost"] or 0, 4),
                "total_cost": round(r["total_cost"] or 0, 4),
                "avg_confidence": round(r["avg_confidence"] or 0.0, 3)
            })
            
        def get_batch_time(b):
            t = b["batch_time"]
            if not t:
                return datetime.min
            try:
                t_clean = t.replace('T', ' ')
                if '.' in t_clean:
                    return datetime.strptime(t_clean, "%Y-%m-%d %H:%M:%S.%f")
                return datetime.strptime(t_clean, "%Y-%m-%d %H:%M:%S")
            except Exception:
                # Fallback: if it's already a datetime/date object
                if hasattr(t, 'strftime'):
                    return t
                return datetime.min
                
        batches.sort(key=get_batch_time)
            
        # 3. expert_locked_fields timeline (Audits over time)
        cursor.execute("SELECT timestamp, field_name FROM feedback_audit ORDER BY timestamp ASC")
        audit_rows = cursor.fetchall()
        
        # Group audits by date (YYYY-MM-DD)
        audits_timeline = {}
        for r in audit_rows:
            if r["timestamp"]:
                date_str = r["timestamp"][:10]  # Get YYYY-MM-DD
                if date_str not in audits_timeline:
                    audits_timeline[date_str] = {"count": 0, "fields": {}}
                audits_timeline[date_str]["count"] += 1
                field = r["field_name"]
                audits_timeline[date_str]["fields"][field] = audits_timeline[date_str]["fields"].get(field, 0) + 1
                
        sorted_timeline = []
        cumulative_locks = 0
        for d in sorted(audits_timeline.keys()):
            cumulative_locks += audits_timeline[d]["count"]
            sorted_timeline.append({
                "date": d,
                "count": audits_timeline[d]["count"],
                "cumulative": cumulative_locks,
                "fields": audits_timeline[d]["fields"]
            })
            
        # 4. Few-shot Learning Effectiveness
        cursor.execute("SELECT paper_id, timestamp, few_shot_similarity, classification_confidence FROM llm_calls_log WHERE paper_id IS NOT NULL")
        calls = [dict(row) for row in cursor.fetchall()]
        
        cursor.execute("SELECT paper_id, timestamp FROM feedback_audit")
        audits = [dict(row) for row in cursor.fetchall()]
        
        # Map paper corrections by timestamp
        audit_map = {}
        for a in audits:
            pid = a["paper_id"]
            if pid not in audit_map:
                audit_map[pid] = []
            audit_map[pid].append(a["timestamp"])
            
        # Segment calls into with-fewshot (sim > 0.25) vs without-fewshot
        fs_trigger_count = 0
        total_calls = len(calls)
        sum_similarity = 0.0
        
        with_fs_total = 0
        with_fs_corrected = 0
        with_fs_conf_sum = 0.0
        no_fs_total = 0
        no_fs_corrected = 0
        no_fs_conf_sum = 0.0
        
        sim_distribution = {
            "0.0-0.25": 0,
            "0.25-0.4": 0,
            "0.4-0.6": 0,
            "0.6-0.8": 0,
            "0.8-1.0": 0
        }
        
        for c in calls:
            pid = c["paper_id"]
            sim = c["few_shot_similarity"] or 0.0
            sum_similarity += sim
            conf = c["classification_confidence"] or 0.0
            
            # Buckets
            if sim <= 0.25:
                sim_distribution["0.0-0.25"] += 1
            elif sim <= 0.4:
                sim_distribution["0.25-0.4"] += 1
                fs_trigger_count += 1
            elif sim <= 0.6:
                sim_distribution["0.4-0.6"] += 1
                fs_trigger_count += 1
            elif sim <= 0.8:
                sim_distribution["0.6-0.8"] += 1
                fs_trigger_count += 1
            else:
                sim_distribution["0.8-1.0"] += 1
                fs_trigger_count += 1
                
            is_fs = sim > 0.25
            
            # Check if corrected *after* classification call
            corrected = False
            if pid in audit_map:
                for audit_time in audit_map[pid]:
                    if audit_time > c["timestamp"]:
                        corrected = True
                        break
                        
            if is_fs:
                with_fs_total += 1
                with_fs_conf_sum += conf
                if corrected:
                    with_fs_corrected += 1
            else:
                no_fs_total += 1
                no_fs_conf_sum += conf
                if corrected:
                    no_fs_corrected += 1
                    
        # 5. Confidence Distribution
        cursor.execute("""
            SELECT 
                SUM(CASE WHEN classification_confidence >= 0.85 THEN 1 ELSE 0 END) as high,
                SUM(CASE WHEN classification_confidence >= 0.60 AND classification_confidence < 0.85 THEN 1 ELSE 0 END) as med,
                SUM(CASE WHEN classification_confidence < 0.60 THEN 1 ELSE 0 END) as low
            FROM llm_calls_log
            WHERE classification_confidence IS NOT NULL
        """)
        conf_dist_row = cursor.fetchone()
        conf_distribution = {
            "high": conf_dist_row["high"] or 0,
            "medium": conf_dist_row["med"] or 0,
            "low": conf_dist_row["low"] or 0
        }
        
        # 6. Version Comparison & Reinforcement Learning Metrics
        cursor.execute("""
            SELECT 
                classifier_version,
                COUNT(*) as total_classified,
                AVG(classification_confidence) as avg_confidence
            FROM papers
            WHERE classifier_version IS NOT NULL AND classifier_version LIKE 'llm-%'
            GROUP BY classifier_version
            ORDER BY classifier_version ASC
        """)
        version_rows = cursor.fetchall()
        
        cursor.execute("""
            SELECT 
                classifier_version,
                AVG(cost) as avg_cost
            FROM llm_calls_log
            GROUP BY classifier_version
        """)
        cost_rows = {r["classifier_version"]: r["avg_cost"] for r in cursor.fetchall()}
        
        cursor.execute("""
            SELECT 
                classifier_version,
                COUNT(DISTINCT paper_id) as corrected_count
            FROM feedback_audit
            GROUP BY classifier_version
        """)
        audit_counts = {r["classifier_version"]: r["corrected_count"] for r in cursor.fetchall()}
        
        versions = []
        for v_row in version_rows:
            v_name = v_row["classifier_version"]
            total_c = v_row["total_classified"] or 0
            corr_c = audit_counts.get(v_name, 0)
            if corr_c > total_c:
                corr_c = total_c
            error_rate = corr_c / total_c if total_c > 0 else 0.0
            versions.append({
                "version": v_name,
                "total_classified": total_c,
                "corrected_count": corr_c,
                "error_rate": round(error_rate, 4),
                "avg_confidence": round(v_row["avg_confidence"] or 0.0, 3),
                "avg_cost": round(cost_rows.get(v_name, 0.0), 4)
            })

        # Prepare response payload
        metrics = {
            "summary": {
                "total_llm_classified": total_llm_classified,
                "total_locked_papers": total_locked_papers,
                "total_locked_fields": total_locked_fields_count,
                "avg_confidence": round(avg_confidence, 3),
                "total_cost": round(total_cost, 4),
                "total_tokens": total_tokens
            },
            "batches": batches,
            "expert_locks": {
                "active_distribution": active_locks_dist,
                "timeline": sorted_timeline
            },
            "few_shot": {
                "trigger_rate": round(fs_trigger_count / total_calls, 3) if total_calls > 0 else 0.0,
                "avg_similarity": round(sum_similarity / total_calls, 3) if total_calls > 0 else 0.0,
                "similarity_distribution": sim_distribution,
                "comparison": {
                    "with_few_shot": {
                        "total": with_fs_total,
                        "corrected": with_fs_corrected,
                        "rate": round(with_fs_corrected / with_fs_total, 3) if with_fs_total > 0 else 0.0,
                        "avg_confidence": round(with_fs_conf_sum / with_fs_total, 3) if with_fs_total > 0 else 0.0
                    },
                    "without_few_shot": {
                        "total": no_fs_total,
                        "corrected": no_fs_corrected,
                        "rate": round(no_fs_corrected / no_fs_total, 3) if no_fs_total > 0 else 0.0,
                        "avg_confidence": round(no_fs_conf_sum / no_fs_total, 3) if no_fs_total > 0 else 0.0
                    }
                }
            },
            "confidence": {
                "distribution": conf_distribution
            },
            "versions": versions
        }
        
        return jsonify(metrics)
        
    except Exception as e:
        app.logger.error(f"Error compiling Learning Dashboard metrics: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ─── Graph / Connectivity API Routes ──────────────────────────────


@app.route("/api/graph/stats", methods=["GET"])
def api_graph_stats():
    """Return citation graph statistics."""
    db = DatabaseManager()
    cg = CitationGraph(db)
    try:
        stats = cg.get_graph_stats()
        return jsonify(stats)
    except Exception as e:
        app.logger.error(f"Graph stats error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/graph/network", methods=["GET"])
def api_graph_network():
    """Return full network data (nodes with degree + internal edges) for visualization."""
    max_nodes = int(request.args.get("max_nodes", 2000))
    db = DatabaseManager()
    cg = CitationGraph(db)
    try:
        data = cg.get_network_data(max_nodes=max_nodes)
        return jsonify(data)
    except Exception as e:
        app.logger.error(f"Graph network error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/papers/search-simple", methods=["GET"])
def api_papers_search_simple():
    """Simple paper search returning id + title for the paper selector."""
    q = request.args.get("q", "").strip()
    limit = int(request.args.get("limit", 50))
    db = DatabaseManager()
    conn = db.get_connection()
    try:
        if q:
            rows = conn.execute("""
                SELECT id, title, year, authors FROM papers
                WHERE title LIKE ? ORDER BY year DESC LIMIT ?
            """, (f"%{q}%", limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, title, year, authors FROM papers
                ORDER BY year DESC LIMIT ?
            """, (limit,)).fetchall()
        results = [dict(r) for r in rows]
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/papers/<int:paper_id>/references", methods=["GET"])
def api_paper_references(paper_id):
    """Return references for a paper."""
    include_ext = request.args.get("include_external", "false") == "true"
    db = DatabaseManager()
    cg = CitationGraph(db)
    try:
        refs = cg.get_references(paper_id, include_external=include_ext)
        return jsonify(refs)
    except Exception as e:
        app.logger.error(f"References error for {paper_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/papers/<int:paper_id>/cited-by", methods=["GET"])
def api_paper_cited_by(paper_id):
    """Return papers that cite this paper."""
    include_ext = request.args.get("include_external", "false") == "true"
    db = DatabaseManager()
    cg = CitationGraph(db)
    try:
        citing = cg.get_cited_by(paper_id, include_external=include_ext)
        return jsonify(citing)
    except Exception as e:
        app.logger.error(f"Cited-by error for {paper_id}: {e}")
        return jsonify({"error": str(e)}), 500


# Start the background daily scheduler thread, protected against debug reloader double-runs and unit tests
import sys
if (not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true") and "unittest" not in sys.modules:
    logging.getLogger("scheduler").info("Launching daily automatic harvest scheduler thread...")
    threading.Thread(target=daily_harvest_scheduler, daemon=True).start()

if __name__ == "__main__":
    # Start server on local network port 5001 to bypass macOS default AirPlay port conflict (5000)
    app.run(debug=True, port=5001)

