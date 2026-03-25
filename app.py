from flask import Flask, render_template, session, redirect, jsonify, request
from config import db, doctors, patients, agents, prescriptions, adherence_logs, medical_records
from routes.auth_routes import auth
from bson import ObjectId
import json
import re as _re
from datetime import datetime, timedelta

app = Flask(__name__)
app.register_blueprint(auth)
app.secret_key = "wellsync_secret_key"


# ─────────────────────────────────────────────
# HELPER: Deduplicate medicines list
# ─────────────────────────────────────────────
def deduplicate_medicines(medicines):
    seen = set()
    unique = []
    for m in medicines:
        key = (
            m.get("name", "").strip().lower(),
            m.get("dosage", "").strip().lower(),
            m.get("frequency", "").strip().lower(),
        )
        if key not in seen:
            seen.add(key)
            unique.append(m)
    return unique


# ─────────────────────────────────────────────
# HELPER: Find patient by ID (case-insensitive)
# ─────────────────────────────────────────────
def find_patient(patient_id):
    pat = patients.find_one({"patient_id": patient_id})
    if not pat:
        pat = patients.find_one({
            "patient_id": {"$regex": "^" + _re.escape(patient_id) + "$", "$options": "i"}
        })
    return pat


# ─────────────────────────────────────────────
# HELPER: Get adherence logs for a patient (case-insensitive)
# ─────────────────────────────────────────────
def get_adherence_logs(patient_id):
    """
    Fetches adherence logs trying both exact and case-insensitive match.
    Also handles logs saved by WhatsApp reply (source=whatsapp_reply).
    """
    # Try exact match first
    logs = list(adherence_logs.find({"patient_id": patient_id}))
    if logs:
        return logs
    # Try case-insensitive
    pat = find_patient(patient_id)
    if pat:
        actual_id = pat["patient_id"]
        logs = list(adherence_logs.find({"patient_id": actual_id}))
    return logs


# ─────────────────────────────────────────────
# HOME
# ─────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


# ─────────────────────────────────────────────
# PATIENT DASHBOARD
# ─────────────────────────────────────────────
@app.route("/patient_dashboard")
def patient_dashboard():
    if session.get("role") != "patient":
        return redirect("/login")

    patient = patients.find_one({"patient_id": session["user_id"]})
    if not patient:
        return redirect("/login")

    logs = get_adherence_logs(session["user_id"])

    taken   = sum(1 for l in logs if l.get("status") == "taken")
    missed  = sum(1 for l in logs if l.get("status") == "missed")
    skipped = sum(1 for l in logs if l.get("status") == "skipped")
    total   = taken + missed + skipped
    adherence_pct = round((taken / total * 100)) if total > 0 else 0

    patient_prescriptions = list(prescriptions.find({"patient_id": session["user_id"]}))
    active_rx = [r for r in patient_prescriptions if r.get("status") == "Active"]

    daily_labels, daily_taken, daily_missed, daily_skipped = [], [], [], []
    today = datetime.today()
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_str = day.strftime("%Y-%m-%d")
        day_logs = [l for l in logs if str(l.get("date", "")).startswith(day_str)]
        daily_labels.append(day.strftime("%a"))
        daily_taken.append(sum(1 for l in day_logs if l.get("status") == "taken"))
        daily_missed.append(sum(1 for l in day_logs if l.get("status") == "missed"))
        daily_skipped.append(sum(1 for l in day_logs if l.get("status") == "skipped"))

    next_reminder = "No reminders"
    if active_rx:
        next_reminder = active_rx[0].get("schedule", "Check prescription")

    def serialize(doc):
        d = dict(doc)
        d["_id"] = str(d["_id"])
        d.pop("password", None)
        for k, v in list(d.items()):
            if isinstance(v, bytes):
                d[k] = v.decode("utf-8", errors="ignore")
        return d

    grouped_prescriptions = []
    for rx in patient_prescriptions:
        d = serialize(rx)
        if d.get("medicines"):
            deduped = deduplicate_medicines(d["medicines"])
            grouped_prescriptions.append({
                "_id":       d["_id"],
                "date":      d.get("date", d.get("created_at", "")[:10]),
                "status":    d.get("status", "Active"),
                "agent_id":  d.get("agent_id", ""),
                "doctor_id": d.get("doctor_id", ""),
                "medicines": deduped
            })
        else:
            grouped_prescriptions.append({
                "_id":       d["_id"],
                "date":      d.get("created_at", "")[:10],
                "status":    d.get("status", "Active"),
                "agent_id":  d.get("agent_id", ""),
                "doctor_id": d.get("doctor_id", ""),
                "medicines": [{
                    "name":         d.get("medication", ""),
                    "dosage":       d.get("dosage", ""),
                    "frequency":    d.get("frequency", ""),
                    "schedule":     d.get("schedule", ""),
                    "duration":     d.get("duration", ""),
                    "doctor_id":    d.get("doctor_id", ""),
                    "instructions": d.get("instructions", ""),
                    "before_meal":  d.get("before_meal", False),
                    "after_meal":   d.get("after_meal", False),
                }]
            })

    active_rx = [r for r in grouped_prescriptions if r.get("status") == "Active"]

    return render_template(
        "patient_dashboard1.html",
        patient=serialize(patient),
        prescriptions=grouped_prescriptions,
        total_records=len(grouped_prescriptions),
        active_prescriptions=len(active_rx),
        adherence_pct=adherence_pct,
        next_reminder=next_reminder,
        taken=taken,
        missed=missed,
        skipped=skipped,
        daily_labels=json.dumps(daily_labels),
        daily_taken=json.dumps(daily_taken),
        daily_missed=json.dumps(daily_missed),
        daily_skipped=json.dumps(daily_skipped),
    )


