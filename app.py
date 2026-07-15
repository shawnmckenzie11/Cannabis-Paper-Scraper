# app.py
import os
import json
import re
import threading
import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from dashboard_ui_config import build_dashboard_ui_config
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
import calibration_metrics
import maude_feedback

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "mckenzian-secret-key-12345")
ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "admin123")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RELIABILITY_MANIFEST_FILE = os.path.join(BASE_DIR, "reliability_manifest.json")

def load_reliability_manifest():
    """Loads the repo-local reliability manifest when it exists."""
    if not os.path.exists(RELIABILITY_MANIFEST_FILE):
        return None
    try:
        with open(RELIABILITY_MANIFEST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        app.logger.warning(f"Failed to load reliability manifest: {e}")
        return None

def coerce_positive_int(value, default: int, maximum: int) -> int:
    """Coerces request values into bounded positive integers."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, maximum))

def mvp_gate(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        return jsonify({"error": "This feature is locked in the MVP release."}), 403
    return decorated_function


def login_required(f):
    """Require a signed-in session for the wrapped route."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({
                "error": "Authentication required.",
                "login_required": True,
                "login_url": url_for("login", next=request.path),
            }), 401
        return f(*args, **kwargs)

    return decorated_function

ADMIN_EMAILS = {"shawnmckenzie11.sm@gmail.com", "solutions@mckenzian.com", "nadiadalim@gmail.com"}
if os.getenv("ADMIN_EMAILS"):
    ADMIN_EMAILS.update(email.strip() for email in os.getenv("ADMIN_EMAILS").split(","))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Authentication required.", "login_url": url_for("login", next=request.path)}), 401
        user_email = session.get("email")
        if not user_email or user_email not in ADMIN_EMAILS:
            return jsonify({"error": "This action is restricted to administrators.", "email": user_email}), 403
        return f(*args, **kwargs)
    return decorated_function


def _safe_next_url(next_url):
    """Returns a same-site relative redirect target when safe."""
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return None
    return next_url


def _get_session_user(db=None):
    """Return the logged-in user row, or None if the session is missing/stale."""
    user_id = session.get("user_id")
    if not user_id or not session.get("logged_in"):
        return None
    if db is None:
        db = DatabaseManager()
    user = db.get_user_by_id(int(user_id))
    if not user:
        session.clear()
        return None
    return user

def _get_unique_username(base_name):
    db = DatabaseManager()
    username = base_name
    # Remove any non-alphanumeric characters for a clean username
    username = re.sub(r'[^a-zA-Z0-9_]', '', username)
    if not username:
        username = "user"
    base_name = username
    counter = 1
    while db.get_user_by_username_or_email(username) is not None:
        username = f"{base_name}{counter}"
        counter += 1
    return username

# Core global state to track active background harvesting progress
harvest_lock = threading.Lock()
harvest_state = {
    "status": "idle",      # idle, running, success, error
    "progress": "",        # Real-time text output
    "error": None,         # Error message
    "start_time": None
}

_pending_pdf_uploads: Dict[str, Dict[str, Any]] = {}
_pending_pdf_lock = threading.Lock()


def _store_pending_pdf_upload(
    pdf_bytes: bytes,
    filename: str,
    *,
    proposed_paper: Optional[Dict[str, Any]] = None,
    existing_row: Optional[Dict[str, Any]] = None,
    is_new_paper: bool = True,
    paper_id: Optional[int] = None,
) -> str:
    """Store uploaded PDF bytes and review context temporarily for user confirmation."""
    token = str(uuid.uuid4())
    with _pending_pdf_lock:
        _pending_pdf_uploads[token] = {
            "pdf_bytes": pdf_bytes,
            "filename": filename,
            "proposed_paper": proposed_paper or {},
            "existing_row": existing_row or {},
            "is_new_paper": bool(is_new_paper),
            "paper_id": paper_id,
        }
    return token


def _get_pending_pdf_upload(token: str) -> Optional[Dict[str, Any]]:
    """Retrieve a pending PDF upload without removing it."""
    with _pending_pdf_lock:
        pending = _pending_pdf_uploads.get(token)
        return dict(pending) if pending else None


def _update_pending_pdf_upload(token: str, **updates: Any) -> Optional[Dict[str, Any]]:
    """Update fields on an existing pending PDF upload."""
    with _pending_pdf_lock:
        pending = _pending_pdf_uploads.get(token)
        if not pending:
            return None
        pending.update(updates)
        return dict(pending)


def _pop_pending_pdf_upload(token: str) -> Optional[Dict[str, Any]]:
    """Retrieve and remove a pending PDF upload by token."""
    with _pending_pdf_lock:
        return _pending_pdf_uploads.pop(token, None)

