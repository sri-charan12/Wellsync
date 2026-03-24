import os
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")

client = MongoClient(MONGO_URI)

db = client["wellsync_db"]
print("MONGO_URI:", MONGO_URI)
# Collections
doctors = db["doctors"]
patients = db["patients"]
agents = db["agents"]
prescriptions = db["prescriptions"]
adherence_logs = db["adherence_logs"]
medical_records = db["medical_records"]