# ─────────────────────────────────────────────
# DOCTOR DASHBOARD
# ─────────────────────────────────────────────
@app.route("/doctor_dashboard")
def doctor_dashboard():
    if session.get("role") != "doctor":
        return redirect("/login")

    doctor = doctors.find_one({"doctor_id": session["user_id"]})
    if not doctor:
        return redirect("/login")

    doctor_patients = list(patients.find({}))

    doctor_prescriptions_raw = list(prescriptions.find({}))
    doctor_prescriptions = []
    for rx in doctor_prescriptions_raw:
        d = dict(rx)
        d["_id"] = str(d["_id"])
        if not d.get("medicines"):
            d["medicines"] = [{
                "name":         d.get("medication",""),
                "dosage":       d.get("dosage",""),
                "frequency":    d.get("frequency",""),
                "schedule":     d.get("schedule",""),
                "duration":     d.get("duration",""),
                "instructions": d.get("instructions",""),
            }]
        else:
            d["medicines"] = deduplicate_medicines(d["medicines"])
        doctor_prescriptions.append(d)

    all_patient_ids = [p["patient_id"] for p in doctor_patients]
    all_logs = list(adherence_logs.find({"patient_id": {"$in": all_patient_ids}})) if all_patient_ids else []

    total_taken = sum(1 for l in all_logs if l.get("status") == "taken")
    total_total = len(all_logs)
    overall_adherence = round((total_taken / total_total * 100)) if total_total > 0 else 0

    good_count = moderate_count = poor_count = 0
    for p in doctor_patients:
        p_logs = [l for l in all_logs if l.get("patient_id") == p["patient_id"]]
        if not p_logs:
            continue
        pct = (sum(1 for l in p_logs if l.get("status") == "taken") / len(p_logs)) * 100
        if pct >= 80:
            good_count += 1
        elif pct >= 60:
            moderate_count += 1
        else:
            poor_count += 1

    def serialize(doc):
        d = dict(doc)
        d["_id"] = str(d["_id"])
        d.pop("password", None)
        for k, v in list(d.items()):
            if isinstance(v, bytes):
                d[k] = v.decode("utf-8", errors="ignore")
        return d

    return render_template(
        "doctor_dashboard1.html",
        doctor=serialize(doctor),
        patients=[serialize(p) for p in doctor_patients],
        prescriptions=doctor_prescriptions,
        total_patients=len(doctor_patients),
        pending_prescriptions=len([r for r in doctor_prescriptions if r.get("status") == "Active"]),
        total_records=len(doctor_prescriptions),
        pending_reviews=0,
        overall_adherence=overall_adherence,
        total_active_rx=len([r for r in doctor_prescriptions if r.get("status") == "Active"]),
        good_count=good_count,
        moderate_count=moderate_count,
        poor_count=poor_count,
        adherence_pie=json.dumps([good_count, moderate_count, poor_count]),
    )


# ─────────────────────────────────────────────
# AGENT DASHBOARD
# ─────────────────────────────────────────────
@app.route("/agent_dashboard")
def agent_dashboard():
    if session.get("role") != "agent":
        return redirect("/login")

    agent = agents.find_one({"agent_id": session["user_id"]})
    if not agent:
        return redirect("/login")

    def serialize(doc):
        d = dict(doc)
        d["_id"] = str(d["_id"])
        d.pop("password", None)
        for k, v in list(d.items()):
            if isinstance(v, bytes):
                d[k] = v.decode("utf-8", errors="ignore")
        return d

    return render_template("agent_dashboard1.html", agent=serialize(agent))


# ─────────────────────────────────────────────
# API: PATIENT ANALYTICS (doctor / agent AJAX)
# KEY FIX: Uses case-insensitive patient_id lookup
# ─────────────────────────────────────────────
@app.route("/api/patient_analytics/<patient_id>")
def patient_analytics_api(patient_id):
    if session.get("role") not in ("doctor", "agent"):
        return jsonify({"error": "Unauthorized"}), 403

    # ── FIX: resolve the actual patient_id from DB ──
    pat = find_patient(patient_id)
    actual_id = pat["patient_id"] if pat else patient_id

    # Fetch logs using actual_id
    logs = list(adherence_logs.find({"patient_id": actual_id}))

    taken   = sum(1 for l in logs if l.get("status") == "taken")
    missed  = sum(1 for l in logs if l.get("status") == "missed")
    skipped = sum(1 for l in logs if l.get("status") == "skipped")
    total   = taken + missed + skipped
    pct     = round((taken / total * 100)) if total > 0 else 0

    med_map = {}
    for l in logs:
        med = l.get("medication", "Unknown")
        if med not in med_map:
            med_map[med] = {"taken": 0, "total": 0}
        med_map[med]["total"] += 1
        if l.get("status") == "taken":
            med_map[med]["taken"] += 1

    meds = [
        {
            "name": m,
            "adherence": round((d["taken"] / d["total"] * 100)) if d["total"] > 0 else 0
        }
        for m, d in med_map.items()
    ]

    return jsonify({
        "taken":        taken,
        "missed":       missed,
        "skipped":      skipped,
        "adherence_pct": pct,
        "medications":  meds
    })


# ─────────────────────────────────────────────
# API: PATIENT RECORDS (doctor search)
# ─────────────────────────────────────────────
@app.route("/api/patient_records/<patient_id>")
def patient_records_api(patient_id):
    if session.get("role") not in ("doctor", "agent"):
        return jsonify({"error": "Unauthorized"}), 403
    try:
        return _patient_records_inner(patient_id)
    except Exception as ex:
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Server error: " + str(ex)}), 500

