import vertexai
import os
from vertexai.preview import reasoning_engines
from dotenv import load_dotenv

load_dotenv()

PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")

vertexai.init(project=PROJECT_ID, location=LOCATION)

with open(".last_resource_name") as f:
    resource_name = f.read().strip()

print(f"Inspecting agent: {resource_name}")
remote_app = reasoning_engines.ReasoningEngine(resource_name)

print("\nOperation Schemas:")
for s in remote_app.operation_schemas():
    print(f"- {s.get('name')} (mode: {s.get('api_mode')})")
