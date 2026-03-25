import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

# Support both MONGO_URL and MONGO_URI env variable names
MONGO_URI = os.getenv("MONGO_URI") or os.getenv("MONGO_URL")

if not MONGO_URI:
    raise ValueError("❌ Neither MONGO_URI nor MONGO_URL is set in your .env file!")

client = MongoClient(MONGO_URI)

db = client["wellsync_db"]
print("✅ MongoDB connected:", MONGO_URI)

# Collections
doctors        = db["doctors"]
patients       = db["patients"]
agents         = db["agents"]
prescriptions  = db["prescriptions"]
adherence_logs = db["adherence_logs"]
medical_records = db["medical_records"]