def _patient_records_inner(patient_id):
    patient = find_patient(patient_id)
    if not patient:
        return jsonify({"error": "Patient '" + patient_id + "' not found. Check the ID."}), 404

    actual_pid = patient["patient_id"]
    patient_rx = list(prescriptions.find({"patient_id": actual_pid}))

    def serialize(doc):
        d = dict(doc)
        d["_id"] = str(d["_id"])
        d.pop("password", None)
        for k, v in list(d.items()):
            if isinstance(v, bytes):
                d[k] = v.decode("utf-8", errors="ignore")
        return d

    grouped = []
    for rx in patient_rx:
        d = serialize(rx)
        if d.get("medicines"):
            grouped.append({
                "_id":          d["_id"],
                "patient_id":   d.get("patient_id", ""),
                "patient_name": d.get("patient_name", ""),
                "date":         (d.get("date") or d.get("created_at") or "")[:10],
                "status":       d.get("status", "Active"),
                "agent_id":     d.get("agent_id", ""),
                "doctor_id":    d.get("doctor_id", ""),
                "created_at":   d.get("created_at") or "",
                "medicines":    deduplicate_medicines(d["medicines"])
            })
        else:
            grouped.append({
                "_id":          d["_id"],
                "patient_id":   d.get("patient_id", ""),
                "patient_name": d.get("patient_name", ""),
                "date":         (d.get("created_at") or "")[:10],
                "status":       d.get("status", "Active"),
                "agent_id":     d.get("agent_id", ""),
                "doctor_id":    d.get("doctor_id", ""),
                "created_at":   d.get("created_at") or "",
                "medicines": [{
                    "name":         d.get("medication", ""),
                    "dosage":       d.get("dosage", ""),
                    "frequency":    d.get("frequency", ""),
                    "schedule":     d.get("schedule", ""),
                    "duration":     d.get("duration", ""),
                    "instructions": d.get("instructions", ""),
                    "before_meal":  d.get("before_meal", False),
                    "after_meal":   d.get("after_meal", False),
                }]
            })

    return jsonify({"patient": serialize(patient), "prescriptions": grouped})


# ─────────────────────────────────────────────
# API: SAVE PATIENT SETTINGS
# ─────────────────────────────────────────────
@app.route("/api/save_patient_settings", methods=["POST"])
def save_patient_settings():
    if session.get("role") != "patient":
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    allowed = ["fullname", "phone", "email", "address",
               "conditionsDesc", "allergiesDesc", "medicationsDesc", "surgeriesDesc"]
    update = {k: v for k, v in data.items() if k in allowed}
    if update:
        patients.update_one({"patient_id": session["user_id"]}, {"$set": update})
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# API: SAVE DOCTOR SETTINGS
# ─────────────────────────────────────────────
@app.route("/api/save_doctor_settings", methods=["POST"])
def save_doctor_settings():
    if session.get("role") != "doctor":
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    allowed = ["fullname", "phone", "email", "specialization"]
    update = {k: v for k, v in data.items() if k in allowed}
    if update:
        doctors.update_one({"doctor_id": session["user_id"]}, {"$set": update})
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# API: LOG ADHERENCE (patient marks dose)
# ─────────────────────────────────────────────
@app.route("/api/log_adherence", methods=["POST"])
def log_adherence():
    if session.get("role") != "patient":
        return jsonify({
            "error": "Session expired or wrong account active. Please log out and log back in as a patient."
        }), 403
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data received"}), 400
    data["patient_id"] = session["user_id"]
    data["logged_at"]  = datetime.utcnow().isoformat()
    adherence_logs.insert_one(data)
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# API: ADD PRESCRIPTION (doctor)
# ─────────────────────────────────────────────
@app.route("/api/add_prescription", methods=["POST"])
def add_prescription():
    if session.get("role") != "doctor":
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    data["doctor_id"]  = session["user_id"]
    data["status"]     = "Active"
    data["created_at"] = datetime.utcnow().isoformat()
    prescriptions.insert_one(data)
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# API: CHANGE PASSWORD (all roles)
# ─────────────────────────────────────────────
@app.route("/api/change_password", methods=["POST"])
def change_password():
    import bcrypt
    data    = request.get_json()
    role    = session.get("role")
    user_id = session.get("user_id")
    if not role or not user_id:
        return jsonify({"error": "Not logged in"}), 403

    col_map = {"patient": (patients, "patient_id"),
               "doctor":  (doctors,  "doctor_id"),
               "agent":   (agents,   "agent_id")}
    if role not in col_map:
        return jsonify({"error": "Unknown role"}), 400
    col, id_field = col_map[role]

    user = col.find_one({id_field: user_id})
    if not user:
        return jsonify({"error": "User not found"}), 404
    if not bcrypt.checkpw(data.get("current_password", "").encode(), user["password"]):
        return jsonify({"error": "Current password is incorrect"}), 400

    new_pw = data.get("new_password", "")
    if new_pw != data.get("confirm_password", ""):
        return jsonify({"error": "Passwords do not match"}), 400
    if len(new_pw) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    col.update_one({id_field: user_id},
                   {"$set": {"password": bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt())}})
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# API: PATIENT LOOKUP (agent searches by ID)
# ─────────────────────────────────────────────
@app.route("/api/lookup_patient/<patient_id>")
def lookup_patient(patient_id):
    if session.get("role") not in ("agent", "doctor"):
        return jsonify({
            "error": "Session conflict: Logged in as '{}'. Use a separate browser for each role.".format(
                session.get("role", "unknown"))
        }), 403
    try:
        patient = find_patient(patient_id)
        if not patient:
            return jsonify({"error": "Patient '" + patient_id + "' not found. Check the ID."}), 404

        def safe_str(v):
            if v is None: return ""
            if isinstance(v, bytes): return v.decode("utf-8", errors="ignore")
            return str(v)

        return jsonify({
            "patient_id":     safe_str(patient.get("patient_id")),
            "fullname":       safe_str(patient.get("fullname")),
            "bloodgroup":     safe_str(patient.get("bloodgroup")),
            "phone":          safe_str(patient.get("phone")),
            "gender":         safe_str(patient.get("gender")),
            "dob":            safe_str(patient.get("dob")),
            "conditionsDesc": safe_str(patient.get("conditionsDesc")),
            "allergiesDesc":  safe_str(patient.get("allergiesDesc")),
        })
    except Exception as ex:
        import traceback; traceback.print_exc()
        return jsonify({"error": "Server error: " + str(ex)}), 500


