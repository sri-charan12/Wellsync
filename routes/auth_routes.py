from flask import Blueprint, request, redirect, session, url_for, render_template, jsonify
from config import agents, doctors, patients
import bcrypt
import re

auth = Blueprint("auth", __name__)


# ==========================
# AGENT SIGNUP
# ==========================
@auth.route("/agent_signup", methods=["GET", "POST"])
def agent_signup():
    if request.method == "POST":
        agent_id = request.form.get("agent_id", "").strip()
        password = request.form.get("password", "")
        if not agent_id or not password:
            return render_template("agentsignin.html", error="Missing required fields.")
        if agents.find_one({"agent_id": agent_id}):
            return render_template("agentsignin.html", error="Agent ID already exists. Choose another.")
        hashed_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        agents.insert_one({
            "fullname":  request.form.get("fullname"),
            "gender":    request.form.get("gender"),
            "phone":     request.form.get("phone"),
            "email":     request.form.get("email"),
            "address":   request.form.get("address"),
            "agent_id":  agent_id,
            "password":  hashed_pw
        })
        return redirect(url_for("auth.login"))
    return render_template("agentsignin.html")


# ==========================
# DOCTOR SIGNUP
# ==========================
@auth.route("/doctor_signup", methods=["GET", "POST"])
def doctor_signup():
    if request.method == "POST":
        doctor_id = request.form.get("doctor_id", "").strip()
        password  = request.form.get("password", "")
        if not doctor_id or not password:
            return render_template("docsignin.html", error="Missing required fields.")
        if doctors.find_one({"doctor_id": doctor_id}):
            return render_template("docsignin.html", error="Doctor ID already exists. Choose another.")
        hashed_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        doctors.insert_one({
            "fullname":       request.form.get("fullname"),
            "dob":            request.form.get("dob"),
            "gender":         request.form.get("gender"),
            "phone":          request.form.get("phone"),
            "email":          request.form.get("email"),
            "address":        request.form.get("address"),
            "qualification":  request.form.get("qualification"),
            "specialization": request.form.get("specialization"),
            "license":        request.form.get("license"),
            "doctor_id":      doctor_id,
            "password":       hashed_pw
        })
        return redirect(url_for("auth.login"))
    return render_template("docsignin.html")


# ==========================
# PATIENT SIGNUP (self-register)
# ==========================
@auth.route("/patient_signup", methods=["GET", "POST"])
def patient_signup():
    if request.method == "POST":
        patient_id       = request.form.get("patient_id", "").strip()
        password         = request.form.get("password", "")
        confirm_password = request.form.get("confirmPassword", "")
        if not patient_id or not password:
            return render_template("patsignin.html", error="Missing required fields.")
        if patients.find_one({"patient_id": patient_id}):
            return render_template("patsignin.html", error="Patient ID already exists. Choose another.")
        if password != confirm_password:
            return render_template("patsignin.html", error="Passwords do not match.")
        hashed_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
        patients.insert_one({
            "fullname":          request.form.get("fullname"),
            "gender":            request.form.get("gender"),
            "bloodgroup":        request.form.get("bloodgroup"),
            "phone":             request.form.get("phone"),
            "dob":               request.form.get("dob"),
            "email":             request.form.get("email"),
            "address":           request.form.get("address"),
            "patient_id":        patient_id,
            "password":          hashed_pw,
            "conditionsOption":  request.form.get("conditionsOption"),
            "conditionsDesc":    request.form.get("conditionsDesc"),
            "allergiesOption":   request.form.get("allergiesOption"),
            "allergiesDesc":     request.form.get("allergiesDesc"),
            "medicationsOption": request.form.get("medicationsOption"),
            "medicationsDesc":   request.form.get("medicationsDesc"),
            "surgeriesOption":   request.form.get("surgeriesOption"),
            "surgeriesDesc":     request.form.get("surgeriesDesc"),
            "profile_complete":  True   # self-registered = complete
        })
        return redirect(url_for("auth.login"))
    return render_template("patsignin.html")


