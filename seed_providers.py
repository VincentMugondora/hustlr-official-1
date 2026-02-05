#!/usr/bin/env python3
"""
Seed minimal test providers for Harare (electrician and gardener/lawn).
Run: python seed_providers.py
"""

import asyncio
import sys
import os
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.utils.mongo_service import MongoService

PROVIDERS = [
    {
        "name": "BE GRASSCUTTERS",
        "service_type": "Lawn service",
        "location": "Mufakose, Harare",
        "whatsapp_number": "263771234567",
        "status": "active",
        "created_at": datetime.utcnow().isoformat(),
    },
    {
        "name": "John Electrician",
        "service_type": "Electrician",
        "location": "Mufakose, Harare",
        "whatsapp_number": "263777654321",
        "status": "active",
        "created_at": datetime.utcnow().isoformat(),
    },
]

async def main():
    mongo = MongoService()
    for p in PROVIDERS:
        try:
            result = await mongo.create_provider(p)
            print(f"Created provider: {p['name']} ({p['service_type']}) at {p['location']} -> _id={result.get('_id')}")
        except Exception as e:
            print(f"Failed to create {p['name']}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
