import asyncio
import sys
import os

# Ensure project root on path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app.utils.fuzzy_match import find_best_service_match, find_best_location_match, get_matching_providers
from app.utils.mongo_service import MongoService


async def main():
    # Test service matching
    print("=== Service Matching Tests ===")
    test_services = [
        "lawn mowing",
        "grass cutter", 
        "software developer",
        "web developer",
        "plumbing service",
        "electrical repair"
    ]
    
    for service in test_services:
        match = find_best_service_match(service)
        print(f"'{service}' -> '{match}'")
    
    print("\n=== Location Matching Tests ===")
    # Test location matching with sample providers
    sample_providers = [
        {"location": "Mufakose, Harare"},
        {"location": "Harare CBD"},
        {"location": "Bulawayo"},
        {"location": "5 Douglas St, Tonawanda, Mufakose"}
    ]
    
    test_locations = ["mufakose", "harare", "cbd", "tonawanda"]
    for loc in test_locations:
        match = find_best_location_match(loc, sample_providers)
        print(f"'{loc}' -> '{match}'")
    
    print("\n=== Full Provider Matching Tests ===")
    # Test with real DB data
    db_service = MongoService()
    all_providers = await db_service.get_all_providers()
    print(f"Total providers in DB: {len(all_providers)}")
    
    test_cases = [
        ("lawn mowing", "mufakose"),
        ("software developer", "harare"),
        ("plumber", "harare"),
        ("electrician", "bulawayo"),
        ("grass cutter", "mufakose")
    ]
    
    for service, location in test_cases:
        matches, error = get_matching_providers(service, location, all_providers)
        if error:
            print(f"{service} in {location}: {error}")
        else:
            print(f"{service} in {location}: Found {len(matches)} providers")
            for p in matches[:2]:  # Show first 2
                print(f"  - {p.get('name')} ({p.get('service_type')}) in {p.get('location')}")


if __name__ == "__main__":
    asyncio.run(main())