# ==========================
# LOGIN  (supports phone login for patients)
# ==========================
@auth.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role     = request.form.get("role", "")
        user_id  = request.form.get("user_id", "").strip()
        password = request.form.get("password", "")

        if not role or not user_id or not password:
            return render_template("login.html", error="Please fill in all fields.")

        user = None

        if role == "agent":
            user = agents.find_one({"agent_id": user_id})

        elif role == "doctor":
            user = doctors.find_one({"doctor_id": user_id})

        elif role == "patient":
            # Try patient_id first, then phone number
            user = patients.find_one({"patient_id": user_id})
            if not user:
                # Normalize phone — strip spaces/dashes
                clean_phone = re.sub(r"[\s\-]", "", user_id)
                user = patients.find_one({"phone": {"$regex": "^\\+?91?" + re.escape(clean_phone[-10:]) + "$"}})
                if not user:
                    # Try exact phone match
                    user = patients.find_one({"phone": user_id})
            if user:
                # Use patient_id as the session identifier
                user_id = user["patient_id"]

        if not user:
            return render_template("login.html", error="User not found. Check your ID or phone number.")

        # ── Password check (handles str, bytes, and MongoDB BSON Binary) ──
        stored_pw = user.get("password")
        if not stored_pw:
            return render_template("login.html", error="Account has no password set. Contact support.")
        if isinstance(stored_pw, str):
            stored_pw = stored_pw.encode("utf-8")
        elif not isinstance(stored_pw, bytes):
            stored_pw = bytes(stored_pw)   # ✅ fixes BSON Binary type from MongoDB

        if not bcrypt.checkpw(password.encode("utf-8"), stored_pw):
            return render_template("login.html", error="Incorrect password. Please try again.")

        session["user_id"] = user_id
        session["role"]    = role

        if role == "agent":
            return redirect("/agent_dashboard")
        elif role == "doctor":
            return redirect("/doctor_dashboard")
        elif role == "patient":
            if user.get("created_by_agent") and not user.get("profile_complete", False):
                return redirect("/complete_profile")
            return redirect("/patient_dashboard")

    return render_template("login.html")


# ==========================
# COMPLETE PROFILE (agent-created patients)
# ==========================
@auth.route("/complete_profile", methods=["GET", "POST"])
def complete_profile():
    if session.get("role") != "patient":
        return redirect("/login")

    patient = patients.find_one({"patient_id": session["user_id"]})
    if not patient:
        return redirect("/login")

    # Already complete
    if patient.get("profile_complete"):
        return redirect("/patient_dashboard")

    if request.method == "POST":
        patients.update_one(
            {"patient_id": session["user_id"]},
            {"$set": {
                "gender":            request.form.get("gender", ""),
                "bloodgroup":        request.form.get("bloodgroup", ""),
                "dob":               request.form.get("dob", ""),
                "email":             request.form.get("email", ""),
                "address":           request.form.get("address", ""),
                "conditionsOption":  request.form.get("conditionsOption", "No"),
                "conditionsDesc":    request.form.get("conditionsDesc", ""),
                "allergiesOption":   request.form.get("allergiesOption", "No"),
                "allergiesDesc":     request.form.get("allergiesDesc", ""),
                "medicationsOption": request.form.get("medicationsOption", "No"),
                "medicationsDesc":   request.form.get("medicationsDesc", ""),
                "profile_complete":  True
            }}
        )
        return redirect("/patient_dashboard")

    return render_template("complete_profile.html", patient=patient)


# ==========================
# FORGOT PASSWORD
# ==========================
@auth.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        role    = request.form.get("role", "patient")
        user_id = request.form.get("user_id", "").strip()
        new_pw  = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if new_pw != confirm:
            return render_template("forget1.html", error="Passwords do not match.")
        if len(new_pw) < 6:
            return render_template("forget1.html", error="Password must be at least 6 characters.")
        hashed = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt())
        if role == "patient":
            r = patients.update_one({"patient_id": user_id}, {"$set": {"password": hashed}})
        elif role == "doctor":
            r = doctors.update_one({"doctor_id": user_id}, {"$set": {"password": hashed}})
        else:
            r = agents.update_one({"agent_id": user_id}, {"$set": {"password": hashed}})
        if r.matched_count == 0:
            return render_template("forget1.html", error="User ID not found.")
        return redirect(url_for("auth.login"))
    return render_template("forget1.html")


# ==========================
# LOGOUT
# ==========================
@auth.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
