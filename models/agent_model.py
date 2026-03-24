from config import agents

def create_agent(data):
    return agents.insert_one(data)