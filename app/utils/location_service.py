"""Location service for reverse geocoding coordinates to location names"""

import logging
from typing import Optional, Tuple
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

logger = logging.getLogger(__name__)


class LocationService:
    """Service for converting coordinates to location names"""
    
    def __init__(self):
        """Initialize the geocoder"""
        self.geocoder = Nominatim(user_agent="hustlr_bot")
        # Zimbabwe bounding box for filtering results
        self.zimbabwe_bounds = {
            'north': -8.0,
            'south': -22.5,
            'east': 33.0,
            'west': 25.0
        }
    
    async def reverse_geocode(self, latitude: float, longitude: float) -> Optional[str]:
        """
        Convert coordinates to location name using reverse geocoding.
        
        Args:
            latitude: Latitude coordinate
            longitude: Longitude coordinate
            
        Returns:
            Location name (e.g., "Mufakose, Harare") or None if lookup fails
        """
        try:
            # Validate coordinates are in Zimbabwe
            if not self._is_in_zimbabwe(latitude, longitude):
                logger.warning(f"Coordinates ({latitude}, {longitude}) outside Zimbabwe bounds")
                return None
            
            # Perform reverse geocoding
            location = self.geocoder.reverse(f"{latitude}, {longitude}", language='en')
            
            if location:
                address = location.address
                logger.info(f"Reverse geocoded ({latitude}, {longitude}) to: {address}")
                
                # Extract meaningful location parts
                location_name = self._extract_location_name(address)
                return location_name
            else:
                logger.warning(f"No location found for coordinates ({latitude}, {longitude})")
                return None
                
        except GeocoderTimedOut:
            logger.error(f"Geocoding timeout for coordinates ({latitude}, {longitude})")
            return None
        except GeocoderServiceError as e:
            logger.error(f"Geocoding service error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during reverse geocoding: {e}")
            return None
    
    def _is_in_zimbabwe(self, latitude: float, longitude: float) -> bool:
        """Check if coordinates are within Zimbabwe bounds"""
        return (
            self.zimbabwe_bounds['south'] <= latitude <= self.zimbabwe_bounds['north'] and
            self.zimbabwe_bounds['west'] <= longitude <= self.zimbabwe_bounds['east']
        )
    
    def _extract_location_name(self, address: str) -> str:
        """
        Extract meaningful location name from full address.
        
        Nominatim returns addresses like:
        "Mufakose, Harare, Zimbabwe"
        "123 Main Street, Avondale, Harare, Zimbabwe"
        
        We want to extract the suburb/area and city.
        """
        try:
            parts = [p.strip() for p in address.split(',')]
            
            # Remove country (usually last part)
            if parts and parts[-1].lower() == 'zimbabwe':
                parts = parts[:-1]
            
            # Try to find Harare or other major cities
            harare_keywords = ['harare', 'chitungwiza', 'bulawayo', 'mutare', 'gweru', 'kwekwe']
            
            # Look for city in the address
            city = None
            suburb = None
            
            for part in reversed(parts):
                part_lower = part.lower()
                if any(keyword in part_lower for keyword in harare_keywords):
                    city = part
                    break
            
            # Get the first meaningful part as suburb/area
            if len(parts) > 0:
                suburb = parts[0]
            
            # Build location name
            if suburb and city:
                return f"{suburb}, {city}"
            elif city:
                return city
            elif suburb:
                return suburb
            else:
                return address
                
        except Exception as e:
            logger.error(f"Error extracting location name from address: {e}")
            return address


# Global instance
_location_service = None


def get_location_service() -> LocationService:
    """Get or create the location service instance"""
    global _location_service
    if _location_service is None:
        _location_service = LocationService()
    return _location_service