# ─────────────────────────────────────────────
# API: AGENT SAVES PRESCRIPTION TO MONGODB
# FIX: Deduplicates medicines before saving
# ─────────────────────────────────────────────
@app.route("/api/agent_save_prescription", methods=["POST"])
def agent_save_prescription():
    if session.get("role") != "agent":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    if not data.get("patient_id") or not data.get("medicines"):
        return jsonify({"error": "patient_id and medicines are required"}), 400

    # Deduplicate before saving
    clean_medicines = deduplicate_medicines(data["medicines"])

    prescription_doc = {
        "patient_id":   data["patient_id"],
        "patient_name": data.get("patient_name", ""),
        "medicines":    clean_medicines,
        "doctor_id":    data.get("doctor_id", ""),
        "notes":        data.get("notes", ""),
        "agent_id":     session["user_id"],
        "created_at":   datetime.utcnow().isoformat(),
        "date":         datetime.utcnow().strftime("%Y-%m-%d"),
        "status":       "Active",
        "added_by":     "agent"
    }

    result = prescriptions.insert_one(prescription_doc)
    return jsonify({"success": True, "prescription_id": str(result.inserted_id)})


# ─────────────────────────────────────────────
# API: AGENT GETS ALL PRESCRIPTIONS THEY SAVED
# ─────────────────────────────────────────────
@app.route("/api/agent_prescriptions")
def agent_prescriptions():
    if session.get("role") != "agent":
        return jsonify({"error": "Unauthorized"}), 403

    rx_list = list(prescriptions.find({"agent_id": session["user_id"]})
                   .sort("created_at", -1).limit(100))

    def serialize(doc):
        d = dict(doc)
        d["_id"] = str(d["_id"])
        d.pop("password", None)
        for k, v in list(d.items()):
            if isinstance(v, bytes):
                d[k] = v.decode("utf-8", errors="ignore")
        if d.get("medicines"):
            d["medicines"] = deduplicate_medicines(d["medicines"])
        return d

    return jsonify({"prescriptions": [serialize(r) for r in rx_list]})


# ─────────────────────────────────────────────
# API: UPDATE PRESCRIPTION (agent edits)
# ─────────────────────────────────────────────
@app.route("/api/update_prescription/<prescription_id>", methods=["PUT"])
def update_prescription(prescription_id):
    if session.get("role") != "agent":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    updated_medicines = data.get("medicines", [])

    clean_medicines = deduplicate_medicines([{
        "name":         m.get("name", ""),
        "dosage":       m.get("dosage", ""),
        "frequency":    m.get("frequency", m.get("freq", "")),
        "schedule":     m.get("schedule", m.get("time", "")),
        "duration":     m.get("duration", str(m.get("total","")) + " tablets"),
        "doctor_id":    m.get("doctor_id", m.get("doctor", "")),
        "before_meal":  m.get("before_meal", m.get("beforeMeal", False)),
        "after_meal":   m.get("after_meal", m.get("afterMeal", False)),
        "instructions": m.get("instructions", m.get("usage", ""))
    } for m in updated_medicines])

    try:
        result = prescriptions.update_one(
            {"_id": ObjectId(prescription_id), "agent_id": session["user_id"]},
            {"$set": {"medicines": clean_medicines, "updated_at": datetime.utcnow().isoformat()}}
        )
        if result.matched_count == 0:
            return jsonify({"error": "Prescription not found or not yours"}), 404
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─────────────────────────────────────────────
# API: DELETE PRESCRIPTION (agent deletes)
# ─────────────────────────────────────────────
@app.route("/api/delete_prescription/<prescription_id>", methods=["DELETE"])
def delete_prescription(prescription_id):
    if session.get("role") != "agent":
        return jsonify({"error": "Unauthorized"}), 403
    try:
        result = prescriptions.delete_one(
            {"_id": ObjectId(prescription_id), "agent_id": session["user_id"]}
        )
        if result.deleted_count == 0:
            return jsonify({"error": "Prescription not found or not yours"}), 404
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ─────────────────────────────────────────────
# API: DOCTOR SAVES PRESCRIPTION
# ─────────────────────────────────────────────
@app.route("/api/doctor_save_prescription", methods=["POST"])
def doctor_save_prescription():
    if session.get("role") != "doctor":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    if not data.get("patient_id") or not data.get("medicines"):
        return jsonify({"error": "patient_id and medicines are required"}), 400

    prescription_doc = {
        "patient_id":   data["patient_id"],
        "patient_name": data.get("patient_name", ""),
        "medicines":    deduplicate_medicines(data["medicines"]),
        "doctor_id":    session["user_id"],
        "added_by":     "doctor",
        "created_at":   datetime.utcnow().isoformat(),
        "date":         datetime.utcnow().strftime("%Y-%m-%d"),
        "status":       "Active"
    }
    result = prescriptions.insert_one(prescription_doc)
    return jsonify({"success": True, "prescription_id": str(result.inserted_id)})


