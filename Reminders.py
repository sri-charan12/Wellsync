"""
WellSync Reminder System
━━━━━━━━━━━━━━━━━━━━━━━━
Sends WhatsApp reminders with interactive "Took 💊" / "Skipped ⏭" buttons
using Twilio's Content API (Quick Reply template).

Run:
    python reminders.py

Requires in .env:
    TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    TWILIO_PHONE_NUMBER=+1xxxxxxxxxx
    TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886

Webhook:
    POST /webhook/whatsapp  → set in Twilio WhatsApp Sandbox settings
"""

import os
import time
import schedule
import requests
from datetime import datetime
from dotenv import load_dotenv
from pymongo import MongoClient
from twilio.rest import Client

load_dotenv()

# ── MongoDB ──────────────────────────────────────
MONGO_URI       = os.getenv("MONGO_URI")
mongo_client    = MongoClient(MONGO_URI)
db              = mongo_client["wellsync_db"]
patients        = db["patients"]
prescriptions   = db["prescriptions"]
reminder_logs   = db["reminder_logs"]
dose_responses  = db["dose_responses"]

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


# ── CREATE QUICK REPLY CONTENT TEMPLATE ──────────
def create_quick_reply_template(patient_name, med_summary, time_of_day):
    """
    Creates a one-time Twilio Content API template with two Quick Reply buttons:
      [✅ Took my medicines]   [⏭ Skip]
    Returns the content SID to use when sending.
    """
    payload = {
        "friendly_name": f"med_reminder_{patient_name.replace(' ', '_')}_{int(time.time())}",
        "language": "en",
        "variables": {},
        "types": {
            "twilio/quick-reply": {
                "body": (
                    f"💊 *WellSync Medication Reminder*\n"
                    f"Hello {patient_name}! Time for your *{time_of_day}* dose:\n\n"
                    f"{med_summary}\n\n"
                    f"Please take your medicines as prescribed.\n"
                    f"Did you take them? 👇"
                ),
                "actions": [
                    {"title": "✅ Took", "id": "taken"},
                    {"title": "😔 Forgot", "id": "forgotten"}
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
        sid = response.json().get("sid")
        print(f"  📝 Content template created: {sid}")
        return sid
    else:
        print(f"  ❌ Failed to create template: {response.status_code} {response.text}")
        return None


# ── SEND WHATSAPP WITH BUTTONS ────────────────────
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
        print(f"  ✅ WhatsApp (buttons) sent to {to_number} — SID: {msg.sid}")
        return True
    except Exception as e:
        print(f"  ❌ WhatsApp failed to {to_number}: {e}")
        return False


# ── SEND PLAIN SMS (fallback, no buttons on SMS) ──
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
        print(f"  ❌ SMS failed to {to_number}: {e}")
        return False


# ── BUILD MEDICINE SUMMARY ────────────────────────
def build_med_summary(medicines):
    """Formats medicine list (dedup already done in get_medicines_for_time)."""
    lines = []
    for m in medicines:
        name    = m.get("name",         "").strip()
        dosage  = m.get("dosage",       "").strip()
        instruc = m.get("instructions", "").strip()
        line    = " ".join(filter(None, [name, dosage]))
        if instruc:
            line += f" — {instruc}"
        lines.append(f"  \u2022 {line}")
    return "\n".join(lines)


def build_sms_message(patient_name, med_summary, time_of_day):
    return (
        f"WellSync Reminder: Hello {patient_name}!\n"
        f"Time for your {time_of_day} dose:\n\n"
        f"{med_summary}\n\n"
        f"Reply TAKEN or SKIP.\n— WellSync Health System"
    )


# ── DUPLICATE CHECK ───────────────────────────────
def already_sent(patient_id, time_of_day):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    return reminder_logs.find_one({
        "patient_id":  patient_id,
        "time_of_day": time_of_day,
        "date":        today
    }) is not None


def mark_sent(patient_id, time_of_day, channel, success):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    reminder_logs.insert_one({
        "patient_id":  patient_id,
        "time_of_day": time_of_day,
        "channel":     channel,
        "date":        today,
        "sent_at":     datetime.utcnow().isoformat(),
        "success":     success
    })


# ── GET MEDICINES FOR TIME SLOT ───────────────────
def get_medicines_for_time(patient_id, time_slot):
    patient_rxs = list(prescriptions.find({
        "patient_id": patient_id,
        "status":     "Active"
    }))
    seen = set()
    matched = []
    for rx in patient_rxs:
        for med in rx.get("medicines", []):
            schedule_str = (med.get("schedule") or "").lower()
            # Match whole word only — avoids "Morning" matching inside "MorningAfternoon" etc.
            slot_words = [s.strip() for s in schedule_str.split(",")]
            if time_slot.lower() not in slot_words:
                continue
            # Deduplicate by name + dosage across all prescriptions
            key = (
                med.get("name",    "").strip().lower(),
                med.get("dosage",  "").strip().lower(),
            )
            if key not in seen:
                seen.add(key)
                matched.append(med)
    return matched


# ── HANDLE BUTTON REPLY (called from Flask webhook) ──
def handle_reply(from_number, body, channel="whatsapp"):
    """
    Called when patient taps a button.
    Twilio sends the button `id` as the message body:
      "taken"     when they tap [✅ Took]
      "forgotten" when they tap [😔 Forgot]
    """
    clean_number = from_number.replace("whatsapp:", "").strip()
    if not clean_number.startswith("+"):
        clean_number = "+91" + clean_number.lstrip("0")

    reply = body.strip().lower()

    if reply in ("taken", "1", "yes", "✅ took", "took"):
        action = "taken"
    elif reply in ("forgotten", "2", "no", "forgot", "😔 forgot"):
        action = "forgotten"
    else:
        return (
            "Please tap one of the buttons in the reminder message, "
            "or reply *TAKEN* or *FORGOT*."
        )

    patient = patients.find_one({"phone": {"$regex": clean_number[-10:]}})
    if not patient:
        return "Thank you for your response!"

    patient_id   = patient["patient_id"]
    first_name   = patient.get("fullname", "Patient").split()[0]
    today        = datetime.utcnow().strftime("%Y-%m-%d")

    last_reminder = reminder_logs.find_one(
        {"patient_id": patient_id, "date": today, "success": True},
        sort=[("sent_at", -1)]
    )
    time_of_day = last_reminder["time_of_day"] if last_reminder else "Unknown"

    dose_responses.insert_one({
        "patient_id":   patient_id,
        "patient_name": patient.get("fullname"),
        "phone":        clean_number,
        "channel":      channel,
        "time_of_day":  time_of_day,
        "date":         today,
        "action":       action,
        "replied_at":   datetime.utcnow().isoformat()
    })

    print(f"  📩 {patient.get('fullname')} replied: {action} [{time_of_day}]")

    if action == "taken":
        return (
            f"✅ Great job, {first_name}! "
            f"Your *{time_of_day}* dose is marked as *Taken*. 💪\n"
            f"— WellSync Health System"
        )
    else:
        return (
            f"😔 No worries, {first_name}. Your *{time_of_day}* dose is marked as *Forgotten*.\n"
            f"Please try not to miss your next dose. Stay healthy! 💙\n"
            f"— WellSync Health System"
        )


# ── SEND REMINDERS FOR ALL PATIENTS ───────────────
def send_reminders_for_slot(time_of_day):
    print(f"\n[{datetime.now().strftime('%H:%M')}] Sending {time_of_day} reminders...")

    all_patients = list(patients.find({"reminders_enabled": True}))
    print(f"  Found {len(all_patients)} patients")

    for pat in all_patients:
        patient_id   = pat["patient_id"]
        patient_name = pat.get("fullname", "Patient")
        phone        = pat.get("phone", "")
        sms_enabled  = pat.get("sms_enabled", False)
        wa_enabled   = pat.get("whatsapp_enabled", False)

        if not phone:
            continue

        if already_sent(patient_id, time_of_day):
            print(f"  ⏭  {patient_name} — already sent {time_of_day}")
            continue

        medicines = get_medicines_for_time(patient_id, time_of_day)
        if not medicines:
            continue

        med_summary = build_med_summary(medicines)

        # WhatsApp with Quick Reply buttons
        if wa_enabled:
            content_sid = create_quick_reply_template(patient_name, med_summary, time_of_day)
            if content_sid:
                ok = send_whatsapp_buttons(phone, content_sid)
                mark_sent(patient_id, time_of_day, "whatsapp", ok)

        # SMS plain text fallback
        if sms_enabled:
            ok = send_sms(phone, build_sms_message(patient_name, med_summary, time_of_day))
            mark_sent(patient_id, time_of_day, "sms", ok)

        if not wa_enabled and not sms_enabled:
            print(f"  ⚠️  {patient_name}: no channel enabled")


# ── SCHEDULE ──────────────────────────────────────
schedule.every().day.at("08:00").do(send_reminders_for_slot, "Morning")
schedule.every().day.at("13:00").do(send_reminders_for_slot, "Afternoon")
schedule.every().day.at("18:00").do(send_reminders_for_slot, "Evening")
schedule.every().day.at("21:00").do(send_reminders_for_slot, "Night")


# ── MAIN ──────────────────────────────────────────
if __name__ == "__main__":
    print("="*55)
    print("  WellSync Reminder System — Starting")
    print("="*55)
    print(f"  MongoDB : {'✅ connected' if MONGO_URI else '❌ not set'}")
    print(f"  Twilio  : {'✅ set' if TWILIO_SID else '⚠️  not set'}")
    print(f"  WA from : {TWILIO_WA}")
    print()
    print("  WhatsApp buttons:")
    print("    [✅ Took my medicines]  [⏭ Skip]")
    print()
    print("  Scheduled times:")
    print("    08:00 Morning | 13:00 Afternoon | 18:00 Evening | 21:00 Night")
    print()
    print("  Running... (Ctrl+C to stop)")
    print("="*55)

    now_hour = datetime.now().hour
    if 6 <= now_hour < 12:
        send_reminders_for_slot("Morning")
    elif 12 <= now_hour < 16:
        send_reminders_for_slot("Afternoon")
    elif 16 <= now_hour < 20:
        send_reminders_for_slot("Evening")
    else:
        send_reminders_for_slot("Night")

    while True:
        schedule.run_pending()
        time.sleep(30)