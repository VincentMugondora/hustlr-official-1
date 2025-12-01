import json
import sys
from pathlib import Path
from typing import Any, List

from pymongo import MongoClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings


# Provider data from Pindula (hospitals, cleaning, landscaping, electricians, plumbers)
PROVIDERS_DATA: List[List[dict[str, Any]]] = [
    # Hospitals and Medical Services
    [
        {
            "whatsapp_number": "2638677186798",
            "name": "Belvedere Medical Centre",
            "service_type": "hospital",
            "location": "189 Samora Machel, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000016",
        },
        {
            "whatsapp_number": "2638677006175",
            "name": "The Avenues Clinic",
            "service_type": "hospital",
            "location": "Corner Baines & Mazowe St, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000017",
        },
        {
            "whatsapp_number": "263774796500",
            "name": "DermaCare Skin Clinic",
            "service_type": "dermatologist",
            "location": "17225 Kudu Close Vainona, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000018",
        },
        {
            "whatsapp_number": "263777289797",
            "name": "Venus Medical & Dental Centre",
            "service_type": "medical center",
            "location": "Cambitzis Building, Suite 14 - 1st Floor, King George Road, Avondale, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000019",
        },
        {
            "whatsapp_number": "2634250335",
            "name": "The Eye Zone",
            "service_type": "ophthalmologist",
            "location": "Fife Avenue, Avenues, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000020",
        },
        {
            "whatsapp_number": "2638677004965",
            "name": "Citimed Chitungwiza Hospital",
            "service_type": "hospital",
            "location": "14656 Hadzinanhanga Road, Zengeza 4, Chitungwiza",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000021",
        },
        {
            "whatsapp_number": "263779610360",
            "name": "Provision Optometrists",
            "service_type": "optometrist",
            "location": "Shop 8a Village Walk Shopping Centre, Borrowdale, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000022",
        },
        {
            "whatsapp_number": "2634701555",
            "name": "Parirenyatwa Hospital",
            "service_type": "hospital",
            "location": "Mazowe Street, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000023",
        },
        {
            "whatsapp_number": "263242621100",
            "name": "Harare Central Hospital",
            "service_type": "hospital",
            "location": "Talbot Rd, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000024",
        },
    ],
    # Cleaning Services
    [
        {
            "whatsapp_number": "263773854360",
            "name": "Crisp N Clean Dry Cleaners",
            "service_type": "laundry services",
            "location": "Corner Gleneagles / Dagenham Rd, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000025",
        },
        {
            "whatsapp_number": "263719938576",
            "name": "Rise&CLEAN Professional Cleaners",
            "service_type": "carpet cleaning",
            "location": "8 Normandy Road Alex Park, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000026",
        },
        {
            "whatsapp_number": "263242485579",
            "name": "Cleanland Dry Cleaners",
            "service_type": "dry cleaning",
            "location": "51A Steven Drive Msasa, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000027",
        },
        {
            "whatsapp_number": "263242333055",
            "name": "ClearWorld Cleaners",
            "service_type": "carpet cleaning",
            "location": "6 Pinxton Close Strathaven, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000028",
        },
        {
            "whatsapp_number": "263242770301",
            "name": "Central Dry Cleaners",
            "service_type": "dry cleaning",
            "location": "Crawford Road, Graniteside, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000029",
        },
        {
            "whatsapp_number": "263781144089",
            "name": "Vaal's Cleaning Services",
            "service_type": "office cleaning",
            "location": "89 Kwame Nkrumah Avenue, Central Business District",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000030",
        },
        {
            "whatsapp_number": "263717352687",
            "name": "Kleentaste Cleaning Services",
            "service_type": "home cleaning",
            "location": "Bertram Road 30, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000031",
        },
        {
            "whatsapp_number": "263779366248",
            "name": "NuWay Cleaners",
            "service_type": "dry cleaning",
            "location": "Jason Moyo Avenue, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000032",
        },
    ],
    # Landscaping
    [
        {
            "whatsapp_number": "263777214124",
            "name": "Grass Cutting Services (Pvt) Ltd",
            "service_type": "landscaping",
            "location": "283 Herbert Chitepo Street, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000033",
        },
        {
            "whatsapp_number": "2638677008685",
            "name": "Cutting Edge",
            "service_type": "farm equipment & supply",
            "location": "159 Citroen Rd, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000034",
        },
    ],
    # Electricians
    [
        {
            "whatsapp_number": "2634758886",
            "name": "HE Jackson Electrical and Engineering",
            "service_type": "electrician",
            "location": "1 Boshoff Drive Graniteside, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000001",
        },
        {
            "whatsapp_number": "263773505093",
            "name": "Juwacorn Electrical and Construction",
            "service_type": "construction services",
            "location": "28 George Silundika, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000002",
        },
        {
            "whatsapp_number": "2638644203020",
            "name": "JAMES LLOYD ELECTRICAL (PVT) LTD",
            "service_type": "electrician",
            "location": "LOT 380, TRANSTOBAC COMPLEX, NUMBER 34, HILLSIDE ROAD EXTENSION, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000003",
        },
        {
            "whatsapp_number": "263242775137",
            "name": "ZETDC",
            "service_type": "electrician",
            "location": "Electricity Centre, 25 Samora Machel Avenue, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000004",
        },
        {
            "whatsapp_number": "",
            "name": "AE Electrical",
            "service_type": "electrician",
            "location": "61 Mbuya Nehanda Street, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000005",
        },
        {
            "whatsapp_number": "2634758796",
            "name": "Greystone Park Electrical",
            "service_type": "electrician",
            "location": "3 Cardiff Avenue Belvedere, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000006",
        },
        {
            "whatsapp_number": "26344499033",
            "name": "MJB Electrical",
            "service_type": "electrician",
            "location": "5 Bridgenorth Road Greendale, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000007",
        },
        {
            "whatsapp_number": "2638677410800",
            "name": "Powerspeed ELECTRICAL LTD",
            "service_type": "business manufacturing & supply",
            "location": "Stand 17568, Corner Cripps Road And Kelvin Road North, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000008",
        },
        {
            "whatsapp_number": "",
            "name": "Electrician",
            "service_type": "electrician",
            "location": "1533 Hunyani, Chinhoyi",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000009",
        },
        {
            "whatsapp_number": "",
            "name": "Bietech Electrical",
            "service_type": "electrician",
            "location": "4 Park Vista 44 Park Rd Darlington, Mutare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000010",
        },
        {
            "whatsapp_number": "26347774051",
            "name": "Eltex Engineering",
            "service_type": "electrician",
            "location": "Unit 15 Stand17004 Sande Crescent, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000011",
        },
        {
            "whatsapp_number": "",
            "name": "Eurostar Electric Co. Ltd.",
            "service_type": "electrician",
            "location": "1 Ludlow Road, Highlands, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000012",
        },
        {
            "whatsapp_number": "2638677009585",
            "name": "CENTRAGRID PRIVATE LIMITED",
            "service_type": "electrician",
            "location": "Section 2, Of Penrose Farm, Chirundu Highway, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000013",
        },
        {
            "whatsapp_number": "2634772279",
            "name": "ALRICH ELECTRICAL and HARDWARE",
            "service_type": "electrical supply store",
            "location": "114 SEKE ROAD GRANITESIDE, Central Business District",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000014",
        },
        {
            "whatsapp_number": "",
            "name": "CREISTLE ENTERPRISES (PVT) LTD",
            "service_type": "electrician",
            "location": "784 Nzou Drive, Kariba",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000015",
        },
    ],
    # Plumbers
    [
        {
            "whatsapp_number": "263718275163",
            "name": "Seagate Plumbers",
            "service_type": "plumber",
            "location": "6 Weale Road, Milton Park, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000001",
        },
        {
            "whatsapp_number": "2634740800",
            "name": "JAYHIND (PVT) LTD",
            "service_type": "plumber",
            "location": "104 Coventry Road, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000002",
        },
        {
            "whatsapp_number": "263242486531",
            "name": "John Hook & Son Pvt LTD",
            "service_type": "plumber",
            "location": "26 George Avenue, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000003",
        },
        {
            "whatsapp_number": "2634302152",
            "name": "Solae Edwards Zimbabwe",
            "service_type": "plumber",
            "location": "111 Broadlands Road, Emerald Hill, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000004",
        },
        {
            "whatsapp_number": "",
            "name": "MB Plumbers",
            "service_type": "irrigation",
            "location": "Central Business District, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000005",
        },
        {
            "whatsapp_number": "",
            "name": "NEW DEN PLUMBERS",
            "service_type": "plumber",
            "location": "Central Business District, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000006",
        },
        {
            "whatsapp_number": "263782983880",
            "name": "PME Design & Build Construction",
            "service_type": "contractor",
            "location": "30 Benghazi Road, Braeside, Harare",
            "status": "pending",
            "registered_at": "2025-12-01T09:15:23.000007",
        },
    ],
]


def import_providers() -> None:
    """Import all provider data into MongoDB."""
    client = MongoClient(settings.MONGODB_URI)
    db = client[settings.MONGODB_DB_NAME]
    collection = db["providers"]

    all_providers: List[dict[str, Any]] = []
    for group in PROVIDERS_DATA:
        all_providers.extend(group)

    # Filter out providers with empty whatsapp_number
    valid_providers = [p for p in all_providers if p.get("whatsapp_number", "").strip()]

    if not valid_providers:
        print("No valid providers to import (all have empty whatsapp_number).")
        return

    try:
        result = collection.insert_many(valid_providers)
        print(f"✅ Successfully imported {len(result.inserted_ids)} providers to MongoDB.")
        print(f"   Database: {settings.MONGODB_DB_NAME}")
        print(f"   Collection: providers")
    except Exception as e:
        print(f"❌ Error importing providers: {e}")


if __name__ == "__main__":
    import_providers()
