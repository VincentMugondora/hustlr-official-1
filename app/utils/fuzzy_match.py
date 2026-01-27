from typing import List, Optional, Sequence
from rapidfuzz import fuzz, process

# Lightweight alias map to canonical service categories used in the app
# Canonicals align with MessageHandler.extract_service_type categories
SERVICE_ALIASES = {
    # Developer/software
    "software developer": "developer",
    "software engineer": "developer",
    "programmer": "developer",
    "coder": "developer",
    "web developer": "developer",
    "web designer": "developer",
    "frontend": "developer",
    "backend": "developer",
    "fullstack": "developer",
    "mobile developer": "developer",
    "android": "developer",
    "ios": "developer",
    "flutter": "developer",
    "react": "developer",

    # Gardening / lawn
    "gardener": "gardener",
    "garden": "gardener",
    "landscaper": "gardener",
    "landscape": "gardener",
    "landscaping": "gardener",
    "lawn": "gardener",
    "lawn service": "gardener",
    "lawn mowing": "gardener",
    "mower": "gardener",
    "mowing": "gardener",
    "grass": "gardener",
    "grass cutter": "gardener",
    "yard": "gardener",

    # Home services
    "plumber": "plumber",
    "plumbing": "plumber",
    "electrician": "electrician",
    "electrical": "electrician",
    "carpenter": "carpenter",
    "carpentry": "carpenter",
    "painter": "painter",
    "painting": "painter",
    "cleaner": "cleaner",
    "cleaning": "cleaner",
    "mechanic": "mechanic",
    "auto": "mechanic",

    # Technicians and appliances
    "technician": "technician",
    "appliance": "technician",
    "fridge": "technician",
    "cctv": "technician",
    "dstv": "technician",
    "solar": "technician",
    "inverter": "technician",
    "aircon": "technician",
    "air con": "technician",
    "air conditioner": "technician",

    # Personal services
    "laundry": "laundry",
    "washing": "laundry",
    "ironing": "laundry",
    "gas": "gas",
    "refill": "gas",
    "tutor": "tutor",
    "lessons": "tutor",
    "catering": "catering",
    "chef": "catering",
    "cook": "catering",
    "photography": "photography",
    "photographer": "photography",

    # Beauty
    "beautician": "beautician",
    "beauty": "beautician",
    "nails": "beautician",
    "manicure": "beautician",
    "pedicure": "beautician",
    "makeup": "beautician",
    "hair": "beautician",
    "barber": "beautician",
    "salon": "beautician",

    # Fitness and wellness
    "fitness": "fitness",
    "trainer": "fitness",
    "gym": "fitness",
    "massage": "massage",
    "therapist": "massage",

    # Business and professional
    "accountant": "accountant",
    "accounting": "accountant",
    "bookkeeper": "accountant",
    "tax": "accountant",
    "consultant": "consultant",
    "marketing": "consultant",
    "legal": "legal",
    "lawyer": "legal",

    # Security/pest
    "security": "security",
    "guard": "security",
    "bouncer": "security",
    "fumigation": "fumigation",
    "pest control": "fumigation",

    # Interior / upholstery
    "interior": "interior",
    "decor": "interior",
    "decorator": "interior",
    "upholstery": "interior",

    # Logistics and hire
    "courier": "courier",
    "delivery": "courier",
    "errands": "courier",
    "car hire": "car",
    "car rental": "car",
    "vehicle hire": "car",

    # Events
    "event": "event",
    "event planner": "event",
    "dj": "event",
    "mc": "event",

    # Property
    "real estate": "real",
    "estate agent": "real",
    "property": "real",

    # Borehole
    "borehole": "borehole",
    "drilling": "borehole",
    "water": "borehole",
    "pump": "borehole",
    "tank": "borehole",
}

CANONICAL_SERVICES = sorted(set(SERVICE_ALIASES.values()))
ALIAS_KEYS = list(SERVICE_ALIASES.keys())


def _best_match(text: str, choices: Sequence[str], threshold: int = 80) -> Optional[str]:
    if not text:
        return None
    q = (text or "").strip().lower()
    if not q:
        return None
    # Prefer token_set_ratio for robustness to word order
    match = process.extractOne(q, choices, scorer=fuzz.token_set_ratio)
    if match and match[1] >= threshold:
        return match[0]
    return None


def find_best_service_match(text: str, threshold: int = 80) -> Optional[str]:
    # Substring quick path over aliases
    q = (text or "").strip().lower()
    if not q:
        return None
    for alias, canonical in SERVICE_ALIASES.items():
        if alias in q:
            return canonical
    # Fuzzy over aliases
    alias = _best_match(q, ALIAS_KEYS, threshold)
    if alias:
        return SERVICE_ALIASES.get(alias)
    # Fuzzy directly over canonicals as a final attempt
    canon = _best_match(q, CANONICAL_SERVICES, threshold)
    if canon:
        return canon
    return None


def find_best_location_match(text: str, candidates: Sequence[str], threshold: int = 80) -> Optional[str]:
    q = (text or "").strip().lower()
    if not q or not candidates:
        return None
    # Substring quick path
    for cand in candidates:
        try:
            if cand and cand.lower() in q:
                return cand
            if cand and q in cand.lower() and len(q) >= 3:
                return cand
        except Exception:
            continue
    # Fuzzy match over given candidate list
    best = process.extractOne(q, list(candidates), scorer=fuzz.token_set_ratio)
    if best and best[1] >= threshold:
        return best[0]
    return None
