"""Extract and manage available locations from provider database"""

import logging
import re
from typing import List, Dict, Set, Optional

logger = logging.getLogger(__name__)


class LocationExtractor:
    """Extract and manage available cities/towns from provider locations"""
    
    # Zimbabwe major cities and towns
    ZIMBABWE_CITIES = {
        'harare': 'Harare',
        'bulawayo': 'Bulawayo',
        'chitungwiza': 'Chitungwiza',
        'mutare': 'Mutare',
        'gweru': 'Gweru',
        'kwekwe': 'Kwekwe',
        'norton': 'Norton',
        'masvingo': 'Masvingo',
        'chinhoyi': 'Chinhoyi',
        'kariba': 'Kariba',
    }
    
    # Harare suburbs/areas
    HARARE_SUBURBS = {
        'avondale': 'Avondale',
        'borrowdale': 'Borrowdale',
        'belvedere': 'Belvedere',
        'greendale': 'Greendale',
        'graniteside': 'Graniteside',
        'msasa': 'Msasa',
        'strathaven': 'Strathaven',
        'vainona': 'Vainona',
        'milton park': 'Milton Park',
        'mufakose': 'Mufakose',
        'emerald hill': 'Emerald Hill',
        'highlands': 'Highlands',
        'downtown': 'Downtown',
        'city centre': 'City Centre',
    }
    
    def __init__(self):
        """Initialize location extractor"""
        self.available_cities: Set[str] = set()
        self.location_map: Dict[str, str] = {}  # normalized -> display name
    
    def extract_city_from_location(self, location_str: str) -> Optional[str]:
        """
        Extract city/town name from a provider location string.
        
        Args:
            location_str: Full location string (e.g., "189 Samora Machel, Harare")
            
        Returns:
            Normalized city name or None if not found
        """
        if not location_str:
            return None
        
        location_lower = location_str.lower()
        
        # Check for major cities first
        for city_key, city_name in self.ZIMBABWE_CITIES.items():
            if city_key in location_lower:
                return city_name
        
        # Check for Harare suburbs
        for suburb_key, suburb_name in self.HARARE_SUBURBS.items():
            if suburb_key in location_lower:
                return suburb_name
        
        return None
    
    def build_available_locations(self, providers: List[Dict]) -> Set[str]:
        """
        Build set of available cities/towns from providers.
        
        Args:
            providers: List of provider documents from database
            
        Returns:
            Set of unique city/town names
        """
        cities = set()
        
        for provider in providers:
            location = provider.get('location', '')
            city = self.extract_city_from_location(location)
            if city:
                cities.add(city)
        
        self.available_cities = cities
        logger.info(f"Available cities/towns: {sorted(cities)}")
        return cities
    
    def get_available_locations_for_service(self, providers: List[Dict]) -> List[str]:
        """
        Get sorted list of available locations for a service.
        
        Args:
            providers: List of provider documents
            
        Returns:
            Sorted list of unique city/town names
        """
        locations = self.build_available_locations(providers)
        return sorted(list(locations))
    
    def normalize_user_location(self, user_input: str) -> Optional[str]:
        """
        Normalize user input location to match database locations.
        
        Args:
            user_input: User's location input
            
        Returns:
            Normalized location name or None if not recognized
        """
        if not user_input:
            return None
        
        user_lower = user_input.lower().strip()
        
        # Check against major cities
        for city_key, city_name in self.ZIMBABWE_CITIES.items():
            if city_key in user_lower:
                return city_name
        
        # Check against Harare suburbs
        for suburb_key, suburb_name in self.HARARE_SUBURBS.items():
            if suburb_key in user_lower:
                return suburb_name
        
        return None
    
    def filter_providers_by_location(self, providers: List[Dict], location: str) -> List[Dict]:
        """
        Filter providers by location, matching against normalized city/town names.
        
        Args:
            providers: List of provider documents
            location: Target location (city/town name)
            
        Returns:
            Filtered list of providers in that location
        """
        if not location:
            return providers
        
        location_lower = location.lower()
        filtered = []
        
        for provider in providers:
            provider_location = provider.get('location', '')
            extracted_city = self.extract_city_from_location(provider_location)
            
            if extracted_city and extracted_city.lower() == location_lower:
                filtered.append(provider)
        
        return filtered


# Global instance
_location_extractor = None


def get_location_extractor() -> LocationExtractor:
    """Get or create location extractor instance"""
    global _location_extractor
    if _location_extractor is None:
        _location_extractor = LocationExtractor()
    return _location_extractor