# ─────────────────────────────────────────────
# API: UPLOAD MEDICAL RECORD (agent or patient)
# ─────────────────────────────────────────────
@app.route("/api/upload_medical_record", methods=["POST"])
def upload_medical_record():
    role = session.get("role")
    if role not in ("agent", "patient"):
        return jsonify({"error": "Unauthorized"}), 403

    data        = request.get_json()
    patient_id  = data.get("patient_id", "")
    title       = data.get("title", "Untitled Record")
    record_type = data.get("record_type", "General")
    file_data   = data.get("file_data", "")
    file_name   = data.get("file_name", "")
    file_type   = data.get("file_type", "")

    if not patient_id or not file_data:
        return jsonify({"error": "patient_id and file_data are required"}), 400

    if role == "agent":
        pat = patients.find_one({"patient_id": patient_id})
        if not pat:
            return jsonify({"error": "Patient not found"}), 404
    else:
        if patient_id != session["user_id"]:
            return jsonify({"error": "You can only upload your own records"}), 403

    record = {
        "patient_id":    patient_id,
        "title":         title,
        "record_type":   record_type,
        "file_name":     file_name,
        "file_type":     file_type,
        "file_data":     file_data,
        "uploaded_by":   session["user_id"],
        "uploader_role": role,
        "uploaded_at":   datetime.utcnow().isoformat(),
        "date":          datetime.utcnow().strftime("%Y-%m-%d"),
    }
    result = medical_records.insert_one(record)
    return jsonify({"success": True, "record_id": str(result.inserted_id)})


# ─────────────────────────────────────────────
# API: GET MEDICAL RECORDS FOR A PATIENT
# ─────────────────────────────────────────────
@app.route("/api/medical_records/<patient_id>")
def get_medical_records(patient_id):
    role = session.get("role")
    if role not in ("patient", "doctor", "agent"):
        return jsonify({"error": "Unauthorized - please log in"}), 403
    if role == "patient" and patient_id != session.get("user_id"):
        return jsonify({"error": "You can only view your own records"}), 403

    pat        = find_patient(patient_id)
    actual_pid = pat["patient_id"] if pat else patient_id
    records    = list(medical_records.find({"patient_id": actual_pid}, {"file_data": 0}).sort("uploaded_at", -1))

    def serialize(doc):
        d = dict(doc)
        d["_id"] = str(d["_id"])
        d.pop("password", None)
        for k, v in list(d.items()):
            if isinstance(v, bytes):
                d[k] = v.decode("utf-8", errors="ignore")
        return d

    return jsonify({"records": [serialize(r) for r in records]})


# ─────────────────────────────────────────────
# API: GET ONE RECORD FILE (view/download)
# ─────────────────────────────────────────────
@app.route("/api/medical_record_file/<record_id>")
def get_medical_record_file(record_id):
    role = session.get("role")
    if role not in ("patient", "doctor", "agent"):
        return jsonify({"error": "Unauthorized"}), 403
    try:
        rec = medical_records.find_one({"_id": ObjectId(record_id)})
    except Exception:
        return jsonify({"error": "Invalid record ID"}), 400
    if not rec:
        return jsonify({"error": "Record not found"}), 404
    if role == "patient" and rec["patient_id"] != session["user_id"]:
        return jsonify({"error": "Unauthorized"}), 403
    return jsonify({
        "record_id": str(rec["_id"]),
        "file_data": rec.get("file_data", ""),
        "file_type": rec.get("file_type", ""),
        "file_name": rec.get("file_name", ""),
        "title":     rec.get("title", ""),
    })


# ─────────────────────────────────────────────
# API: DELETE MEDICAL RECORD
# ─────────────────────────────────────────────
@app.route("/api/delete_medical_record/<record_id>", methods=["DELETE"])
def delete_medical_record(record_id):
    role = session.get("role")
    if role not in ("agent", "patient"):
        return jsonify({"error": "Unauthorized"}), 403
    try:
        rec = medical_records.find_one({"_id": ObjectId(record_id)})
    except Exception:
        return jsonify({"error": "Invalid ID"}), 400
    if not rec:
        return jsonify({"error": "Not found"}), 404
    if role == "patient" and rec["patient_id"] != session["user_id"]:
        return jsonify({"error": "Unauthorized"}), 403
    medical_records.delete_one({"_id": ObjectId(record_id)})
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# API: PATIENT MEDICINE INTAKE HISTORY
# ─────────────────────────────────────────────
@app.route("/api/medicine_intake_history")
def medicine_intake_history():
    if session.get("role") != "patient":
        return jsonify({"error": "Unauthorized"}), 403

    logs = list(adherence_logs.find({"patient_id": session["user_id"]}).sort("logged_at", -1))

    med_map = {}
    for l in logs:
        med = l.get("medication", "Unknown")
        if med not in med_map:
            med_map[med] = {"taken": 0, "missed": 0, "skipped": 0, "logs": []}
        status = l.get("status", "taken")
        med_map[med][status] = med_map[med].get(status, 0) + 1
        med_map[med]["logs"].append({
            "status":    status,
            "date":      l.get("date", ""),
            "time":      l.get("time", ""),
            "logged_at": l.get("logged_at", "")
        })

    result = []
    for med_name, data in med_map.items():
        total = data["taken"] + data["missed"] + data["skipped"]
        pct   = round((data["taken"] / total * 100)) if total > 0 else 0
        result.append({
            "medication":    med_name,
            "taken":         data["taken"],
            "missed":        data["missed"],
            "skipped":       data["skipped"],
            "total":         total,
            "adherence_pct": pct,
            "recent_logs":   data["logs"][:10]
        })

    result.sort(key=lambda x: -x["total"])
    return jsonify({"medicines": result})


