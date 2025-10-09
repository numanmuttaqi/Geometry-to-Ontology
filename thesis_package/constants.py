"""Centralized constants shared across the geometry-to-ontology package."""

# --- plan categories ---
ROOM_KEYS = [
    "bedroom",
    "bathroom",
    "kitchen",
    "living",
    "balcony",
    "storage",
    "stair",
    "veranda",
    "parking",
]

STRUCT_KEYS = [
    "interior_wall",
    "exterior_wall",
    "door",
    "window",
    "front_door",
]

GEOM_LAYERS = ["inner", "garden", "land", "pool"]

META_KEYS = ["id", "unitType", "area", "net_area", "wall_depth"]

# --- visualization colours ---
ROOM_COLORS = {
    "living": "#d9d9d9",
    "bedroom": "#66c2a5",
    "bathroom": "#fc8d62",
    "kitchen": "#8da0cb",
    "balcony": "#b3b3b3",
    "storage": "#cccccc",
    "stair": "#aaaaaa",
    "veranda": "#bbbbbb",
    "parking": "#dddddd",
}

STRUCT_COLORS = {
    "interior_wall": "#445DFF",
    "exterior_wall": "#FFD344",
    "door": "#e78ac3",
    "window": "#a6d854",
    "front_door": "#a63603",
}

# --- tolerances (metres) ---
EPS_LEN = 0.02
EPS_AREA = 0.01
WALL_BUFFER = 0.02
OPENING_BUFFER = 0.005

# --- id prefixes (rooms) ---
ROOM_PREFIX = {
    "bathroom": "BTH",
    "balcony": "BAL",
    "bedroom": "BED",
    "living": "LIV",
    "kitchen": "KIT",
    "corridor": "COR",
    "hall": "HAL",
    "storage": "STRG",
    "toilet": "WC",
    "dining": "DIN",
    "study": "STD",
    "laundry": "LDY",
    "stair": "STR",
    "veranda": "VER",
    "parking": "PRK",
}

__all__ = [
    "ROOM_KEYS",
    "STRUCT_KEYS",
    "GEOM_LAYERS",
    "META_KEYS",
    "ROOM_COLORS",
    "STRUCT_COLORS",
    "EPS_LEN",
    "EPS_AREA",
    "WALL_BUFFER",
    "OPENING_BUFFER",
    "ROOM_PREFIX",
]
