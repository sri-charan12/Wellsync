from config import doctors

def create_doctor(data):
    return doctors.insert_one(data)