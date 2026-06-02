"""Montana warrant source registry — all 56 counties plus major city PD / municipal courts."""

from __future__ import annotations

# (county display name, source URL, optional adapter slug)
# Adapters: flathead, rosebud, billings, great-falls, zuercher, generic (default PDF/HTML scan)

COUNTY_SOURCES: dict[str, dict[str, str]] = {
    "beaverhead": {
        "county": "Beaverhead",
        "url": "https://beaverheadcountymt.gov/departments/sheriff/",
    },
    "big-horn": {
        "county": "Big Horn",
        "url": "https://www.bighorncountymt.gov/239/Sheriff",
    },
    "blaine": {
        "county": "Blaine",
        "url": "https://blainecounty-mt.gov/departments/sheriff/",
    },
    "broadwater": {
        "county": "Broadwater",
        "url": "https://broadwater-so-mt.zuercherportal.com/#/warrants",
        "adapter": "zuercher",
    },
    "carbon": {
        "county": "Carbon",
        "url": "https://carbon-so-mt.zuercherportal.com/#/warrants",
        "adapter": "zuercher",
    },
    "carter": {
        "county": "Carter",
        "url": "https://cartercountymt.gov/sheriff/",
    },
    "cascade": {
        "county": "Cascade",
        "url": "https://www.cascadecountymt.gov/313/Sheriffs-Office",
    },
    "chouteau": {
        "county": "Chouteau",
        "url": "https://www.chouteaucountymt.gov/sheriff",
    },
    "custer": {
        "county": "Custer",
        "url": "https://custercountymt.gov/departments/sheriff/",
    },
    "daniels": {
        "county": "Daniels",
        "url": "https://www.danielscountymt.gov/sheriff",
    },
    "dawson": {
        "county": "Dawson",
        "url": "https://www.dawsoncountymontana.com/sheriff",
    },
    "deer-lodge": {
        "county": "Deer Lodge",
        "url": "https://www.deerlodgecountymt.gov/sheriff",
    },
    "fallon": {
        "county": "Fallon",
        "url": "https://falloncountymt.gov/sheriff/",
    },
    "fergus": {
        "county": "Fergus",
        "url": "https://fergusmt.gov/detention-center-roster",
    },
    "flathead": {
        "county": "Flathead",
        "url": "https://apps.flathead.mt.gov/warrants/warrants_list.php",
        "adapter": "flathead",
    },
    "gallatin": {
        "county": "Gallatin",
        "url": "https://gallatin-so-mt.zuercherportal.com/#/warrants",
        "adapter": "zuercher",
    },
    "garfield": {
        "county": "Garfield",
        "url": "https://garfieldcountymt.gov/sheriff/",
    },
    "glacier": {
        "county": "Glacier",
        "url": "https://glaciercountymt.gov/departments/sheriff/",
    },
    "golden-valley": {
        "county": "Golden Valley",
        "url": "https://www.goldenvalleymt.gov/sheriff",
    },
    "granite": {
        "county": "Granite",
        "url": "https://granitecountyjail.org/",
    },
    "hill": {
        "county": "Hill",
        "url": "https://www.hillcountymt.gov/sheriff",
    },
    "jefferson": {
        "county": "Jefferson",
        "url": "https://jefferson-so-mt.zuercherportal.com/#/warrants",
        "adapter": "zuercher",
    },
    "judith-basin": {
        "county": "Judith Basin",
        "url": "https://www.judithbasincountymt.gov/sheriff",
    },
    "lake": {
        "county": "Lake",
        "url": "https://www.lakemt.gov/sheriff",
    },
    "lewis-and-clark": {
        "county": "Lewis and Clark",
        "url": "https://www.lccountymt.gov/Government/Justice-Court/Arrest-Warrant-List",
        "adapter": "lewis-clark",
    },
    "liberty": {
        "county": "Liberty",
        "url": "https://www.libertycountymt.gov/sheriff",
    },
    "lincoln": {
        "county": "Lincoln",
        "url": "https://lincolncountymt.us/sheriff-home/",
    },
    "madison": {
        "county": "Madison",
        "url": "https://madison-so-mt.zuercherportal.com/#/warrants",
        "adapter": "zuercher",
    },
    "mccone": {
        "county": "McCone",
        "url": "https://mcconecountymt.gov/departments/sheriff-coroner",
    },
    "meagher": {
        "county": "Meagher",
        "url": "https://meagher-so-mt.zuercherportal.com/#/warrants",
        "adapter": "zuercher",
    },
    "mineral": {
        "county": "Mineral",
        "url": "https://co.mineral.mt.us/departments/sheriff/",
    },
    "missoula": {
        "county": "Missoula",
        "url": "https://www.missoulacounty.gov/departments/sheriffs-office/",
    },
    "musselshell": {
        "county": "Musselshell",
        "url": "https://www.musselshellcountymt.gov/sheriff",
    },
    "park": {
        "county": "Park",
        "url": "https://www.parkcounty.org/Government-Departments/Sheriff-s-Office/",
    },
    "petroleum": {
        "county": "Petroleum",
        "url": "https://www.petroleumcountymt.gov/sheriff",
    },
    "phillips": {
        "county": "Phillips",
        "url": "https://phillipscosheriff.com/",
    },
    "pondera": {
        "county": "Pondera",
        "url": "https://www.ponderacountymt.gov/sheriff",
    },
    "powder-river": {
        "county": "Powder River",
        "url": "https://www.powderrivercounty.org/sheriff",
    },
    "powell": {
        "county": "Powell",
        "url": "https://www.powellcountymt.gov/sheriff",
    },
    "prairie": {
        "county": "Prairie",
        "url": "https://prairiecountymt.gov/sheriff",
    },
    "ravalli": {
        "county": "Ravalli",
        "url": "https://ravalli-so-mt.zuercherportal.com/#/warrants",
        "adapter": "zuercher",
    },
    "richland": {
        "county": "Richland",
        "url": "https://www.richlandcountymt.gov/sheriff",
    },
    "roosevelt": {
        "county": "Roosevelt",
        "url": "https://roosevelt-so-mt.zuercherportal.com/#/warrants",
        "adapter": "zuercher",
    },
    "rosebud": {
        "county": "Rosebud",
        "url": "https://rosebudcountymt.gov/departments/sheriff/",
    },
    "sanders": {
        "county": "Sanders",
        "url": "https://sanders-mt.publiclogs.com/",
    },
    "sheridan": {
        "county": "Sheridan",
        "url": "https://www.sheridancountymt.gov/sheriff",
        "adapter": "sheridan",
    },
    "silver-bow": {
        "county": "Silver Bow",
        "url": "https://co.silverbow.mt.us/3274/Detention-Center",
    },
    "stillwater": {
        "county": "Stillwater",
        "url": "https://stillwater-so-mt.zuercherportal.com/#/warrants",
        "adapter": "zuercher",
    },
    "sweet-grass": {
        "county": "Sweet Grass",
        "url": "https://www.sweetgrasscountymt.gov/sheriff",
    },
    "teton": {
        "county": "Teton",
        "url": "https://www.tetoncountymt.gov/sheriff",
    },
    "toole": {
        "county": "Toole",
        "url": "https://toolecountymt.gov/sheriffs-office/",
    },
    "treasure": {
        "county": "Treasure",
        "url": "https://www.treasurecountymt.gov/tcsheriff",
    },
    "valley": {
        "county": "Valley",
        "url": "https://valley-so-mt.zuercherportal.com/#/warrants",
        "adapter": "zuercher",
    },
    "wheatland": {
        "county": "Wheatland",
        "url": "https://wheatland-so-mt.zuercherportal.com/#/warrants",
        "adapter": "zuercher",
    },
    "wibaux": {
        "county": "Wibaux",
        "url": "https://www.wibauxcountymt.gov/sheriff",
    },
    "yellowstone": {
        "county": "Yellowstone",
        "url": "https://www.yellowstonecountymt.gov/sheriff/",
    },
}

CITY_SOURCES: dict[str, dict[str, str]] = {
    "billings-pd": {
        "county": "Yellowstone",
        "city": "Billings",
        "url": "https://www.billingsmt.gov/115/Municipal-Court",
        "adapter": "billings",
    },
    "great-falls-muni": {
        "county": "Cascade",
        "city": "Great Falls",
        "url": "https://coljportal.pubcourts.mt.gov/fullcourtweb/start.do",
        "adapter": "great-falls",
    },
    "helena-muni": {
        "county": "Lewis and Clark",
        "city": "Helena",
        "url": "https://www.helenamt.gov/Departments/Municipal-Court/Arrest-Warrants-List",
        "adapter": "helena",
    },
    "yellowstone-jc": {
        "county": "Yellowstone",
        "city": "Billings",
        "url": "https://www.yellowstonecountymt.gov/justicecourt/",
        "adapter": "municipal-table",
    },
}

SOURCES: dict[str, dict[str, str]] = {**COUNTY_SOURCES, **CITY_SOURCES}
