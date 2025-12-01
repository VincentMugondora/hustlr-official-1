import json
from datetime import datetime

def create_provider(whatsapp_number, name, service_type, location, status="pending"):
    provider = {
        "whatsapp_number": whatsapp_number,
        "name": name,
        "service_type": service_type,
        "location": location,
        "status": status,
        "registered_at": datetime.utcnow().isoformat()
    }
    return provider

# Example usage
provider = create_provider(
    whatsapp_number="263777530322",
    name="John Dube",
    service_type="plumber",
    location="Avondale, Harare"
)

# Print JSON
print(json.dumps(provider, indent=2))