# ─────────────────────────────────────────────
# API: LOOKUP PATIENT BY PHONE NUMBER
# ─────────────────────────────────────────────
@app.route("/api/lookup_by_phone/<path:phone>")
def lookup_by_phone(phone):
    if session.get("role") not in ("agent", "doctor"):
        return jsonify({"error": "Unauthorized"}), 403
    try:
        clean   = _re.sub(r"[\s\-]", "", phone)
        patient = patients.find_one({"phone": phone})
        if not patient:
            patient = patients.find_one({"phone": {"$regex": _re.escape(clean[-10:]) + "$"}})
        if not patient:
            return jsonify({"found": False, "phone": phone})
        def safestr(v):
            if v is None: return ""
            if isinstance(v, bytes): return v.decode("utf-8", errors="ignore")
            return str(v)
        return jsonify({
            "found":            True,
            "patient_id":       safestr(patient.get("patient_id")),
            "fullname":         safestr(patient.get("fullname")),
            "phone":            safestr(patient.get("phone")),
            "bloodgroup":       safestr(patient.get("bloodgroup")),
            "conditionsDesc":   safestr(patient.get("conditionsDesc")),
            "profile_complete": patient.get("profile_complete", False)
        })
    except Exception as ex:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(ex)}), 500


# ─────────────────────────────────────────────
# API: AGENT CREATES UNREGISTERED PATIENT
# ─────────────────────────────────────────────
@app.route("/api/agent_create_patient", methods=["POST"])
def agent_create_patient():
    if session.get("role") != "agent":
        return jsonify({
            "error": "Session conflict: You appear to be logged in as '{}' in this browser. "
                     "Please open the Agent Dashboard in a separate browser or incognito window.".format(
                         session.get("role", "unknown"))
        }), 403
    try:
        import bcrypt as _bc
        data     = request.get_json()
        fullname = data.get("fullname", "").strip()
        phone    = data.get("phone", "").strip()
        password = data.get("password", "").strip()
        if not fullname or not phone or not password:
            return jsonify({"error": "fullname, phone and password required"}), 400
        existing = patients.find_one({"phone": phone})
        if existing:
            def s(v): return "" if v is None else (v.decode() if isinstance(v,bytes) else str(v))
            return jsonify({"success": True, "already_exists": True,
                            "patient_id": s(existing.get("patient_id")),
                            "fullname":   s(existing.get("fullname"))})
        clean_phone = _re.sub(r"[^0-9]", "", phone)
        base_id     = "PH" + clean_phone[-6:]
        patient_id  = base_id
        suffix = 1
        while patients.find_one({"patient_id": patient_id}):
            patient_id = base_id + str(suffix); suffix += 1
        hashed_pw = _bc.hashpw(password.encode("utf-8"), _bc.gensalt())
        patients.insert_one({
            "patient_id": patient_id, "fullname": fullname, "phone": phone,
            "password": hashed_pw, "profile_complete": False,
            "created_by_agent": session["user_id"],
            "created_at": datetime.utcnow().isoformat(),
            "gender": "", "bloodgroup": "", "dob": "", "email": "", "address": ""
        })
        return jsonify({"success": True, "patient_id": patient_id, "fullname": fullname, "phone": phone})
    except Exception as ex:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(ex)}), 500


# ─────────────────────────────────────────────
# API: GET REMINDER SETTINGS (patient)
# ─────────────────────────────────────────────
@app.route("/api/reminder_settings")
def get_reminder_settings():
    if session.get("role") != "patient":
        return jsonify({"error": "Unauthorized"}), 403
    pat = patients.find_one({"patient_id": session["user_id"]})
    if not pat:
        return jsonify({"error": "Patient not found"}), 404
    return jsonify({
        "reminders_enabled": pat.get("reminders_enabled", False),
        "sms_enabled":       pat.get("sms_enabled", False),
        "whatsapp_enabled":  pat.get("whatsapp_enabled", False),
        "phone":             pat.get("phone", ""),
        "reminder_times":    pat.get("reminder_times", ["Morning","Evening"])
    })


