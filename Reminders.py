"""
WellSync Reminder System
━━━━━━━━━━━━━━━━━━━━━━━━
Retry schedule per slot:
  T+0   → First reminder  (scheduled time: 8am/1pm/6pm/9pm)
  T+5   → Retry 1         (if no response)
  T+30  → Retry 2         (if still no response)
  T+45  → Retry 3 / Final (if still no response)
  T+46  → Mark as Missed  (logs to adherence_logs)

Runs automatically inside Flask (app.py imports this).
No need to run separately.
"""

import os
import time
import threading
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pymongo import MongoClient
from twilio.rest import Client


# ── MongoDB ──────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    raise ValueError("❌ MONGO_URI is not set!")
from config import db, patients, prescriptions, reminder_logs, dose_responses, adherence_logs
db             = mongo_client["wellsync_db"]
patients       = db["patients"]
prescriptions  = db["prescriptions"]
reminder_logs  = db["reminder_logs"]
dose_responses = db["dose_responses"]
adherence_logs = db["adherence_logs"]

# ── Twilio ───────────────────────────────────────
TWILIO_SID   = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER")
TWILIO_WA    = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

twilio_client = None

def get_twilio():
    global twilio_client
    if twilio_client is None:
        if not TWILIO_SID or not TWILIO_TOKEN:
            print("⚠️  Twilio credentials not set — reminders disabled")
            return None
        twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)
    return twilio_client


# ── RETRY SCHEDULE ────────────────────────────────
# Minutes after the first send to retry
RETRY_MINUTES = [5, 30, 45]
# Minutes after the LAST retry to mark as missed
MISSED_AFTER_MINUTES = 46


# ── SEND WHATSAPP WITH QUICK REPLY BUTTONS ────────
def create_quick_reply_template(patient_name, med_summary, time_of_day, attempt):
    """Creates Twilio Content API template with Took/Skip buttons."""
    attempt_note = ""
    if attempt == 2:
        attempt_note = "\n⏰ *Reminder:* You haven't responded yet."
    elif attempt == 3:
        attempt_note = "\n⚠️ *Final reminder* for this dose."

    payload = {
        "friendly_name": f"med_reminder_{patient_name.replace(' ','_')}_{int(time.time())}",
        "language": "en",
        "variables": {},
        "types": {
            "twilio/quick-reply": {
                "body": (
                    f"💊 *WellSync Medication Reminder*\n"
                    f"Hello {patient_name}! Time for your *{time_of_day}* dose:\n\n"
                    f"{med_summary}\n"
                    f"{attempt_note}\n"
                    f"Did you take them? 👇"
                ),
                "actions": [
                    {"title": "✅ Took my medicines", "id": "taken"},
                    {"title": "⏭ Skip",               "id": "skipped"}
                ]
            }
        }
    }
    response = requests.post(
        "https://content.twilio.com/v1/Content",
        json=payload,
        auth=(TWILIO_SID, TWILIO_TOKEN)
    )
    if response.status_code in (200, 201):
        return response.json().get("sid")
    print(f"  ❌ Template creation failed: {response.status_code} {response.text}")
    return None


def send_whatsapp_buttons(to_number, content_sid):
    tc = get_twilio()
    if not tc:
        return False
    try:
        if not to_number.startswith("+"):
            to_number = "+91" + to_number.lstrip("0")
        msg = tc.messages.create(
            from_=TWILIO_WA,
            to="whatsapp:" + to_number,
            content_sid=content_sid
        )
        print(f"  ✅ WhatsApp sent to {to_number} — SID: {msg.sid}")
        return True
    except Exception as e:
        print(f"  ❌ WhatsApp failed: {e}")
        return False


def send_sms(to_number, message):
    tc = get_twilio()
    if not tc or not TWILIO_PHONE:
        return False
    try:
        if not to_number.startswith("+"):
            to_number = "+91" + to_number.lstrip("0")
        msg = tc.messages.create(body=message, from_=TWILIO_PHONE, to=to_number)
        print(f"  ✅ SMS sent to {to_number} — SID: {msg.sid}")
        return True
    except Exception as e:
        print(f"  ❌ SMS failed: {e}")
        return False


# ── MEDICINE HELPERS ──────────────────────────────
def build_med_summary(medicines):
    seen, lines = set(), []
    for m in medicines:
        key = (m.get("name","").strip().lower(), m.get("dosage","").strip().lower())
        if key not in seen:
            seen.add(key)
            lines.append(f"  • {m['name']} {m.get('dosage','')}  ({m.get('instructions','')})")
    return "\n".join(lines)


def build_sms_message(patient_name, med_summary, time_of_day, attempt):
    note = ""
    if attempt == 2: note = " (Reminder)"
    if attempt == 3: note = " (FINAL reminder)"
    return (
        f"WellSync{note}: Hello {patient_name}!\n"
        f"Time for your {time_of_day} dose:\n\n"
        f"{med_summary}\n\n"
        f"Reply TAKEN or SKIP.\n— WellSync Health System"
    )


