from typing import List, Optional, Tuple
from rapidfuzz import fuzz
from app.utils.service_mapping import SERVICE_SYNONYMS


def find_best_service_match(user_input: str) -> Optional[str]:
    """Normalize user service input to your DB service_type."""
    user_input = user_input.lower().strip()
    best_match = None
    highest_score = 0

    for key, synonyms in SERVICE_SYNONYMS.items():
        # Check main service type
        score = fuzz.partial_ratio(user_input, key)
        if score > highest_score:
            best_match = key
            highest_score = score

        # Check synonyms
        for syn in synonyms:
            score = fuzz.partial_ratio(user_input, syn)
            if score > highest_score:
                best_match = key
                highest_score = score

    return best_match if highest_score > 70 else None  # threshold can be adjusted


def find_best_location_match(user_input: str, providers: List[dict]) -> Optional[str]:
    """Find best location match from provider list."""
    user_input = user_input.lower().strip()
    best_match = None
    highest_score = 0

    for provider in providers:
        loc = provider.get("location", "").lower()
        score = fuzz.partial_ratio(user_input, loc)
        if score > highest_score:
            best_match = loc
            highest_score = score

    return best_match if highest_score > 70 else None


def get_matching_providers(
    user_service_input: str,
    user_location_input: str,
    providers_db: List[dict]
) -> Tuple[List[dict], Optional[str]]:
    """Find providers matching user service and location using fuzzy matching."""
    # Normalize user input to DB values
    normalized_service = find_best_service_match(user_service_input)
    normalized_location = find_best_location_match(user_location_input, providers_db)

    if not normalized_service:
        return [], f"Sorry, I couldn't understand the service '{user_service_input}'. Can you rephrase?"

    if not normalized_location:
        return [], f"Sorry, I couldn't find providers near '{user_location_input}'. Can you clarify your location?"

    # Filter providers
    matching_providers = [
        p for p in providers_db
        if p['service_type'].lower() == normalized_service
        and normalized_location in p['location'].lower()
        and p.get('status') == "active"
    ]

    if not matching_providers:
        return [], f"I couldn't find any {normalized_service} providers near {normalized_location}."

    return matching_providers, None
