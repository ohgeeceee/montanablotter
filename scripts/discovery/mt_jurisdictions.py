#!/usr/bin/env python3
"""
Montana jurisdictions master list.
56 counties with county seats and major cities.
"""

COUNTIES = [
    ("Beaverhead", "Dillon", ["Dillon", "Lima"]),
    ("Big Horn", "Hardin", ["Hardin", "Crow Agency", "Lodge Grass"]),
    ("Blaine", "Chinook", ["Chinook", "Harlem", "Fort Belknap"]),
    ("Broadwater", "Townsend", ["Townsend"]),
    ("Carbon", "Red Lodge", ["Red Lodge", "Columbus", "Bridger", "Joliet"]),
    ("Carter", "Ekalaka", ["Ekalaka"]),
    ("Cascade", "Great Falls", ["Great Falls", "Black Eagle", "Cascade", "Belt"]),
    ("Chouteau", "Fort Benton", ["Fort Benton", "Geraldine", "Big Sandy"]),
    ("Custer", "Miles City", ["Miles City"]),
    ("Daniels", "Scobey", ["Scobey", "Flaxville"]),
    ("Dawson", "Glendive", ["Glendive", "Richey"]),
    ("Deer Lodge", "Anaconda", ["Anaconda"]),
    ("Fallon", "Baker", ["Baker", "Plevna"]),
    ("Fergus", "Lewistown", ["Lewistown", "Denton", "Moore"]),
    ("Flathead", "Kalispell", ["Kalispell", "Columbia Falls", "Whitefish", "Bigfork", "Lakeside", "Somers"]),
    ("Gallatin", "Bozeman", ["Bozeman", "Belgrade", "Three Forks", "Manhattan", "West Yellowstone"]),
    ("Garfield", "Jordan", ["Jordan"]),
    ("Glacier", "Cut Bank", ["Cut Bank", "Browning"]),
    ("Golden Valley", "Ryegate", ["Ryegate", "Lavina"]),
    ("Granite", "Philipsburg", ["Philipsburg", "Drummond"]),
    ("Hill", "Havre", ["Havre", "Rocky Boy"]),
    ("Jefferson", "Boulder", ["Boulder", "Whitehall", "Cardwell"]),
    ("Judith Basin", "Stanford", ["Stanford", "Hobson"]),
    ("Lake", "Polson", ["Polson", "Ronan", "St. Ignatius", "Arlee"]),
    ("Lewis and Clark", "Helena", ["Helena", "East Helena"]),
    ("Liberty", "Chester", ["Chester", "Joplin"]),
    ("Lincoln", "Libby", ["Libby", "Eureka", "Troy"]),
    ("Madison", "Virginia City", ["Virginia City", "Ennis", "Twin Bridges", "Sheridan"]),
    ("McCone", "Circle", ["Circle", "Brockway"]),
    ("Meagher", "White Sulphur Springs", ["White Sulphur Springs"]),
    ("Mineral", "Superior", ["Superior", "St. Regis", "Alberton"]),
    ("Missoula", "Missoula", ["Missoula", "Lolo", "Frenchtown", "Bonner", "Seeley Lake", "Clinton"]),
    ("Musselshell", "Roundup", ["Roundup", "Melstone"]),
    ("Park", "Livingston", ["Livingston", "Gardiner", "Clyde Park", "Shields Valley"]),
    ("Petroleum", "Winnett", ["Winnett", "Cat Creek"]),
    ("Phillips", "Malta", ["Malta", "Saco", "Dodson"]),
    ("Pondera", "Conrad", ["Conrad", "Valier", "Brady"]),
    ("Powder River", "Broadus", ["Broadus", "Biddle"]),
    ("Powell", "Deer Lodge", ["Deer Lodge", "Avon", "Elliston", "Garrison"]),
    ("Prairie", "Terry", ["Terry", "Fallon"]),
    ("Ravalli", "Hamilton", ["Hamilton", "Stevensville", "Darby", "Victor", "Florence", "Conner"]),
    ("Richland", "Sidney", ["Sidney", "Fairview", "Lambert", "Savage", "Crane"]),
    ("Roosevelt", "Wolf Point", ["Wolf Point", "Poplar", "Culbertson", "Bainville", "Brockton"]),
    ("Rosebud", "Forsyth", ["Forsyth", "Colstrip", "Lame Deer", "Ashland"]),
    ("Sanders", "Thompson Falls", ["Thompson Falls", "Plains", "Hot Springs", "Noxon", "Trout Creek"]),
    ("Sheridan", "Plentywood", ["Plentywood", "Medicine Lake", "Westby", "Antelope", "Reserve", "Outlook", "Dagmar"]),
    ("Silver Bow", "Butte", ["Butte", "Walkerville"]),
    ("Stillwater", "Columbus", ["Columbus", "Absarokee", "Nye", "Park City", "Reed Point"]),
    ("Sweet Grass", "Big Timber", ["Big Timber", "Greycliff", "McLeod", "Melville"]),
    ("Teton", "Choteau", ["Choteau", "Fairfield", "Power", "Dutton", "Pendroy", "Bynum"]),
    ("Toole", "Shelby", ["Shelby", "Sunburst", "Kevin", "Sweet Grass"]),
    ("Treasure", "Hysham", ["Hysham", "Bighorn", "Myers"]),
    ("Valley", "Glasgow", ["Glasgow", "Fort Peck", "Opheim", "Glentana", "Hinsdale", "Nashua", "St. Marie"]),
    ("Wheatland", "Harlowton", ["Harlowton", "Judith Gap", "Shawmut", "Two Dot"]),
    ("Wibaux", "Wibaux", ["Wibaux"]),
    ("Yellowstone", "Billings", ["Billings", "Laurel", "Lockwood", "Worden", "Huntley", "Custer", "Shepherd"]),
]

CITIES_BY_COUNTY = {}
for county, seat, cities in COUNTIES:
    CITIES_BY_COUNTY[county] = {"seat": seat, "cities": cities}

if __name__ == "__main__":
    print(f"Total counties: {len(COUNTIES)}")
    total_cities = sum(len(v["cities"]) for v in CITIES_BY_COUNTY.values())
    print(f"Total cities: {total_cities}")