def get_medicines_for_time(patient_id, time_slot):
    rxs = list(prescriptions.find({"patient_id": patient_id, "status": "Active"}))
    matched = []
    for rx in rxs:
        for med in rx.get("medicines", []):
            if time_slot.lower() in (med.get("schedule") or "").lower():
                matched.append(med)
    return matched


# ── RESPONSE CHECK ────────────────────────────────
def patient_responded(patient_id, time_of_day, since_dt):
    """
    Returns True if the patient tapped Took or Skip
    after `since_dt` for this time slot today.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Check dose_responses (WhatsApp button taps)
    resp = dose_responses.find_one({
        "patient_id":  patient_id,
        "time_of_day": time_of_day,
        "date":        today,
        "replied_at":  {"$gte": since_dt.isoformat()}
    })
    if resp:
        return True

    # Check adherence_logs (manual log from patient dashboard)
    log = adherence_logs.find_one({
        "patient_id": patient_id,
        "date":       today,
        "time":       time_of_day,
        "logged_at":  {"$gte": since_dt.isoformat()}
    })
    return log is not None


# ── MARK AS MISSED ────────────────────────────────
def mark_as_missed(patient_id, time_of_day, medicines):
    today   = datetime.utcnow().strftime("%Y-%m-%d")
    now_iso = datetime.utcnow().isoformat()

    seen = set()
    for med in medicines:
        med_name = med.get("name", "Unknown")
        key = med_name.strip().lower()
        if key in seen:
            continue
        seen.add(key)

        # Only insert if not already logged today for this slot
        existing = adherence_logs.find_one({
            "patient_id": patient_id,
            "medication": med_name,
            "date":       today,
            "time":       time_of_day
        })
        if not existing:
            adherence_logs.insert_one({
                "patient_id": patient_id,
                "medication": med_name,
                "dosage":     med.get("dosage", ""),
                "status":     "missed",
                "date":       today,
                "time":       time_of_day,
                "source":     "auto_missed",
                "logged_at":  now_iso
            })
    print(f"  ❌ Marked as MISSED: {patient_id} [{time_of_day}]")


def log_reminder_sent(patient_id, time_of_day, channel, attempt, success):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    reminder_logs.insert_one({
        "patient_id":  patient_id,
        "time_of_day": time_of_day,
        "channel":     channel,
        "attempt":     attempt,
        "date":        today,
        "sent_at":     datetime.utcnow().isoformat(),
        "success":     success
    })


def already_sent_attempt(patient_id, time_of_day, attempt):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return reminder_logs.find_one({
        "patient_id":  patient_id,
        "time_of_day": time_of_day,
        "attempt":     attempt,
        "date":        today
    }) is not None


# ── SEND ONE REMINDER (single patient, single attempt) ───
def send_one_reminder(pat, time_of_day, attempt):
    patient_id   = pat["patient_id"]
    patient_name = pat.get("fullname", "Patient")
    phone        = pat.get("phone", "")
    sms_enabled  = pat.get("sms_enabled", False)
    wa_enabled   = pat.get("whatsapp_enabled", False)

    if not phone:
        return

    medicines = get_medicines_for_time(patient_id, time_of_day)
    if not medicines:
        return

    med_summary = build_med_summary(medicines)
    print(f"  📤 Attempt {attempt} → {patient_name} [{time_of_day}]")

    if wa_enabled:
        sid = create_quick_reply_template(patient_name, med_summary, time_of_day, attempt)
        if sid:
            ok = send_whatsapp_buttons(phone, sid)
            log_reminder_sent(patient_id, time_of_day, "whatsapp", attempt, ok)

    if sms_enabled:
        msg = build_sms_message(patient_name, med_summary, time_of_day, attempt)
        ok  = send_sms(phone, msg)
        log_reminder_sent(patient_id, time_of_day, "sms", attempt, ok)


# ── RETRY WORKER (runs in background thread per patient per slot) ──
def retry_worker(pat, time_of_day, first_sent_at):
    """
    Runs in its own thread for each patient+slot.
    Waits between retries, checks if patient responded,
    sends retry if not, marks missed after final retry.
    """
    patient_id = pat["patient_id"]

    for i, wait_mins in enumerate(RETRY_MINUTES):
        attempt = i + 2  # attempts are 1-indexed; first send = 1

        # Wait until retry time
        time.sleep(wait_mins * 60)

        # Check if patient already responded since first send
        if patient_responded(patient_id, time_of_day, first_sent_at):
            print(f"  ✅ {pat.get('fullname')} responded — no retry needed [{time_of_day}]")
            return

        # Skip if already sent this attempt (crash recovery)
        if already_sent_attempt(patient_id, time_of_day, attempt):
            continue

        send_one_reminder(pat, time_of_day, attempt)

    # After all retries — wait remaining time then mark missed
    time.sleep(MISSED_AFTER_MINUTES * 60 - sum(RETRY_MINUTES) * 60)

    if not patient_responded(patient_id, time_of_day, first_sent_at):
        medicines = get_medicines_for_time(patient_id, time_of_day)
        mark_as_missed(patient_id, time_of_day, medicines)


# ── MAIN SLOT TRIGGER ─────────────────────────────
def send_reminders_for_slot(time_of_day):
    """
    Called at 08:00 / 13:00 / 18:00 / 21:00.
    Sends first reminder to all eligible patients,
    then spawns a retry thread per patient.
    """
    now = datetime.utcnow()
    print(f"\n[{datetime.now().strftime('%H:%M')}] ── {time_of_day} reminders ──")

    all_patients = list(patients.find({"reminders_enabled": True}))
    print(f"  Found {len(all_patients)} patients")

    for pat in all_patients:
        patient_id = pat["patient_id"]

        # Skip if attempt 1 already sent today (restart recovery)
        if already_sent_attempt(patient_id, time_of_day, 1):
            print(f"  ⏭  {pat.get('fullname')} — already sent attempt 1")
            # Still need to spawn retry worker if not responded
            if not patient_responded(patient_id, time_of_day, now - timedelta(minutes=1)):
                t = threading.Thread(
                    target=retry_worker, args=(pat, time_of_day, now), daemon=True
                )
                t.start()
            continue

        medicines = get_medicines_for_time(patient_id, time_of_day)
        if not medicines:
            continue

        # Send attempt 1
        send_one_reminder(pat, time_of_day, attempt=1)
        first_sent_at = datetime.utcnow()

        # Spawn retry thread for this patient
        t = threading.Thread(
            target=retry_worker,
            args=(pat, time_of_day, first_sent_at),
            daemon=True
        )
        t.start()


# ── HANDLE WHATSAPP BUTTON REPLY ──────────────────
def handle_reply(from_number, body, channel="whatsapp"):
    """
    Called from Flask webhook when patient taps a button.
    Logs to both dose_responses AND adherence_logs.
    """
    import re
    clean_number = from_number.replace("whatsapp:", "").strip()
    if not clean_number.startswith("+"):
        clean_number = "+91" + clean_number.lstrip("0")

    reply = body.strip().lower()

    if reply in ("taken", "1", "yes", "✅ took my medicines", "took my medicines"):
        action = "taken"
    elif reply in ("skipped", "2", "no", "skip", "⏭ skip"):
        action = "skipped"
    else:
        return (
            "Please tap one of the buttons in the reminder message, "
            "or reply *TAKEN* or *SKIP*."
        )

    patient = patients.find_one({"phone": {"$regex": re.escape(clean_number[-10:]) + "$"}})
    if not patient:
        print(f"  ⚠️  No patient found for {clean_number}")
        return "Thank you for your response!"

    patient_id  = patient["patient_id"]
    first_name  = patient.get("fullname", "Patient").split()[0]
    today       = datetime.utcnow().strftime("%Y-%m-%d")
    now_iso     = datetime.utcnow().isoformat()

    # Get the last reminder slot sent today
    last_reminder = reminder_logs.find_one(
        {"patient_id": patient_id, "date": today, "success": True},
        sort=[("sent_at", -1)]
    )
    time_of_day = last_reminder["time_of_day"] if last_reminder else "Unknown"

    # 1. Save to dose_responses
    dose_responses.insert_one({
        "patient_id":   patient_id,
        "patient_name": patient.get("fullname"),
        "phone":        clean_number,
        "channel":      channel,
        "time_of_day":  time_of_day,
        "date":         today,
        "action":       action,
        "replied_at":   now_iso
    })

    # 2. Save to adherence_logs (so dashboards update)
    medicines_for_slot = get_medicines_for_time(patient_id, time_of_day)
    seen = set()
    for med in medicines_for_slot:
        med_name = med.get("name", "Unknown")
        key = med_name.strip().lower()
        if key in seen:
            continue
        seen.add(key)

        # Only insert if not already logged (avoid duplicates from retries)
        existing = adherence_logs.find_one({
            "patient_id": patient_id,
            "medication": med_name,
            "date":       today,
            "time":       time_of_day
        })
        if not existing:
            adherence_logs.insert_one({
                "patient_id": patient_id,
                "medication": med_name,
                "dosage":     med.get("dosage", ""),
                "status":     action,
                "date":       today,
                "time":       time_of_day,
                "source":     "whatsapp_reply",
                "logged_at":  now_iso
            })

    print(f"  📩 {patient.get('fullname')} replied: {action} [{time_of_day}] ✅")

    if action == "taken":
        return (
            f"✅ Great job, {first_name}! "
            f"Your *{time_of_day}* dose is marked as *Taken*. 💪\n"
            f"— WellSync Health System"
        )
    else:
        return (
            f"⏭ Noted, {first_name}. Your *{time_of_day}* dose is marked as *Skipped*.\n"
            f"Try to stay on schedule for better recovery.\n"
            f"— WellSync Health System"
        )
   