# ─────────────────────────────────────────────
# API: SAVE REMINDER SETTINGS (patient)
# ─────────────────────────────────────────────
@app.route("/api/save_reminder_settings", methods=["POST"])
def save_reminder_settings():
    if session.get("role") != "patient":
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json()
    patients.update_one(
        {"patient_id": session["user_id"]},
        {"$set": {
            "reminders_enabled": data.get("reminders_enabled", False),
            "sms_enabled":       data.get("sms_enabled", False),
            "whatsapp_enabled":  data.get("whatsapp_enabled", False),
            "phone":             data.get("phone", ""),
            "reminder_times":    data.get("reminder_times", [])
        }}
    )
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# API: AGENT SETS REMINDER FOR A PATIENT
# ─────────────────────────────────────────────
@app.route("/api/agent_set_reminder", methods=["POST"])
def agent_set_reminder():
    if session.get("role") != "agent":
        return jsonify({"error": "Unauthorized"}), 403
    data       = request.get_json()
    patient_id = data.get("patient_id")
    if not patient_id:
        return jsonify({"error": "patient_id required"}), 400
    patients.update_one(
        {"patient_id": patient_id},
        {"$set": {
            "reminders_enabled": data.get("reminders_enabled", True),
            "sms_enabled":       data.get("sms_enabled", False),
            "whatsapp_enabled":  data.get("whatsapp_enabled", True),
            "phone":             data.get("phone", ""),
            "reminder_times":    data.get("reminder_times", ["Morning","Evening"])
        }}
    )
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# WEBHOOK: WHATSAPP BUTTON REPLY
# Patient taps [✅ Took my medicines] or [⏭ Skip]
# Twilio posts the button id ("taken" / "skipped") as the message Body
# Set this URL in Twilio Console → WhatsApp Sandbox →
#   "When a message comes in": https://yourdomain.com/webhook/whatsapp
# ─────────────────────────────────────────────
@app.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook():
    from twilio.twiml.messaging_response import MessagingResponse
    from reminders import handle_reply

    from_number = request.form.get("From", "")
    body        = request.form.get("Body", "").strip()

    print(f"[WhatsApp Reply] From: {from_number} | Body: {body}")

    reply_text = handle_reply(from_number, body, channel="whatsapp")

    resp = MessagingResponse()
    resp.message(reply_text)
    return str(resp), 200, {"Content-Type": "text/xml"}


