import os
from openai import OpenAI

key = os.getenv("OPENAI_API_KEY")

print("LEN:", len(key) if key else 0)
print("HEAD:", key[:15] if key else "NO KEY")

client = OpenAI(api_key=key)

try:
    models = client.models.list()
    print("SUCCESS")
    print("Connected to OpenAI")
except Exception as e:
    print(type(e).__name__)
    print(e)