def daily_harvest_scheduler():
    """Background scheduler for one-shot jobs and the daily harvest pipeline."""
    import time

    import scheduled_jobs
    import maude_reingest_watchdog

    sched_logger = logging.getLogger("scheduler")
    sched_logger.info("Daily background scheduler thread starting...")
    db = DatabaseManager()

    # Store initial info
    db.set_metadata("scheduler_active", "true")
    if not db.get_metadata("last_daily_harvest_status"):
        db.set_metadata("last_daily_harvest_status", "Never run")

    query = "cannabis OR cannabinoid OR marijuana"
    max_results = 200
    last_harvest_check_hour = None

    while True:
        try:
            scheduled_jobs.run_due_jobs(db)
            maude_reingest_watchdog.run_watchdog(db)

            current_hour = datetime.now().hour
            if last_harvest_check_hour != current_hour:
                last_harvest_check_hour = current_hour
                classify = os.getenv("AUTO_HARVEST_CLASSIFY", "false").lower() == "true"
                today_str = date.today().isoformat()  # YYYY-MM-DD
                last_run_date = db.get_metadata("last_daily_harvest_date")

                if last_run_date != today_str:
                    sched_logger.info(f"Daily scheduler: starting automated harvest for query '{query}' (today: {today_str}, last run: {last_run_date})")
                    db.set_metadata("last_daily_harvest_status", f"Running automated harvest since {datetime.now().strftime('%H:%M:%S')}...")

                    try:
                        import manual_edit_cycle
                        if manual_edit_cycle.should_run_pre_harvest_cycle(db):
                            since_ts = manual_edit_cycle.pre_harvest_processing_since(db)
                            pending = db.count_expert_edits_since(since_ts, expert_drawer_only=True)
                            sched_logger.info(
                                "Pre-harvest: %s unprocessed expert edit(s) since last harvest/cycle; running manual edit cycle",
                                pending,
                            )
                            edit_result = manual_edit_cycle.run_manual_edit_cycle(
                                db,
                                since=since_ts,
                                dry_run=False,
                                sqlite_path=os.getenv("SQLITE_PATH", "/data/cannabis_papers.db"),
                            )
                            sched_logger.info("Pre-harvest manual edit cycle: %s", edit_result)
                        else:
                            sched_logger.info(
                                "Pre-harvest: no unprocessed expert edits since last daily harvest; skipping manual edit cycle"
                            )
                    except Exception as edit_err:
                        sched_logger.error("Pre-harvest manual edit cycle failed: %s", edit_err)
                        if os.getenv("MANUAL_EDIT_BLOCK_HARVEST", "0") == "1":
                            raise

                    # Call the unified pipeline
                    success_count, skipped_count, filter_skipped, ingested_ids = harvest.run_harvest_pipeline(
                        query=query,
                        max_results=max_results,
                        update=True,
                        classify=classify
                    )

                    if ingested_ids:
                        try:
                            upgrade = scheduled_jobs.run_post_harvest_maude_upgrade(ingested_ids)
                            sched_logger.info(
                                "Daily scheduler: post-harvest Maude upgrade: %s",
                                upgrade,
                            )
                        except Exception as upgrade_err:
                            sched_logger.error(
                                "Daily scheduler: post-harvest Maude upgrade failed: %s",
                                upgrade_err,
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

        except Exception as e:
            err_msg = f"Background scheduler failed: {e}"
            sched_logger.error(err_msg)
            db.set_metadata("last_daily_harvest_status", f"Error at {datetime.now().strftime('%H:%M:%S')}: {e}")

        # Poll frequently enough for one-shot scheduled jobs (e.g. 11pm re-ingest).
        time.sleep(60)

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
                
        success_count, skipped_count, filter_skipped, ingested_ids = harvest.run_harvest_pipeline(
            query=query,
            max_results=max_results,
            update=update,
            classify=classify,
            progress_callback=update_progress
        )

        if ingested_ids:
            try:
                import scheduled_jobs
                upgrade = scheduled_jobs.run_post_harvest_maude_upgrade(ingested_ids)
                update_progress(
                    f"Queued Maude PDF/full-text upgrade for {upgrade.get('paper_count', len(ingested_ids))} new papers."
                )
            except Exception as upgrade_err:
                logging.getLogger("harvest.worker").error(
                    "Post-harvest Maude upgrade failed: %s",
                    upgrade_err,
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
    next_url = _safe_next_url(request.args.get("next") or request.form.get("next"))
    if session.get("logged_in"):
        return redirect(next_url or url_for("index"))
        
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
                session["user_id"] = user["id"]
                session["username"] = user["username"]
                session["email"] = user["email"]
                session["is_google"] = False
                session.permanent = True
                return redirect(next_url or url_for("index"))
        else:
            error = "Invalid username/email or password."
            
    google_client_id = os.getenv("GOOGLE_CLIENT_ID")
    return render_template("login.html", error=error, google_client_id=google_client_id, next_url=next_url)

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
            session["user_id"] = user["id"]
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
    next_url = _safe_next_url(request.args.get("next"))
    if next_url:
        session["google_oauth_next"] = next_url
    
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
        "state": state,
        "prompt": "select_account"
    }
    
    google_auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return redirect(google_auth_url)

@app.route("/auth/google/callback", methods=["GET", "POST"])
def auth_google_callback():
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
    if not redirect_uri:
        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        redirect_uri = f"{scheme}://{request.host}/auth/google/callback"

    # Support Google Identity Services (One Tap / Auto Sign-In) JWT credential callback
    jwt_token = request.form.get("credential")
    if jwt_token:
        import base64
        try:
            parts = jwt_token.split('.')
            if len(parts) >= 2:
                payload_b64 = parts[1]
                payload_b64 += '=' * (-len(payload_b64) % 4)
                payload_json = base64.b64decode(payload_b64).decode('utf-8')
                payload = json.loads(payload_json)
                
                email = payload.get("email")
                google_id = payload.get("sub")
                name = payload.get("name") or payload.get("given_name") or email.split("@")[0]
                
                if email and google_id:
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
                            unique_username = _get_unique_username(name)
                            db.create_user(username=unique_username, email=email, google_id=google_id, is_verified=1)
                            user = db.get_user_by_google_id(google_id)
                            
                    if not user:
                        return render_template("login.html", error="Google One Tap authentication failed: could not create or retrieve user account.")

                    session["logged_in"] = True
                    session["user_id"] = user["id"]
                    session["username"] = user["username"]
                    session["email"] = user["email"]
                    session["is_google"] = True
                    session.permanent = True
                    return _google_auth_redirect()
        except Exception as e:
            print(f"[GOOGLE ONE TAP AUTH ERROR] {e}")
            return render_template("login.html", error=f"Google One Tap authentication failed: {e}")
        
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
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["email"] = user["email"]
        session["is_google"] = True
        session.permanent = True
        return _google_auth_redirect()
        
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
                unique_username = _get_unique_username(name)
                db.create_user(username=unique_username, email=email, google_id=google_id, is_verified=1)
                user = db.get_user_by_google_id(google_id)
                
        if not user:
            return render_template("login.html", error="Google authentication failed: could not create or retrieve user account.")

        session["logged_in"] = True
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["email"] = user["email"]
        session["is_google"] = True
        session.permanent = True
        return _google_auth_redirect()
        
    except Exception as e:
        print(f"[GOOGLE AUTH ERROR] Failed to exchange token/fetch info: {e}")
        return render_template("login.html", error=f"Google authentication error: {e}")


def _google_auth_redirect():
    """Returns post-login redirect after Google OAuth."""
    return redirect(_safe_next_url(session.pop("google_oauth_next", None)) or url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
def index():
    """Serves the Single-Page Application dynamic research dashboard."""
    return render_template(
        "index.html",
        admin_emails_list=list(ADMIN_EMAILS),
        dashboard_config=build_dashboard_ui_config(),
    )

@app.route("/api/dashboard-config", methods=["GET"])
def api_dashboard_config():
    """Return tab and filter profile configuration for the research dashboard."""
    return jsonify(build_dashboard_ui_config())

@app.route("/api/tab-counts", methods=["GET"])
def api_tab_counts():
    """Return fast indexed counts for each database UI tab."""
    db = DatabaseManager()
    try:
        return jsonify({"counts": db.get_tab_counts()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _build_dashboard_filters_from_request():
    """Parse dashboard search filter query args into a db_manager filter dict."""
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
    publication_type = request.args.get("publication_type")
    publication_type_logic = request.args.get("publication_type_logic", "or")
    cannabis_logic = request.args.get("cannabis_logic", "or")
    method_logic = request.args.get("method_logic", "or")
    outcome_logic = request.args.get("outcome_logic", "or")
    study_logic = request.args.get("study_logic", "or")
    sample_size_min = request.args.get("sample_size_min")
    sample_size_max = request.args.get("sample_size_max")
    dose_mg_min = request.args.get("dose_mg_min")
    dose_mg_max = request.args.get("dose_mg_max")
    duration_days_min = request.args.get("duration_days_min")
    duration_days_max = request.args.get("duration_days_max")
    thc_mg_kg_min = request.args.get("thc_mg_kg_min")
    thc_mg_kg_max = request.args.get("thc_mg_kg_max")
    cbd_mg_kg_min = request.args.get("cbd_mg_kg_min")
    cbd_mg_kg_max = request.args.get("cbd_mg_kg_max")
    thc_mg_ml_min = request.args.get("thc_mg_ml_min")
    thc_mg_ml_max = request.args.get("thc_mg_ml_max")
    cbd_mg_ml_min = request.args.get("cbd_mg_ml_min")
    cbd_mg_ml_max = request.args.get("cbd_mg_ml_max")
    thc_uM_min = request.args.get("thc_uM_min")
    thc_uM_max = request.args.get("thc_uM_max")
    cbd_uM_min = request.args.get("cbd_uM_min")
    cbd_uM_max = request.args.get("cbd_uM_max")
    puff_count_min = request.args.get("puff_count_min")
    species = request.args.get("species")
    exposure_regimen_bin = request.args.get("exposure_regimen_bin")
    population_age = request.args.get("population_age")
    population_sex = request.args.get("population_sex")
    cbd_min = request.args.get("cbd_min")
    cbd_max = request.args.get("cbd_max")
    has_pdf = request.args.get("has_pdf")
    has_full_text = request.args.get("has_full_text")

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
    if recent_range:
        clean_filters["recent_range"] = recent_range
    elif recent and recent.lower() == "true":
        clean_filters["recent"] = True
    if cannabis_type and cannabis_type != "ALL":
        clean_filters["cannabis_type"] = cannabis_type
    if claude_classified:
        clean_filters["claude_classified"] = claude_classified == "true"
    if classification_level and classification_level != "ALL":
        clean_filters["classification_level"] = classification_level
    if publication_type and publication_type != "ALL":
        clean_filters["publication_type"] = publication_type
        clean_filters["publication_type_logic"] = publication_type_logic

    _FLOAT_FILTER_KEYS = (
        "sample_size_min", "sample_size_max",
        "dose_mg_min", "dose_mg_max",
        "duration_days_min", "duration_days_max",
        "thc_mg_kg_min", "thc_mg_kg_max",
        "cbd_mg_kg_min", "cbd_mg_kg_max",
        "thc_mg_ml_min", "thc_mg_ml_max",
        "cbd_mg_ml_min", "cbd_mg_ml_max",
        "thc_uM_min", "thc_uM_max",
        "cbd_uM_min", "cbd_uM_max",
        "puff_count_min",
        "cbd_min", "cbd_max",
    )
    _local_vars = locals()
    for key in _FLOAT_FILTER_KEYS:
        raw = _local_vars.get(key)
        if raw is not None and raw != "":
            clean_filters[key] = float(raw)
    if species:
        clean_filters["species"] = species
    if exposure_regimen_bin:
        clean_filters["exposure_regimen_bin"] = exposure_regimen_bin
    if population_age:
        clean_filters["population_age"] = population_age
    if population_sex:
        clean_filters["population_sex"] = population_sex
    if has_pdf and has_pdf.lower() in ("true", "1", "yes"):
        clean_filters["has_pdf"] = True
    if has_full_text and has_full_text.lower() in ("true", "1", "yes"):
        clean_filters["has_full_text"] = True

    clean_filters["cannabis_logic"] = cannabis_logic
    clean_filters["exposure_logic"] = method_logic
    clean_filters["outcome_logic"] = outcome_logic
    clean_filters["study_logic"] = study_logic
    return clean_filters


@app.route("/api/search/section-stats", methods=["GET"])
def api_search_section_stats():
    """Return methods/results section coverage for the current filtered dataset."""
    import section_stats

    db = DatabaseManager()
    try:
        papers = db.search_papers_minimal_for_section_stats(_build_dashboard_filters_from_request())
        stats = section_stats.compute_section_stats(papers)
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/search", methods=["GET"])
def api_search():
    """API endpoint to query, dynamic filter, and sort papers."""
    db = DatabaseManager()
    
    page = request.args.get("page", 1)
    limit = request.args.get("limit", 50)
    skip_count = request.args.get("skip_count", "").lower() in ("1", "true", "yes")
    known_total = request.args.get("known_total")
    try:
        page = int(page)
    except (ValueError, TypeError):
        page = 1
    try:
        limit = int(limit)
    except (ValueError, TypeError):
        limit = 50

    clean_filters = _build_dashboard_filters_from_request()
        
    import time
    search_started = time.perf_counter()
    try:
        if skip_count and known_total is not None:
            total_count = int(known_total)
            clean_filters["limit"] = limit
            clean_filters["offset"] = (page - 1) * limit
            results = db.search_papers(clean_filters)
            count_ms = 0.0
        else:
            clean_filters["limit"] = limit
            clean_filters["offset"] = (page - 1) * limit
            count_started = time.perf_counter()
            results, total_count = db.search_papers(clean_filters, include_total=True)
            count_ms = (time.perf_counter() - count_started) * 1000.0
        
        search_ms = (time.perf_counter() - search_started) * 1000.0
        
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
            "limit": limit,
            "search_ms": round(search_ms, 1),
            "count_ms": round(count_ms, 1),
            "skipped_count": bool(skip_count and known_total is not None),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/papers/<int:paper_id>", methods=["GET"])
def api_get_paper(paper_id):
    """Return a single paper row for drawer/detail views."""
    db = DatabaseManager()
    paper = db.get_paper(paper_id)
    if not paper:
        return jsonify({"error": "Paper not found."}), 404
    return jsonify({"paper": paper})

@app.route("/api/papers/delete", methods=["POST"])
@admin_required
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
@admin_required
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
        "repeat_exposure_count", "exposure_regimen_bin",
        "sample_size", "outcome_domain", "cannabis_type", "summary", "abstract",
        "population_age", "population_sex", "inclusion_criteria", "exclusion_criteria",
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
        import heuristics_engine
        rules_version = heuristics_engine.load_rules_config().get("version", "1.0.0")

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
                
                # Insert into feedback_audit (FTS index sync handled by db layer/triggers)
                db.insert_feedback_audit(
                    paper_id=paper_id,
                    field_name=field,
                    old_value=old_str,
                    new_value=new_str,
                    title=paper.get("title"),
                    abstract=paper.get("abstract"),
                    timestamp=now_str,
                    confidence_before_review=paper.get("classification_confidence"),
                    classifier_version=paper.get("classifier_version") or rules_version,
                    cursor=cursor,
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
            db.sync_tab_flags_for_paper(paper_id, conn=conn)
            conn.commit()
            db.increment_metadata("feedback_corrections_since_eval", changes_logged)
            db.set_metadata("last_feedback_audit_timestamp", now_str)
            
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
        "repeat_exposure_count", "exposure_regimen_bin",
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
            db.sync_tab_flags_for_paper(paper_id, conn=conn)
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
@admin_required
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
    import heuristics_engine
    rules_version = heuristics_engine.load_rules_config().get("version", "1.0.0")

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
        "repeat_exposure_count", "exposure_regimen_bin",
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
            db.sync_tab_flags_for_paper(paper_id, conn=conn)
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
@admin_required
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
    # Manual harvest always uses internal Maude/heuristic classification.
    classify = False
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
@admin_required
def api_harvest_status():
    """Returns the real-time status and logs of the background harvest worker."""
    with harvest_lock:
        return jsonify(harvest_state)

@app.route("/api/papers/upload-pdf", methods=["POST"])
@admin_required
def api_upload_pdf():
    """Ingest or update a paper from an uploaded PDF using the Maude/heuristic pipeline."""
    merge_token = (request.form.get("merge_token") or "").strip()
    match_choice = (request.form.get("match_choice") or "").strip()
    merge_selections = None
    custom_values = None
    force_paper_id = None
    proposed_paper = None
    review_existing_row = None
    is_new_paper = None
    filename = "upload.pdf"
    pdf_bytes: bytes

    if merge_token and match_choice:
        pending = _get_pending_pdf_upload(merge_token)
        if not pending:
            return jsonify({"error": "Upload session expired. Please re-upload the PDF."}), 400
        proposed_paper = pending.get("proposed_paper") or {}
        force_new = match_choice.lower() in {"new", "none", "create"}
        selected_id = None
        if not force_new:
            try:
                selected_id = int(match_choice)
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid match_choice. Use a paper id or 'new'."}), 400
        try:
            result = harvest.build_pdf_upload_review(
                proposed_paper,
                selected_paper_id=selected_id,
                force_new=force_new,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        _update_pending_pdf_upload(
            merge_token,
            existing_row=result.get("existing_row") or {},
            is_new_paper=result.get("is_new_paper", True),
            paper_id=result.get("paper_id"),
        )
        payload = dict(result)
        payload["merge_token"] = merge_token
        payload.pop("proposed_paper", None)
        payload.pop("existing_row", None)
        return jsonify(payload)

    if merge_token:
        pending = _pop_pending_pdf_upload(merge_token)
        if not pending:
            return jsonify({"error": "Upload session expired. Please re-upload the PDF."}), 400
        pdf_bytes = pending["pdf_bytes"]
        filename = pending.get("filename") or filename
        proposed_paper = pending.get("proposed_paper") or {}
        review_existing_row = pending.get("existing_row") or {}
        is_new_paper = pending.get("is_new_paper", True)
        force_paper_id = pending.get("paper_id")
        try:
            merge_selections = json.loads(request.form.get("merge_selections") or "{}")
        except json.JSONDecodeError:
            return jsonify({"error": "Invalid merge selections payload."}), 400
        try:
            custom_values = json.loads(request.form.get("custom_values") or "{}")
        except json.JSONDecodeError:
            return jsonify({"error": "Invalid custom values payload."}), 400
        paper_id_raw = request.form.get("paper_id")
        if paper_id_raw:
            try:
                force_paper_id = int(paper_id_raw)
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid paper_id for merge."}), 400
    else:
        upload = request.files.get("pdf") or request.files.get("file")
        if not upload or not upload.filename:
            return jsonify({"error": "A PDF file is required (field name: pdf)."}), 400

        filename = upload.filename or "upload.pdf"
        if not filename.lower().endswith(".pdf"):
            return jsonify({"error": "Only PDF uploads are supported."}), 400

        pdf_bytes = upload.read()

    max_bytes = 25 * 1024 * 1024
    if len(pdf_bytes) > max_bytes:
        return jsonify({"error": "PDF exceeds the 25 MB upload limit."}), 400
    if not pdf_bytes:
        return jsonify({"error": "Uploaded PDF is empty."}), 400

    try:
        result = harvest.ingest_uploaded_pdf(
            pdf_bytes,
            filename=filename,
            merge_selections=merge_selections,
            custom_values=custom_values,
            force_paper_id=force_paper_id,
            proposed_paper=proposed_paper,
            review_existing_row=review_existing_row,
            is_new_paper=is_new_paper,
        )
        if result.get("status") in {"match_selection_required", "review_required"}:
            result["merge_token"] = _store_pending_pdf_upload(
                pdf_bytes,
                filename,
                proposed_paper=result.get("proposed_paper"),
                existing_row=result.get("existing_row"),
                is_new_paper=result.get("is_new_paper", True),
                paper_id=result.get("paper_id"),
            )
            payload = dict(result)
            payload.pop("proposed_paper", None)
            payload.pop("existing_row", None)
            return jsonify(payload)

        return jsonify({
            "status": "success",
            "message": f"Paper {result['action']} successfully.",
            **result,
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        app.logger.error("PDF upload failed: %s", exc)
        return jsonify({"error": str(exc)}), 500

@app.route("/api/scheduler/status", methods=["GET"])
def api_scheduler_status():
    """Returns the daily background scheduler status from the metadata table."""
    import manual_edit_cycle
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
        "query": "cannabis OR cannabinoid OR marijuana",
        "manual_edit_cycle": {
            "last_cycle_at": db.get_metadata("last_manual_edit_cycle_at"),
            "pending_edits": manual_edit_cycle.pending_edit_count(db),
            "last_report": manual_edit_cycle.load_last_cycle_report(db),
        },
    })

@app.route("/api/manual-edit-cycle/status", methods=["GET"])
@admin_required
def api_manual_edit_cycle_status():
    """Returns manual edit cycle watermark, pending edits, and last run report."""
    import manual_edit_cycle
    db = DatabaseManager()
    return jsonify({
        "last_cycle_at": db.get_metadata(manual_edit_cycle.METADATA_LAST_CYCLE),
        "pending_edits": manual_edit_cycle.pending_edit_count(db),
        "last_report": manual_edit_cycle.load_last_cycle_report(db),
    })


@app.route("/api/manual-edit-cycle/run", methods=["POST"])
@admin_required
def api_manual_edit_cycle_run():
    """Triggers the manual expert-edit RL cycle immediately (for testing or ops)."""
    import manual_edit_cycle
    db = DatabaseManager()
    data = request.get_json(silent=True) or {}
    since = data.get("since")
    paper_ids = data.get("paper_ids")
    dry_run = bool(data.get("dry_run", False))
    result = manual_edit_cycle.run_manual_edit_cycle(
        db,
        since=since,
        sqlite_path=data.get("sqlite_path") or os.getenv("SQLITE_PATH", "cannabis_papers.db"),
        paper_ids=paper_ids,
        dry_run=dry_run,
        apply_cues=not bool(data.get("no_cues", False)),
        bump_version=not bool(data.get("no_version_bump", False)),
    )
    return jsonify(result)

def _calibration_output_dir() -> Path:
    """Returns the active calibration artifacts directory for this deployment."""
    return calibration_metrics.resolve_calibration_output_dir()


@app.route("/calibration/dashboard")
def calibration_dashboard_page():
    """Serves the interactive calibration learning dashboard."""
    output_dir = _calibration_output_dir()
    dashboard_path = output_dir / "dashboard.html"
    if not dashboard_path.exists():
        calibration_metrics.build_dashboard(
            output_dir=output_dir,
            rules_path=Path(BASE_DIR) / "rules_config.json",
        )
    return send_file(dashboard_path)


@app.route("/api/calibration/auth-status", methods=["GET"])
def api_calibration_auth_status():
    """Returns session state for the calibration dashboard resolve workflow."""
    email = session.get("email")
    logged_in = bool(session.get("logged_in"))
    return jsonify({
        "logged_in": logged_in,
        "is_admin": bool(email and email in ADMIN_EMAILS),
        "email": email,
        "login_url": url_for("login", next="/calibration/dashboard"),
    })


@app.route("/api/calibration/dashboard-metrics", methods=["GET"])
def api_calibration_dashboard_metrics():
    """Returns aggregated calibration learning metrics for dashboards and agents."""
    try:
        confidence_threshold = float(request.args.get("confidence_threshold", 0.72))
    except (TypeError, ValueError):
        confidence_threshold = 0.72
    try:
        metrics = calibration_metrics.build_dashboard_metrics(
            output_dir=_calibration_output_dir(),
            rules_config=classifier.load_rules_config(),
            confidence_threshold=confidence_threshold,
        )
        return jsonify(metrics)
    except Exception as e:
        app.logger.error(f"Error compiling calibration dashboard metrics: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/calibration/lock-status", methods=["GET"])
def api_calibration_lock_status():
    """Returns the production calibration coordination lock snapshot."""
    import calibration_coordinator

    db = DatabaseManager()
    config = classifier.load_rules_config()
    return jsonify(calibration_coordinator.get_lock_status(db=db, rules_config=config))


@app.route("/api/calibration/lock", methods=["POST"])
@admin_required
def api_calibration_lock():
    """Acquires or releases the calibration coordination lock (admin only)."""
    import calibration_coordinator

    payload = request.get_json(silent=True) or {}
    action = (payload.get("action") or "").strip().lower()
    db = DatabaseManager()
    if action == "release":
        return jsonify(calibration_coordinator.release_lock(db=db))
    if action == "acquire":
        state = payload.get("state") or "running_batch"
        owner = payload.get("owner") or session.get("email") or "admin"
        subnode = payload.get("subnode")
        try:
            return jsonify(
                calibration_coordinator.acquire_lock(state, owner, subnode=subnode, db=db)
            )
        except calibration_coordinator.CalibrationLockError as exc:
            return jsonify({"error": str(exc)}), 409
    return jsonify({"error": "action must be acquire or release"}), 400


@app.route("/api/calibration/resolve-disagreement", methods=["POST"])
@admin_required
def api_calibration_resolve_disagreement():
    """Resolves a Maude vs LLM disagreement, teaches Maude cues, and logs feedback."""
    payload = request.get_json(silent=True) or {}
    try:
        paper_id = int(payload.get("paper_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "paper_id is required"}), 400
    batch_id = payload.get("batch_id")
    fields = payload.get("fields") or []
    if not batch_id:
        return jsonify({"error": "batch_id is required"}), 400
    if not fields:
        return jsonify({"error": "fields resolutions are required"}), 400

    output_dir = _calibration_output_dir()
    db = DatabaseManager()
    try:
        result = maude_feedback.resolve_disagreement(
            paper_id=paper_id,
            batch_id=batch_id,
            field_resolutions=fields,
            output_dir=output_dir,
            db=db,
        )
        calibration_metrics.build_dashboard(
            output_dir=output_dir,
            rules_path=Path(BASE_DIR) / "rules_config.json",
        )
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"Error resolving Maude disagreement for paper {paper_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/agents/automation-status", methods=["GET"])
def api_agents_automation_status():
    """Returns agent-readable automation, feedback, and review queue status."""
    db = DatabaseManager()
    config = classifier.load_rules_config()
    thresholds = config.get("confidence_thresholds", {})
    review_threshold = float(thresholds.get("review_recommended", 0.6))
    manifest = load_reliability_manifest()
    
    return jsonify({
        "scheduler": {
            "active": db.get_metadata("scheduler_active", "false") == "true",
            "last_daily_harvest_date": db.get_metadata("last_daily_harvest_date", "Never"),
            "last_daily_harvest_status": db.get_metadata("last_daily_harvest_status", "Never run")
        },
        "feedback": {
            "corrections_since_eval": int(db.get_metadata("feedback_corrections_since_eval", "0") or 0),
            "last_feedback_audit_timestamp": db.get_metadata("last_feedback_audit_timestamp")
        },
        "classification": {
            "low_confidence_queue_count": db.count_low_confidence_papers(review_threshold),
            "review_threshold": review_threshold,
            "auto_accept_threshold": float(thresholds.get("auto_accept", 0.85)),
            "rules_version": config.get("version", "1.0.0")
        },
        "agent_automation": config.get("agent_automation", {}),
        "reliability_manifest": manifest
    })

@app.route("/api/classification/queue", methods=["GET"])
def api_classification_queue():
    """Returns low-confidence papers that need expert or agent review."""
    config = classifier.load_rules_config()
    thresholds = config.get("confidence_thresholds", {})
    try:
        confidence_max = float(request.args.get("confidence_max", thresholds.get("review_recommended", 0.6)))
    except (TypeError, ValueError):
        confidence_max = float(thresholds.get("review_recommended", 0.6))
    limit = coerce_positive_int(request.args.get("limit"), default=20, maximum=100)
    
    db = DatabaseManager()
    papers = db.get_low_confidence_papers(confidence_max=confidence_max, limit=limit)
    return jsonify({
        "confidence_max": confidence_max,
        "limit": limit,
        "papers": papers
    })

@app.route("/api/feedback/recent", methods=["GET"])
def api_feedback_recent():
    """Returns recent feedback audit rows for prompt and decision-chart context."""
    limit = coerce_positive_int(request.args.get("limit"), default=50, maximum=200)
    db = DatabaseManager()
    return jsonify({
        "limit": limit,
        "feedback": db.get_recent_feedback(limit=limit)
    })

@app.route("/api/classification/run-eval", methods=["POST"])
@admin_required
def api_classification_run_eval():
    """Runs reliability evaluation and resets the feedback-since-eval counter."""
    try:
        import eval_reliability
        
        eval_reliability.main()
        db = DatabaseManager()
        now_str = datetime.now().isoformat()
        db.set_metadata("last_reliability_eval_timestamp", now_str)
        db.set_metadata("feedback_corrections_since_eval", "0")
        return jsonify({
            "message": "Reliability evaluation completed.",
            "last_reliability_eval_timestamp": now_str,
            "reliability_manifest": load_reliability_manifest()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ─── Analyses Endpoints ─────────────────────────────────────────

ANALYSIS_QUANTITATIVE_FIELDS = (
    ("thc_pct", "THC %"),
    ("cbd_pct", "CBD %"),
    ("sample_size", "Sample Size"),
    ("dose_mg", "Dose (mg)"),
    ("puff_count", "Puff Count"),
    ("duration_days", "Study Duration (days)"),
    ("thc_mg_kg", "THC (mg/kg)"),
    ("cbd_mg_kg", "CBD (mg/kg)"),
    ("thc_mg_ml", "THC (mg/mL)"),
    ("cbd_mg_ml", "CBD (mg/mL)"),
    ("thc_mg_g", "THC (mg/g)"),
    ("cbd_mg_g", "CBD (mg/g)"),
    ("thc_uM", "THC (µM)"),
    ("cbd_uM", "CBD (µM)"),
    ("repeat_exposure_count", "Repeat Exposures"),
    ("citation_count", "Citations"),
)

ANALYSIS_PAPER_EXTRA_FIELDS = (
    "treatment_duration",
    "administration_frequency",
    "population_sex",
    "population_age",
)


def _numeric_field_value(value):
    """Return a float when value is numeric, else None."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _median_value(values):
    """Return the median of a non-empty numeric sequence."""
    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2:
        return ordered[mid]
    return round((ordered[mid - 1] + ordered[mid]) / 2, 2)


def _compute_quantitative_aggregates(papers):
    """Min, median, max, and count for each numeric field with at least one reported value."""
    aggregates = {}
    for field_key, label in ANALYSIS_QUANTITATIVE_FIELDS:
        values = [
            num for p in papers
            if (num := _numeric_field_value(p.get(field_key))) is not None
        ]
        if values:
            values.sort()
            aggregates[field_key] = {
                "label": label,
                "kind": "numeric",
                "count": len(values),
                "n": len(values),
                "min": values[0],
                "median": _median_value(values),
                "max": values[-1],
                "avg": _median_value(values),
            }
    return aggregates


def _slim_paper_for_analysis(paper):
    """Minimal paper payload for My Analyses chart drill-down."""
    slim = {
        "id": paper.get("id"),
        "title": paper.get("title") or "Untitled",
        "year": paper.get("year"),
        "publication_date": paper.get("publication_date"),
        "study_type": paper.get("study_type"),
        "exposure_method": paper.get("exposure_method"),
        "cannabis_type": paper.get("cannabis_type"),
        "outcome_domain": paper.get("outcome_domain") or [],
    }
    for field_key, _label in ANALYSIS_QUANTITATIVE_FIELDS:
        slim[field_key] = paper.get(field_key)
    for field_key in ANALYSIS_PAPER_EXTRA_FIELDS:
        slim[field_key] = paper.get(field_key)
    return slim


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
    quantitative_aggregates = _compute_quantitative_aggregates(papers)

    study_design = {}
    timeline = {}
    thc_bins = {"zero": 0, "low": 0, "medLow": 0, "med": 0, "medHigh": 0, "high": 0, "veryHigh": 0, "ultraHigh": 0, "notReported": 0}
    cbd_bins = {"zero": 0, "low": 0, "medLow": 0, "med": 0, "medHigh": 0, "high": 0, "veryHigh": 0, "ultraHigh": 0, "notReported": 0}
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
        yr = str(p.get("year") or "N/A")
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

        cbd = p.get("cbd_pct")
        if cbd is None:
            cbd_bins["notReported"] += 1
        elif cbd == 0:
            cbd_bins["zero"] += 1
        elif cbd <= 5:
            cbd_bins["low"] += 1
        elif cbd <= 10:
            cbd_bins["medLow"] += 1
        elif cbd <= 15:
            cbd_bins["med"] += 1
        elif cbd <= 20:
            cbd_bins["medHigh"] += 1
        elif cbd <= 25:
            cbd_bins["high"] += 1
        elif cbd <= 30:
            cbd_bins["veryHigh"] += 1
        else:
            cbd_bins["ultraHigh"] += 1

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
        "quantitative_aggregates": quantitative_aggregates,
        "study_design": study_design,
        "thc_bins": thc_bins,
        "cbd_bins": cbd_bins,
        "timeline": timeline,
        "clinical_exposure": clinical_exp,
        "vitro_exposure": vitro_exp,
        "vivo_exposure": vivo_exp,
        "cannabis_type": cannabis_type,
        "outcome": outcome
    }


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """Accepts filter params, fetches matching papers, computes chart data, saves analysis to DB if logged in."""
    data = request.get_json() or {}
    filters = data.get("filters", {})
    name = data.get("name", f"Analysis {datetime.now().strftime('%b %d %Y %H:%M')}")

    db = DatabaseManager()
    db.init_analyses_table()

    try:
        filters = dict(filters)
        filters["limit"] = 100000
        filters["offset"] = 0
        papers = db.search_papers_for_analysis(filters)

        chart_data = _compute_analysis_chart_data(papers)
        chart_data["paper_ids"] = [p["id"] for p in papers]
        analysis_papers = [_slim_paper_for_analysis(p) for p in papers]

        user = _get_session_user(db)
        if not user:
            return jsonify({
                "id": None,
                "name": name,
                "paper_count": chart_data["paper_count"],
                "filter_settings": filters,
                "chart_data": chart_data,
                "papers": analysis_papers,
                "created_at": datetime.now().isoformat(),
            })

        analysis_id = db.create_analysis(
            name=name,
            filter_settings=json.dumps(filters, default=str),
            paper_count=chart_data["paper_count"],
            chart_data=json.dumps(chart_data, default=str),
            user_id=user["id"],
        )

        return jsonify({
            "id": analysis_id,
            "name": name,
            "paper_count": chart_data["paper_count"],
            "filter_settings": filters,
            "chart_data": chart_data,
            "papers": analysis_papers,
            "created_at": datetime.now().isoformat(),
        })
    except Exception as e:
        app.logger.error(f"Analysis failed: {e}")
        return jsonify({"error": str(e)}), 500


DASHBOARD_VISIBLE_COLUMN_KEYS = frozenset({
    "duration", "treatment_duration", "year", "study_type", "exposure_method",
    "publication_type", "citations", "links", "cannabis_type", "outcome_domain",
    "dose_mg", "puff_count", "admin_frequency", "population_age", "population_sex",
    "thc_mg_ml", "cbd_mg_ml", "thc_mg_kg", "cbd_mg_kg", "thc_uM", "cbd_uM",
    "strain_reported", "strain_normalized",
})


def _sanitize_visible_columns(raw):
    """Keep only known dashboard column keys with boolean values."""
    if not isinstance(raw, dict):
        return {}
    return {
        key: bool(value)
        for key, value in raw.items()
        if key in DASHBOARD_VISIBLE_COLUMN_KEYS and isinstance(value, bool)
    }


@app.route("/api/user/dashboard-preferences", methods=["GET"])
def api_get_dashboard_preferences():
    """Return saved dashboard UI preferences for the logged-in user."""
    user = _get_session_user()
    if not user:
        return jsonify({"error": "Unauthorized", "login_required": True}), 401

    db = DatabaseManager()
    prefs = db.get_user_dashboard_preferences(user["id"])
    visible_columns = _sanitize_visible_columns(prefs.get("visible_columns"))
    return jsonify({"visible_columns": visible_columns})


@app.route("/api/user/dashboard-preferences", methods=["PUT"])
def api_set_dashboard_preferences():
    """Persist dashboard UI preferences for the logged-in user."""
    user = _get_session_user()
    if not user:
        return jsonify({"error": "Unauthorized", "login_required": True}), 401

    data = request.get_json(silent=True) or {}
    visible_columns = _sanitize_visible_columns(data.get("visible_columns"))
    db = DatabaseManager()
    existing = db.get_user_dashboard_preferences(user["id"])
    merged = {**existing, "visible_columns": visible_columns}
    if not db.set_user_dashboard_preferences(user["id"], merged):
        return jsonify({"error": "Failed to save preferences"}), 500
    return jsonify({"success": True, "visible_columns": visible_columns})


@app.route("/api/analyses/save-guest", methods=["POST"])
def api_save_guest_analysis():
    """Persist a guest preview analysis to the logged-in user's account."""
    user = _get_session_user()
    if not user:
        return jsonify({"error": "Unauthorized", "login_required": True}), 401

    data = request.get_json() or {}
    chart_data = data.get("chart_data")
    if not isinstance(chart_data, dict):
        return jsonify({"error": "Missing or invalid chart_data"}), 400

    filter_settings = data.get("filter_settings") or {}
    if not isinstance(filter_settings, dict):
        return jsonify({"error": "Invalid filter_settings"}), 400

    name = (data.get("name") or "").strip() or f"Analysis {datetime.now().strftime('%b %d %Y %H:%M')}"
    paper_count = int(data.get("paper_count") or chart_data.get("paper_count") or 0)

    db = DatabaseManager()
    db.init_analyses_table()
    analysis_id = db.create_analysis(
        name=name,
        filter_settings=json.dumps(filter_settings, default=str),
        paper_count=paper_count,
        chart_data=json.dumps(chart_data, default=str),
        user_id=user["id"],
    )

    return jsonify({
        "id": analysis_id,
        "name": name,
        "paper_count": paper_count,
        "filter_settings": filter_settings,
        "chart_data": chart_data,
        "created_at": datetime.now().isoformat(),
    })


@app.route("/api/analyses", methods=["GET"])
def api_list_analyses():
    """Returns all saved analyses for the logged-in user, or empty list if logged out."""
    user = _get_session_user()
    if not user:
        return jsonify([]), 200

    db = DatabaseManager()
    analyses = db.list_analyses(user_id=user["id"])
    # Parse JSON fields for the frontend
    for a in analyses:
        try:
            a["filter_settings"] = json.loads(a["filter_settings"])
        except (json.JSONDecodeError, TypeError):
            a["filter_settings"] = {}
    return jsonify(analyses)


def _fetch_analysis_papers(db, chart_data, filter_settings):
    """Load slim paper rows for an analysis from stored paper_ids."""
    paper_ids = chart_data.get("paper_ids") or filter_settings.get("paper_ids") or []
    if not paper_ids:
        return []
    import sqlite3
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        placeholders = ",".join(["?"] * len(paper_ids))
        cursor.execute(f"SELECT * FROM papers WHERE id IN ({placeholders})", paper_ids)
        rows = cursor.fetchall()
        papers = []
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
            for json_field in ["study_type", "exposure_method", "cannabis_type"]:
                if res.get(json_field):
                    try:
                        val = res[json_field].strip()
                        if val.startswith("[") and val.endswith("]"):
                            res[json_field] = json.loads(res[json_field])
                    except Exception:
                        pass
            papers.append(_slim_paper_for_analysis(res))
        return papers
    finally:
        conn.close()


@app.route("/api/analyses/<int:analysis_id>/papers", methods=["GET"])
def api_get_analysis_papers(analysis_id):
    """Returns slim paper rows for chart drill-down on a saved analysis."""
    user = _get_session_user()
    if not user:
        return jsonify({"error": "Unauthorized", "login_required": True}), 401

    db = DatabaseManager()
    analysis = db.get_analysis(analysis_id)
    if not analysis:
        return jsonify({"error": "Analysis not found"}), 404
    if analysis.get("user_id") != user["id"]:
        return jsonify({"error": "Forbidden"}), 403

    try:
        filter_settings = json.loads(analysis["filter_settings"])
    except (json.JSONDecodeError, TypeError):
        filter_settings = {}
    try:
        chart_data = json.loads(analysis["chart_data"])
    except (json.JSONDecodeError, TypeError):
        chart_data = {}

    return jsonify({"papers": _fetch_analysis_papers(db, chart_data, filter_settings)})


@app.route("/api/analyses/<int:analysis_id>", methods=["GET"])
def api_get_analysis(analysis_id):
    """Returns full analysis data including chart_data if owned by user."""
    user = _get_session_user()
    if not user:
        return jsonify({"error": "Unauthorized", "login_required": True}), 401

    db = DatabaseManager()
    analysis = db.get_analysis(analysis_id)
    if not analysis:
        return jsonify({"error": "Analysis not found"}), 404

    if analysis.get("user_id") != user["id"]:
        return jsonify({"error": "Forbidden"}), 403

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
    """Updates an analysis (e.g. rename) if owned by user."""
    user = _get_session_user()
    if not user:
        return jsonify({"error": "Unauthorized", "login_required": True}), 401

    db = DatabaseManager()
    analysis = db.get_analysis(analysis_id)
    if not analysis:
        return jsonify({"error": "Analysis not found"}), 404

    if analysis.get("user_id") != user["id"]:
        return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
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
    """Deletes an analysis if owned by user."""
    user = _get_session_user()
    if not user:
        return jsonify({"error": "Unauthorized", "login_required": True}), 401

    db = DatabaseManager()
    analysis = db.get_analysis(analysis_id)
    if not analysis:
        return jsonify({"error": "Analysis not found"}), 404

    if analysis.get("user_id") != user["id"]:
        return jsonify({"error": "Forbidden"}), 403

    if db.delete_analysis(analysis_id):
        return jsonify({"success": True})
    return jsonify({"error": "Analysis not found"}), 404


@app.route("/api/analyses/<int:analysis_id>/export-csv", methods=["GET"])
def api_export_analysis_csv(analysis_id):
    """Generates and downloads a CSV export of all papers associated with a saved analysis if owned by user."""
    user = _get_session_user()
    if not user:
        return jsonify({"error": "Unauthorized", "login_required": True}), 401

    db = DatabaseManager()
    analysis = db.get_analysis(analysis_id)
    if not analysis:
        return jsonify({"error": "Analysis not found"}), 404

    if analysis.get("user_id") != user["id"]:
        return jsonify({"error": "Forbidden"}), 403

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
        "repeat_exposure_count", "exposure_regimen_bin",
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
@mvp_gate
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

        # Load reliability manifest
        reliability_manifest = load_reliability_manifest()

        # Compute learning growth trends
        cursor.execute("""
            SELECT 
                classifier_version,
                study_type,
                classification_confidence
            FROM papers
            WHERE classifier_version IS NOT NULL AND classifier_version LIKE 'llm-%' AND classification_confidence IS NOT NULL
        """)
        rows = cursor.fetchall()
        
        version_category_stats = {}
        for r in rows:
            version_str = r["classifier_version"]
            suffix_match = re.search(r"(\d+\.\d+\.\d+)", version_str)
            version_suffix = suffix_match.group(1) if suffix_match else "1.0.0"
            
            study_type_str = r["study_type"] or ""
            if "Clinical" in study_type_str:
                category = "clinical"
            elif "Animal" in study_type_str or "Cell" in study_type_str:
                category = "preclinical"
            else:
                category = "preclinical"
                
            conf = r["classification_confidence"] or 0.0
            
            if version_suffix not in version_category_stats:
                version_category_stats[version_suffix] = {
                    "clinical": {"sum": 0.0, "count": 0},
                    "preclinical": {"sum": 0.0, "count": 0}
                }
                
            version_category_stats[version_suffix][category]["sum"] += conf
            version_category_stats[version_suffix][category]["count"] += 1
            
        sorted_versions = sorted(version_category_stats.keys())
        learning_growth = []
        for v in sorted_versions:
            clin_data = version_category_stats[v]["clinical"]
            pre_data = version_category_stats[v]["preclinical"]
            learning_growth.append({
                "version": v,
                "clinical_avg_conf": round(clin_data["sum"] / clin_data["count"], 3) if clin_data["count"] > 0 else 0.0,
                "clinical_count": clin_data["count"],
                "preclinical_avg_conf": round(pre_data["sum"] / pre_data["count"], 3) if pre_data["count"] > 0 else 0.0,
                "preclinical_count": pre_data["count"]
            })

        # Prepare response payload
        metrics = {
            "reliability_manifest": reliability_manifest,
            "learning_growth": learning_growth,
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
            "versions": versions,
            "calibration": calibration_metrics.build_dashboard_metrics(
                output_dir=_calibration_output_dir(),
                rules_config=classifier.load_rules_config(),
            ),
        }
        
        return jsonify(metrics)
        
    except Exception as e:
        app.logger.error(f"Error compiling Learning Dashboard metrics: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# ─── Graph / Connectivity API Routes ──────────────────────────────


@app.route("/api/graph/stats", methods=["GET"])
@mvp_gate
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
@mvp_gate
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
    custom_graph = CitationGraph(db)
    try:
        citing = custom_graph.get_cited_by(paper_id, include_external=include_ext)
        return jsonify(citing)
    except Exception as e:
        app.logger.error(f"Cited-by error for {paper_id}: {e}")
        return jsonify({"error": str(e)}), 500


# --- Phase 2 Automation Systems: Heuristics & Asynchronous Backpopulation ---

from typing import Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor
import uuid
import time
import extractor
import heuristics_engine

# Global thread pool for asynchronous backpopulation (max 2 threads)
task_executor = ThreadPoolExecutor(max_workers=2)

def _eval_list_match(extracted: Any, ground_truth: Any) -> bool:
    """Helper to compare list-valued fields as sets (case-insensitive)."""
    ext_set = {str(x).strip().lower() for x in (extracted or [])}
    gt_set = {str(x).strip().lower() for x in (ground_truth or [])}
    return ext_set == gt_set

def _eval_val_match(extracted: str, ground_truth: str, is_pub_type: bool = False) -> bool:
    """Helper to compare single-valued fields (case-insensitive)."""
    ext_str = str(extracted).strip().lower() if extracted else ""
    gt_str = str(ground_truth).strip().lower() if ground_truth else ""
    if is_pub_type:
        reviews = ["review", "meta-analysis", "systematic review", "scoping review"]
        if gt_str in reviews:
            return ext_str == "review"
    return ext_str == gt_str

def run_golden_regression_eval(rules_dict: Dict[str, Any]) -> float:
    """Runs a dry-run regression evaluation against the 100 golden papers using the provided rules."""
    # Temporarily apply new rules and compile them
    original_config = heuristics_engine._config
    try:
        heuristics_engine._config = rules_dict
        heuristics_engine.patterns.compile_rules(rules_dict)
        
        # Load golden dataset using absolute path
        dataset_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "golden_dataset.json")
        if not os.path.exists(dataset_path):
            logger.warning(f"Golden dataset not found at {dataset_path} during eval.")
            return 0.0
            
        with open(dataset_path, "r", encoding="utf-8") as f:
            papers = json.load(f)
            
        correct = 0
        total = 0
        
        for p in papers:
            title = p["title"]
            text = p["text"]
            gt = p["ground_truth"]
            
            # Run extraction
            res = extractor.extract_all_heuristics(title=title, abstract=text, full_text=None)
            
            # Tier 1 Routing
            pub_ok = _eval_val_match(res.get("publication_type"), gt.get("publication_type"), is_pub_type=True)
            study_ok = _eval_list_match(res.get("study_type"), gt.get("study_type"))
            
            total += 2
            if pub_ok:
                correct += 1
            if study_ok:
                correct += 1
                
            # Tier 2 Extraction
            pub_coarse = str(res.get("publication_type")).strip().lower()
            gt_pub = str(gt.get("publication_type")).strip().lower()
            gt_pub_coarse = "review" if gt_pub in ["review", "meta-analysis", "systematic review"] else "original research"
            
            if pub_coarse == "original research" and gt_pub_coarse == "original research":
                exposure_ok = _eval_list_match(res.get("exposure_method"), gt.get("exposure_method"))
                cannabis_ok = _eval_list_match(res.get("cannabis_type"), gt.get("cannabis_type"))
                
                total += 2
                if exposure_ok:
                    correct += 1
                if cannabis_ok:
                    correct += 1
                    
                study_types = {str(s).lower() for s in (gt.get("study_type") or [])}
                is_clinical = any("clinical" in s for s in study_types)
                if is_clinical:
                    age_ok = _eval_val_match(res.get("population_age"), gt.get("population_age"))
                    sex_ok = _eval_val_match(res.get("population_sex"), gt.get("population_sex"))
                    total += 2
                    if age_ok:
                        correct += 1
                    if sex_ok:
                        correct += 1
                        
        score = correct / total if total > 0 else 0.0
        return score
    finally:
        # Restore original rules and re-compile
        heuristics_engine._config = original_config
        heuristics_engine.patterns.compile_rules(original_config)


def async_backpopulate_task(task_id: str):
    """Background thread function that reclassifies the database and updates task progress."""
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    is_postgres = "DATABASE_URL" in os.environ
    param = "%s" if is_postgres else "?"
    
    try:
        # Select all papers in the database (limit during testing to prevent locks)
        if app.config.get('TESTING'):
            cursor.execute("SELECT id, title, abstract FROM papers LIMIT 2")
        else:
            cursor.execute("SELECT id, title, abstract FROM papers")
        rows = cursor.fetchall()
        
        papers_to_process = []
        for row in rows:
            p_id = row[0] if isinstance(row, tuple) else row["id"]
            p_title = row[1] if isinstance(row, tuple) else row["title"]
            p_abs = row[2] if isinstance(row, tuple) else row["abstract"]
            papers_to_process.append((p_id, p_title, p_abs))
            
        total_papers = len(papers_to_process)
        
        # Update background_tasks table with total count and set status to running
        cursor.execute(
            f"UPDATE background_tasks SET total_papers = {param}, status = 'running', updated_at = CURRENT_TIMESTAMP WHERE task_id = {param}",
            (total_papers, task_id)
        )
        conn.commit()
        
        processed_count = 0
        for p_id, p_title, p_abs in papers_to_process:
            # Check if there is a cached full text
            full_text = None
            cache_path = f"scratch/paper_cache/text/{p_id}.txt"
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as f_txt:
                        full_text = f_txt.read()
                except:
                    pass
            
            # Run extraction pipeline
            res = extractor.extract_all_heuristics(title=p_title, abstract=p_abs, full_text=full_text)
            
            # Extract fields
            age = res.get("population_age")
            sex = res.get("population_sex")
            study_type_list = res.get("study_type") or []
            pub_type = res.get("publication_type")
            exposure = res.get("exposure_method") or []
            cannabis = res.get("cannabis_type") or []
            
            # Update papers table
            cursor.execute(
                f"UPDATE papers SET "
                f"population_age = {param}, "
                f"population_sex = {param}, "
                f"study_type = {param}, "
                f"publication_type = {param}, "
                f"exposure_method = {param}, "
                f"cannabis_type = {param} "
                f"WHERE id = {param}",
                (age, sex, json.dumps(study_type_list), pub_type, json.dumps(exposure), json.dumps(cannabis), p_id)
            )
            
            processed_count += 1
            if processed_count % 10 == 0 or processed_count == total_papers:
                cursor.execute(
                    f"UPDATE background_tasks SET processed_papers = {param}, updated_at = CURRENT_TIMESTAMP WHERE task_id = {param}",
                    (processed_count, task_id)
                )
                conn.commit()
                
            # Sleep 1ms to prevent database locks
            time.sleep(0.001)
            
        # Successfully completed!
        cursor.execute(
            f"UPDATE background_tasks SET status = 'completed', updated_at = CURRENT_TIMESTAMP WHERE task_id = {param}",
            (task_id,)
        )
        conn.commit()
    except Exception as e:
        app.logger.error(f"Background task {task_id} failed: {e}")
        try:
            cursor.execute(
                f"UPDATE background_tasks SET status = 'failed', error_message = {param}, updated_at = CURRENT_TIMESTAMP WHERE task_id = {param}",
                (str(e), task_id)
            )
            conn.commit()
        except:
            pass
    finally:
        conn.close()


@app.route("/api/heuristics/rules", methods=["GET"])
@admin_required
def api_get_heuristics_rules():
    """Return active heuristics rules from the database."""
    rules = heuristics_engine.load_rules_from_db() or heuristics_engine._config
    return jsonify(rules)


@app.route("/api/llm/rules", methods=["GET"])
@admin_required
def api_get_llm_rules():
    """Return active LLM rules (rules_config) from the database."""
    rules = heuristics_engine.load_rules_config()
    return jsonify(rules)


@app.route("/api/llm/test", methods=["POST"])
@admin_required
def api_test_llm_rules():
    """Validate a proposed LLM rules payload (verifying JSON and prompt compilation)."""
    rules_dict = request.get_json()
    if not rules_dict:
        return jsonify({"error": "Invalid rules payload."}), 400
    try:
        # Validate that we can compile the system prompt
        prompt = classifier.compile_system_prompt(rules_dict)
        if not prompt:
            return jsonify({"error": "System prompt compilation returned empty string."}), 422
        return jsonify({
            "status": "success",
            "message": "LLM rules validated successfully. Prompt compiled with no errors.",
            "prompt_length": len(prompt)
        })
    except Exception as e:
        return jsonify({"error": f"Validation failed: {str(e)}"}), 422


@app.route("/api/llm/rules", methods=["POST"])
@admin_required
def api_save_llm_rules():
    """Validate and save LLM rules to the database."""
    rules_dict = request.get_json()
    if not rules_dict:
        return jsonify({"error": "Invalid rules payload."}), 400
        
    try:
        # 1. Validate prompt compilation
        prompt = classifier.compile_system_prompt(rules_dict)
        if not prompt:
            return jsonify({"error": "System prompt compilation returned empty string."}), 422
            
        # 2. Save and reload
        heuristics_engine.seed_rules_config_to_db(rules_dict)
        heuristics_engine.reload_rules_config()
        
        return jsonify({
            "status": "success",
            "message": "LLM rules updated successfully in database."
        })
    except Exception as e:
        return jsonify({"error": f"Failed to save rules: {str(e)}"}), 500


@app.route("/api/heuristics/test", methods=["POST"])
@admin_required
def api_test_heuristics_rules():
    """Run a dry-run regression evaluation against the golden dataset."""
    rules_dict = request.get_json()
    if not rules_dict:
        return jsonify({"error": "Invalid rules payload."}), 400
    try:
        score = run_golden_regression_eval(rules_dict)
        return jsonify({"score": score})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/heuristics/rules", methods=["POST"])
@admin_required
def api_save_heuristics_rules():
    """Validate rules against the golden dataset and save to database if no regression."""
    rules_dict = request.get_json()
    if not rules_dict:
        return jsonify({"error": "Invalid rules payload."}), 400
        
    try:
        # 1. Run regression evaluation
        score = run_golden_regression_eval(rules_dict)
        
        # 2. Get current score for comparison
        current_db_rules = heuristics_engine.load_rules_from_db() or heuristics_engine.FALLBACK_CONFIG
        current_score = run_golden_regression_eval(current_db_rules)
        
        # Zero-regression gate
        if score < current_score:
            return jsonify({
                "error": f"Save blocked: This change causes a regression. New score: {score * 100:.2f}%, Current score: {current_score * 100:.2f}%"
            }), 422
            
        # 3. Save and reload
        heuristics_engine.seed_rules_to_db(rules_dict)
        heuristics_engine.reload_rules()
        
        return jsonify({
            "score": score,
            "message": f"Rules updated successfully! Golden dataset score: {score * 100:.2f}%"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/backpopulate", methods=["POST"])
@admin_required
def api_trigger_backpopulate():
    """Trigger asynchronous database backpopulation."""
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    is_postgres = "DATABASE_URL" in os.environ
    param = "%s" if is_postgres else "?"
    
    try:
        task_id = str(uuid.uuid4())
        
        # Insert pending task
        cursor.execute(
            f"INSERT INTO background_tasks (task_id, sa_task_type, status, total_papers, processed_papers) "
            f"VALUES ({param}, {param}, {param}, {param}, {param})",
            (task_id, "backpopulation", "pending", 0, 0)
        )
        conn.commit()
        
        # Dispatch to thread pool
        task_executor.submit(async_backpopulate_task, task_id)
        
        return jsonify({"task_id": task_id, "status": "pending"}), 202
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


@app.route("/api/tasks/<task_id>", methods=["GET"])
@admin_required
def api_get_task_status(task_id):
    """Retrieve real-time status and progress of a background task."""
    db = DatabaseManager()
    conn = db.get_connection()
    cursor = conn.cursor()
    
    is_postgres = "DATABASE_URL" in os.environ
    param = "%s" if is_postgres else "?"
    
    try:
        cursor.execute(
            f"SELECT sa_task_type, status, total_papers, processed_papers, error_message FROM background_tasks WHERE task_id = {param}",
            (task_id,)
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Task not found."}), 404
            
        res = {
            "task_id": task_id,
            "task_type": row[0] if isinstance(row, tuple) else row["sa_task_type"],
            "status": row[1] if isinstance(row, tuple) else row["status"],
            "total_papers": row[2] if isinstance(row, tuple) else row["total_papers"],
            "processed_papers": row[3] if isinstance(row, tuple) else row["processed_papers"],
            "error_message": row[4] if isinstance(row, tuple) else row["error_message"]
        }
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# Start the background daily scheduler thread, protected against debug reloader double-runs and unit tests
import sys
if (not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true") and "unittest" not in sys.modules:
    logging.getLogger("scheduler").info("Launching daily automatic harvest scheduler thread...")
    threading.Thread(target=daily_harvest_scheduler, daemon=True).start()

if __name__ == "__main__":
    # Start server on local network port 5001 to bypass macOS default AirPlay port conflict (5000)
    app.run(debug=True, port=5001)

