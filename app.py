"""SafeRoute AI: a local, fictional personal-safety demo."""
from datetime import datetime, timezone
from functools import wraps
import json
import os
import random
import secrets
import sqlite3
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "saferoute.db"
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SAFEROUTE_SECRET", "dev-only-change-me")
app.config["DEMO_MODE"] = True

DEMO_LOCATION = {"lat": 40.7128, "lng": -74.0060, "label": "Demo Central"}
RISK_WEIGHTS = {"crime": 0.40, "time": 0.20, "lighting": 0.15, "crowd": 0.15, "community": 0.10}


def normalize_indian_mobile(value):
    """Accept Indian numbers and the fictional 555 numbers used by this demo."""
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if digits.startswith("555") and len(digits) in (7, 10):
        return f"+1{digits}"
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        raise ValueError("Enter a valid 10-digit Indian mobile number.")
    return f"+91{digits}"


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    connection = db()
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, mobile TEXT UNIQUE NOT NULL,
            email TEXT, password_hash TEXT NOT NULL, otp TEXT, otp_verified INTEGER DEFAULT 0,
            blood_group TEXT, allergies TEXT, medical_info TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trusted_contacts (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL, mobile TEXT NOT NULL, relationship TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS risk_zones (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, lat REAL NOT NULL, lng REAL NOT NULL,
            radius REAL NOT NULL, score INTEGER NOT NULL, lighting TEXT NOT NULL, crowd TEXT NOT NULL,
            sensitive INTEGER DEFAULT 0, description TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            lat REAL NOT NULL, lng REAL NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
            severity TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT DEFAULT 'Review queued'
        );
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS emergency_alerts (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status TEXT NOT NULL, lat REAL, lng REAL, risk_score INTEGER, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS location_shares (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            lat REAL NOT NULL, lng REAL NOT NULL, active INTEGER DEFAULT 1, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS privacy_settings (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            location_sharing INTEGER DEFAULT 1, contact_sharing INTEGER DEFAULT 1,
            community_reports INTEGER DEFAULT 1
        );
        """
    )
    if connection.execute("SELECT COUNT(*) FROM risk_zones").fetchone()[0] == 0:
        zones = [
            ("Demo Zone A", 40.714, -74.009, 0.004, 76, "Poor", "Low", 0, "Late-night demo zone with limited lighting."),
            ("Demo Zone B", 40.709, -74.001, 0.003, 48, "Moderate", "Moderate", 0, "Moderate awareness recommended."),
            ("Demo Greenway", 40.718, -74.012, 0.004, 18, "Good", "Crowded", 0, "Well-lit fictional public space."),
            ("Demo Transit Hub", 40.706, -74.008, 0.003, 63, "Moderate", "Low", 0, "Demo transit area; stay aware after dark."),
            ("Sensitive Arts Quarter", 40.721, -74.002, 0.003, 52, "Good", "Moderate", 1, "Additional awareness area; not a claim about people or businesses."),
            ("Demo Zone C", 40.701, -74.014, 0.004, 84, "Poor", "Isolated", 0, "Critical demo score for route planning."),
            ("Demo Market", 40.725, -74.010, 0.003, 26, "Good", "Crowded", 0, "Busy fictional market area."),
            ("Demo Zone D", 40.731, -74.004, 0.004, 57, "Moderate", "Moderate", 0, "Elevated demo risk."),
            ("Demo Riverside", 40.695, -74.006, 0.004, 69, "Poor", "Low", 0, "Use a companion and prefer lit routes."),
            ("Sensitive Nightlife Area", 40.713, -73.994, 0.003, 44, "Moderate", "Crowded", 1, "Additional awareness area; not a claim about people or businesses."),
        ]
        connection.executemany("INSERT INTO risk_zones(name,lat,lng,radius,score,lighting,crowd,sensitive,description) VALUES (?,?,?,?,?,?,?,?,?)", zones)
    connection.commit()
    connection.close()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def current_user(connection=None):
    owns = connection is None
    connection = connection or db()
    user = connection.execute("SELECT * FROM users WHERE id=?", (session.get("user_id"),)).fetchone()
    if owns:
        connection.close()
    return user


def contacts_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        connection = db()
        count = connection.execute("SELECT COUNT(*) FROM trusted_contacts WHERE user_id=?", (session["user_id"],)).fetchone()[0]
        connection.close()
        if count == 0:
            return redirect(url_for("contacts"))
        return view(*args, **kwargs)
    return wrapped


def calculate_risk_score(crime_reports=50, time_of_day="evening", lighting="moderate", crowd_level="moderate", community_reports=30):
    """Return a transparent demo score from 0-100; this is not a validated safety measure."""
    time_values = {"morning": 15, "afternoon": 20, "evening": 55, "night": 90}
    lighting_values = {"good": 10, "moderate": 50, "poor": 90}
    crowd_values = {"crowded": 10, "moderate": 45, "low": 70, "isolated": 95}
    score = (
        max(0, min(100, crime_reports)) * RISK_WEIGHTS["crime"]
        + time_values.get(time_of_day.lower(), 55) * RISK_WEIGHTS["time"]
        + lighting_values.get(lighting.lower(), 50) * RISK_WEIGHTS["lighting"]
        + crowd_values.get(crowd_level.lower(), 45) * RISK_WEIGHTS["crowd"]
        + max(0, min(100, community_reports)) * RISK_WEIGHTS["community"]
    )
    return round(score)


def risk_label(score):
    return "SAFE" if score <= 20 else "MODERATE" if score <= 40 else "ELEVATED" if score <= 60 else "HIGH"


def add_notification(connection, user_id, title, body):
    connection.execute("INSERT INTO notifications(user_id,title,body,created_at) VALUES(?,?,?,?)", (user_id, title, body, now()))


def seed_demo_incidents():
    connection = db()
    if connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 0:
        categories = ["Poor lighting", "Unsafe road", "Suspicious activity", "Isolated area", "Harassment"]
        for index in range(20):
            connection.execute("INSERT INTO incidents(user_id,lat,lng,category,description,severity,created_at) VALUES(NULL,?,?,?,?,?,?)", (40.700 + (index % 7) * .004, -74.015 + (index % 5) * .005, categories[index % len(categories)], "Fictional anonymous demo report.", ["Low", "Medium", "High"][index % 3], now()))
        connection.commit()
    connection.close()


@app.route("/")
def index():
    return redirect(url_for("dashboard")) if session.get("user_id") else redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        form = request.form
        if not form.get("name") or not form.get("mobile") or not form.get("password"):
            error = "Name, mobile number, and password are required."
        else:
            connection = db()
            try:
                mobile = normalize_indian_mobile(form["mobile"])
                otp = f"{secrets.randbelow(1000000):06d}"
                cursor = connection.execute("INSERT INTO users(name,mobile,email,password_hash,otp,created_at) VALUES(?,?,?,?,?,?)", (form["name"].strip(), mobile, form.get("email", "").strip(), generate_password_hash(form["password"]), otp, now()))
                connection.commit()
                session["pending_user"] = cursor.lastrowid
                session["demo_otp"] = otp
                connection.close()
                print(f"[SafeRoute DEMO OTP] {otp}")
                return redirect(url_for("verify_otp"))
            except ValueError as validation_error:
                connection.close()
                error = str(validation_error)
            except sqlite3.IntegrityError:
                connection.close()
                error = "That mobile number is already registered."
    return render_template("auth.html", mode="signup", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        connection = db()
        try:
            mobile = normalize_indian_mobile(request.form.get("mobile"))
        except ValueError:
            mobile = ""
        user = connection.execute("SELECT * FROM users WHERE mobile=?", (mobile,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            otp = f"{secrets.randbelow(1000000):06d}"
            connection.execute("UPDATE users SET otp=?,otp_verified=0 WHERE id=?", (otp, user["id"]))
            connection.commit()
            session["pending_user"] = user["id"]
            session["demo_otp"] = otp
            connection.close()
            print(f"[SafeRoute DEMO OTP] {otp}")
            return redirect(url_for("verify_otp"))
        connection.close()
        error = "Invalid mobile number or password."
    return render_template("auth.html", mode="login", error=error)


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    if not session.get("pending_user"):
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        if secrets.compare_digest(str(request.form.get("otp", "")), str(session.get("demo_otp", ""))):
            connection = db()
            connection.execute("UPDATE users SET otp_verified=1 WHERE id=?", (session["pending_user"],))
            connection.commit()
            connection.close()
            session["user_id"] = session.pop("pending_user")
            session.pop("demo_otp", None)
            return redirect(url_for("contacts"))
        error = "That demo OTP is not correct."
    return render_template("otp.html", error=error, demo_otp=session.get("demo_otp"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/contacts", methods=["GET", "POST"])
@login_required
def contacts():
    connection = db()
    if request.method == "POST":
        form = request.form
        if form.get("name") and form.get("mobile") and form.get("relationship"):
            try:
                mobile = normalize_indian_mobile(form["mobile"])
                connection.execute("INSERT INTO trusted_contacts(user_id,name,mobile,relationship) VALUES(?,?,?,?)", (session["user_id"], form["name"].strip(), mobile, form["relationship"].strip()))
                connection.commit()
                add_notification(connection, session["user_id"], "TRUSTED CONTACT ADDED", "Demo contact setup is complete.")
                connection.commit()
            except ValueError:
                pass
    items = connection.execute("SELECT * FROM trusted_contacts WHERE user_id=?", (session["user_id"],)).fetchall()
    connection.close()
    return render_template("contacts.html", contacts=items)


@app.route("/dashboard")
@login_required
@contacts_required
def dashboard():
    connection = db()
    user = current_user(connection)
    zones = [dict(row) for row in connection.execute("SELECT * FROM risk_zones").fetchall()]
    incidents = [dict(row) for row in connection.execute("SELECT id,lat,lng,category,severity,created_at FROM incidents ORDER BY id DESC LIMIT 30").fetchall()]
    notifications = [dict(row) for row in connection.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 12", (session["user_id"],)).fetchall()]
    contacts_list = [dict(row) for row in connection.execute("SELECT * FROM trusted_contacts WHERE user_id=?", (session["user_id"],)).fetchall()]
    connection.close()
    score = calculate_risk_score(45, "evening", "moderate", "moderate", 25)
    return render_template("dashboard.html", user=dict(user), zones=zones, incidents=incidents, notifications=notifications, contacts=contacts_list, score=score, label=risk_label(score), demo_location=DEMO_LOCATION)


@app.route("/api/safety-zones")
@login_required
def safety_zones():
    connection = db(); result = [dict(row) for row in connection.execute("SELECT * FROM risk_zones").fetchall()]; connection.close()
    return jsonify(result)


@app.route("/api/risk-score")
@login_required
def risk_score():
    score = calculate_risk_score(int(request.args.get("crime", 45)), request.args.get("time", "evening"), request.args.get("lighting", "moderate"), request.args.get("crowd", "moderate"), int(request.args.get("community", 25)))
    return jsonify({"score": score, "label": risk_label(score), "disclaimer": "Demo risk score - not a guarantee of safety."})


@app.route("/api/checkin", methods=["POST"])
@login_required
def checkin():
    status = request.json.get("status", "safe") if request.is_json else "safe"
    connection = db(); connection.execute("INSERT INTO checkins(user_id,status,created_at) VALUES(?,?,?)", (session["user_id"], status, now())); add_notification(connection, session["user_id"], "SAFETY CHECK-IN", "Your check-in was recorded in demo mode."); connection.commit(); connection.close()
    return jsonify({"ok": True, "status": status})


@app.route("/api/sos", methods=["POST"])
@login_required
def sos():
    payload = request.get_json(silent=True) or {}
    connection = db(); score = calculate_risk_score(55, "night", "poor", "low", 55)
    connection.execute("INSERT INTO emergency_alerts(user_id,status,lat,lng,risk_score,created_at) VALUES(?,?,?,?,?,?)", (session["user_id"], "SOS ACTIVATED - DEMO", payload.get("lat", DEMO_LOCATION["lat"]), payload.get("lng", DEMO_LOCATION["lng"]), score, now()))
    add_notification(connection, session["user_id"], "SOS ACTIVATED", "DEMO NOTIFICATION: trusted contacts would be alerted.")
    connection.commit(); connection.close()
    return jsonify({"ok": True, "message": "SOS ACTIVATED", "simulated": True, "score": score})


@app.route("/api/location/share", methods=["POST"])
@login_required
def share_location():
    payload = request.get_json(silent=True) or {}
    connection = db(); connection.execute("UPDATE location_shares SET active=0 WHERE user_id=?", (session["user_id"],)); connection.execute("INSERT INTO location_shares(user_id,lat,lng,updated_at) VALUES(?,?,?,?)", (session["user_id"], payload.get("lat", DEMO_LOCATION["lat"]), payload.get("lng", DEMO_LOCATION["lng"]), now())); add_notification(connection, session["user_id"], "LOCATION SHARING STARTED", "DEMO: trusted contacts can see your latest shared location."); connection.commit(); connection.close()
    return jsonify({"ok": True, "active": True})


@app.route("/api/location/stop", methods=["POST"])
@login_required
def stop_location():
    connection = db(); connection.execute("UPDATE location_shares SET active=0 WHERE user_id=?", (session["user_id"],)); add_notification(connection, session["user_id"], "LOCATION SHARING STOPPED", "Your demo location is no longer being shared."); connection.commit(); connection.close(); return jsonify({"ok": True, "active": False})


@app.route("/api/incidents", methods=["GET", "POST"])
@login_required
def incidents():
    connection = db()
    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        connection.execute("INSERT INTO incidents(user_id,lat,lng,category,description,severity,created_at) VALUES(?,?,?,?,?,?,?)", (session["user_id"], float(payload.get("lat", DEMO_LOCATION["lat"])), float(payload.get("lng", DEMO_LOCATION["lng"])), payload.get("category", "Other"), payload.get("description", "Anonymous demo report"), payload.get("severity", "Medium"), now()))
        connection.commit(); connection.close(); return jsonify({"ok": True, "message": "Anonymous report queued for review."}), 201
    result = [dict(row) for row in connection.execute("SELECT id,lat,lng,category,severity,created_at FROM incidents ORDER BY id DESC").fetchall()]; connection.close(); return jsonify(result)


@app.route("/api/trusted-contacts", methods=["GET", "POST"])
@login_required
def trusted_contacts_api():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        try:
            mobile = normalize_indian_mobile(payload["mobile"])
        except (KeyError, ValueError) as error:
            return jsonify({"ok": False, "error": str(error)}), 400
        connection = db(); connection.execute("INSERT INTO trusted_contacts(user_id,name,mobile,relationship) VALUES(?,?,?,?)", (session["user_id"], payload["name"], mobile, payload["relationship"])); connection.commit(); connection.close(); return jsonify({"ok": True}), 201
    connection = db(); result = [dict(row) for row in connection.execute("SELECT id,name,mobile,relationship FROM trusted_contacts WHERE user_id=?", (session["user_id"],)).fetchall()]; connection.close(); return jsonify(result)


@app.route("/api/notifications")
@login_required
def notifications():
    connection = db(); result = [dict(row) for row in connection.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC", (session["user_id"],)).fetchall()]; connection.close(); return jsonify(result)


@app.route("/api/privacy/delete-data", methods=["POST"])
@login_required
def delete_data():
    user_id = session["user_id"]; connection = db(); connection.execute("DELETE FROM users WHERE id=?", (user_id,)); connection.commit(); connection.close(); session.clear(); return jsonify({"ok": True, "message": "Your demo account data was deleted."})


@app.route("/api/nearby-help")
@login_required
def nearby_help():
    return jsonify([
        {"type": "Police", "name": "Demo Police Station 1", "distance": "0.6 km", "lat": 40.716, "lng": -74.004, "phone": "112"},
        {"type": "Hospital", "name": "Demo Hospital", "distance": "1.2 km", "lat": 40.709, "lng": -74.012, "phone": "112"},
        {"type": "Pharmacy", "name": "Demo Pharmacy", "distance": "0.4 km", "lat": 40.719, "lng": -74.001, "phone": "112"},
    ])


@app.cli.command("init-db")
def init_db_command():
    init_db(); seed_demo_incidents(); print("Database initialized.")


init_db()
seed_demo_incidents()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