# ─────────────────────────────────────────────
# API: SEND TEST REMINDER (agent/doctor)
# ─────────────────────────────────────────────
@app.route("/api/send_test_reminder", methods=["POST"])
def send_test_reminder():
    if session.get("role") not in ("agent", "doctor"):
        return jsonify({"error": "Unauthorized"}), 403

    data       = request.get_json()
    patient_id = data.get("patient_id")
    channel    = data.get("channel", "sms")

    pat = find_patient(patient_id)

    if not pat:
        try:
            rx = prescriptions.find_one({"_id": ObjectId(str(patient_id))})
            if rx:
                pat = find_patient(rx.get("patient_id", ""))
        except Exception:
            pass

    if not pat:
        return jsonify({
            "error": "Patient not found. ID received: " + str(patient_id),
            "hint":  "Make sure the patient has a phone number saved in their profile."
        }), 404

    phone = pat.get("phone", "")
    if not phone:
        return jsonify({"error": "Patient has no phone number saved"}), 400

    actual_pid  = pat["patient_id"]
    patient_rxs = list(prescriptions.find({"patient_id": actual_pid, "status": "Active"}))
    all_meds    = []
    seen_meds   = set()
    for rx in patient_rxs:
        for m in rx.get("medicines", []):
            key = m.get("name","").strip().lower()
            if key not in seen_meds:
                seen_meds.add(key)
                all_meds.append(m)

    if not all_meds:
        return jsonify({"error": "No active prescriptions for this patient"}), 400

    parts = []
    for m in all_meds[:5]:
        parts.append("  - " + m.get("name","?") + " " + m.get("dosage","") + " - " + m.get("frequency",""))
    med_lines = "\n".join(parts)
    message = (
        "WellSync Medication Reminder\n"
        "Hello " + pat.get("fullname","Patient") + "!\n\n"
        "Your current medicines:\n" + med_lines + "\n\n"
        "Please take your medicines as prescribed.\n"
        "- WellSync Health System"
    )

    import os
    TWILIO_SID   = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER")
    TWILIO_WA    = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

    if not TWILIO_SID or not TWILIO_TOKEN:
        return jsonify({
            "success": False,
            "error": "Twilio credentials not configured in .env file",
            "hint": "Add TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER to your .env"
        }), 400

    try:
        from twilio.rest import Client as TwilioClient
        tc         = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
        to_number  = phone if phone.startswith("+") else "+91" + phone.lstrip("0")

        if channel == "whatsapp":
            msg = tc.messages.create(body=message, from_=TWILIO_WA, to="whatsapp:" + to_number)
        else:
            msg = tc.messages.create(body=message, from_=TWILIO_PHONE, to=to_number)

        db["reminder_logs"].insert_one({
            "patient_id": actual_pid,
            "channel":    channel,
            "type":       "test",
            "sent_at":    datetime.utcnow().isoformat(),
            "success":    True,
            "twilio_sid": msg.sid
        })
        return jsonify({"success": True, "message": f"Test {channel} sent to {to_number}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ─────────────────────────────────────────────
# API: ALL MEDICATION ADHERENCE (doctor analytics)
# ─────────────────────────────────────────────
@app.route("/api/all_medication_adherence")
def all_medication_adherence():
    if session.get("role") != "doctor":
        return jsonify({"error": "Unauthorized"}), 403

    logs    = list(adherence_logs.find({}))
    med_map = {}
    for l in logs:
        med = l.get("medication", "Unknown")
        if med not in med_map:
            med_map[med] = {"taken": 0, "total": 0}
        med_map[med]["total"] += 1
        if l.get("status") == "taken":
            med_map[med]["taken"] += 1

    medications = []
    for name, data in sorted(med_map.items(), key=lambda x: -x[1]["total"])[:10]:
        pct = round((data["taken"] / data["total"] * 100)) if data["total"] > 0 else 0
        medications.append({"name": name, "adherence": pct, "total": data["total"]})

    return jsonify({"medications": medications})


# ─────────────────────────────────────────────
# ONE-TIME FIX: Mark self-registered patients as complete
# Visit: /fix_existing_patients
# ─────────────────────────────────────────────
@app.route("/fix_existing_patients")
def fix_existing_patients():
    result = patients.update_many(
        {"created_by_agent": {"$exists": False}, "profile_complete": {"$exists": False}},
        {"$set": {"profile_complete": True}}
    )
    return f"Fixed {result.modified_count} patients."


# ─────────────────────────────────────────────
# DEBUG: LIST ALL PATIENT IDs (remove in production)
# ─────────────────────────────────────────────
@app.route("/debug/patients")
def debug_patients():
    all_patients = list(patients.find({}, {"patient_id": 1, "fullname": 1, "_id": 0}))
    result = [{"patient_id": p.get("patient_id","MISSING"), "fullname": p.get("fullname","MISSING")} for p in all_patients]
    return jsonify({"count": len(result), "patients": result})


# ─────────────────────────────────────────────
# API: SAVE CLINICAL NOTE (doctor)
# ─────────────────────────────────────────────
@app.route("/api/save_clinical_note", methods=["POST"])
def save_clinical_note():
    if session.get("role") != "doctor":
        return jsonify({"error": "Unauthorized"}), 403

    data       = request.get_json()
    patient_id = data.get("patient_id", "").strip()
    note       = data.get("note", "").strip()

    if not patient_id or not note:
        return jsonify({"error": "patient_id and note are required"}), 400

    # Resolve doctor info
    doctor = doctors.find_one({"doctor_id": session["user_id"]})
    doctor_name = doctor.get("fullname", session["user_id"]) if doctor else session["user_id"]

    db["clinical_notes"].insert_one({
        "patient_id":  patient_id,
        "note":        note,
        "doctor_id":   session["user_id"],
        "doctor_name": doctor_name,
        "created_at":  datetime.utcnow().isoformat(),
        "date":        datetime.utcnow().strftime("%Y-%m-%d"),
    })
    return jsonify({"success": True})


# ─────────────────────────────────────────────
# API: GET CLINICAL NOTES FOR A PATIENT (doctor)
# ─────────────────────────────────────────────
@app.route("/api/get_clinical_notes/<patient_id>")
def get_clinical_notes(patient_id):
    if session.get("role") != "doctor":
        return jsonify({"error": "Unauthorized"}), 403

    # Resolve actual patient_id (case-insensitive)
    pat        = find_patient(patient_id)
    actual_id  = pat["patient_id"] if pat else patient_id

    notes = list(
        db["clinical_notes"]
        .find({"patient_id": actual_id}, {"_id": 0})
        .sort("created_at", -1)
    )
    return jsonify({"notes": notes})


# ─────────────────────────────────────────────
# API: FIRE REMINDER NOW (for testing)
# Visit: /api/fire_reminder_now?slot=Morning
# ─────────────────────────────────────────────
@app.route("/api/fire_reminder_now")
def fire_reminder_now():
    slot = request.args.get("slot", "Morning")
    if slot not in ("Morning", "Afternoon", "Evening", "Night"):
        return jsonify({"error": "slot must be Morning/Afternoon/Evening/Night"}), 400
    from reminders import send_reminders_for_slot
    import threading
    threading.Thread(target=send_reminders_for_slot, args=(slot,), daemon=True).start()
    return jsonify({"success": True, "message": f"Firing {slot} reminders now — check terminal!"})


# ─────────────────────────────────────────────
# TEST DB
# ─────────────────────────────────────────────
@app.route("/test-db")
def test_db():
    return "MongoDB Connected Successfully!"


# ─────────────────────────────────────────────
# API: REMINDER SCHEDULER STATUS (agent/doctor)
# ─────────────────────────────────────────────
@app.route("/api/reminder_scheduler_status")
def reminder_scheduler_status():
    if session.get("role") not in ("agent", "doctor"):
        return jsonify({"error": "Unauthorized"}), 403
    jobs = _schedule.get_jobs()
    return jsonify({
        "running": True,
        "jobs": [str(j) for j in jobs],
        "next_runs": [str(j.next_run) for j in jobs]
    })


# ─────────────────────────────────────────────
# BACKGROUND REMINDER SCHEDULER
# Starts automatically when Flask starts.
# No need to run reminders.py separately ever.
# ─────────────────────────────────────────────
import threading
import time as _time
import schedule as _schedule

def _run_reminder_scheduler():
    """Runs in a background thread — sends reminders automatically."""
    from reminders import send_reminders_for_slot
    from datetime import datetime as _dt

    _schedule.every().day.at("08:00").do(send_reminders_for_slot, "Morning")
    _schedule.every().day.at("13:00").do(send_reminders_for_slot, "Afternoon")
    _schedule.every().day.at("18:00").do(send_reminders_for_slot, "Evening")
    _schedule.every().day.at("21:00").do(send_reminders_for_slot, "Night")

    print("✅ WellSync Reminder Scheduler running in background")
    print("   08:00 Morning | 13:00 Afternoon | 18:00 Evening | 21:00 Night")

    # Fire immediately for current time slot on startup
    _now_hour = _dt.now().hour
    if 6 <= _now_hour < 12:
        print("   ⚡ Firing Morning slot immediately on startup...")
        send_reminders_for_slot("Morning")
    elif 12 <= _now_hour < 16:
        print("   ⚡ Firing Afternoon slot immediately on startup...")
        send_reminders_for_slot("Afternoon")
    elif 16 <= _now_hour < 20:
        print("   ⚡ Firing Evening slot immediately on startup...")
        send_reminders_for_slot("Evening")
    else:
        print("   ⚡ Firing Night slot immediately on startup...")
        send_reminders_for_slot("Night")

    while True:
        _schedule.run_pending()
        _time.sleep(30)


# Start only once — not in Flask reloader child process
import os as _os
if __name__ == "__main__":
    _t = threading.Thread(target=_run_reminder_scheduler, daemon=True)
    _t.start()
    app.run(debug=True)
