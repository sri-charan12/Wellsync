from config import patients

def create_patient(data):
    return patients.insert_one(data)