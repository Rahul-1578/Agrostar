"""
AgroStar — AI-Powered Crop Yield Prediction & Optimization
Flask backend. Modular route structure: page routes render templates,
/api/* routes return JSON (mocked for demo — swap in real model/DB calls later).
"""
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta
import random
import os
import io
import re
import json
import difflib
import uuid
import requests
from dotenv import load_dotenv

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("[AgroStar] WARNING: Pillow is not installed — photo validity checking "
          "(pest_sentinel_analyze) will accept every photo as valid. "
          "Run: pip install -r requirements.txt")

load_dotenv()  # reads a local .env file (if present) into os.environ —
                # lets MSG91_AUTH_KEY / OPENAI_API_KEY / etc. be set once in
                # a file instead of exporting them in every terminal session.
                # .env is gitignored, so real keys never get committed.

app = Flask(__name__)
app.secret_key = "agrostar-dev-secret-change-in-production"

# Global upload size cap — set slightly above the largest single upload this
# app accepts (Farmer Reels video, 60MB) so Flask itself rejects an oversized
# request before it's read into memory, rather than the process trying to
# buffer an arbitrarily large file first. Individual routes (reels, diary
# audio, receipt/pest photos) still enforce their own tighter limits too.
app.config["MAX_CONTENT_LENGTH"] = 70 * 1024 * 1024  # 70MB

# ---------------------------------------------------------------------------
# FIREBASE (Google Sign-In) — web config is not secret (it's shipped to the
# browser either way), but we still read it from env vars so the same code
# works across dev/staging/prod without editing app.py. Fill these in
# .env — see .env.example. If FIREBASE_API_KEY is empty, the "Continue with
# Google" button on the login page hides itself automatically.
# ---------------------------------------------------------------------------
FIREBASE_CONFIG = {
    "apiKey": os.getenv("FIREBASE_API_KEY", ""),
    "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN", ""),
    "projectId": os.getenv("FIREBASE_PROJECT_ID", ""),
    "storageBucket": os.getenv("FIREBASE_STORAGE_BUCKET", ""),
    "messagingSenderId": os.getenv("FIREBASE_MESSAGING_SENDER_ID", ""),
    "appId": os.getenv("FIREBASE_APP_ID", ""),
}

# Bumps on every server start so browsers always fetch the latest static
# JS/CSS instead of an old cached copy — important during development
# when i18n.js / app.js change but Ctrl+F5 is easy to forget.
import time
app.config["ASSET_VERSION"] = str(int(time.time()))

# ---------------------------------------------------------------------------
# LOCATIONS — Bhubaneswar city localities + nearby towns/mandis, used across
# Weather, Market Prices, and Profile so farmers can pick their exact area
# instead of one fixed default location.
# ---------------------------------------------------------------------------
BHUBANESWAR_LOCATIONS = [
    # Core Bhubaneswar city localities / wards
    "Bhubaneswar (City Centre)", "Nayapalli", "Patia", "Chandrasekharpur",
    "Saheed Nagar", "Jaydev Vihar", "Rasulgarh", "Kalinga Nagar", "Bharatpur",
    "Sundarpada", "Dumduma", "Baramunda", "Pokhariput", "Old Town (Ekamra)",
    "Khandagiri", "Jagamara", "Tamando", "Niladri Vihar", "Aiginia",
    "Vani Vihar", "Sailashree Vihar", "Damana", "Kalinganagar Industrial",
    # Nearby towns / mandi hubs in the Bhubaneswar-Cuttack-Khordha belt
    "Cuttack", "Puri", "Khordha", "Jatani", "Balianta", "Barang",
    "Tangi", "Banki", "Pipili", "Jankia", "Balugaon", "Chandaka",
]


@app.route("/api/locations", methods=["GET"])
def locations():
    return jsonify({"locations": BHUBANESWAR_LOCATIONS})

# ---------------------------------------------------------------------------
# CROP ↔ LOCATION INTELLIGENCE
#
# Powers two things:
#   1. /api/crop_recommendation — farmer picks soil+climate manually.
#   2. /api/best_crops_for_location — farmer picks (or GPS-detects) an actual
#      place from BHUBANESWAR_LOCATIONS; that place is mapped to a soil type
#      + climate zone below, then crops are ranked by BOTH how well they'd
#      actually grow there AND how profitable they are in the market —
#      because a crop can be highly profitable and still be a bad
#      recommendation if the local land can't grow it well (e.g. strawberries
#      fetch a great price but need a cool climate Odisha doesn't have).
# ---------------------------------------------------------------------------

# Approximate real-world coordinates for each locality, used to find the
# nearest one to a farmer's GPS position (see /api/nearest_location).
LOCATION_COORDS = {
    "Bhubaneswar (City Centre)": (20.2961, 85.8245), "Nayapalli": (20.2960, 85.8180),
    "Patia": (20.3530, 85.8180), "Chandrasekharpur": (20.3430, 85.8190),
    "Saheed Nagar": (20.3010, 85.8450), "Jaydev Vihar": (20.2970, 85.8070),
    "Rasulgarh": (20.3050, 85.8550), "Kalinga Nagar": (20.3310, 85.8170),
    "Bharatpur": (20.3180, 85.8340), "Sundarpada": (20.2430, 85.7830),
    "Dumduma": (20.3280, 85.8360), "Baramunda": (20.2830, 85.7950),
    "Pokhariput": (20.2630, 85.8000), "Old Town (Ekamra)": (20.2380, 85.8340),
    "Khandagiri": (20.2580, 85.7800), "Jagamara": (20.2760, 85.7690),
    "Tamando": (20.2290, 85.7500), "Niladri Vihar": (20.3480, 85.8090),
    "Aiginia": (20.2200, 85.8260), "Vani Vihar": (20.2960, 85.8370),
    "Sailashree Vihar": (20.3450, 85.8220), "Damana": (20.3650, 85.8130),
    "Kalinganagar Industrial": (20.3350, 85.8150),
    "Cuttack": (20.4625, 85.8828), "Puri": (19.8135, 85.8312),
    "Khordha": (20.1830, 85.6122), "Jatani": (20.1667, 85.7000),
    "Balianta": (20.2020, 85.9130), "Barang": (20.3830, 85.9300),
    "Tangi": (20.1000, 85.5670), "Banki": (20.3667, 85.9167),
    "Pipili": (20.1167, 85.8333), "Jankia": (20.0330, 85.6330),
    "Balugaon": (19.7333, 85.1333), "Chandaka": (20.3500, 85.7500),
}

# Soil type + climate zone for each locality — a light generalisation of
# Odisha's real geography (coastal belt = sandy/coastal, Mahanadi delta
# around Cuttack = clay-rich, Bhubaneswar's urban-fringe farmland = loamy,
# the laterite uplands near Khandagiri/Chandaka = red laterite/semi-arid).
LOCATION_SOIL_PROFILE = {
    "Bhubaneswar (City Centre)": ("Loamy", "Humid subtropical"),
    "Nayapalli": ("Loamy", "Humid subtropical"), "Patia": ("Loamy", "Humid subtropical"),
    "Chandrasekharpur": ("Loamy", "Humid subtropical"), "Saheed Nagar": ("Loamy", "Humid subtropical"),
    "Jaydev Vihar": ("Loamy", "Humid subtropical"), "Rasulgarh": ("Clay", "Humid subtropical"),
    "Kalinga Nagar": ("Red laterite", "Semi-arid"), "Bharatpur": ("Loamy", "Humid subtropical"),
    "Sundarpada": ("Red laterite", "Semi-arid"), "Dumduma": ("Loamy", "Humid subtropical"),
    "Baramunda": ("Loamy", "Humid subtropical"), "Pokhariput": ("Clay", "Humid subtropical"),
    "Old Town (Ekamra)": ("Loamy", "Humid subtropical"), "Khandagiri": ("Red laterite", "Semi-arid"),
    "Jagamara": ("Red laterite", "Semi-arid"), "Tamando": ("Red laterite", "Semi-arid"),
    "Niladri Vihar": ("Loamy", "Humid subtropical"), "Aiginia": ("Clay", "Humid subtropical"),
    "Vani Vihar": ("Loamy", "Humid subtropical"), "Sailashree Vihar": ("Loamy", "Humid subtropical"),
    "Damana": ("Red laterite", "Semi-arid"), "Kalinganagar Industrial": ("Red laterite", "Semi-arid"),
    "Cuttack": ("Clay", "Humid subtropical"), "Puri": ("Sandy", "Coastal"),
    "Khordha": ("Loamy", "Humid subtropical"), "Jatani": ("Loamy", "Humid subtropical"),
    "Balianta": ("Clay", "Humid subtropical"), "Barang": ("Clay", "Humid subtropical"),
    "Tangi": ("Clay", "Humid subtropical"), "Banki": ("Clay", "Humid subtropical"),
    "Pipili": ("Sandy", "Coastal"), "Jankia": ("Sandy", "Coastal"),
    "Balugaon": ("Sandy", "Coastal"), "Chandaka": ("Red laterite", "Semi-arid"),
}

# Candidate crops per soil type, with a base growability score (0-100) and
# a short reason. Climate zone then nudges the score up/down per crop —
# shared by /api/crop_recommendation and /api/best_crops_for_location.
SOIL_CROP_CANDIDATES = {
    "Loamy": [
        ("Rice", 88, "High water retention matches soil type"),
        ("Wheat", 82, "Balanced texture supports strong root growth"),
        ("Sugarcane", 80, "Retains moisture and nutrients well"),
        ("Maize", 78, "Good aeration and drainage for maize roots"),
        ("Green Gram (Moong)", 74, "Improves nitrogen for next rotation"),
    ],
    "Clay": [
        ("Rice", 90, "Excellent water retention ideal for paddy"),
        ("Sugarcane", 83, "Holds moisture through the long growing season"),
        ("Pigeon Pea (Arhar)", 78, "Tolerates heavier soils with good structure"),
        ("Cotton", 76, "Deep clay supports strong taproot development"),
    ],
    "Sandy": [
        ("Groundnut", 87, "Good drainage and warm climate fit"),
        ("Watermelon", 83, "Sandy soil warms quickly, ideal for melons"),
        ("Green Gram (Moong)", 80, "Improves nitrogen for next rotation"),
        ("Bajra (Pearl Millet)", 78, "Thrives in low-moisture, well-drained soil"),
    ],
    "Red laterite": [
        ("Groundnut", 85, "Good drainage and warm climate fit"),
        ("Cashew", 83, "Well suited to acidic, well-drained laterite soil"),
        ("Ragi (Finger Millet)", 79, "Hardy crop suited to low-fertility red soils"),
        ("Green Gram (Moong)", 76, "Improves nitrogen for next rotation"),
    ],
}

# Bonus/penalty per crop for each climate zone.
CROP_CLIMATE_FIT = {
    "Rice": {"Humid subtropical": 8, "Coastal": 6, "Semi-arid": -10},
    "Wheat": {"Humid subtropical": 3, "Semi-arid": 5, "Coastal": -5},
    "Sugarcane": {"Humid subtropical": 6, "Coastal": 4, "Semi-arid": -8},
    "Maize": {"Humid subtropical": 4, "Semi-arid": 3, "Coastal": 0},
    "Pigeon Pea (Arhar)": {"Semi-arid": 6, "Humid subtropical": 2, "Coastal": 0},
    "Cotton": {"Semi-arid": 6, "Humid subtropical": 0, "Coastal": -4},
    "Groundnut": {"Semi-arid": 5, "Coastal": 5, "Humid subtropical": 0},
    "Watermelon": {"Semi-arid": 4, "Coastal": 6, "Humid subtropical": 0},
    "Green Gram (Moong)": {"Semi-arid": 5, "Humid subtropical": 2, "Coastal": 2},
    "Bajra (Pearl Millet)": {"Semi-arid": 8, "Coastal": -6, "Humid subtropical": -4},
    "Cashew": {"Coastal": 8, "Humid subtropical": 2, "Semi-arid": -6},
    "Ragi (Finger Millet)": {"Semi-arid": 6, "Humid subtropical": 0, "Coastal": -2},
}

# Market profitability database — price, demand trend, and a 0-100 profit
# score per crop. Includes a few high-value crops (Strawberry, Saffron,
# Apple) that fetch excellent prices but need a cool/temperate climate
# Odisha's coastal belt doesn't have, on purpose: the ranking below scores
# them low on growability despite the high profit score, and the frontend
# explicitly flags them as "profitable but poor fit for your land".
CROP_PROFITABILITY = {
    "Rice": {"price_per_quintal": 2100, "demand": "Steady", "profit_score": 62},
    "Wheat": {"price_per_quintal": 2300, "demand": "Steady", "profit_score": 60},
    "Sugarcane": {"price_per_quintal": 350, "demand": "Steady", "profit_score": 58},
    "Maize": {"price_per_quintal": 2000, "demand": "Rising", "profit_score": 65},
    "Green Gram (Moong)": {"price_per_quintal": 7500, "demand": "Rising", "profit_score": 78},
    "Pigeon Pea (Arhar)": {"price_per_quintal": 7000, "demand": "Steady", "profit_score": 74},
    "Cotton": {"price_per_quintal": 6800, "demand": "Steady", "profit_score": 63},
    "Groundnut": {"price_per_quintal": 5800, "demand": "Rising", "profit_score": 72},
    "Watermelon": {"price_per_quintal": 1200, "demand": "Seasonal spike", "profit_score": 68},
    "Bajra (Pearl Millet)": {"price_per_quintal": 2500, "demand": "Rising", "profit_score": 70},
    "Cashew": {"price_per_quintal": 14000, "demand": "Steady", "profit_score": 80},
    "Ragi (Finger Millet)": {"price_per_quintal": 3800, "demand": "Rising", "profit_score": 75},
    "Strawberry": {"price_per_quintal": 12000, "demand": "Rising", "profit_score": 88},
    "Saffron": {"price_per_quintal": 250000, "demand": "Steady", "profit_score": 95},
    "Apple": {"price_per_quintal": 8000, "demand": "Steady", "profit_score": 82},
}

# Crops that need a genuinely cool/temperate climate — never a good fit for
# Odisha's tropical belt, regardless of soil type. The adjustment below
# still varies their grow score by climate zone (coastal humidity hurts
# them most, the drier semi-arid uplands are the "least bad" — still poor)
# so the ranking visibly responds to location here too, not just for the
# crops that ARE locally suited.
CLIMATE_MISMATCH_CROPS = {"Strawberry", "Saffron", "Apple"}
CLIMATE_MISMATCH_ADJUST = {"Coastal": -6, "Humid subtropical": 0, "Semi-arid": 8}


def _haversine_km(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, sqrt, atan2
    r = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return r * 2 * atan2(sqrt(a), sqrt(1 - a))


@app.route("/api/nearest_location", methods=["POST"])
def nearest_location():
    """Given a farmer's GPS lat/lng (from the browser's Geolocation API),
    finds the nearest known locality so the rest of the app (Weather,
    Market Prices, Best Crops for You) can use it automatically instead of
    the farmer typing/selecting it by hand."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        lat = float(data.get("lat"))
        lng = float(data.get("lng"))
    except (TypeError, ValueError):
        return jsonify({"error": "lat/lng required"}), 400

    best_name, best_dist = None, None
    for name, (loc_lat, loc_lng) in LOCATION_COORDS.items():
        dist = _haversine_km(lat, lng, loc_lat, loc_lng)
        if best_dist is None or dist < best_dist:
            best_dist, best_name = dist, name

    return jsonify({"location": best_name, "distance_km": round(best_dist, 1)})


@app.route("/api/best_crops_for_location", methods=["POST"])
def best_crops_for_location():
    """Ranks crops for a specific location by combining two independent
    scores: how well the crop actually GROWS there (soil + climate fit)
    and how PROFITABLE it currently is in the market — so a farmer sees,
    for example, that Strawberry has a great market price but a poor grow
    fit for their land, while Cashew is both a strong fit and profitable."""
    data = request.get_json(force=True, silent=True) or {}
    location = data.get("location", "Bhubaneswar (City Centre)")
    soil_type, climate_zone = LOCATION_SOIL_PROFILE.get(
        location, LOCATION_SOIL_PROFILE["Bhubaneswar (City Centre)"]
    )

    candidates = SOIL_CROP_CANDIDATES.get(soil_type, SOIL_CROP_CANDIDATES["Loamy"])
    candidate_lookup = {c[0]: (c[1], c[2]) for c in candidates}

    ranking = []
    for crop, info in CROP_PROFITABILITY.items():
        if crop in candidate_lookup:
            base, reason = candidate_lookup[crop]
            bonus = CROP_CLIMATE_FIT.get(crop, {}).get(climate_zone, 0)
            grow_score = max(60, min(97, base + bonus))
            fit_note = reason
        elif crop in CLIMATE_MISMATCH_CROPS:
            grow_score = max(8, min(35, 18 + CLIMATE_MISMATCH_ADJUST.get(climate_zone, 0)))
            fit_note = f"Needs a cool/temperate climate — {climate_zone} Odisha weather doesn't suit it"
        else:
            bonus = CROP_CLIMATE_FIT.get(crop, {}).get(climate_zone, 0)
            grow_score = max(30, min(60, 45 + bonus))
            fit_note = f"Not a typical crop for {soil_type} soil here — possible with extra soil prep, but riskier"

        profit_score = info["profit_score"]
        overall_score = round(0.55 * grow_score + 0.45 * profit_score)
        ranking.append({
            "crop": crop,
            "grow_score": grow_score,
            "profit_score": profit_score,
            "overall_score": overall_score,
            "fit_note": fit_note,
            "price_per_quintal": info["price_per_quintal"],
            "demand": info["demand"],
            "poor_fit_high_profit": grow_score < 40 and profit_score >= 75,
        })

    ranking.sort(key=lambda r: r["overall_score"], reverse=True)

    return jsonify({
        "location": location,
        "soil_type": soil_type,
        "climate_zone": climate_zone,
        "ranking": ranking,
    })

# ---------------------------------------------------------------------------
# AI PROVIDER CONFIG — set these as environment variables before running:
#   export OPENAI_API_KEY="sk-..."     (uses GPT-5 via /chat/completions style API)
#   export GEMINI_API_KEY="..."        (used only if OPENAI_API_KEY is not set)
# If neither is set, the chatbot automatically falls back to the built-in
# rule-based responder below — so live demos never break on stage even
# without internet or a key.
# ---------------------------------------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

AASHA_SYSTEM_PROMPT = (
    "You are AASHA, a friendly farming assistant for Indian farmers using the "
    "AgroStar app. Answer in the language requested (English, Hindi, or Odia). "
    "Give a helpful, DETAILED answer of about 4-6 sentences — enough to actually "
    "guide the farmer's next action, not just a one-liner. Be specific and "
    "practical (mention timing, quantities, or simple checks where relevant), "
    "and specific to Indian farming — weather, soil, fertilizer, pests, crop "
    "stages, mandi prices, or government schemes. Avoid jargon; a farmer with "
    "no technical background should understand every sentence easily."
)

# Multilingual keyword lists per topic — used for direct substring matching.
# Includes English, Devanagari/Odia script, AND common romanized (Latin-script)
# spelling variants for Hindi/Odia — since many farmers type in Hinglish/Odia-
# in-English rather than the native script (e.g. "khaad" or "khad" for खाद).
# Each topic also has a short list of "core" words (English + romanized)
# checked with fuzzy matching, so a typo like "wastrher" or "khaad" (extra
# vowel) still lands on the right topic instead of falling through to the
# generic fallback.
AASHA_TOPIC_KEYWORDS = {
    "weather": ["weather", "climate", "rain", "rainfall", "forecast", "temperature",
                "mausam", "mousam", "mosam", "barish", "baarish", "barsha", "varsha",
                "मौसम", "बारिश", "वर्षा", "ପାଣିପାଗ", "ବର୍ଷା", "monsoon"],
    "soil": ["soil", "mitti", "matti", "mati", "मिट्टी", "ମାଟି", "ph", "nutrient", "npk",
             "nitrogen", "phosphorus", "potassium"],
    "fertilizer": ["fertilizer", "fertiliser", "khad", "khaad", "khaada", "khata",
                   "खाद", "ସାର", "urea", "dap", "compost", "manure"],
    "pest": ["pest", "keet", "keeda", "kira", "kirra", "keeda-makoda",
             "कीट", "कीड़ा", "କୀଟ", "disease", "rog", "roga", "bimari", "beemari",
             "रोग", "बीमारी", "ରୋଗ", "insect", "bug", "fungus", "blight", "hopper",
             "caterpillar"],
    "market": ["market", "mandi", "मंडी", "ମଣ୍ଡି", "price", "bhav", "bhaav", "daam",
               "dam", "keemat", "kimat", "बाज़ार", "भाव", "क़ीमत", "ବଜାର", "rate",
               "ଦର", "sell", "bech", "bechna"],
    "irrigation": ["irrigation", "water", "paani", "pani", "jal", "पानी", "जल",
                   "ପାଣି", "sinchai", "sinchayi", "सिंचाई", "ଜଳସେଚନ", "drip", "canal"],
    "growth": ["growth", "stage", "tillering", "sowing", "bowai", "buvai", "ropai",
               "harvest", "katai", "kataai", "फसल", "बुवाई", "रोपाई", "कटाई",
               "ଅବସ୍ଥା", "ଅମଳ"],
    "scheme": ["scheme", "yojana", "yojna", "योजना", "ଯୋଜନା", "loan", "rin", "karza",
               "karz", "ऋण", "कर्ज़", "ଋଣ", "insurance", "bima", "beema", "बीमा",
               "ବୀମା", "kcc", "pmfby", "subsidy", "subsidi", "sahayata", "सहायता"],
    "greeting": ["hi", "hello", "hey", "namaste", "namaskar", "namaskaar",
                 "नमस्ते", "नमस्कार", "ନମସ୍କାର"],
}

# For fuzzy-matching typos, check against several core words per topic
# (English + common romanized Hindi/Odia spellings) rather than just one —
# this catches typos in romanized Hindi/Odia too, not only English.
AASHA_FUZZY_CORE = {
    "weather": ["weather", "mausam", "barish", "monsoon"],
    "soil": ["soil", "mitti", "mati"],
    "fertilizer": ["fertilizer", "khaad", "khad", "urea"],
    "pest": ["pest", "keeda", "kira", "rog", "bimari"],
    "market": ["market", "mandi", "bhaav", "daam"],
    "irrigation": ["irrigation", "paani", "sinchai"],
    "growth": ["growth", "fasal", "katai"],
    "scheme": ["scheme", "yojana", "karza", "bima"],
}


def classify_aasha_intent(message_lower):
    # 1. Direct substring match (handles English + Hindi + Odia keywords,
    #    both native script and common romanized spellings)
    for topic, keywords in AASHA_TOPIC_KEYWORDS.items():
        for kw in keywords:
            if kw in message_lower:
                return topic

    # 2. Fuzzy match each Latin-script word in the message against core
    #    words (English + romanized Hindi/Odia) for every topic, to catch
    #    typos/spelling variants like "wastrher" (weather), "fertlizer"
    #    (fertilizer), or "khaad" vs "khad" (fertilizer).
    words = re.findall(r"[a-zA-Z]+", message_lower)
    best_topic, best_ratio = None, 0.0
    for word in words:
        if len(word) < 4:
            continue
        for topic, core_words in AASHA_FUZZY_CORE.items():
            for core in core_words:
                ratio = difflib.SequenceMatcher(None, word, core).ratio()
                if ratio > best_ratio:
                    best_ratio, best_topic = ratio, topic
    if best_ratio >= 0.72:
        return best_topic
    return None


AASHA_RESPONSES = {
    "weather": {
        "en": [
            "The next 3 days look dry with no significant rain expected, so this is a good window to plan any field work. Irrigate this evening rather than midday — less water is lost to evaporation, and roots absorb it better overnight. Keep an eye on wind speed too if you're planning to spray anything, since strong wind wastes pesticide/fertilizer spray.",
            "Expect mostly clear skies over the next few days with daytime temperatures staying warm. If you're sowing or transplanting, early morning or late evening is best to reduce heat stress on young plants. No rain is forecast, so don't delay any planned irrigation.",
        ],
        "hi": [
            "अगले 3 दिन सूखे रहने की संभावना है, बारिश की कोई खास उम्मीद नहीं है। आज शाम सिंचाई करें, दोपहर के बजाय — इससे पानी कम भाप बनकर उड़ेगा और जड़ों को रातभर बेहतर पानी मिलेगा। अगर छिड़काव की योजना है तो तेज़ हवा के समय स्प्रे करने से बचें, नहीं तो दवा बर्बाद हो सकती है।",
        ],
        "or": [
            "ପରବର୍ତ୍ତୀ 3 ଦିନ ଶୁଷ୍କ ରହିବାର ସମ୍ଭାବନା ଅଛି, ବଡ ବର୍ଷାର ଆଶା ନାହିଁ। ଆଜି ସନ୍ଧ୍ୟାରେ ଜଳସେଚନ କରନ୍ତୁ, ମଧ୍ୟାହ୍ନ ପରିବର୍ତ୍ତେ — ଏହା ଦ୍ୱାରା ପାଣି କମ୍ ବାଷ୍ପ ହେବ ଏବଂ ମୂଳ ରାତିସାରା ଭଲ ପାଣି ପାଇବ। ଛିଡ଼କାଇବାର ଯୋଜନା ଥିଲେ ପ୍ରବଳ ପବନ ସମୟରେ ଏଡାନ୍ତୁ।",
        ],
    },
    "soil": {
        "en": [
            "For healthy soil, aim for a pH between 6 and 7.5 for most crops — get it tested at your nearest Soil Health Card centre if you haven't in the last 2 years, it's free. Adding organic matter like farmyard manure or compost each season improves both nutrient content and water retention. If your soil feels hard and cracks in dry weather, that usually means low organic carbon — mulching and crop rotation with legumes can help rebuild it over a few seasons.",
        ],
        "hi": [
            "ज़्यादातर फसलों के लिए मिट्टी का pH 6 से 7.5 के बीच होना अच्छा है — अगर पिछले 2 साल में जांच नहीं कराई है तो नज़दीकी मृदा स्वास्थ्य कार्ड केंद्र पर मुफ्त जांच कराएं। हर सीज़न गोबर की खाद या कम्पोस्ट डालने से मिट्टी में पोषक तत्व और नमी दोनों बढ़ते हैं। अगर सूखे मौसम में मिट्टी सख्त होकर फटती है, तो ये जैविक कार्बन कम होने का संकेत है — मल्चिंग और दलहन के साथ फसल चक्र अपनाने से यह कुछ सीज़न में सुधर जाता है।",
        ],
        "or": [
            "ଅଧିକାଂଶ ଫସଲ ପାଇଁ ମାଟିର pH 6 ରୁ 7.5 ମଧ୍ୟରେ ଭଲ — ଗତ 2 ବର୍ଷରେ ଯାଞ୍ଚ ହୋଇନଥିଲେ ନିକଟସ୍ଥ ମୃତ୍ତିକା ସ୍ୱାସ୍ଥ୍ୟ କାର୍ଡ କେନ୍ଦ୍ରରେ ମାଗଣା ଯାଞ୍ଚ କରାନ୍ତୁ। ପ୍ରତି ଋତୁରେ ଗୋବର ସାର କିମ୍ବା କମ୍ପୋଷ୍ଟ ମିଶାଇଲେ ମାଟିର ପୋଷକ ତତ୍ତ୍ୱ ଓ ଆର୍ଦ୍ରତା ଦୁଇଟି ବଢେ। ଶୁଷ୍କ ଋତୁରେ ମାଟି ଶକ୍ତ ହୋଇ ଫାଟୁଥିଲେ, ଏହା ଜୈବିକ କାର୍ବନ କମ୍ ହେବାର ସୂଚନା — ମଲ୍ଚିଂ ଏବଂ ଡାଲି ଫସଲ ସହିତ ଚକ୍ର କରିଲେ କିଛି ଋତୁରେ ସୁଧରିଯାଏ।",
        ],
    },
    "fertilizer": {
        "en": [
            "Your crop is likely in the tillering stage right now, which is the right time for a urea top-dressing — split it into 2 doses rather than one big dose, so the plant absorbs it better and less gets washed away by irrigation or rain. Apply when the soil is moist but not waterlogged, and avoid spraying right before heavy rain is expected. Along with urea, a small dose of potash at this stage helps strengthen stems against lodging later.",
        ],
        "hi": [
            "आपकी फसल अभी संभवतः कल्ले निकलने (टिलरिंग) की अवस्था में है, जो यूरिया टॉप-ड्रेसिंग के लिए सही समय है — इसे एक बड़ी खुराक की बजाय 2 हिस्सों में डालें, ताकि पौधा इसे बेहतर तरीके से सोख सके और सिंचाई या बारिश से कम बहे। मिट्टी नम हो पर पानी भरा न हो तब डालें, और भारी बारिश से ठीक पहले न डालें। यूरिया के साथ थोड़ी मात्रा में पोटाश डालने से बाद में तने मजबूत होते हैं।",
        ],
        "or": [
            "ଆପଣଙ୍କ ଫସଲ ବର୍ତ୍ତମାନ ସମ୍ଭବତଃ ଟିଲରିଂ ଅବସ୍ଥାରେ ଅଛି, ଯାହା ୟୁରିଆ ଟପ୍-ଡ୍ରେସିଂ ପାଇଁ ସଠିକ ସମୟ — ଏହାକୁ ଗୋଟିଏ ବଡ ମାତ୍ରା ପରିବର୍ତ୍ତେ 2 ଭାଗରେ ଦିଅନ୍ତୁ, ଯାହା ଦ୍ୱାରା ଗଛ ଏହାକୁ ଭଲ ଭାବରେ ଶୋଷିପାରିବ। ମାଟି ଓଦା ଥିଲେ ପର ପାଣି ଭରା ନ ଥିଲେ ଦିଅନ୍ତୁ, ଏବଂ ଭାରି ବର୍ଷା ପୂର୍ବରୁ ଦିଅନ୍ତୁ ନାହିଁ। ୟୁରିଆ ସହିତ ଅଳ୍ପ ପୋଟାସ ଦେଲେ ପରେ ଡାଳ ଦୃଢ ହୁଏ।",
        ],
    },
    "pest": {
        "en": [
            "For early pest or disease detection, check the undersides of leaves and the base of the plant every few days — that's where most infestations start before they're visible from a distance. If you spot yellowing, spots, or holes, try to identify it early using the Pest Detection tool in AgroStar so treatment can be targeted rather than blanket spraying. Avoid excess nitrogen fertilizer, as lush soft growth actually attracts more pests like aphids and plant hoppers.",
        ],
        "hi": [
            "कीट या रोग की जल्दी पहचान के लिए हर कुछ दिनों में पत्तियों के नीचे और पौधे के तने के पास जांच करें — ज़्यादातर संक्रमण वहीं से शुरू होता है, दूर से दिखने से पहले। पीलापन, धब्बे या छेद दिखें तो AgroStar के Pest Detection टूल से जल्दी पहचान करें ताकि इलाज सही जगह पर हो, पूरे खेत में छिड़काव न करना पड़े। ज़्यादा नाइट्रोजन खाद से बचें, क्योंकि ज़्यादा नरम बढ़वार से एफिड और प्लांट हॉपर जैसे कीट ज़्यादा आते हैं।",
        ],
        "or": [
            "କୀଟ କିମ୍ବା ରୋଗର ଶୀଘ୍ର ଚିହ୍ନଟ ପାଇଁ ପ୍ରତି କିଛି ଦିନରେ ପତ୍ରର ତଳ ଏବଂ ଗଛର ମୂଳ ପାଖ ଯାଞ୍ଚ କରନ୍ତୁ — ଅଧିକାଂଶ ସଂକ୍ରମଣ ସେଠାରୁ ଆରମ୍ଭ ହୁଏ। ହଳଦିଆ ଦାଗ, ଛିଦ୍ର ଦେଖିଲେ AgroStar ର Pest Detection ଉପକରଣ ବ୍ୟବହାର କରି ଶୀଘ୍ର ଚିହ୍ନଟ କରନ୍ତୁ, ଯାହା ଫଳରେ ଚିକିତ୍ସା ସଠିକ୍ ସ୍ଥାନରେ ହେବ। ଅଧିକ ନାଇଟ୍ରୋଜେନ ସାରରୁ ଦୂରେଇ ରୁହନ୍ତୁ।",
        ],
    },
    "market": {
        "en": [
            "Mandi prices change daily based on arrivals and demand, so it's worth checking the Market Prices feature in AgroStar before you decide to sell — prices can vary noticeably even between nearby mandis. If a price is trending down, and your produce can wait a few days without spoiling, it's often better to hold rather than sell into a falling market. For perishables though, don't wait too long — a small price drop is usually better than losing the produce to spoilage.",
        ],
        "hi": [
            "मंडी भाव आवक और मांग के हिसाब से हर दिन बदलते हैं, इसलिए बेचने से पहले AgroStar के Market Prices फीचर में जांच लें — पास की मंडियों में भी भाव अलग हो सकते हैं। अगर भाव गिर रहा है और आपकी फसल कुछ दिन रुक सकती है बिना खराब हुए, तो गिरते बाज़ार में बेचने के बजाय रुकना बेहतर हो सकता है। लेकिन जल्दी खराब होने वाली फसल के लिए ज़्यादा इंतज़ार न करें।",
        ],
        "or": [
            "ମଣ୍ଡି ଦର ଆଗମନ ଏବଂ ଚାହିଦା ଅନୁସାରେ ପ୍ରତିଦିନ ବଦଳେ, ତେଣୁ ବିକ୍ରୟ ପୂର୍ବରୁ AgroStar ର Market Prices ଫିଚର୍ ଯାଞ୍ଚ କରନ୍ତୁ — ନିକଟସ୍ଥ ମଣ୍ଡିରେ ମଧ୍ୟ ଦର ଭିନ୍ନ ହୋଇପାରେ। ଦର କମୁଥିଲେ ଏବଂ ଆପଣଙ୍କ ଫସଲ କିଛି ଦିନ ରହିପାରିଲେ, ପଡୁଥିବା ବଜାରରେ ବିକ୍ରୟ ପରିବର୍ତ୍ତେ ଅପେକ୍ଷା କରିବା ଭଲ। ମାତ୍ର ଶୀଘ୍ର ନଷ୍ଟ ହେଉଥିବା ଫସଲ ପାଇଁ ଅଧିକ ଅପେକ୍ଷା କରନ୍ତୁ ନାହିଁ।",
        ],
    },
    "irrigation": {
        "en": [
            "Water in the early morning or evening rather than midday — you'll lose much less to evaporation and the crop absorbs it more efficiently. Check soil moisture by pushing a finger 5-6 cm into the soil near the root zone; if it's dry at that depth, it's time to irrigate. During flowering and grain-filling stages, crops are most sensitive to water stress, so try not to skip irrigation during those windows even if the schedule is tight.",
        ],
        "hi": [
            "दोपहर के बजाय सुबह जल्दी या शाम को सिंचाई करें — इससे पानी कम भाप बनकर उड़ेगा और फसल इसे बेहतर तरीके से सोखेगी। मिट्टी की नमी जांचने के लिए जड़ों के पास 5-6 सेमी गहराई में उंगली डालकर देखें; अगर वहां सूखा लगे तो सिंचाई का समय है। फूल आने और दाना भरने की अवस्था में फसल पानी की कमी के प्रति सबसे संवेदनशील होती है, इसलिए उस समय सिंचाई न छोड़ें।",
        ],
        "or": [
            "ମଧ୍ୟାହ୍ନ ପରିବର୍ତ୍ତେ ସକାଳ କିମ୍ବା ସନ୍ଧ୍ୟାରେ ଜଳସେଚନ କରନ୍ତୁ — ଏହା ଦ୍ୱାରା ପାଣି କମ୍ ବାଷ୍ପ ହେବ ଏବଂ ଫସଲ ଏହାକୁ ଭଲ ଭାବରେ ଶୋଷିବ। ମୂଳ ପାଖରେ 5-6 ସେମି ଗଭୀରରେ ଆଙ୍ଗୁଠି ଦେଇ ମାଟିର ଆର୍ଦ୍ରତା ଯାଞ୍ଚ କରନ୍ତୁ। ଫୁଲ ଏବଂ ଦାନା ପୂରଣ ଅବସ୍ଥାରେ ଫସଲ ପାଣି ଅଭାବ ପ୍ରତି ସବୁଠାରୁ ସମ୍ବେଦନଶୀଳ, ତେଣୁ ସେହି ସମୟରେ ଜଳସେଚନ ଛାଡନ୍ତୁ ନାହିଁ।",
        ],
    },
    "growth": {
        "en": [
            "Crop growth generally moves through sowing, vegetative growth, tillering, flowering, grain-filling, and finally harvest — each stage has different water and nutrient needs. You can check your crop's current stage and what to do next in the Growth Monitoring feature in AgroStar. Keeping a close eye during the flowering and grain-filling stages is especially important, since stress during these windows affects final yield the most.",
        ],
        "hi": [
            "फसल की बढ़वार आमतौर पर बुवाई, वानस्पतिक वृद्धि, कल्ले निकलना, फूल आना, दाना भरना और अंत में कटाई से होकर गुज़रती है — हर अवस्था में पानी और पोषक तत्वों की ज़रूरत अलग होती है। अपनी फसल की मौजूदा अवस्था और आगे क्या करना है, ये AgroStar के Growth Monitoring फीचर में देख सकते हैं। फूल आने और दाना भरने की अवस्था में खास ध्यान दें, क्योंकि इसी समय की परेशानी उपज पर सबसे ज़्यादा असर डालती है।",
        ],
        "or": [
            "ଫସଲ ବୃଦ୍ଧି ସାଧାରଣତଃ ବୁଣା, ବାନସ୍ପତିକ ବୃଦ୍ଧି, ଟିଲରିଂ, ଫୁଲ, ଦାନା ପୂରଣ ଏବଂ ଶେଷରେ ଅମଳ ଦେଇ ଗତି କରେ — ପ୍ରତି ଅବସ୍ଥାରେ ପାଣି ଏବଂ ପୋଷକ ତତ୍ତ୍ୱର ଆବଶ୍ୟକତା ଭିନ୍ନ। ଆପଣଙ୍କ ଫସଲର ବର୍ତ୍ତମାନ ଅବସ୍ଥା AgroStar ର Growth Monitoring ଫିଚର୍ ରେ ଦେଖିପାରିବେ। ଫୁଲ ଏବଂ ଦାନା ପୂରଣ ଅବସ୍ଥାରେ ବିଶେଷ ଧ୍ୟାନ ଦିଅନ୍ତୁ।",
        ],
    },
    "scheme": {
        "en": [
            "There are several government schemes worth checking: PM-KISAN gives ₹6,000/year direct to your bank account, PMFBY (crop insurance) covers you against weather and pest losses at a very low premium, and the Kisan Credit Card gives you short-term credit at a subsidised 4% interest rate. Check your eligibility for each in the Government Schemes feature in AgroStar — most just need land records and a bank account linked to Aadhaar.",
        ],
        "hi": [
            "जांचने लायक कई सरकारी योजनाएं हैं: PM-KISAN से ₹6,000/साल सीधे बैंक खाते में आते हैं, PMFBY (फसल बीमा) से बहुत कम प्रीमियम पर मौसम और कीट से होने वाले नुकसान का बीमा होता है, और किसान क्रेडिट कार्ड से 4% सब्सिडी वाले ब्याज पर अल्पकालिक ऋण मिलता है। AgroStar के Government Schemes फीचर में अपनी पात्रता जांच लें — ज़्यादातर के लिए बस भूमि रिकॉर्ड और आधार से जुड़ा बैंक खाता चाहिए।",
        ],
        "or": [
            "ଯାଞ୍ଚ କରିବା ଯୋଗ୍ୟ ଅନେକ ସରକାରୀ ଯୋଜନା ଅଛି: PM-KISAN ରୁ ₹6,000/ବର୍ଷ ସିଧାସଳଖ ବ୍ୟାଙ୍କ ଖାତାକୁ ଆସେ, PMFBY (ଫସଲ ବୀମା) ଦ୍ୱାରା ଅତି କମ୍ ପ୍ରିମିୟମରେ ପାଣିପାଗ ଏବଂ କୀଟ କ୍ଷତି ବୀମା ହୁଏ, ଏବଂ କିଷାନ କ୍ରେଡିଟ୍ କାର୍ଡ ଦ୍ୱାରା 4% ସବସିଡି ସୁଧରେ ଋଣ ମିଳେ। AgroStar ର Government Schemes ଫିଚର୍ ରେ ଆପଣଙ୍କ ଯୋଗ୍ୟତା ଯାଞ୍ଚ କରନ୍ତୁ।",
        ],
    },
    "greeting": {
        "en": [
            "Namaste! I'm here to help with anything about your farm — weather forecasts, soil health, fertilizer timing, pest and disease identification, market prices, or government schemes. Just ask in your own words, in English, Hindi, or Odia, and I'll give you a clear, practical answer.",
        ],
        "hi": [
            "नमस्ते! मैं आपकी खेती से जुड़ी किसी भी बात में मदद के लिए यहां हूं — मौसम पूर्वानुमान, मिट्टी की सेहत, खाद का समय, कीट-रोग पहचान, बाज़ार भाव, या सरकारी योजनाएं। बस अपनी भाषा में पूछें, मैं साफ़ और व्यावहारिक जवाब दूंगा।",
        ],
        "or": [
            "ନମସ୍କାର! ମୁଁ ଆପଣଙ୍କ ଚାଷ ସମ୍ବନ୍ଧୀୟ ଯେକୌଣସି ବିଷୟରେ ସାହାଯ୍ୟ ପାଇଁ ଏଠାରେ ଅଛି — ପାଣିପାଗ, ମାଟି ସ୍ୱାସ୍ଥ୍ୟ, ସାର ସମୟ, କୀଟ-ରୋଗ ଚିହ୍ନଟ, ବଜାର ଦର, କିମ୍ବା ସରକାରୀ ଯୋଜନା। ନିଜ ଭାଷାରେ ପଚାରନ୍ତୁ।",
        ],
    },
    "fallback": {
        "en": [
            "I didn't quite catch that, but I can help with quite a lot on the farming side — try asking me something specific like \"how's the weather this week\", \"when should I add fertilizer\", \"my leaves have spots, what should I do\", \"what's the mandi price for rice\", or \"am I eligible for PM-KISAN\". The more specific your question, the more useful an answer I can give you.",
        ],
        "hi": [
            "मुझे ठीक से समझ नहीं आया, लेकिन मैं खेती से जुड़ी कई बातों में मदद कर सकता हूँ — कुछ ऐसा पूछ के देखें जैसे \"इस हफ्ते मौसम कैसा रहेगा\", \"खाद कब डालूं\", \"मेरी पत्तियों पर धब्बे हैं, क्या करूं\", \"चावल का मंडी भाव क्या है\", या \"क्या मैं PM-KISAN के लिए पात्र हूं\"। जितना सटीक सवाल होगा, उतना ही उपयोगी जवाब मिलेगा।",
        ],
        "or": [
            "ମୁଁ ଠିକ୍ ଭାବରେ ବୁଝିପାରିଲି ନାହିଁ, କିନ୍ତୁ ମୁଁ ଚାଷ ସମ୍ବନ୍ଧୀୟ ଅନେକ ବିଷୟରେ ସାହାଯ୍ୟ କରିପାରିବି — ଏପରି କିଛି ପଚାରନ୍ତୁ ଯେପରି \"ଏହି ସପ୍ତାହ ପାଣିପାଗ କେମିତି ରହିବ\", \"ସାର କେବେ ଦେବି\", \"ମୋର ପତ୍ରରେ ଦାଗ ଅଛି, କଣ କରିବି\", କିମ୍ବା \"ମୁଁ PM-KISAN ପାଇଁ ଯୋଗ୍ୟ କି\"। ଯେତେ ନିର୍ଦ୍ଦିଷ୍ଟ ପ୍ରଶ୍ନ, ସେତେ ଉପଯୋଗୀ ଉତ୍ତର।",
        ],
    },
}


def call_gpt(message, lang):
    """Calls OpenAI-compatible chat completions endpoint (GPT-5)."""
    lang_name = {"en": "English", "hi": "Hindi", "or": "Odia"}.get(lang, "English")
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "gpt-5",
            "messages": [
                {"role": "system", "content": AASHA_SYSTEM_PROMPT},
                {"role": "user", "content": f"Reply in {lang_name}. Farmer's question: {message}"},
            ],
            "max_tokens": 350,
        },
        timeout=12,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def call_gemini(message, lang):
    """Calls Google Gemini generateContent endpoint."""
    lang_name = {"en": "English", "hi": "Hindi", "or": "Odia"}.get(lang, "English")
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
        json={
            "contents": [{
                "parts": [{"text": f"{AASHA_SYSTEM_PROMPT}\n\nReply in {lang_name}. Farmer's question: {message}"}]
            }]
        },
        timeout=12,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


# ---------------------------------------------------------------------------
# SMS GATEWAY CONFIG — for real OTP delivery. Set ONE of these provider's
# env vars before running:
#   Twilio:  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
#   MSG91:   MSG91_AUTH_KEY  (India-focused, usually cheaper/faster for
#            Indian numbers; MSG91_SENDER_ID is optional, defaults below)
# If neither is set, /api/send_otp automatically falls back to demo mode
# (the OTP is shown directly in the UI instead of being texted) — so the
# app always works for a demo even without a paid SMS account.
# ---------------------------------------------------------------------------
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER")  # e.g. "+15551234567"

MSG91_AUTH_KEY = os.environ.get("MSG91_AUTH_KEY")
MSG91_SENDER_ID = os.environ.get("MSG91_SENDER_ID", "AGROSTAR")


def send_sms_via_twilio(phone, message):
    """Sends an SMS via Twilio's REST API. Docs: https://www.twilio.com/docs/sms/send-messages"""
    to_number = phone if phone.startswith("+") else f"+91{phone}"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    resp = requests.post(
        url,
        data={"From": TWILIO_FROM_NUMBER, "To": to_number, "Body": message},
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        timeout=10,
    )
    resp.raise_for_status()
    return True


def send_sms_via_msg91(phone, otp):
    """Sends an OTP SMS via MSG91's dedicated OTP API (India-focused).
    Docs: https://docs.msg91.com/p/tf9GTBn10/e/kfCoFGiIL1/MSG91 — verify the
    exact endpoint/params against current MSG91 docs before going live, as
    SMS gateway APIs occasionally change their parameter names."""
    resp = requests.post(
        "https://control.msg91.com/api/v5/otp",
        params={
            "otp": otp,
            "mobile": f"91{phone}",
            "authkey": MSG91_AUTH_KEY,
            "sender": MSG91_SENDER_ID,
        },
        timeout=10,
    )
    resp.raise_for_status()
    return True


def send_otp_sms(phone, otp):
    """Tries a real SMS gateway if one is configured; returns True if a real
    SMS was actually sent, False otherwise (caller should fall back to demo
    mode and show the OTP directly in the UI)."""
    message = f"Your AgroStar login OTP is {otp}. Valid for {OTP_VALID_SECONDS // 60} minutes. Do not share this code."
    try:
        if MSG91_AUTH_KEY:
            return send_sms_via_msg91(phone, otp)
        if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER:
            return send_sms_via_twilio(phone, message)
    except Exception as e:
        app.logger.warning(f"SMS send failed, falling back to demo mode: {e}")
    return False


# ---------------------------------------------------------------------------
# PAGE ROUTES — the 5 default pages + feature detail pages
# ---------------------------------------------------------------------------

@app.route("/")
def welcome():
    return render_template("welcome.html", firebase_config=FIREBASE_CONFIG)


def login_required(view_func):
    """Gate a page behind Log In / Sign Up / Continue as Guest — any of
    the three sets session['user'], so this only blocks someone who
    hasn't gone through any of them yet (e.g. typing /features straight
    into the address bar)."""
    from functools import wraps

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("welcome"))
        return view_func(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# AUTH — Signup + Password login + OTP login. Users stored in-memory for the
# demo (USERS dict, keyed by phone). In production swap this for a real
# database (e.g. Supabase) with hashed passwords (use werkzeug.security's
# generate_password_hash / check_password_hash — plaintext here is ONLY for
# demo simplicity).
# ---------------------------------------------------------------------------
USERS = {}          # phone -> {"name": ..., "password": ...}
OTP_STORE = {}       # phone -> {"otp": "1234", "expires": datetime}
OTP_VALID_SECONDS = 120


@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    password = data.get("password", "")

    if not name or not phone or len(phone) < 10:
        return jsonify({"status": "error", "message": "Enter a valid name and 10-digit phone number"}), 400
    if len(password) < 4:
        return jsonify({"status": "error", "message": "Password must be at least 4 characters"}), 400
    if phone in USERS:
        return jsonify({"status": "error", "message": "This number is already registered — try logging in instead"}), 400

    USERS[phone] = {"name": name, "password": password}
    session["user"] = {"name": name, "phone": phone, "guest": False}
    return jsonify({"status": "ok", "redirect": "/dashboard"})


@app.route("/api/login_password", methods=["POST"])
def login_password():
    data = request.get_json(force=True, silent=True) or {}
    phone = data.get("phone", "").strip()
    password = data.get("password", "")

    user = USERS.get(phone)
    if not user or user["password"] != password:
        return jsonify({"status": "error", "message": "Incorrect phone number or password"}), 401

    session["user"] = {"name": user["name"], "phone": phone, "guest": False}
    return jsonify({"status": "ok", "redirect": "/dashboard"})


@app.route("/api/send_otp", methods=["POST"])
def send_otp():
    """Generates a one-time code and sends it via a real SMS gateway if one
    is configured (MSG91 or Twilio — see the SMS GATEWAY CONFIG section
    above). If no gateway is configured, or the send fails for any reason
    (bad credentials, no internet, etc.), it automatically falls back to
    demo mode: the OTP is returned directly in the response and the
    frontend shows it in a "Demo Mode" note — so login always works for a
    demo even without a paid SMS account."""
    data = request.get_json(force=True, silent=True) or {}
    phone = data.get("phone", "").strip()
    if not phone or len(phone) < 10:
        return jsonify({"status": "error", "message": "Enter a valid 10-digit number"}), 400

    otp = f"{random.randint(1000, 9999)}"
    OTP_STORE[phone] = {"otp": otp, "expires": datetime.now() + timedelta(seconds=OTP_VALID_SECONDS)}

    sms_sent = send_otp_sms(phone, otp)

    # New phone numbers get auto-registered on first successful OTP login
    # (common pattern for OTP-first farmer apps — no separate signup needed).
    response = {
        "status": "sent",
        "message": f"OTP sent to {phone}" if sms_sent else f"OTP generated for {phone} (demo mode — no SMS gateway configured)",
        "demo_mode": not sms_sent,
        "expires_in": OTP_VALID_SECONDS,
    }
    if not sms_sent:
        response["otp_demo"] = otp  # only exposed when no real SMS gateway is configured

    return jsonify(response)


@app.route("/api/verify_otp", methods=["POST"])
def verify_otp():
    data = request.get_json(force=True, silent=True) or {}
    phone = data.get("phone", "").strip()
    otp = data.get("otp", "").strip()

    record = OTP_STORE.get(phone)
    if not record:
        return jsonify({"status": "error", "message": "Request a new OTP first"}), 400
    if datetime.now() > record["expires"]:
        del OTP_STORE[phone]
        return jsonify({"status": "error", "message": "OTP expired — request a new one"}), 400
    if otp != record["otp"]:
        return jsonify({"status": "error", "message": "Incorrect OTP"}), 401

    del OTP_STORE[phone]  # one-time use
    user = USERS.get(phone)
    name = user["name"] if user else "Farmer"
    if not user:
        USERS[phone] = {"name": name, "password": None}  # OTP-only account

    session["user"] = {"name": name, "phone": phone, "guest": False}
    return jsonify({"status": "ok", "redirect": "/dashboard"})


@app.route("/api/login_google", methods=["POST"])
def login_google():
    """Verifies the Firebase ID token the browser got back from Google Sign-In
    (see the Firebase JS SDK call in welcome.html), then logs the user in the
    same way password/OTP login does — no separate "Google user" type needed.
    Verification is done against Google's public keys (via the google-auth
    package), so a forged/expired token is rejected server-side; we never
    trust anything the browser sends without checking it first."""
    if not FIREBASE_CONFIG.get("projectId"):
        return jsonify({"status": "error", "message": "Google Sign-In is not configured on this server yet"}), 503

    data = request.get_json(force=True, silent=True) or {}
    token = data.get("idToken", "")
    if not token:
        return jsonify({"status": "error", "message": "Missing Google sign-in token"}), 400

    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_auth_requests
        claims = google_id_token.verify_firebase_token(
            token, google_auth_requests.Request(), audience=FIREBASE_CONFIG["projectId"]
        )
        if not claims:
            raise ValueError("empty claims")
    except Exception:
        return jsonify({"status": "error", "message": "Google sign-in verification failed — try again"}), 401

    email = (claims.get("email") or "").strip()
    name = claims.get("name") or (email.split("@")[0] if email else "Farmer")
    uid = claims.get("user_id") or claims.get("sub") or email
    key = email or uid  # USERS dict is keyed by phone elsewhere; Google users key by email/uid instead

    if key not in USERS:
        USERS[key] = {"name": name, "password": None, "google": True}

    session["user"] = {"name": name, "phone": key, "guest": False, "auth": "google", "picture": claims.get("picture", "")}
    return jsonify({"status": "ok", "redirect": "/dashboard"})


@app.route("/guest")
def guest():
    session["user"] = {"name": "Guest", "guest": True}
    return jsonify({"status": "ok", "redirect": "/dashboard"})


@app.route("/features")
@login_required
def features():
    return render_template("features.html")


@app.route("/chatbot")
@login_required
def chatbot():
    return render_template("chatbot.html")


@app.route("/dashboard")
@login_required
def dashboard():
    user = session.get("user", {"name": "Farmer", "guest": True})
    return render_template("dashboard.html", user=user)


@app.route("/profile")
@login_required
def profile():
    user = session.get("user", {"name": "Farmer", "guest": True})
    return render_template("profile.html", user=user)


FEATURE_PAGES = {
    "yield-prediction": "feature_yield.html",
    "soil-health": "feature_soil.html",
    "cover-crops": "feature_cover_crops.html",
    "weather": "feature_weather.html",
    "crop-recommendation": "feature_crop_reco.html",
    "best-crops-for-you": "feature_best_crops.html",
    "fertilizer-plan": "feature_fertilizer.html",
    "growth-monitoring": "feature_growth.html",
    "pest-detection": "feature_pest.html",
    "reminders": "feature_reminders.html",
    "climate-adaptation": "feature_climate.html",
    "market-prices": "feature_market.html",
    "satellite-health": "feature_satellite.html",
    "govt-schemes": "feature_schemes.html",
    "insurance-loans": "feature_insurance.html",
    "community": "feature_community.html",
    "sustainability": "feature_sustainability.html",
    "rainfall-history": "feature_rainfall.html",
    "games": "feature_games.html",
    "games-quiz": "feature_games_quiz.html",
    "games-memory": "feature_games_memory.html",
    "pest-sentinel-network": "feature_pest_sentinel.html",
    "postharvest-quality-grading": "feature_postharvest_quality.html",
    "autonomous-weeding-robot": "feature_autonomous_weeder.html",
    "farm-ledger": "feature_ledger.html",
    "buyer-connect": "feature_buyer_connect.html",
    "irrigation-scheduler": "feature_irrigation.html",
    "groundwater-tracker": "feature_groundwater.html",
    "equipment-rental": "feature_equipment_rental.html",
    "agri-shop-prices": "feature_agri_shop_prices.html",
    "disease-alerts": "feature_disease_alerts.html",
    "video-tutorials": "feature_video_tutorials.html",
    "pashu-health": "feature_pashu_health.html",
    "cold-storage": "feature_cold_storage.html",
    "group-selling": "feature_group_selling.html",
    "budget-planner": "feature_budget_planner.html",
    "notification-alerts": "feature_alerts.html",
    "farmer-reels": "feature_reels.html",
    "leaderboard": "feature_leaderboard.html",
    "referral": "feature_referral.html",
    "labour-marketplace": "feature_labour.html",
    "nearest-mandi": "feature_nearest_mandi.html",
    "kvk-expert-booking": "feature_kvk_booking.html",
    "vet-finder": "feature_vet_finder.html",
    "seed-varieties": "feature_seed_varieties.html",
    "sowing-calendar": "feature_sowing_calendar.html",
    "crop-rotation": "feature_crop_rotation.html",
    "qr-traceability": "feature_qr_traceability.html",
    "spray-advisory": "feature_spray_advisory.html",
    "farm-diary": "feature_farm_diary.html",
    "yield-benchmark": "feature_yield_benchmark.html",
    "govt-camps": "feature_govt_camps.html",
    "water-quality": "feature_water_quality.html",
    "solar-pump-scheme": "feature_solar_pump.html",
    "storage-loss-calculator": "feature_storage_loss.html",
    "fpo-registration-guide": "feature_fpo_guide.html",
    "my-fields": "feature_my_fields.html",
    "enam-info": "feature_enam_info.html",
}


@app.route("/feature/<slug>")
@login_required
def feature_page(slug):
    template = FEATURE_PAGES.get(slug)
    if not template:
        return "Feature not found", 404
    return render_template(template)


# ---------------------------------------------------------------------------
# API ROUTES — one per feature, mocked JSON for demo/testing
# ---------------------------------------------------------------------------

@app.route("/api/predict_yield", methods=["POST"])
def predict_yield():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "Rice")
    try:
        area = float(data.get("area", 1))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Please enter a valid area"}), 400
    base_yield = {"Rice": 2.8, "Wheat": 3.2, "Maize": 2.5, "Sugarcane": 65, "Groundnut": 1.8, "Cotton": 1.5}.get(crop, 2.5)
    variance = random.uniform(0.85, 1.15)
    predicted = round(base_yield * area * variance, 2)
    return jsonify({
        "crop": crop,
        "area_hectare": area,
        "predicted_yield_tonnes": predicted,
        "confidence": f"{random.randint(78, 94)}%",
        "tips": [
            f"Optimal sowing window for {crop} in your region is closing in 12 days.",
            "Apply potash before flowering stage to boost grain filling.",
            "Current soil moisture supports yield above regional average.",
        ],
        "factors": [
            {"name": "Historical yield (this field, last 3 seasons)", "weight": "High"},
            {"name": "Soil moisture & NPK levels", "weight": "High"},
            {"name": "7-day weather forecast match", "weight": "Medium"},
            {"name": "Regional average for similar fields", "weight": "Medium"},
        ],
    })


@app.route("/api/yield_history", methods=["GET"])
def yield_history():
    """Past 5 seasons' yield for this crop on this field, to give the
    forecast context — a single number means less without a trend line."""
    crop = request.args.get("crop", "Rice")
    base_yield = {"Rice": 2.8, "Wheat": 3.2, "Maize": 2.5, "Sugarcane": 65, "Groundnut": 1.8, "Cotton": 1.5}.get(crop, 2.5)
    seasons = []
    year = datetime.now().year
    for i in range(5, 0, -1):
        seasons.append({
            "season": f"{year - i}",
            "yield_tonnes": round(base_yield * random.uniform(0.82, 1.1), 2),
        })
    return jsonify({"crop": crop, "history": seasons})


@app.route("/api/soil_health", methods=["POST"])
def soil_health():
    data = request.get_json(force=True, silent=True) or {}
    ph = round(random.uniform(5.8, 7.5), 1)
    n_level = random.choice(["Low", "Medium", "High"])
    p_level = random.choice(["Low", "Medium", "High"])
    k_level = random.choice(["Low", "Medium", "High"])
    oc = round(random.uniform(0.3, 0.9), 2)
    moisture = round(random.uniform(12, 28), 1)
    ec = round(random.uniform(0.2, 1.4), 2)
    zinc = random.choice(["Deficient", "Adequate", "High"])
    sulphur = random.choice(["Low", "Medium", "High"])
    # Simple overall score from the metrics (demo)
    score = 55
    if n_level == "Medium": score += 8
    if n_level == "High": score += 12
    if p_level == "Medium": score += 6
    if p_level == "High": score += 10
    if k_level == "Medium": score += 6
    if k_level == "High": score += 10
    if 6.0 <= ph <= 7.2: score += 8
    if oc >= 0.5: score += 7
    if zinc == "Adequate": score += 4
    score = min(score, 95)

    recommendations = [
        "Add 2 tonnes/acre of well-rotted farmyard manure before next sowing.",
        f"Nitrogen is {n_level.lower()} — split urea application into 3 doses instead of 1." if n_level != "High" else "Nitrogen is adequate — maintain current urea schedule.",
        "Lime application recommended if pH stays below 6.0 next season." if ph < 6.2 else "pH is in a good range — no lime needed this season.",
    ]
    if zinc == "Deficient":
        recommendations.append("Zinc is deficient — apply Zinc Sulphate 10–15 kg/acre or foliar spray.")
    if sulphur == "Low":
        recommendations.append("Sulphur is low — use Single Super Phosphate (SSP) or add elemental sulphur.")

    # Crop rotation benefits — practical suggestions for Indian conditions
    crop_rotation = [
        {
            "sequence": "Rice → Pulse (Moong / Urad) → Wheat",
            "benefit": "Pulse fixes atmospheric nitrogen, reduces urea need by 20–30 kg/acre for the next cereal, and breaks pest cycles.",
        },
        {
            "sequence": "Maize → Groundnut / Soybean → Wheat",
            "benefit": "Legume improves soil structure and organic matter; groundnut adds residual N and oilseed income.",
        },
        {
            "sequence": "Cotton → Chickpea (Gram) → Sorghum / Pearl Millet",
            "benefit": "Chickpea residual nitrogen benefits the next crop; deep-rooted pulse improves subsoil porosity and reduces nematode build-up.",
        },
        {
            "sequence": "Sugarcane → Green-manure (Dhaincha / Sunhemp) → Rice / Vegetables",
            "benefit": "Green manure adds 40–60 kg N/ha equivalent and organic carbon, recovering soil after a long-duration crop.",
        },
    ]

    return jsonify({
        "location": data.get("location", "Your field"),
        "ph": ph,
        "nitrogen": n_level,
        "phosphorus": p_level,
        "potassium": k_level,
        "organic_carbon": f"{oc}%",
        "moisture": f"{moisture}%",
        "ec": f"{ec} dS/m",
        "zinc": zinc,
        "sulphur": sulphur,
        "soil_health_score": score,
        "recommendations": recommendations,
        "crop_rotation": crop_rotation,
    })


@app.route("/api/cover_crops", methods=["POST"])
def cover_crops():
    """Cover Crop Selection Guide with soil-health improvement analysis
    and detailed cost breakdown table."""
    data = request.get_json(force=True, silent=True) or {}
    main_crop = data.get("main_crop", "Rice")
    season = data.get("season", "Rabi")
    goal = data.get("goal", "nitrogen")
    try:
        area = float(data.get("area", 1))  # acres
    except (TypeError, ValueError):
        area = 1.0

    # soil_improve: expected change after one season of cover crop (demo values)
    # cost breakdown keys are ₹/acre
    catalogue = [
        {
            "name": "Dhaincha (Sesbania)",
            "type": "Legume green manure",
            "duration": "45–60 days",
            "seasons": ["Kharif", "Zaid"],
            "goals": ["nitrogen", "organic", "erosion"],
            "after": ["Rice", "Wheat", "Sugarcane", "Fallow", "Vegetables"],
            "why": "Fast-growing legume that can fix 60–80 kg N/ha. Excellent before rice or after sugarcane harvest.",
            "sowing": "Broadcast 20–25 kg seed/acre; light irrigation if dry",
            "incorporation": "Plough in at 40–50 days (before flowering) for maximum N",
            "base_match": 92,
            "costs_per_acre": {
                "seed": 350, "seed_qty": "20–25 kg",
                "land_prep": 300,
                "sowing_labour": 250,
                "irrigation": 200,
                "incorporation_labour": 400,
                "misc": 100,
            },
            "n_value_saved": 1200,
            "fodder_income": 0,
            "grain_income": 0,
            "soil_improve": {
                "organic_carbon": "+0.15 to +0.25%",
                "nitrogen": "+15 to +25 kg/acre residual N",
                "structure": "Better aggregation, less crusting",
                "erosion": "Moderate reduction (fast canopy)",
                "microbes": "Higher microbial activity after incorporation",
                "score_boost": 8,
            },
        },
        {
            "name": "Sunhemp (Crotalaria)",
            "type": "Legume green manure",
            "duration": "50–70 days",
            "seasons": ["Kharif", "Zaid"],
            "goals": ["nitrogen", "organic", "pest"],
            "after": ["Rice", "Cotton", "Maize", "Fallow", "Vegetables"],
            "why": "Fixes nitrogen, suppresses root-knot nematodes and improves soil structure.",
            "sowing": "Broadcast 12–15 kg seed/acre",
            "incorporation": "Incorporate at 50–60 days when plants are 1–1.5 m tall",
            "base_match": 90,
            "costs_per_acre": {
                "seed": 400, "seed_qty": "12–15 kg",
                "land_prep": 250,
                "sowing_labour": 200,
                "irrigation": 150,
                "incorporation_labour": 400,
                "misc": 100,
            },
            "n_value_saved": 1100,
            "fodder_income": 0,
            "grain_income": 0,
            "soil_improve": {
                "organic_carbon": "+0.12 to +0.22%",
                "nitrogen": "+12 to +20 kg/acre residual N",
                "structure": "Deep roots loosen subsoil",
                "erosion": "Good canopy cover",
                "microbes": "Nematode suppression + better biology",
                "score_boost": 9,
            },
        },
        {
            "name": "Berseem (Egyptian clover)",
            "type": "Legume fodder + cover",
            "duration": "Full Rabi (multi-cut)",
            "seasons": ["Rabi"],
            "goals": ["nitrogen", "fodder", "organic", "moisture"],
            "after": ["Rice", "Maize", "Cotton", "Sugarcane", "Fallow"],
            "why": "Best Rabi legume for N-fixation and high-quality fodder (5–6 cuts). Leaves soil richer for next cereal.",
            "sowing": "20–25 kg seed/acre, broadcast with light irrigation",
            "incorporation": "Last cut can be left as green manure or fully harvested as fodder",
            "base_match": 94,
            "costs_per_acre": {
                "seed": 900, "seed_qty": "20–25 kg",
                "land_prep": 400,
                "sowing_labour": 300,
                "irrigation": 800,
                "incorporation_labour": 300,
                "misc": 200,
            },
            "n_value_saved": 1000,
            "fodder_income": 8000,
            "grain_income": 0,
            "soil_improve": {
                "organic_carbon": "+0.20 to +0.35%",
                "nitrogen": "+20 to +30 kg/acre residual N",
                "structure": "Excellent for tilth after rice",
                "erosion": "Full-season winter cover",
                "microbes": "Strong rhizosphere activity",
                "score_boost": 12,
            },
        },
        {
            "name": "Moong (Green gram)",
            "type": "Short-duration pulse",
            "duration": "55–65 days",
            "seasons": ["Zaid", "Kharif"],
            "goals": ["nitrogen", "organic"],
            "after": ["Wheat", "Potato", "Vegetables", "Fallow"],
            "why": "Fits perfectly between wheat harvest and next rice/maize. Residual N + extra income from grain.",
            "sowing": "8–10 kg seed/acre, row or broadcast",
            "incorporation": "Harvest pods; residual biomass ploughed in",
            "base_match": 88,
            "costs_per_acre": {
                "seed": 600, "seed_qty": "8–10 kg",
                "land_prep": 350,
                "sowing_labour": 300,
                "irrigation": 400,
                "incorporation_labour": 250,
                "misc": 200,
            },
            "n_value_saved": 800,
            "fodder_income": 0,
            "grain_income": 5500,
            "soil_improve": {
                "organic_carbon": "+0.08 to +0.15%",
                "nitrogen": "+10 to +18 kg/acre residual N",
                "structure": "Light improvement from root residues",
                "erosion": "Moderate (short duration)",
                "microbes": "N-fixing nodules active",
                "score_boost": 6,
            },
        },
        {
            "name": "Horse gram (Kulthi)",
            "type": "Drought-tolerant legume",
            "duration": "90–120 days",
            "seasons": ["Kharif", "Rabi"],
            "goals": ["nitrogen", "erosion", "moisture"],
            "after": ["Maize", "Cotton", "Fallow", "Groundnut"],
            "why": "Grows on poor soils with little water; good for erosion control on sloping or light soils.",
            "sowing": "10–12 kg seed/acre",
            "incorporation": "Use as dual-purpose (grain + residual biomass)",
            "base_match": 82,
            "costs_per_acre": {
                "seed": 250, "seed_qty": "10–12 kg",
                "land_prep": 200,
                "sowing_labour": 200,
                "irrigation": 100,
                "incorporation_labour": 250,
                "misc": 100,
            },
            "n_value_saved": 600,
            "fodder_income": 0,
            "grain_income": 3000,
            "soil_improve": {
                "organic_carbon": "+0.10 to +0.18%",
                "nitrogen": "+8 to +15 kg/acre residual N",
                "structure": "Good on light / sloping soils",
                "erosion": "Strong cover on poor land",
                "microbes": "Supports biology in low-fertility soils",
                "score_boost": 7,
            },
        },
        {
            "name": "Mustard (as catch / biofumigant)",
            "type": "Brassica cover",
            "duration": "60–90 days",
            "seasons": ["Rabi"],
            "goals": ["pest", "organic", "erosion"],
            "after": ["Rice", "Maize", "Cotton", "Vegetables", "Fallow"],
            "why": "Biofumigation effect reduces soil-borne fungi and nematodes; also yields oilseed income.",
            "sowing": "2–3 kg seed/acre",
            "incorporation": "Can harvest for seed or plough at flowering for biofumigation",
            "base_match": 80,
            "costs_per_acre": {
                "seed": 200, "seed_qty": "2–3 kg",
                "land_prep": 300,
                "sowing_labour": 200,
                "irrigation": 300,
                "incorporation_labour": 300,
                "misc": 150,
            },
            "n_value_saved": 200,
            "fodder_income": 0,
            "grain_income": 7000,
            "soil_improve": {
                "organic_carbon": "+0.05 to +0.12%",
                "nitrogen": "Minimal direct N (non-legume)",
                "structure": "Moderate residue contribution",
                "erosion": "Good winter cover",
                "microbes": "Biofumigation reduces pathogens",
                "score_boost": 5,
            },
        },
        {
            "name": "Cowpea (Lobia)",
            "type": "Legume cover / fodder",
            "duration": "60–80 days",
            "seasons": ["Kharif", "Zaid"],
            "goals": ["nitrogen", "fodder", "erosion", "organic"],
            "after": ["Wheat", "Maize", "Vegetables", "Fallow"],
            "why": "Quick canopy for erosion control, fixes N, and provides excellent green fodder.",
            "sowing": "10–12 kg seed/acre",
            "incorporation": "Cut for fodder or plough in at flowering",
            "base_match": 86,
            "costs_per_acre": {
                "seed": 450, "seed_qty": "10–12 kg",
                "land_prep": 250,
                "sowing_labour": 250,
                "irrigation": 250,
                "incorporation_labour": 300,
                "misc": 150,
            },
            "n_value_saved": 900,
            "fodder_income": 4000,
            "grain_income": 0,
            "soil_improve": {
                "organic_carbon": "+0.12 to +0.20%",
                "nitrogen": "+12 to +20 kg/acre residual N",
                "structure": "Fast canopy protects surface",
                "erosion": "Very good short-term cover",
                "microbes": "Active N-fixation",
                "score_boost": 8,
            },
        },
        {
            "name": "Oats (Jai)",
            "type": "Cereal cover / fodder",
            "duration": "Full Rabi",
            "seasons": ["Rabi"],
            "goals": ["organic", "fodder", "erosion", "moisture"],
            "after": ["Rice", "Maize", "Cotton", "Sugarcane", "Fallow"],
            "why": "Builds organic matter, protects soil over winter, and gives high-quality fodder cuts.",
            "sowing": "30–40 kg seed/acre",
            "incorporation": "Multiple cuts for fodder; last residue ploughed in",
            "base_match": 78,
            "costs_per_acre": {
                "seed": 700, "seed_qty": "30–40 kg",
                "land_prep": 350,
                "sowing_labour": 300,
                "irrigation": 600,
                "incorporation_labour": 350,
                "misc": 200,
            },
            "n_value_saved": 150,
            "fodder_income": 9000,
            "grain_income": 0,
            "soil_improve": {
                "organic_carbon": "+0.18 to +0.30%",
                "nitrogen": "Minimal (cereal); uses residual N",
                "structure": "High biomass improves tilth",
                "erosion": "Excellent full-season cover",
                "microbes": "Supports soil biology via residue",
                "score_boost": 7,
            },
        },
        {
            "name": "Cluster bean (Guar)",
            "type": "Drought legume",
            "duration": "90–110 days",
            "seasons": ["Kharif", "Zaid"],
            "goals": ["nitrogen", "organic", "moisture"],
            "after": ["Wheat", "Cotton", "Fallow", "Groundnut"],
            "why": "Suited to light / sandy soils; residual N and gum value; low water need.",
            "sowing": "6–8 kg seed/acre",
            "incorporation": "Harvest pods; residual biomass returned to soil",
            "base_match": 75,
            "costs_per_acre": {
                "seed": 350, "seed_qty": "6–8 kg",
                "land_prep": 200,
                "sowing_labour": 200,
                "irrigation": 100,
                "incorporation_labour": 250,
                "misc": 100,
            },
            "n_value_saved": 700,
            "fodder_income": 0,
            "grain_income": 4500,
            "soil_improve": {
                "organic_carbon": "+0.10 to +0.18%",
                "nitrogen": "+10 to +16 kg/acre residual N",
                "structure": "Good for sandy soils",
                "erosion": "Moderate cover",
                "microbes": "N-fixation under low water",
                "score_boost": 6,
            },
        },
        {
            "name": "Stylo / Stylosanthes",
            "type": "Perennial forage legume",
            "duration": "Perennial / long-term",
            "seasons": ["Kharif"],
            "goals": ["erosion", "organic", "fodder", "nitrogen"],
            "after": ["Fallow", "Cotton", "Maize"],
            "why": "Excellent for degraded or sloping land; permanent cover + fodder + N fixation.",
            "sowing": "2–3 kg seed/acre, broadcast or in lines",
            "incorporation": "Grazed or cut; permanent stand preferred",
            "base_match": 70,
            "costs_per_acre": {
                "seed": 500, "seed_qty": "2–3 kg",
                "land_prep": 300,
                "sowing_labour": 250,
                "irrigation": 100,
                "incorporation_labour": 150,
                "misc": 150,
            },
            "n_value_saved": 800,
            "fodder_income": 5000,
            "grain_income": 0,
            "soil_improve": {
                "organic_carbon": "+0.25 to +0.40% (year 1–2)",
                "nitrogen": "+15 to +25 kg/acre/year residual N",
                "structure": "Best for degraded / sloping land",
                "erosion": "Excellent permanent cover",
                "microbes": "Long-term biology recovery",
                "score_boost": 14,
            },
        },
    ]

    scored = []
    for c in catalogue:
        match = c["base_match"]
        if season in c["seasons"]:
            match += 8
        else:
            match -= 25
        if goal in c["goals"]:
            match += 10
        if main_crop in c["after"] or main_crop == "Fallow":
            match += 6
        match = max(40, min(99, match))

        cpa = c["costs_per_acre"]
        rows = [
            {"item": "Seed", "detail": cpa.get("seed_qty", ""), "per_acre": cpa["seed"], "total": round(cpa["seed"] * area)},
            {"item": "Land preparation", "detail": "Plough / harrow", "per_acre": cpa["land_prep"], "total": round(cpa["land_prep"] * area)},
            {"item": "Sowing labour", "detail": "Broadcast / line sowing", "per_acre": cpa["sowing_labour"], "total": round(cpa["sowing_labour"] * area)},
            {"item": "Irrigation", "detail": "If required", "per_acre": cpa["irrigation"], "total": round(cpa["irrigation"] * area)},
            {"item": "Incorporation / cutting labour", "detail": "Plough-in or harvest", "per_acre": cpa["incorporation_labour"], "total": round(cpa["incorporation_labour"] * area)},
            {"item": "Misc (tools, transport)", "detail": "", "per_acre": cpa["misc"], "total": round(cpa["misc"] * area)},
        ]
        total_cost = sum(r["total"] for r in rows)
        per_acre_total = sum(r["per_acre"] for r in rows)

        n_saved = round(c.get("n_value_saved", 0) * area)
        fodder = round(c.get("fodder_income", 0) * area)
        grain = round(c.get("grain_income", 0) * area)
        total_benefit = n_saved + fodder + grain
        net = total_benefit - total_cost

        si = c.get("soil_improve", {})
        item = {
            "name": c["name"],
            "type": c["type"],
            "duration": c["duration"],
            "why": c["why"],
            "sowing": c["sowing"],
            "incorporation": c["incorporation"],
            "match": match,
            "cost_table": rows,
            "cost": {
                "total": total_cost,
                "per_acre": per_acre_total,
            },
            "benefit": {
                "n_value_saved": n_saved,
                "fodder_income": fodder,
                "grain_income": grain,
                "total": total_benefit,
            },
            "net_return": net,
            "soil_improve": si,
        }
        scored.append(item)

    scored.sort(key=lambda x: x["match"], reverse=True)
    top = scored[:4]

    tips = [
        "Sow cover crops soon after main-crop harvest so they establish before the dry spell.",
        "For green-manure legumes, incorporate (plough in) just before flowering — that is when nitrogen content is highest.",
        "Never let cover crops set mature seed if they can become weeds in the next main crop.",
        "Combine a legume + a cereal/brassica cover when possible for better biomass and pest control.",
        "On sloping or light soils, prioritise fast-canopy crops (cowpea, sunhemp, stylo) to stop erosion.",
        "Cost figures are approximate Indian averages (₹/acre); local seed and labour rates will vary.",
        "Soil health score boost is an estimated gain on the 0–100 soil health scale after one season.",
    ]

    return jsonify({
        "main_crop": main_crop,
        "season": season,
        "goal": goal,
        "area_acres": area,
        "covers": top,
        "tips": tips,
        "currency": "INR",
    })


# WMO weather interpretation codes → emoji (Open-Meteo)
_WMO_WEATHER_ICONS = {
    0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌧️",
    56: "🌧️", 57: "🌧️",
    61: "🌧️", 63: "🌧️", 65: "🌧️",
    66: "🌧️", 67: "🌧️",
    71: "🌨️", 73: "🌨️", 75: "🌨️", 77: "🌨️",
    80: "🌦️", 81: "🌧️", 82: "⛈️",
    85: "🌨️", 86: "🌨️",
    95: "⛈️", 96: "⛈️", 99: "⛈️",
}


def _weather_icon(code):
    try:
        return _WMO_WEATHER_ICONS.get(int(code), "⛅")
    except (TypeError, ValueError):
        return "⛅"


def _fetch_open_meteo(lat, lon):
    """Live 7-day forecast from Open-Meteo (no API key required)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
        "timezone": "Asia/Kolkata",
        "forecast_days": 7,
    }
    r = requests.get(url, params=params, timeout=8)
    r.raise_for_status()
    return r.json()


def _mock_weather_forecast():
    """Fallback when Open-Meteo is unreachable (offline / no internet)."""
    icons = ["☀️", "⛅", "🌦️", "🌧️", "⛈️", "☁️", "☀️"]
    days = []
    for i in range(7):
        d = datetime.now() + timedelta(days=i)
        days.append({
            "day": d.strftime("%a"),
            "date": d.strftime("%d %b"),
            "icon": icons[i],
            "temp_max": random.randint(28, 38),
            "temp_min": random.randint(18, 26),
            "rain_chance": random.randint(0, 90),
            "rain_mm": round(random.uniform(0, 12), 1),
        })
    return days, None


@app.route("/api/weather", methods=["GET"])
def weather():
    """7-day forecast powered by Open-Meteo real-time data.

    Uses LOCATION_COORDS for the selected place. Falls back to mocked
    numbers only if the external API call fails (no internet, timeout).
    """
    location = request.args.get("location", "Bhubaneswar (City Centre)")
    lang = request.args.get("lang", "en")

    lat, lon = LOCATION_COORDS.get(location, (20.2961, 85.8245))
    source = "open-meteo"
    current = {}
    days = []

    try:
        data = _fetch_open_meteo(lat, lon)
        cur = data.get("current") or {}
        current = {
            "temp": cur.get("temperature_2m"),
            "humidity": cur.get("relative_humidity_2m"),
            "precipitation": cur.get("precipitation"),
            "wind_speed": cur.get("wind_speed_10m"),
            "weather_code": cur.get("weather_code"),
            "icon": _weather_icon(cur.get("weather_code")),
        }
        daily = data.get("daily") or {}
        times = daily.get("time") or []
        for i, date_str in enumerate(times):
            try:
                d = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                d = datetime.now() + timedelta(days=i)
            code = (daily.get("weather_code") or [None])[i] if i < len(daily.get("weather_code") or []) else None
            tmax = (daily.get("temperature_2m_max") or [None])[i] if i < len(daily.get("temperature_2m_max") or []) else None
            tmin = (daily.get("temperature_2m_min") or [None])[i] if i < len(daily.get("temperature_2m_min") or []) else None
            rain_p = (daily.get("precipitation_probability_max") or [0])[i] if i < len(daily.get("precipitation_probability_max") or []) else 0
            rain_mm = (daily.get("precipitation_sum") or [0])[i] if i < len(daily.get("precipitation_sum") or []) else 0
            days.append({
                "day": d.strftime("%a"),
                "date": d.strftime("%d %b"),
                "icon": _weather_icon(code),
                "temp_max": round(tmax) if tmax is not None else None,
                "temp_min": round(tmin) if tmin is not None else None,
                "rain_chance": int(rain_p or 0),
                "rain_mm": round(float(rain_mm or 0), 1),
            })
    except Exception as e:
        app.logger.warning(f"Open-Meteo failed ({e}); using mock forecast")
        days, current = _mock_weather_forecast()[0], {}
        source = "mock"

    # Dynamic irrigation advice from real rain probabilities
    rain_soon = any((d.get("rain_chance") or 0) > 60 for d in days[:3])
    if lang == "hi":
        irrigation_advice = (
            "अगले 2–3 दिन बारिश की अच्छी संभावना है — सिंचाई रोक दें या कम रखें।"
            if rain_soon else
            "अगले 3 दिन में खास बारिश नहीं दिख रही — आज शाम हल्की सिंचाई कर सकते हैं।"
        )
    elif lang == "or":
        irrigation_advice = (
            "ପରବର୍ତ୍ତୀ ୨–୩ ଦିନ ବର୍ଷାର ସମ୍ଭାବନା ଅଛି — ଜଳସେଚନ କମ୍ କରନ୍ତୁ କିମ୍ବା ବନ୍ଦ ରଖନ୍ତୁ।"
            if rain_soon else
            "ପରବର୍ତ୍ତୀ ୩ ଦିନ ବିଶେଷ ବର୍ଷା ନାହିଁ — ଆଜି ସନ୍ଧ୍ୟାରେ ହାଲୁକା ଜଳସେଚନ କରିପାରିବେ।"
        )
    else:
        irrigation_advice = (
            "Rain likely in the next 2–3 days — skip or reduce irrigation."
            if rain_soon else
            "No significant rain expected for 3 days — light irrigation this evening is fine."
        )

    return jsonify({
        "location": location,
        "lat": lat,
        "lon": lon,
        "source": source,
        "current": current,
        "forecast": days,
        "irrigation_advice": irrigation_advice,
    })


@app.route("/api/crop_recommendation", methods=["POST"])
def crop_recommendation():
    data = request.get_json(force=True, silent=True) or {}
    soil_type = data.get("soil_type", "Loamy")
    climate_zone = data.get("climate_zone", "Humid subtropical")

    candidates = SOIL_CROP_CANDIDATES.get(soil_type, SOIL_CROP_CANDIDATES["Loamy"])
    scored = []
    for crop, base, reason in candidates:
        bonus = CROP_CLIMATE_FIT.get(crop, {}).get(climate_zone, 0)
        score = max(60, min(97, base + bonus))
        scored.append((crop, score, reason))
    scored.sort(key=lambda c: c[1], reverse=True)

    recs = [
        {"crop": crop, "suitability": f"{score}%", "reason": reason}
        for crop, score, reason in scored[:3]
    ]

    return jsonify({
        "soil_type": soil_type,
        "climate_zone": climate_zone,
        "recommendations": recs,
        "factors": [
            {"name": "Soil type & drainage compatibility", "weight": "High"},
            {"name": "Local climate zone match", "weight": "High"},
            {"name": "Crop rotation history (nitrogen balance)", "weight": "Medium"},
            {"name": "Regional market demand trend", "weight": "Low"},
        ],
    })


@app.route("/api/fertilizer_plan", methods=["POST"])
def fertilizer_plan():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "Rice")

    plans = {
        "Rice": [
            {"stage": "Sowing / Basal", "days": "Day 0", "fertilizer": "DAP 50kg/acre + Zinc Sulphate 10kg/acre"},
            {"stage": "Tillering", "days": "Day 20-25", "fertilizer": "Urea 40kg/acre"},
            {"stage": "Panicle Initiation", "days": "Day 45-50", "fertilizer": "Urea 30kg/acre + MOP 20kg/acre"},
            {"stage": "Flowering", "days": "Day 65-70", "fertilizer": "Foliar spray — 2% DAP solution"},
        ],
        "Wheat": [
            {"stage": "Sowing / Basal", "days": "Day 0", "fertilizer": "DAP 60kg/acre + Potash 20kg/acre"},
            {"stage": "Crown Root Initiation", "days": "Day 20-25", "fertilizer": "Urea 35kg/acre"},
            {"stage": "Jointing", "days": "Day 40-45", "fertilizer": "Urea 25kg/acre"},
            {"stage": "Grain Filling", "days": "Day 70-75", "fertilizer": "Foliar spray — 2% Urea solution"},
        ],
        "Maize": [
            {"stage": "Sowing / Basal", "days": "Day 0", "fertilizer": "DAP 45kg/acre + Zinc Sulphate 8kg/acre"},
            {"stage": "Knee-High Stage", "days": "Day 25-30", "fertilizer": "Urea 30kg/acre"},
            {"stage": "Tasseling", "days": "Day 50-55", "fertilizer": "Urea 25kg/acre + MOP 15kg/acre"},
            {"stage": "Silking", "days": "Day 65-70", "fertilizer": "Foliar spray — 2% DAP solution"},
        ],
        "Sugarcane": [
            {"stage": "Planting / Basal", "days": "Day 0", "fertilizer": "DAP 80kg/acre + MOP 40kg/acre + Zinc 10kg/acre"},
            {"stage": "Tillering", "days": "Day 45-60", "fertilizer": "Urea 50kg/acre"},
            {"stage": "Grand Growth", "days": "Day 90-120", "fertilizer": "Urea 40kg/acre + MOP 30kg/acre"},
            {"stage": "Maturity", "days": "Day 240-270", "fertilizer": "Foliar spray — 2% Potassium nitrate"},
        ],
        "Groundnut": [
            {"stage": "Sowing / Basal", "days": "Day 0", "fertilizer": "DAP 40kg/acre + Gypsum 100kg/acre"},
            {"stage": "Flowering", "days": "Day 25-30", "fertilizer": "Urea 20kg/acre + Boron foliar"},
            {"stage": "Pegging", "days": "Day 40-45", "fertilizer": "Gypsum 150kg/acre"},
            {"stage": "Pod Filling", "days": "Day 60-70", "fertilizer": "Foliar spray — 2% DAP + Calcium"},
        ],
        "Cotton": [
            {"stage": "Sowing / Basal", "days": "Day 0", "fertilizer": "DAP 50kg/acre + MOP 25kg/acre"},
            {"stage": "Squaring", "days": "Day 35-40", "fertilizer": "Urea 30kg/acre"},
            {"stage": "Flowering / Boll formation", "days": "Day 55-65", "fertilizer": "Urea 25kg/acre + MOP 20kg/acre"},
            {"stage": "Boll development", "days": "Day 80-90", "fertilizer": "Foliar spray — 2% Urea + Micronutrients"},
        ],
    }
    reminders = {
        "Rice": "Urea top-dressing due in 4 days",
        "Wheat": "Urea top-dressing due in 3 days",
        "Maize": "Urea top-dressing due in 5 days",
        "Sugarcane": "Urea top-dressing due in 7 days",
        "Groundnut": "Gypsum application due in 6 days",
        "Cotton": "Urea top-dressing due in 5 days",
    }
    reminder_days = {"Rice": 4, "Wheat": 3, "Maize": 5, "Sugarcane": 7, "Groundnut": 6, "Cotton": 5}

    plan = plans.get(crop, plans["Rice"])
    next_reminder = reminders.get(crop, reminders["Rice"])
    next_reminder_days = reminder_days.get(crop, reminder_days["Rice"])
    return jsonify({
        "crop": crop,
        "plan": plan,
        "next_reminder": next_reminder,
        "next_reminder_days": next_reminder_days,
    })


@app.route("/api/growth_stage", methods=["GET"])
def growth_stage():
    stages = ["Germination", "Seedling", "Tillering", "Stem Elongation", "Flowering", "Maturity"]
    current = 2
    return jsonify({
        "crop": "Rice",
        "current_stage": stages[current],
        "stage_index": current,
        "stages": stages,
        "progress_percent": round((current + 1) / len(stages) * 100),
        "next_action": "Apply first urea top-dressing within 5 days — critical for tillering.",
        "next_action_days": 5,
    })


@app.route("/api/pest_detection", methods=["POST"])
def pest_detection():
    """Pest & disease diagnosis from a leaf/crop photo.

    Only accepts images that look like green vegetation or soil (Excess Green
    Index + soil band). Screenshots, documents, faces, random objects, and
    other-coloured photos are rejected as invalid.
    """
    file = request.files.get("image") or request.files.get("photo") or request.files.get("file")

    if not file or not file.filename:
        return jsonify({
            "valid": False,
            "issue": "No image",
            "confidence": "0%",
            "treatment": "",
            "message": "Please upload or capture a leaf / crop / soil photo first.",
            "message_hi": "पहले पत्ती / फसल / मिट्टी की तस्वीर अपलोड या कैप्चर करें।",
            "follow_up_days": 0,
        })

    # Validate it is crop-green or soil
    if file and file.filename:
        # Reset stream position in case it was read elsewhere
        try:
            file.stream.seek(0)
        except Exception:
            pass
        is_valid, ratio, reason = _is_crop_photo(file)
        if not is_valid:
            return jsonify({
                "valid": False,
                "issue": "Invalid image",
                "confidence": "0%",
                "treatment": "",
                "message": (
                    "Please upload a clear photo of a crop leaf, plant, or soil. "
                    "Screenshots, documents, faces, and other non-crop images are not accepted."
                ),
                "message_hi": (
                    "कृपया फसल की पत्ती, पौधे या मिट्टी की साफ तस्वीर अपलोड करें। "
                    "स्क्रीनशॉट, दस्तावेज़, चेहरा या अन्य रंगों वाली तस्वीरें स्वीकार नहीं की जातीं।"
                ),
                "green_ratio": ratio,
                "debug": reason,
                "follow_up_days": 0,
            })

    diagnoses = [
        {"issue": "Leaf Blast (Fungal)", "confidence": "89%",
         "treatment": "Spray Tricyclazole 75% WP @ 0.6g/litre water, repeat after 10 days.",
         "follow_up_days": 10},
        {"issue": "Brown Plant Hopper", "confidence": "83%",
         "treatment": "Apply Imidacloprid 17.8% SL @ 0.3ml/litre, avoid excess nitrogen.",
         "follow_up_days": 7},
        {"issue": "Powdery Mildew (Fungal)", "confidence": "86%",
         "treatment": "Spray Sulphur 80% WP @ 2.5g/litre water in the evening; repeat after 12–15 days.",
         "follow_up_days": 14},
        {"issue": "Aphid Infestation", "confidence": "84%",
         "treatment": "Spray Neem oil (Azadirachtin 1500 ppm) @ 5ml/litre on undersides of leaves every 7 days.",
         "follow_up_days": 7},
    ]
    result = random.choice(diagnoses)
    result["valid"] = True
    result["message"] = "Diagnosis based on leaf/crop photo."
    return jsonify(result)


def _is_crop_photo(file_storage, accept_ripe_produce=False):
    """Lightweight heuristic image check (not a full ML model).

    Uses the Excess Green Index (ExG = 2G - R - B) for live vegetation,
    plus a tight brown/soil band for bare-field shots. `accept_ripe_produce`
    additionally allows a strict "ripe red/orange produce" band — needed
    for Quality Grading, since a ripe tomato is red, not green, and a plain
    ExG check would wrongly reject a genuine tomato photo.

    Falls back to 'valid' if Pillow isn't installed or the file can't be
    read, so the demo never hard-fails.
    """
    if not PIL_AVAILABLE:
        # Without Pillow we cannot verify crop vs document — reject so
        # non-crop images never get a false diagnosis in demo.
        return False, 0.0, "pillow_not_installed"
    try:
        img = Image.open(file_storage.stream).convert("RGB")
        img.thumbnail((150, 150))
        pixels = list(img.getdata())
        if not pixels:
            return True, 1.0, "empty_image"

        green_hits = 0
        soil_hits = 0
        produce_hits = 0
        for r, g, b in pixels:
            # Excess Green Index — live vegetation (leaves/canopy) scores
            # clearly positive here. Requiring g > b + 8 on top of the ExG
            # threshold specifically excludes teal/cyan/blue tones (fabric,
            # sky, water, denim) that can otherwise score a positive ExG
            # just from G being elevated alongside B — a real false-accept
            # this closes (teal ≈ (40,90,110) used to pass; it no longer does).
            exg = (2 * g) - r - b
            if exg > 18 and g > b + 8:
                green_hits += 1
                continue
            # Tight bare-soil band: brown, low saturation, mid-low brightness,
            # with a real (not tiny) gap between R, G and B — narrower than
            # typical skin tone so faces/hands don't get counted as soil.
            if (70 <= r <= 150 and 45 <= g <= 110 and b < g < r
                    and (r - g) >= 18 and (g - b) >= 8 and (r - b) <= 90):
                soil_hits += 1
                continue
            # Ripe red/orange produce band (tomato, chilli, ripe fruit) —
            # only used when accept_ripe_produce=True. Requires a LARGE gap
            # between R and both G and B, which genuine skin tones don't
            # have (skin keeps G and B closer to R) — this is what stops a
            # hand/face in frame from being counted as "ripe produce".
            if accept_ripe_produce and r >= 120 and (r - g) >= 55 and (r - b) >= 55:
                produce_hits += 1

        total = len(pixels)
        green_ratio = green_hits / total
        soil_ratio = soil_hits / total
        produce_ratio = produce_hits / total

        # Document / screenshot detector: many near-white or near-gray pixels
        # (text pages, UI captures, slides) — reject even if a few green UI
        # pixels leak through.
        white_gray = 0
        for r, g, b in pixels:
            brightness = (r + g + b) / 3
            sat = max(r, g, b) - min(r, g, b)
            if brightness >= 200 and sat <= 30:
                white_gray += 1
            elif sat <= 25 and 40 <= brightness <= 200:
                white_gray += 1
        white_gray_ratio = white_gray / total

        # Flat/near-uniform images rarely have real field texture
        r_vals = [p[0] for p in pixels]
        variance_ok = (max(r_vals) - min(r_vals)) > 12

        # Reject document-like images first
        if white_gray_ratio >= 0.45:
            return False, round(max(green_ratio, soil_ratio, produce_ratio), 2), "document_or_screenshot"

        is_valid = (
            green_ratio >= 0.15
            or (soil_ratio >= 0.30 and variance_ok)
            or (accept_ripe_produce and produce_ratio >= 0.15)
        )
        return is_valid, round(max(green_ratio, soil_ratio, produce_ratio), 2), "ok"
    except Exception as e:
        # On read error, reject rather than silently accepting non-crop photos
        return False, 0.0, f"check_error: {e}"


@app.route("/api/pest_sentinel_analyze", methods=["POST"])
def pest_sentinel_analyze():
    """Sentinel Network demo endpoint: farmer uploads a crop photo →
    validates it's actually a crop/leaf photo → returns pesticide,
    water, and temperature guidance. In production the validity check
    and diagnosis both run through a trained edge-AI vision model;
    here validity uses a lightweight colour heuristic and the
    diagnosis is mocked realistic sample data.
    """
    photo = request.files.get("photo")
    if not photo or photo.filename == "":
        return jsonify({"valid": False, "message": "No photo received. Please upload a crop/leaf photo."}), 400

    is_valid, green_ratio, debug_reason = _is_crop_photo(photo)
    if not is_valid:
        return jsonify({
            "valid": False,
            "message": "This doesn't look like a crop/leaf photo. Please upload a clear photo of the affected plant, leaf, or field.",
            "green_ratio": green_ratio,
            "debug": debug_reason,
        })

    diagnoses = [
        {
            "issue": "Leaf Blast (Fungal)", "confidence": "91%",
            "pesticide": "Tricyclazole 75% WP",
            "dosage": "0.6 g per litre of water, spray every 10 days until symptoms clear",
            "water": "25–30 litres of spray solution per acre; irrigate field lightly a day before spraying (avoid waterlogging)",
            "temperature": "Best sprayed when air temperature is 20–28°C — avoid spraying in peak afternoon heat above 32°C",
            "follow_up_days": 10,
        },
        {
            "issue": "Brown Plant Hopper", "confidence": "86%",
            "pesticide": "Imidacloprid 17.8% SL",
            "dosage": "0.3 ml per litre of water; avoid excess nitrogen fertilizer which attracts hoppers",
            "water": "Maintain shallow standing water (2–3 cm); drain field for 2–3 days if hopper count is high",
            "temperature": "Hopper activity peaks in humid, 25–30°C conditions — inspect base of plants closely in this range",
            "follow_up_days": 7,
        },
        {
            "issue": "Powdery Mildew (Fungal)", "confidence": "88%",
            "pesticide": "Sulphur 80% WP (wettable sulphur)",
            "dosage": "2.5 g per litre of water, spray in the evening, repeat after 12–15 days",
            "water": "20–25 litres of spray solution per acre; keep foliage dry between waterings to limit fungal spread",
            "temperature": "Avoid spraying sulphur above 30°C (can scorch leaves) — best applied when temperature is below 28°C",
            "follow_up_days": 14,
        },
        {
            "issue": "Aphid Infestation", "confidence": "84%",
            "pesticide": "Neem oil (Azadirachtin 1500 ppm) — biological/low-toxicity option",
            "dosage": "5 ml per litre of water, spray on undersides of leaves every 7 days",
            "water": "15–20 litres of spray solution per acre; light irrigation, avoid over-watering which encourages soft new growth aphids prefer",
            "temperature": "Spray early morning or evening, ideally below 30°C, to protect beneficial insects and improve neem oil effectiveness",
            "follow_up_days": 7,
        },
    ]
    result = random.choice(diagnoses)
    result["valid"] = True
    result["green_ratio"] = green_ratio
    result["debug"] = debug_reason
    return jsonify(result)


CLIMATE_ADAPTATION_I18N = {
    "en": {
        "season_label": "Kharif",
        "tips": [
            "Switch to short-duration paddy varieties if monsoon onset is delayed.",
            "Mulch fields to retain soil moisture during dry spells.",
            "Build farm ponds to harvest excess monsoon runoff for later use.",
            "Intercrop with legumes to build soil resilience against erratic rainfall.",
        ],
    },
    "hi": {
        "season_label": "खरीफ",
        "tips": [
            "अगर मानसून देर से आए तो कम अवधि की धान किस्मों पर स्विच करें।",
            "सूखे के दौरान मिट्टी की नमी बनाए रखने के लिए खेतों में मल्चिंग करें।",
            "बाद में उपयोग के लिए अतिरिक्त मानसून पानी जमा करने हेतु खेत तालाब बनाएं।",
            "अनियमित बारिश से बचने के लिए मिट्टी की मजबूती बढ़ाने हेतु दलहन के साथ अंतरफसल करें।",
        ],
    },
    "or": {
        "season_label": "ଖରିଫ",
        "tips": [
            "ମୌସୁମୀ ବିଳମ୍ବ ହେଲେ କମ୍ ଅବଧିର ଧାନ ପ୍ରଜାତିକୁ ବଦଳାନ୍ତୁ।",
            "ଶୁଷ୍କ ଅବଧିରେ ମାଟିର ଆର୍ଦ୍ରତା ବଜାୟ ରଖିବାକୁ ଖେତରେ ମଲ୍ଚିଂ କରନ୍ତୁ।",
            "ପରବର୍ତ୍ତୀ ବ୍ୟବହାର ପାଇଁ ଅତିରିକ୍ତ ମୌସୁମୀ ପାଣି ସାଇତିବାକୁ ଖେତ ପୋଖରୀ ତିଆରି କରନ୍ତୁ।",
            "ଅନିୟମିତ ବର୍ଷା ବିରୁଦ୍ଧରେ ମାଟିର ସ୍ଥିରତା ବଢାଇବାକୁ ଡାଲି ଫସଲ ସହିତ ମିଶ୍ର ଚାଷ କରନ୍ତୁ।",
        ],
    },
}


@app.route("/api/climate_adaptation", methods=["GET"])
def climate_adaptation():
    lang = request.args.get("lang", "en")
    content = CLIMATE_ADAPTATION_I18N.get(lang, CLIMATE_ADAPTATION_I18N["en"])
    return jsonify({
        "season": content["season_label"],
        "tips": content["tips"],
    })


@app.route("/api/market_prices", methods=["GET"])
def market_prices():
    mandi = request.args.get("location", "Bhubaneswar (City Centre)")
    crops = ["Rice", "Wheat", "Maize", "Groundnut", "Soybean"]
    data = []
    for c in crops:
        base = random.randint(1800, 4500)
        data.append({
            "crop": c,
            "mandi": f"{mandi} Mandi",
            "price_per_quintal": base,
            "trend": random.choice(["up", "down", "stable"]),
            "change_percent": round(random.uniform(-8, 8), 1),
        })
    return jsonify({"location": mandi, "prices": data, "updated": datetime.now().strftime("%d %b %Y, %I:%M %p")})


@app.route("/api/chatbot", methods=["POST"])
def chatbot_api():
    data = request.get_json(force=True, silent=True) or {}
    raw_message = data.get("message", "")
    message = raw_message.lower()
    lang = data.get("lang", "en")

    # Try real AI providers first, fall back to rule-based responder on any
    # failure (missing key, no internet, rate limit) so the demo never breaks.
    if OPENAI_API_KEY:
        try:
            return jsonify({"reply": call_gpt(raw_message, lang), "lang": lang, "source": "gpt-5"})
        except Exception as e:
            app.logger.warning(f"GPT call failed, falling back: {e}")
    elif GEMINI_API_KEY:
        try:
            return jsonify({"reply": call_gemini(raw_message, lang), "lang": lang, "source": "gemini"})
        except Exception as e:
            app.logger.warning(f"Gemini call failed, falling back: {e}")

    # ---- Rule-based fallback (also the default for local/offline demo) ----
    topic = classify_aasha_intent(message)
    if topic is None:
        topic = "fallback"
    reply_options = AASHA_RESPONSES[topic].get(lang, AASHA_RESPONSES[topic]["en"])
    reply = random.choice(reply_options)

    return jsonify({"reply": reply, "lang": lang, "source": "rule-based", "topic": topic})


# ---------------------------------------------------------------------------
# NEW: Offline-first support — SMS fallback subscription
# ---------------------------------------------------------------------------

@app.route("/api/sms_subscribe", methods=["POST"])
def sms_subscribe():
    """Registers a farmer's phone number for SMS fallback alerts.
    In production this hooks into an SMS gateway (e.g. Twilio, MSG91) so that
    weather warnings, fertilizer reminders, and price alerts still reach the
    farmer over plain SMS when there's no mobile data — critical for many
    rural pockets with patchy 4G."""
    data = request.get_json(force=True, silent=True) or {}
    phone = data.get("phone", "")
    alerts = data.get("alerts", ["weather", "fertilizer"])
    if not phone or len(phone) < 10:
        return jsonify({"status": "error", "message": "Enter a valid 10-digit number"}), 400
    return jsonify({
        "status": "subscribed",
        "phone": phone,
        "alerts": alerts,
        "message": f"SMS alerts activated for {phone}. You'll get texts even without internet.",
    })


@app.route("/api/sync_status", methods=["GET"])
def sync_status():
    """Reports what's cached locally vs needs a fresh sync — powers the
    offline banner on the frontend."""
    return jsonify({
        "last_synced": (datetime.now() - timedelta(hours=random.randint(0, 5))).strftime("%d %b, %I:%M %p"),
        "cached_modules": ["weather", "market_prices", "fertilizer_plan", "growth_stage"],
    })


# ---------------------------------------------------------------------------
# NEW: Government scheme integration
# ---------------------------------------------------------------------------

GOVT_SCHEMES_I18N = {
    "en": {
        "pm-kisan": {"name": "PM-KISAN", "benefit": "₹6,000/year in 3 installments directly to bank account",
                     "eligibility": "All landholding farmer families"},
        "soil-health-card": {"name": "Soil Health Card Scheme",
                             "benefit": "Free soil testing every 2 years with nutrient-wise recommendations",
                             "eligibility": "All farmers with registered land records"},
        "pmfby": {"name": "PM Fasal Bima Yojana (PMFBY)",
                  "benefit": "Crop insurance premium as low as 1.5-2% of sum insured",
                  "eligibility": "Farmers growing notified crops in notified areas"},
        "kcc": {"name": "Kisan Credit Card (KCC)",
                "benefit": "Short-term credit up to ₹3 lakh at 4% subsidised interest",
                "eligibility": "Farmers, tenant farmers, and sharecroppers"},
        "note_eligible": "You appear eligible — apply via nearest CSC or agriculture office.",
        "note_pm_kisan_ineligible": "Land record required to apply.",
    },
    "hi": {
        "pm-kisan": {"name": "पीएम-किसान", "benefit": "₹6,000/वर्ष, 3 किश्तों में सीधे बैंक खाते में",
                     "eligibility": "सभी भूमिधारक किसान परिवार"},
        "soil-health-card": {"name": "मृदा स्वास्थ्य कार्ड योजना",
                             "benefit": "हर 2 साल में मुफ्त मिट्टी जांच, पोषक तत्व-वार सिफारिशों के साथ",
                             "eligibility": "पंजीकृत भूमि रिकॉर्ड वाले सभी किसान"},
        "pmfby": {"name": "पीएम फसल बीमा योजना (PMFBY)",
                  "benefit": "बीमित राशि का मात्र 1.5-2% फसल बीमा प्रीमियम",
                  "eligibility": "अधिसूचित क्षेत्रों में अधिसूचित फसल उगाने वाले किसान"},
        "kcc": {"name": "किसान क्रेडिट कार्ड (KCC)",
                "benefit": "4% सब्सिडी वाले ब्याज पर ₹3 लाख तक का अल्पकालिक ऋण",
                "eligibility": "किसान, बटाईदार किसान और काश्तकार"},
        "note_eligible": "आप पात्र दिख रहे हैं — नज़दीकी CSC या कृषि कार्यालय के माध्यम से आवेदन करें।",
        "note_pm_kisan_ineligible": "आवेदन के लिए भूमि रिकॉर्ड आवश्यक है।",
    },
    "or": {
        "pm-kisan": {"name": "ପିଏମ୍-କିଷାନ", "benefit": "₹6,000/ବର୍ଷ, 3 କିସ୍ତିରେ ସିଧାସଳଖ ବ୍ୟାଙ୍କ ଖାତାକୁ",
                     "eligibility": "ସମସ୍ତ ଜମି ମାଲିକ କୃଷକ ପରିବାର"},
        "soil-health-card": {"name": "ମୃତ୍ତିକା ସ୍ୱାସ୍ଥ୍ୟ କାର୍ଡ ଯୋଜନା",
                             "benefit": "ପ୍ରତି 2 ବର୍ଷରେ ମାଗଣା ମାଟି ପରୀକ୍ଷା, ପୋଷକ ତତ୍ତ୍ୱ-ଅନୁସାରେ ପରାମର୍ଶ ସହିତ",
                             "eligibility": "ପଞ୍ଜୀକୃତ ଜମି ରେକର୍ଡ ଥିବା ସମସ୍ତ କୃଷକ"},
        "pmfby": {"name": "ପିଏମ୍ ଫସଲ ବୀମା ଯୋଜନା (PMFBY)",
                  "benefit": "ବୀମିତ ରାଶିର ମାତ୍ର 1.5-2% ଫସଲ ବୀମା ପ୍ରିମିୟମ",
                  "eligibility": "ବିଜ୍ଞପ୍ତ ଅଞ୍ଚଳରେ ବିଜ୍ଞପ୍ତ ଫସଲ ଚାଷ କରୁଥିବା କୃଷକ"},
        "kcc": {"name": "କିଷାନ କ୍ରେଡିଟ୍ କାର୍ଡ (KCC)",
                "benefit": "4% ସବସିଡି ସୁଧରେ ₹3 ଲକ୍ଷ ପର୍ଯ୍ୟନ୍ତ ସ୍ୱଳ୍ପକାଳୀନ ଋଣ",
                "eligibility": "କୃଷକ, ବଟାଇଦାର କୃଷକ ଏବଂ ଭାଗଚାଷୀ"},
        "note_eligible": "ଆପଣ ଯୋଗ୍ୟ ଦେଖାଯାଉଛନ୍ତି — ନିକଟସ୍ଥ CSC କିମ୍ବା କୃଷି କାର୍ଯ୍ୟାଳୟ ମାଧ୍ୟମରେ ଆବେଦନ କରନ୍ତୁ।",
        "note_pm_kisan_ineligible": "ଆବେଦନ ପାଇଁ ଜମି ରେକର୍ଡ ଆବଶ୍ୟକ।",
    },
}

GOVT_SCHEMES = [
    {"id": "pm-kisan", "status_field": "Income support"},
    {"id": "soil-health-card", "status_field": "Soil testing"},
    {"id": "pmfby", "status_field": "Crop insurance"},
    {"id": "kcc", "status_field": "Credit access"},
]


def localized_schemes(lang):
    content = GOVT_SCHEMES_I18N.get(lang, GOVT_SCHEMES_I18N["en"])
    out = []
    for s in GOVT_SCHEMES:
        info = content[s["id"]]
        out.append({**s, **info})
    return out


@app.route("/api/govt_schemes", methods=["GET"])
def govt_schemes():
    lang = request.args.get("lang", "en")
    return jsonify({"schemes": localized_schemes(lang)})


@app.route("/api/scheme_eligibility", methods=["POST"])
def scheme_eligibility():
    data = request.get_json(force=True, silent=True) or {}
    try:
        land_size = float(data.get("land_size", 1))
    except (TypeError, ValueError):
        land_size = 1.0
    category = data.get("category", "General")
    lang = data.get("lang", "en")
    content = GOVT_SCHEMES_I18N.get(lang, GOVT_SCHEMES_I18N["en"])

    results = []
    for s in localized_schemes(lang):
        eligible = True
        note = content["note_eligible"]
        if s["id"] == "pm-kisan" and land_size <= 0:
            eligible = False
            note = content["note_pm_kisan_ineligible"]
        results.append({**s, "eligible": eligible, "note": note})

    return jsonify({"land_size": land_size, "category": category, "results": results})


# ---------------------------------------------------------------------------
# NEW: Satellite / NDVI crop health monitoring
# ---------------------------------------------------------------------------

@app.route("/api/satellite_health", methods=["GET"])
def satellite_health():
    """In production this calls Google Earth Engine's free-tier NDVI
    (Normalized Difference Vegetation Index) endpoint for the field's
    coordinates using Sentinel-2 imagery. Mocked here with a realistic
    zoned breakdown for the demo."""
    ndvi = round(random.uniform(0.35, 0.82), 2)
    health = "Excellent" if ndvi > 0.7 else "Good" if ndvi > 0.5 else "Stressed"
    zones = [
        {"zone": "North plot", "ndvi": round(random.uniform(0.4, 0.85), 2)},
        {"zone": "Central plot", "ndvi": round(random.uniform(0.4, 0.85), 2)},
        {"zone": "South plot", "ndvi": round(random.uniform(0.4, 0.85), 2)},
    ]
    return jsonify({
        "field_ndvi": ndvi,
        "health_status": health,
        "captured": datetime.now().strftime("%d %b %Y"),
        "source": "Sentinel-2 (Google Earth Engine)",
        "zones": zones,
        "insight": "Central plot shows lower vigour — check for waterlogging or nutrient deficiency."
        if any(z["ndvi"] < 0.5 for z in zones) else "All zones showing healthy canopy growth.",
    })


# ---------------------------------------------------------------------------
# NEW: Gamification — streaks & badges
# ---------------------------------------------------------------------------

FARMER_PROGRESS = {"streak_days": 12, "points": 340, "badges_earned": ["early-bird", "soil-saver", "water-wise"]}

ALL_BADGES = [
    {"id": "early-bird", "name": "Early Sower", "desc": "Sowed within the optimal window"},
    {"id": "soil-saver", "name": "Soil Saver", "desc": "Followed 3 soil health recommendations"},
    {"id": "water-wise", "name": "Water Wise", "desc": "Followed irrigation advice for 7 days"},
    {"id": "pest-guard", "name": "Pest Guard", "desc": "Caught and treated a pest issue early"},
    {"id": "green-streak", "name": "Green Streak", "desc": "Opened AgroStar 15 days in a row"},
]


@app.route("/api/gamification", methods=["GET"])
def gamification():
    badges = [{**b, "earned": b["id"] in FARMER_PROGRESS["badges_earned"]} for b in LEADERBOARD_BADGES]
    return jsonify({
        "streak_days": FARMER_PROGRESS["streak_days"],
        "points": FARMER_PROGRESS["points"],
        "badges": badges,
        "next_milestone": "5 more days for the 'Green Streak' badge",
    })


# ---------------------------------------------------------------------------
# NEW: Community / farmer forum
# ---------------------------------------------------------------------------

COMMUNITY_POSTS_I18N = {
    "en": [
        {"id": 1, "author": "Sunita Devi, Cuttack", "text": "Used the fertilizer plan for my paddy this season — yield went up almost 15%! Thank you AgroStar.",
         "likes": 24, "replies": 6, "tag": "Success Story"},
        {"id": 2, "author": "Manoj Sahoo, Puri", "text": "Anyone facing brown plant hopper in their rice this week? Found it early using the pest detection tool.",
         "likes": 11, "replies": 9, "tag": "Question"},
        {"id": 3, "author": "Ashok Patra, Balasore", "text": "Switched to short-duration paddy after the climate adaptation tip — saved my crop from the delayed monsoon.",
         "likes": 31, "replies": 4, "tag": "Success Story"},
    ],
    "hi": [
        {"id": 1, "author": "सुनीता देवी, कटक", "text": "इस सीज़न अपने धान के लिए खाद योजना का इस्तेमाल किया — उपज लगभग 15% बढ़ गई! धन्यवाद AgroStar।",
         "likes": 24, "replies": 6, "tag": "सफलता की कहानी"},
        {"id": 2, "author": "मनोज साहू, पुरी", "text": "क्या इस हफ्ते किसी के धान में ब्राउन प्लांट हॉपर आया है? कीट पहचान टूल से मुझे जल्दी पता चल गया।",
         "likes": 11, "replies": 9, "tag": "सवाल"},
        {"id": 3, "author": "अशोक पात्रा, बालासोर", "text": "जलवायु अनुकूलन सुझाव के बाद कम अवधि के धान पर स्विच किया — देर से आए मानसून से अपनी फसल बचा ली।",
         "likes": 31, "replies": 4, "tag": "सफलता की कहानी"},
    ],
    "or": [
        {"id": 1, "author": "ସୁନୀତା ଦେବୀ, କଟକ", "text": "ଏହି ଋତୁରେ ମୋର ଧାନ ପାଇଁ ସାର ଯୋଜନା ବ୍ୟବହାର କଲି — ଅମଳ ପ୍ରାୟ 15% ବଢିଗଲା! ଧନ୍ୟବାଦ AgroStar।",
         "likes": 24, "replies": 6, "tag": "ସଫଳତାର କାହାଣୀ"},
        {"id": 2, "author": "ମନୋଜ ସାହୁ, ପୁରୀ", "text": "ଏହି ସପ୍ତାହରେ କାହାର ଧାନରେ ବ୍ରାଉନ ପ୍ଲାଣ୍ଟ ହପର୍ ଆସିଛି କି? କୀଟ ଚିହ୍ନଟ ଉପକରଣ ମାଧ୍ୟମରେ ମୁଁ ଶୀଘ୍ର ଜାଣିଲି।",
         "likes": 11, "replies": 9, "tag": "ପ୍ରଶ୍ନ"},
        {"id": 3, "author": "ଅଶୋକ ପାତ୍ର, ବାଲେଶ୍ୱର", "text": "ଜଳବାୟୁ ଅନୁକୂଳନ ପରାମର୍ଶ ପରେ କମ୍ ଅବଧିର ଧାନକୁ ବଦଳାଇଲି — ବିଳମ୍ବିତ ମୌସୁମୀରୁ ମୋର ଫସଲ ବଞ୍ଚାଇଲି।",
         "likes": 31, "replies": 4, "tag": "ସଫଳତାର କାହାଣୀ"},
    ],
}

# User-submitted posts during this server run (kept separate from the
# seeded multilingual demo posts above so a new post doesn't need a
# translation into all three languages before it can show up).
COMMUNITY_USER_POSTS = []


@app.route("/api/community_posts", methods=["GET"])
def community_posts():
    lang = request.args.get("lang", "en")
    seeded = COMMUNITY_POSTS_I18N.get(lang, COMMUNITY_POSTS_I18N["en"])
    all_posts = seeded + COMMUNITY_USER_POSTS
    return jsonify({"posts": sorted(all_posts, key=lambda p: -p["id"])})


@app.route("/api/community_post", methods=["POST"])
def add_community_post():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"status": "error", "message": "Post cannot be empty"}), 400
    all_ids = [p["id"] for p in COMMUNITY_POSTS_I18N["en"]] + [p["id"] for p in COMMUNITY_USER_POSTS]
    new_post = {
        "id": max(all_ids) + 1,
        "author": data.get("author", "You"),
        "text": text,
        "likes": 0,
        "replies": 0,
        "tag": data.get("tag", "Discussion"),
    }
    COMMUNITY_USER_POSTS.append(new_post)
    return jsonify({"status": "posted", "post": new_post})


# ---------------------------------------------------------------------------
# NEW: Insurance & loan eligibility checker
# ---------------------------------------------------------------------------

@app.route("/api/insurance_check", methods=["POST"])
def insurance_check():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "Rice")
    try:
        area = float(data.get("area", 1))
    except (TypeError, ValueError):
        area = 1.0
    sum_insured_per_ha = {"Rice": 40000, "Wheat": 35000, "Maize": 30000, "Sugarcane": 90000}.get(crop, 30000)
    sum_insured = round(sum_insured_per_ha * area)
    premium = round(sum_insured * 0.02)  # 2% farmer share for Kharif crops under PMFBY
    return jsonify({
        "scheme": "PM Fasal Bima Yojana (PMFBY)",
        "crop": crop,
        "sum_insured": sum_insured,
        "your_premium": premium,
        "govt_subsidy_covers_rest": True,
        "note": "Premium must be paid before the cut-off sowing date for your district.",
    })


@app.route("/api/loan_eligibility", methods=["POST"])
def loan_eligibility():
    data = request.get_json(force=True, silent=True) or {}
    try:
        land_size = float(data.get("land_size", 1))
    except (TypeError, ValueError):
        land_size = 1.0
    has_kcc = data.get("has_kcc", False)
    eligible_amount = round(land_size * 50000)
    interest_rate = "4% (subsidised)" if has_kcc else "7% (standard KCC rate before subvention)"
    return jsonify({
        "eligible_amount": min(eligible_amount, 300000),
        "interest_rate": interest_rate,
        "scheme": "Kisan Credit Card (KCC)",
        "documents_needed": ["Land record (RoR)", "Aadhaar card", "Bank passbook", "Passport photo"],
    })


# ---------------------------------------------------------------------------
# NEW: Carbon footprint / sustainability score
# ---------------------------------------------------------------------------

@app.route("/api/sustainability_score", methods=["GET"])
def sustainability_score():
    score = random.randint(58, 88)
    rating = "Excellent" if score > 80 else "Good" if score > 60 else "Needs improvement"
    return jsonify({
        "score": score,
        "rating": rating,
        "co2_saved_kg": round(score * 3.2),
        "breakdown": [
            {"factor": "Fertilizer efficiency", "impact": "positive", "note": "Following split-dose urea plan"},
            {"factor": "Water usage", "impact": "positive", "note": "Irrigation matched to weather forecast"},
            {"factor": "Pesticide use", "impact": "neutral", "note": "Moderate chemical pesticide use this season"},
        ],
        "tips": [
            "Switch to neem-based biopesticide for minor infestations to raise your score.",
            "Try direct-seeded rice next season to cut water use by up to 30%.",
        ],
    })


# ---------------------------------------------------------------------------
# NEW: Rainfall History Tracker
# In production this would pull from IMD (India Meteorological Department)
# historical rainfall data for the farmer's district. Mocked here with
# realistic monthly figures so the frontend can be built and tested today.
# ---------------------------------------------------------------------------

@app.route("/api/rainfall_history", methods=["GET"])
def rainfall_history():
    location = request.args.get("location", "Bhubaneswar (City Centre)")
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    # Rough Odisha seasonal pattern: dry Jan-May, monsoon peak Jul-Sep, tapering after
    normal_mm = [12, 18, 25, 40, 80, 220, 300, 280, 210, 110, 30, 8]

    history = []
    for i, month in enumerate(months):
        actual = round(normal_mm[i] * random.uniform(0.7, 1.3))
        history.append({
            "month": month,
            "actual_mm": actual,
            "normal_mm": normal_mm[i],
            "status": "above" if actual > normal_mm[i] * 1.1 else "below" if actual < normal_mm[i] * 0.9 else "normal",
        })

    total_actual = sum(h["actual_mm"] for h in history)
    total_normal = sum(normal_mm)
    deviation_percent = round((total_actual - total_normal) / total_normal * 100, 1)

    return jsonify({
        "location": location,
        "history": history,
        "total_actual_mm": total_actual,
        "total_normal_mm": total_normal,
        "deviation_percent": deviation_percent,
        "summary": f"Rainfall is {'above' if deviation_percent > 0 else 'below'} normal by {abs(deviation_percent)}% this year.",
    })


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(e):
    """Defense-in-depth: catches any UNEXPECTED bug that slips past the
    per-route validation elsewhere in this file, so a future crash degrades
    to a friendly page/JSON response instead of taking down the request
    (or, worse, showing a raw stack trace to a farmer using the app)."""
    app.logger.exception("Unhandled server error")
    if request.path.startswith("/api/"):
        return jsonify({"status": "error", "message": "Something went wrong on our end — please try again."}), 500
    return render_template("500.html"), 500


@app.errorhandler(413)
def request_too_large(e):
    """Triggered by MAX_CONTENT_LENGTH below — e.g. an oversized video/audio
    upload — before it can consume memory trying to process it."""
    if request.path.startswith("/api/"):
        return jsonify({"status": "error", "message": "File is too large."}), 413
    return render_template("500.html"), 413


# ---------------------------------------------------------------------------
# NEW: Games — for farmers' free/rest time. Light, quick, and genuinely
# farming-related (not just filler), so time spent playing still reinforces
# useful knowledge.
# ---------------------------------------------------------------------------

QUIZ_QUESTIONS = {
    "en": [
        {"q": "Which nutrient helps most with leaf and stem growth?", "options": ["Nitrogen", "Potassium", "Sulphur", "Zinc"], "answer": 0,
         "explain": "Nitrogen (N) drives vegetative growth — leaves and stems. That's why urea (high-N) is applied early in the season."},
        {"q": "What is the ideal soil pH range for most crops?", "options": ["3.0–4.5", "6.0–7.5", "8.5–10", "2.0–3.0"], "answer": 1,
         "explain": "Most crops absorb nutrients best when soil pH is between 6.0 and 7.5 — close to neutral."},
        {"q": "Which crop is known to naturally fix nitrogen in soil?", "options": ["Rice", "Wheat", "Moong (green gram)", "Maize"], "answer": 2,
         "explain": "Legumes like moong, urad, and other pulses fix atmospheric nitrogen into the soil via root nodules."},
        {"q": "PM-KISAN gives eligible farmers how much per year?", "options": ["₹2,000", "₹4,000", "₹6,000", "₹10,000"], "answer": 2,
         "explain": "PM-KISAN provides ₹6,000/year in 3 equal installments directly to the farmer's bank account."},
        {"q": "What does NDVI (used in satellite crop monitoring) measure?", "options": ["Soil temperature", "Plant vegetation health", "Rainfall amount", "Wind speed"], "answer": 1,
         "explain": "NDVI (Normalized Difference Vegetation Index) measures how green/healthy plant cover is, from satellite imagery."},
        {"q": "Which pest is a common threat to paddy (rice) crops?", "options": ["Brown Plant Hopper", "Aphid", "Locust", "Termite"], "answer": 0,
         "explain": "Brown Plant Hopper is one of the most damaging rice pests in India, especially under high-nitrogen, low-drainage conditions."},
        {"q": "What is Kisan Credit Card (KCC) mainly used for?", "options": ["Crop insurance", "Short-term farm credit", "Land registration", "Fertilizer subsidy"], "answer": 1,
         "explain": "KCC gives farmers short-term credit — for seeds, fertilizer, and other input costs — at a subsidised interest rate."},
        {"q": "Direct-seeded rice (DSR) mainly helps save which resource?", "options": ["Fertilizer", "Water", "Labour hours only", "Sunlight"], "answer": 1,
         "explain": "DSR skips the water-heavy transplanting/puddling stage, cutting water use by up to 30% compared to traditional rice sowing."},
    ],
    "hi": [
        {"q": "पत्तों और तने की बढ़त के लिए सबसे ज़्यादा कौन सा पोषक तत्व ज़रूरी है?", "options": ["नाइट्रोजन", "पोटैशियम", "सल्फर", "जिंक"], "answer": 0,
         "explain": "नाइट्रोजन (N) पौधे के हरे भाग — पत्तों और तने — की बढ़त को बढ़ाता है। इसीलिए यूरिया (जिसमें N ज़्यादा होता है) सीज़न की शुरुआत में डाला जाता है।"},
        {"q": "ज़्यादातर फसलों के लिए मिट्टी का सही pH स्तर क्या है?", "options": ["3.0–4.5", "6.0–7.5", "8.5–10", "2.0–3.0"], "answer": 1,
         "explain": "ज़्यादातर फसलें 6.0 से 7.5 pH (लगभग neutral) पर पोषक तत्व सबसे अच्छे से सोख पाती हैं।"},
        {"q": "कौन सी फसल मिट्टी में प्राकृतिक रूप से नाइट्रोजन जोड़ती है?", "options": ["चावल", "गेहूं", "मूंग (हरी दाल)", "मक्का"], "answer": 2,
         "explain": "मूंग, उड़द जैसी दलहनी फसलें अपनी जड़ों के ज़रिए हवा की नाइट्रोजन को मिट्टी में मिला देती हैं।"},
        {"q": "PM-KISAN योजना में पात्र किसान को हर साल कितनी राशि मिलती है?", "options": ["₹2,000", "₹4,000", "₹6,000", "₹10,000"], "answer": 2,
         "explain": "PM-KISAN के तहत ₹6,000 सालाना, 3 बराबर किस्तों में सीधे किसान के बैंक खाते में आते हैं।"},
        {"q": "सैटेलाइट से फसल निगरानी में NDVI किसे मापता है?", "options": ["मिट्टी का तापमान", "पौधों की हरियाली/स्वास्थ्य", "बारिश की मात्रा", "हवा की गति"], "answer": 1,
         "explain": "NDVI सैटेलाइट तस्वीरों से यह मापता है कि पौधे कितने हरे-भरे और स्वस्थ हैं।"},
        {"q": "धान की फसल में कौन सा कीट आम खतरा है?", "options": ["भूरा फुदका (Brown Plant Hopper)", "एफिड", "टिड्डी", "दीमक"], "answer": 0,
         "explain": "भूरा फुदका धान की सबसे नुकसानदायक कीटों में से एक है, खासकर ज़्यादा नाइट्रोजन और कम जल-निकासी वाले खेतों में।"},
        {"q": "किसान क्रेडिट कार्ड (KCC) मुख्यतः किसलिए इस्तेमाल होता है?", "options": ["फसल बीमा", "छोटी अवधि का खेती ऋण", "ज़मीन रजिस्ट्री", "खाद सब्सिडी"], "answer": 1,
         "explain": "KCC किसानों को बीज, खाद जैसे खर्चों के लिए कम ब्याज दर पर छोटी अवधि का ऋण देता है।"},
        {"q": "सीधी बुवाई वाला धान (DSR) मुख्यतः किस चीज़ की बचत करता है?", "options": ["खाद", "पानी", "सिर्फ मज़दूरी के घंटे", "धूप"], "answer": 1,
         "explain": "DSR में पानी-गहन रोपाई की ज़रूरत नहीं पड़ती, जिससे पारंपरिक धान की तुलना में 30% तक पानी बचता है।"},
    ],
    "or": [
        {"q": "ପତ୍ର ଏବଂ ଡେମ୍ଫର ବୃଦ୍ଧି ପାଇଁ କେଉଁ ପୋଷକ ତତ୍ତ୍ୱ ସବୁଠାରୁ ଅଧିକ ସାହାଯ୍ୟ କରେ?", "options": ["ନାଇଟ୍ରୋଜେନ", "ପୋଟାସିୟମ", "ସଲଫର", "ଜିଙ୍କ"], "answer": 0,
         "explain": "ନାଇଟ୍ରୋଜେନ (N) ଉଦ୍ଭିଦର ପତ୍ର ଓ ଡେମ୍ଫ ବୃଦ୍ଧିକୁ ବଢ଼ାଏ। ଏହି କାରଣରୁ ୟୁରିଆ (ଉଚ୍ଚ-N) ଋତୁ ଆରମ୍ଭରେ ପ୍ରୟୋଗ ହୁଏ।"},
        {"q": "ଅଧିକାଂଶ ଫସଲ ପାଇଁ ମାଟିର ଆଦର୍ଶ pH ପରିସର କ'ଣ?", "options": ["3.0–4.5", "6.0–7.5", "8.5–10", "2.0–3.0"], "answer": 1,
         "explain": "ଅଧିକାଂଶ ଫସଲ 6.0 ରୁ 7.5 pH ରେ ପୋଷକ ତତ୍ତ୍ୱ ସବୁଠାରୁ ଭଲ ଭାବରେ ଶୋଷେ।"},
        {"q": "କେଉଁ ଫସଲ ମାଟିରେ ପ୍ରାକୃତିକ ଭାବେ ନାଇଟ୍ରୋଜେନ ମିଶାଏ?", "options": ["ଚାଉଳ", "ଗହମ", "ମୁଗ (ହରି ଡାଲି)", "ମକା"], "answer": 2,
         "explain": "ମୁଗ, ବିରି ଭଳି ଡାଲି ଫସଲ ସେମାନଙ୍କ ମୂଳ ମାଧ୍ୟମରେ ବାୟୁମଣ୍ଡଳର ନାଇଟ୍ରୋଜେନକୁ ମାଟିରେ ମିଶାନ୍ତି।"},
        {"q": "PM-KISAN ଯୋଜନାରେ ଯୋଗ୍ୟ କୃଷକଙ୍କୁ ବର୍ଷକୁ କେତେ ଟଙ୍କା ମିଳେ?", "options": ["₹2,000", "₹4,000", "₹6,000", "₹10,000"], "answer": 2,
         "explain": "PM-KISAN ଅନ୍ତର୍ଗତ ବର୍ଷକୁ ₹6,000, 3 ସମାନ କିସ୍ତିରେ ସିଧାସଳଖ କୃଷକଙ୍କ ବ୍ୟାଙ୍କ ଖାତାକୁ ଆସେ।"},
        {"q": "ଉପଗ୍ରହ ମାଧ୍ୟମରେ ଫସଲ ନିରୀକ୍ଷଣରେ NDVI କ'ଣ ମାପେ?", "options": ["ମାଟିର ତାପମାତ୍ରା", "ଉଦ୍ଭିଦର ସବୁଜିମା/ସ୍ୱାସ୍ଥ୍ୟ", "ବର୍ଷାର ପରିମାଣ", "ପବନର ବେଗ"], "answer": 1,
         "explain": "NDVI ଉପଗ୍ରହ ପ୍ରତିଛବିରୁ ମାପେ ଯେ ଉଦ୍ଭିଦ କେତେ ସବୁଜ ଏବଂ ସୁସ୍ଥ ଅଛନ୍ତି।"},
        {"q": "ଧାନ ଫସଲରେ କେଉଁ କୀଟ ଏକ ସାଧାରଣ ବିପଦ?", "options": ["ବ୍ରାଉନ ପ୍ଲାଣ୍ଟ ହପର", "ଏଫିଡ", "ପଙ୍ଗପାଳ", "ଦିମା"], "answer": 0,
         "explain": "ବ୍ରାଉନ ପ୍ଲାଣ୍ଟ ହପର ଧାନର ସବୁଠାରୁ କ୍ଷତିକାରକ କୀଟ ମଧ୍ୟରୁ ଗୋଟିଏ, ବିଶେଷତଃ ଅଧିକ ନାଇଟ୍ରୋଜେନ ଓ କମ ଜଳ ନିଷ୍କାସନ ଥିବା ଜମିରେ।"},
        {"q": "କିଷାନ କ୍ରେଡିଟ କାର୍ଡ (KCC) ମୁଖ୍ୟତଃ କେଉଁଥିପାଇଁ ବ୍ୟବହୃତ ହୁଏ?", "options": ["ଫସଲ ବୀମା", "ଅଳ୍ପ ଅବଧିର ଚାଷ ଋଣ", "ଜମି ପଞ୍ଜୀକରଣ", "ସାର ସବସିଡି"], "answer": 1,
         "explain": "KCC କୃଷକଙ୍କୁ ବିହନ, ସାର ଭଳି ଖର୍ଚ୍ଚ ପାଇଁ କମ ସୁଧ ହାରରେ ଅଳ୍ପ ଅବଧିର ଋଣ ଦିଏ।"},
        {"q": "ସିଧାସଳଖ ବୁଣା ଧାନ (DSR) ମୁଖ୍ୟତଃ କେଉଁ ସମ୍ପଦ ସାଶ୍ରୟ କରେ?", "options": ["ସାର", "ପାଣି", "କେବଳ ମଜୁରି ସମୟ", "ସୂର୍ଯ୍ୟାଲୋକ"], "answer": 1,
         "explain": "DSR ପାଣି-ଅଧିକ ରୋପଣ ପ୍ରକ୍ରିୟାକୁ ଏଡ଼ାଏ, ଯାହା ପାରମ୍ପରିକ ଧାନ ତୁଳନାରେ 30% ପର୍ଯ୍ୟନ୍ତ ପାଣି ସାଶ୍ରୟ କରେ।"},
    ],
}


@app.route("/api/quiz_questions", methods=["GET"])
def quiz_questions():
    lang = request.args.get("lang", "en")
    pool = QUIZ_QUESTIONS.get(lang, QUIZ_QUESTIONS["en"])
    try:
        requested_count = int(request.args.get("count", 5))
    except (TypeError, ValueError):
        requested_count = 5
    count = max(0, min(requested_count, len(pool)))
    picked = random.sample(pool, count)
    return jsonify({"questions": picked, "lang": lang})


# ---------------------------------------------------------------------------
# NEW: Post-Harvest Quality Grading — vision + sensor unit simulation.
# Calibrated for ONE commodity (Tomato) per the brief. Real hardware would
# feed a photo + live sensor readings into these same endpoints — the API
# contract here is designed so a Raspberry Pi/ESP32 unit in the field could
# call it directly with no changes. See HARDWARE_DESIGN.md for the physical
# unit's bill of materials, block diagram, and calibration procedure.
# ---------------------------------------------------------------------------

QUALITY_GRADING_COMMODITY = "Tomato"  # the one commodity this unit is calibrated for

# Baseline shelf life (days) for Tomato at ideal storage conditions
# (~12-15°C, ~90-95% RH, low ethylene) — the calibration anchor point.
TOMATO_BASELINE_SHELF_LIFE_DAYS = 12
TOMATO_IDEAL_TEMP_RANGE = (12, 15)      # °C
TOMATO_IDEAL_HUMIDITY_RANGE = (90, 95)  # % RH
TOMATO_SAFE_GAS_PPM = 50                # ethylene-correlated VOC proxy reading

QUALITY_REPORTS = {}  # batch_id -> report dict, in-memory for demo


@app.route("/api/quality_grading/sensors", methods=["GET"])
def quality_grading_sensors():
    """Simulates a live read from the unit's onboard sensors (DHT22 temp +
    humidity, MQ-135-class gas/VOC sensor used as an ethylene proxy). A real
    unit would return actual GPIO sensor readings from this same endpoint
    shape, so the frontend and grading logic don't need to change."""
    return jsonify({
        "temperature_c": round(random.uniform(18, 34), 1),
        "humidity_pct": round(random.uniform(55, 95), 1),
        "gas_ppm": round(random.uniform(20, 180), 1),
        "captured_at": datetime.now().strftime("%d %b %Y, %I:%M:%S %p"),
    })


@app.route("/api/quality_grading/analyze", methods=["POST"])
def quality_grading_analyze():
    """Grades a batch using (simulated) vision + sensor fusion. An uploaded
    image is optional in this demo — sensor-only analysis still works. But
    if a photo IS provided, it's validated with the same Excess-Green-Index
    crop-photo check used by Pest Detection, so an unrelated photo (a
    person, a document, fabric, etc.) is rejected rather than silently
    graded as if it were produce."""
    try:
        temperature_c = float(request.form.get("temperature_c", 24))
        humidity_pct = float(request.form.get("humidity_pct", 80))
        gas_ppm = float(request.form.get("gas_ppm", 60))
    except (TypeError, ValueError):
        temperature_c, humidity_pct, gas_ppm = 24.0, 80.0, 60.0

    # --- Vision component (heuristic stand-in for a trained CV model) ---
    image_file = request.files.get("image")
    visual_score = random.uniform(0.6, 0.95)  # simulated defect/ripeness confidence
    if image_file and image_file.filename:
        is_valid, match_ratio, debug_reason = _is_crop_photo(image_file, accept_ripe_produce=True)
        if not is_valid:
            return jsonify({
                "valid": False,
                "message": "This doesn't look like a photo of produce (fruit/vegetable/grain). Please upload a clear photo of the actual batch, or skip the photo and grade from sensor data alone.",
                "match_ratio": match_ratio,
                "debug": debug_reason,
            }), 400
        image_file.stream.seek(0)  # _is_crop_photo already consumed the stream — rewind before reading again
        try:
            from PIL import Image
            img = Image.open(image_file.stream).convert("RGB")
            img.thumbnail((64, 64))
            pixels = list(img.getdata())
            avg_r = sum(p[0] for p in pixels) / len(pixels)
            avg_g = sum(p[1] for p in pixels) / len(pixels)
            # Redder average = riper tomato = nudge visual score based on
            # how close the red/green balance is to a healthy ripe tomato.
            redness = avg_r / (avg_g + 1)
            visual_score = max(0.4, min(0.97, 1.0 - abs(redness - 1.6) * 0.25))
        except Exception as e:
            app.logger.warning(f"Image analysis fallback (using heuristic score): {e}")

    # --- Sensor-driven spoilage risk ---
    temp_penalty = max(0, temperature_c - TOMATO_IDEAL_TEMP_RANGE[1]) * 0.35
    humidity_penalty = max(0, TOMATO_IDEAL_HUMIDITY_RANGE[0] - humidity_pct) * 0.08
    gas_penalty = max(0, gas_ppm - TOMATO_SAFE_GAS_PPM) * 0.03
    risk_score = temp_penalty + humidity_penalty + gas_penalty  # higher = worse

    combined_score = visual_score - (risk_score * 0.03)
    combined_score = max(0.15, min(0.98, combined_score))

    if combined_score >= 0.8:
        grade, defect_notes = "A", "No visible bruising or blemishes detected. Uniform ripeness and color."
    elif combined_score >= 0.6:
        grade, defect_notes = "B", "Minor surface blemishes or slight over-ripeness detected — still market-fit."
    else:
        grade, defect_notes = "C", "Visible bruising, soft spots, or uneven ripeness detected — prioritize for immediate sale or processing."

    # --- Remaining shelf life ---
    shelf_life_days = TOMATO_BASELINE_SHELF_LIFE_DAYS
    shelf_life_days -= temp_penalty * 1.4
    shelf_life_days -= humidity_penalty * 0.6
    shelf_life_days -= gas_penalty * 1.1
    shelf_life_days -= {"A": 0, "B": 1.5, "C": 4}[grade]
    shelf_life_days = max(0.5, round(shelf_life_days, 1))

    # --- Storage action recommendation ---
    actions = []
    if temperature_c > TOMATO_IDEAL_TEMP_RANGE[1]:
        actions.append(f"Move to cold storage ({TOMATO_IDEAL_TEMP_RANGE[0]}–{TOMATO_IDEAL_TEMP_RANGE[1]}°C) within {max(1, int(4 - temp_penalty))} hours — current temperature is accelerating ripening.")
    if humidity_pct < TOMATO_IDEAL_HUMIDITY_RANGE[0]:
        actions.append("Increase humidity (target 90–95% RH) to reduce moisture loss and shriveling.")
    elif humidity_pct > TOMATO_IDEAL_HUMIDITY_RANGE[1] + 3:
        actions.append("Improve ventilation — excess humidity raises fungal rot risk.")
    if gas_ppm > TOMATO_SAFE_GAS_PPM:
        actions.append("Ventilate storage area and separate from ethylene-emitting produce (e.g. bananas, apples) to slow over-ripening.")
    if shelf_life_days <= 2:
        actions.append("Priority sale — route to nearest mandi within 24–48 hours rather than holding in storage.")
    if not actions:
        actions.append("Conditions are within ideal range — continue standard storage and monitoring.")

    return jsonify({
        "commodity": QUALITY_GRADING_COMMODITY,
        "grade": grade,
        "defect_notes": defect_notes,
        "confidence": f"{round(combined_score * 100)}%",
        "remaining_shelf_life_days": shelf_life_days,
        "storage_actions": actions,
        "sensor_readings": {"temperature_c": temperature_c, "humidity_pct": humidity_pct, "gas_ppm": gas_ppm},
        "risk_level": "Low" if risk_score < 2 else "Medium" if risk_score < 5 else "High",
    })


@app.route("/api/quality_grading/report", methods=["POST"])
def quality_grading_create_report():
    """Creates a permanent, shareable traceability record for one graded
    batch — the artifact a farmer hands to an aggregator or buyer as proof
    of quality. Publicly viewable (no login) at /report/<batch_id> since
    buyers/aggregators won't have AgroStar accounts."""
    data = request.get_json(force=True, silent=True) or {}
    batch_id = uuid.uuid4().hex[:8].upper()
    user = session.get("user", {"name": "Guest Farmer"})

    report = {
        "batch_id": batch_id,
        "commodity": QUALITY_GRADING_COMMODITY,
        "grade": data.get("grade", "—"),
        "defect_notes": data.get("defect_notes", ""),
        "confidence": data.get("confidence", "—"),
        "remaining_shelf_life_days": data.get("remaining_shelf_life_days", "—"),
        "storage_actions": data.get("storage_actions", []),
        "sensor_readings": data.get("sensor_readings", {}),
        "risk_level": data.get("risk_level", "—"),
        "farmer_name": user.get("name", "Guest Farmer"),
        "location": data.get("location", "Bhubaneswar (City Centre)"),
        "graded_at": datetime.now().strftime("%d %b %Y, %I:%M %p"),
    }
    QUALITY_REPORTS[batch_id] = report
    return jsonify({"status": "created", "batch_id": batch_id, "report_url": f"/report/{batch_id}"})


@app.route("/report/<batch_id>")
def quality_grading_view_report(batch_id):
    """Public traceability report — intentionally has NO @login_required,
    since the whole point is that an aggregator or buyer with just the
    link/QR code (and no AgroStar account) can verify a batch's quality."""
    report = QUALITY_REPORTS.get(batch_id)
    if not report:
        return "Report not found — the link or QR code may be incorrect, or this batch hasn't been generated in this session.", 404
    return render_template("quality_report.html", report=report)


# ---------------------------------------------------------------------------
# NEW: Farm Expense/Income Ledger — no API route needed. Entries live in the
# browser's localStorage (see templates/feature_ledger.html), the same
# offline-first approach used for Farm Reminders, since this is private
# per-farmer data that doesn't need a server round-trip.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# NEW: Direct Mandi Bidding / Buyer Connect — cuts out the middleman
# (bichauliya) by letting farmers list produce for sale AND letting
# buyers/aggregators post open requirements, so both sides can see and
# reach each other directly. Buyer requests are seeded demo data; farmer
# listings are stored in-memory for the session.
# ---------------------------------------------------------------------------
BUYER_REQUESTS = [
    {"id": 1, "buyer": "Odisha AgroFresh Pvt Ltd", "crop": "Tomato", "quantity_quintal": 40, "offer_price": 1450, "location": "Bhubaneswar (City Centre)", "contact": "+91 98765 43210", "posted": "2 days ago"},
    {"id": 2, "buyer": "Cuttack Mandi Traders", "crop": "Rice", "quantity_quintal": 100, "offer_price": 2150, "location": "Cuttack", "contact": "+91 90123 45678", "posted": "1 day ago"},
    {"id": 3, "buyer": "FreshCart Retail Chain", "crop": "Onion", "quantity_quintal": 25, "offer_price": 1850, "location": "Khordha", "contact": "+91 91234 56789", "posted": "5 hours ago"},
    {"id": 4, "buyer": "Puri Foods Processing Unit", "crop": "Groundnut", "quantity_quintal": 15, "offer_price": 5600, "location": "Puri", "contact": "+91 99887 76655", "posted": "3 days ago"},
    {"id": 5, "buyer": "Nayapalli Wholesale Market", "crop": "Maize", "quantity_quintal": 60, "offer_price": 1980, "location": "Jatani", "contact": "+91 97123 45610", "posted": "6 hours ago"},
]
FARMER_PRODUCE_LISTINGS = []  # in-memory demo store — farmer's own produce listed for sale


@app.route("/api/buyer_requests", methods=["GET"])
def buyer_requests():
    crop = request.args.get("crop", "").strip().lower()
    reqs = BUYER_REQUESTS
    if crop:
        reqs = [r for r in reqs if crop in r["crop"].lower()]
    return jsonify({"requests": sorted(reqs, key=lambda r: -r["id"])})


@app.route("/api/produce_listings", methods=["GET"])
def produce_listings():
    return jsonify({"listings": sorted(FARMER_PRODUCE_LISTINGS, key=lambda l: -l["id"])})


@app.route("/api/produce_listing", methods=["POST"])
def add_produce_listing():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "").strip()
    quantity = data.get("quantity_quintal")
    asking_price = data.get("asking_price")
    location = data.get("location", "Bhubaneswar (City Centre)")
    if not crop or not quantity or not asking_price:
        return jsonify({"status": "error", "message": "Crop, quantity aur asking price bharein"}), 400
    try:
        quantity = float(quantity)
        asking_price = float(asking_price)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Please enter valid numbers"}), 400
    user = session.get("user", {"name": "Farmer"})
    listing = {
        "id": len(FARMER_PRODUCE_LISTINGS) + 1,
        "farmer": user.get("name", "Farmer"),
        "crop": crop,
        "quantity_quintal": quantity,
        "asking_price": asking_price,
        "location": location,
        "posted": "Just now",
    }
    FARMER_PRODUCE_LISTINGS.append(listing)
    matches = [r for r in BUYER_REQUESTS if r["crop"].lower() == crop.lower()]
    return jsonify({"status": "listed", "listing": listing, "matching_buyers": matches})


# ---------------------------------------------------------------------------
# NEW: KCC Loan EMI Calculator — turns a loan amount into a real monthly
# instalment, so a farmer can see what they'd actually repay each month
# before applying (complements /api/loan_eligibility above).
# ---------------------------------------------------------------------------
@app.route("/api/emi_calculator", methods=["POST"])
def emi_calculator():
    data = request.get_json(force=True, silent=True) or {}
    try:
        principal = float(data.get("principal", 100000))
        annual_rate = float(data.get("annual_rate", 7))
        tenure_months = int(data.get("tenure_months", 12))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Please enter valid numbers"}), 400

    monthly_rate = annual_rate / 12 / 100
    if monthly_rate == 0:
        emi = principal / tenure_months
    else:
        emi = principal * monthly_rate * (1 + monthly_rate) ** tenure_months / ((1 + monthly_rate) ** tenure_months - 1)
    total_payment = emi * tenure_months
    total_interest = total_payment - principal
    return jsonify({
        "emi": round(emi),
        "total_payment": round(total_payment),
        "total_interest": round(total_interest),
        "principal": round(principal),
        "tenure_months": tenure_months,
        "annual_rate": annual_rate,
    })


# ---------------------------------------------------------------------------
# NEW: Smart Irrigation Scheduler — combines a farmer's manual soil-moisture
# reading with the location's rain forecast (reusing the same Open-Meteo /
# mock-fallback pipeline as /api/weather) into one clear "irrigate now /
# wait" call, instead of just showing raw forecast numbers.
# ---------------------------------------------------------------------------
CROP_MOISTURE_THRESHOLD = {  # below this %, the crop needs water regardless of rain
    "Rice": 70, "Wheat": 35, "Maize": 40, "Sugarcane": 45,
    "Groundnut": 30, "Cotton": 35, "Vegetables": 40,
}


@app.route("/api/irrigation_schedule", methods=["POST"])
def irrigation_schedule():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "Rice")
    try:
        soil_moisture = float(data.get("soil_moisture_percent", 40))
    except (TypeError, ValueError):
        soil_moisture = 40.0
    location = data.get("location", "Bhubaneswar (City Centre)")
    threshold = CROP_MOISTURE_THRESHOLD.get(crop, 40)

    lat, lon = LOCATION_COORDS.get(location, (20.2961, 85.8245))
    try:
        fdata = _fetch_open_meteo(lat, lon)
        precip_sums = (fdata.get("daily") or {}).get("precipitation_sum") or []
        rain_next_2_days = sum(precip_sums[:2]) if precip_sums else 0
    except Exception:
        days, _ = _mock_weather_forecast()
        rain_next_2_days = sum(d["rain_mm"] for d in days[:2])

    if soil_moisture >= threshold + 15:
        action, reason = "skip", "Soil is already well moist for this crop — no irrigation needed right now."
    elif rain_next_2_days >= 15:
        action, reason = "wait", f"Rain of ~{round(rain_next_2_days)}mm is expected in the next 2 days — that should be enough."
    elif soil_moisture < threshold:
        action, reason = "irrigate_today", "Soil moisture is below the safe level for this crop and no significant rain is expected soon."
    else:
        action, reason = "irrigate_in_2_days", "Moisture is borderline — irrigate within the next 2 days if no rain falls."

    return jsonify({
        "crop": crop,
        "location": location,
        "soil_moisture_percent": soil_moisture,
        "threshold_percent": threshold,
        "rain_next_2_days_mm": round(rain_next_2_days, 1),
        "action": action,
        "reason": reason,
    })


# ---------------------------------------------------------------------------
# NEW: Groundwater / Borewell Level Tracker — shows how the water table in
# a farmer's area has moved over the last 6 months. Seeded demo data (same
# location always shows the same trend); real deployments would swap this
# for India-WRIS / Central Ground Water Board API data.
# ---------------------------------------------------------------------------
@app.route("/api/groundwater_trend", methods=["GET"])
def groundwater_trend():
    location = request.args.get("location", "Bhubaneswar (City Centre)")
    rng = random.Random(location)  # local RNG seeded by location — deterministic, doesn't touch global random state
    level = round(rng.uniform(6, 14), 1)
    history = []
    for i in range(6):
        d = datetime.now() - timedelta(days=30 * (5 - i))
        level = round(max(2, level + rng.uniform(-1.2, 0.8)), 1)
        history.append({"month": d.strftime("%b %Y"), "level_m": level})

    trend = "declining" if history[-1]["level_m"] > history[0]["level_m"] + 1 else \
        "rising" if history[-1]["level_m"] < history[0]["level_m"] - 1 else "stable"
    advisory = {
        "declining": "Water table is dropping — consider drip irrigation and avoid new borewells nearby.",
        "rising": "Water table has recovered well — a good time to recharge structures like farm ponds.",
        "stable": "Water table is holding steady — continue current usage but keep monitoring.",
    }[trend]
    return jsonify({
        "location": location,
        "unit": "metres below ground level (higher = deeper/worse)",
        "history": history,
        "current_level_m": history[-1]["level_m"],
        "trend": trend,
        "advisory": advisory,
    })


# ---------------------------------------------------------------------------
# NEW: Equipment Rental Marketplace — lets smallholders rent expensive
# machinery (tractor, harvester, sprayer, drone) from a nearby owner
# instead of buying it, and lets any farmer list their own idle equipment.
# ---------------------------------------------------------------------------
EQUIPMENT_LISTINGS = [
    {"id": 1, "owner": "Ramesh Patra", "equipment": "Tractor (45 HP)", "rate": "₹700/hour", "location": "Jatani", "contact": "+91 98111 22334", "available": True},
    {"id": 2, "owner": "Suresh Behera", "equipment": "Combine Harvester", "rate": "₹1,800/hour", "location": "Cuttack", "contact": "+91 97222 33445", "available": True},
    {"id": 3, "owner": "Krishna Sahoo", "equipment": "Power Sprayer", "rate": "₹250/day", "location": "Khordha", "contact": "+91 96333 44556", "available": True},
    {"id": 4, "owner": "Bijay Nayak", "equipment": "Rotavator", "rate": "₹550/hour", "location": "Banki", "contact": "+91 95444 55667", "available": False},
    {"id": 5, "owner": "Debashish Rout", "equipment": "Agri Drone (Spraying)", "rate": "₹400/acre", "location": "Puri", "contact": "+91 94555 66778", "available": True},
    {"id": 6, "owner": "Ashok Jena", "equipment": "Seed Drill", "rate": "₹350/day", "location": "Pipili", "contact": "+91 93666 77889", "available": True},
]


@app.route("/api/equipment_listings", methods=["GET"])
def equipment_listings():
    location = request.args.get("location", "").strip()
    listings = EQUIPMENT_LISTINGS
    if location:
        filtered = [e for e in listings if e["location"] == location]
        listings = filtered if filtered else listings
    return jsonify({"listings": sorted(listings, key=lambda e: -e["id"])})


@app.route("/api/equipment_listing", methods=["POST"])
def add_equipment_listing():
    data = request.get_json(force=True, silent=True) or {}
    equipment = data.get("equipment", "").strip()
    rate = data.get("rate", "").strip()
    location = data.get("location", "Bhubaneswar (City Centre)")
    if not equipment or not rate:
        return jsonify({"status": "error", "message": "Equipment name aur rate bharein"}), 400
    user = session.get("user", {"name": "Farmer"})
    listing = {
        "id": len(EQUIPMENT_LISTINGS) + 1,
        "owner": user.get("name", "Farmer"),
        "equipment": equipment,
        "rate": rate,
        "location": location,
        "contact": data.get("contact", "—"),
        "available": True,
    }
    EQUIPMENT_LISTINGS.append(listing)
    return jsonify({"status": "listed", "listing": listing})


# ---------------------------------------------------------------------------
# NEW: Nearby Agri-Shop Price Compare — the same fertilizer/seed/pesticide
# item often varies in price shop to shop; this surfaces that gap directly.
# ---------------------------------------------------------------------------
AGRI_SHOP_PRICES = {
    "Urea (45kg bag)": [
        {"shop": "Kisan Seva Kendra, Nayapalli", "price": 266, "distance_km": 1.2},
        {"shop": "Odisha Agro Store, Patia", "price": 275, "distance_km": 2.8},
        {"shop": "Krushi Bhavan, Cuttack Rd", "price": 260, "distance_km": 4.5},
    ],
    "DAP (50kg bag)": [
        {"shop": "Kisan Seva Kendra, Nayapalli", "price": 1350, "distance_km": 1.2},
        {"shop": "Odisha Agro Store, Patia", "price": 1385, "distance_km": 2.8},
        {"shop": "Krushi Bhavan, Cuttack Rd", "price": 1340, "distance_km": 4.5},
    ],
    "Hybrid Rice Seed (5kg)": [
        {"shop": "Kisan Seva Kendra, Nayapalli", "price": 850, "distance_km": 1.2},
        {"shop": "Odisha Agro Store, Patia", "price": 795, "distance_km": 2.8},
        {"shop": "Green Field Agro, Khordha", "price": 820, "distance_km": 6.1},
    ],
    "Pesticide - Chlorpyrifos (1L)": [
        {"shop": "Kisan Seva Kendra, Nayapalli", "price": 420, "distance_km": 1.2},
        {"shop": "Odisha Agro Store, Patia", "price": 445, "distance_km": 2.8},
        {"shop": "Green Field Agro, Khordha", "price": 410, "distance_km": 6.1},
    ],
    "NPK 20:20:0 (50kg bag)": [
        {"shop": "Kisan Seva Kendra, Nayapalli", "price": 1180, "distance_km": 1.2},
        {"shop": "Krushi Bhavan, Cuttack Rd", "price": 1210, "distance_km": 4.5},
        {"shop": "Green Field Agro, Khordha", "price": 1165, "distance_km": 6.1},
    ],
}


@app.route("/api/agri_shop_prices", methods=["GET"])
def agri_shop_prices():
    item = request.args.get("item", "Urea (45kg bag)")
    rows = AGRI_SHOP_PRICES.get(item, [])
    rows_sorted = sorted(rows, key=lambda r: r["price"])
    cheapest = rows_sorted[0] if rows_sorted else None
    return jsonify({
        "item": item,
        "items_available": list(AGRI_SHOP_PRICES.keys()),
        "shops": rows_sorted,
        "cheapest": cheapest,
    })


# ---------------------------------------------------------------------------
# NEW: Region-wide Disease/Pest Outbreak Alert — a lightweight early-warning
# network: when one farmer reports an outbreak, every nearby farmer sees it
# immediately instead of finding out only after it reaches their own field.
# ---------------------------------------------------------------------------
DISEASE_ALERTS = [
    {"id": 1, "crop": "Rice", "issue": "Brown Plant Hopper", "location": "Cuttack", "severity": "High", "reported_by": "Bijay Nayak", "posted": "1 day ago"},
    {"id": 2, "crop": "Tomato", "issue": "Early Blight", "location": "Khordha", "severity": "Medium", "reported_by": "Sarita Mohanty", "posted": "3 days ago"},
    {"id": 3, "crop": "Groundnut", "issue": "Leaf Spot", "location": "Puri", "severity": "Low", "reported_by": "Debendra Sahu", "posted": "5 days ago"},
]


@app.route("/api/disease_alerts", methods=["GET"])
def disease_alerts():
    location = request.args.get("location", "").strip()
    alerts = DISEASE_ALERTS
    if location:
        filtered = [a for a in alerts if a["location"] == location]
        alerts = filtered if filtered else alerts
    return jsonify({"alerts": sorted(alerts, key=lambda a: -a["id"])})


@app.route("/api/disease_alert", methods=["POST"])
def add_disease_alert():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "").strip()
    issue = data.get("issue", "").strip()
    location = data.get("location", "Bhubaneswar (City Centre)")
    severity = data.get("severity", "Medium")
    if not crop or not issue:
        return jsonify({"status": "error", "message": "Crop aur issue ka naam bharein"}), 400
    user = session.get("user", {"name": "Farmer"})
    alert = {
        "id": len(DISEASE_ALERTS) + 1,
        "crop": crop,
        "issue": issue,
        "location": location,
        "severity": severity,
        "reported_by": user.get("name", "Farmer"),
        "posted": "Just now",
    }
    DISEASE_ALERTS.append(alert)
    broadcast_push_topic(
        "disease",
        f"🚨 {issue} reported in {location}",
        f"{alert['reported_by']} reported {issue} on {crop} near {location} — check your crop if you're nearby.",
        "/feature/disease-alerts",
    )
    return jsonify({"status": "reported", "alert": alert})


# ---------------------------------------------------------------------------
# NEW: Video Tutorials Library — short how-to topics in the farmer's
# language, more engaging than text-only tips. Links out to a YouTube
# search for the topic (rather than a single hardcoded video), so the
# farmer always lands on real, current results.
# ---------------------------------------------------------------------------
from urllib.parse import quote as _urlquote

VIDEO_TUTORIALS = {
    "en": [
        {"id": 1, "title": "How to test soil pH at home", "category": "Soil", "duration": "~4 min", "query": "how to test soil pH at home farming"},
        {"id": 2, "title": "Correct spacing for rice transplanting", "category": "Rice", "duration": "~6 min", "query": "correct spacing rice transplanting method"},
        {"id": 3, "title": "Identifying early blight in tomato", "category": "Pest Control", "duration": "~4 min", "query": "identify early blight tomato leaves"},
        {"id": 4, "title": "Setting up drip irrigation on a budget", "category": "Water", "duration": "~7 min", "query": "low cost drip irrigation setup farm"},
        {"id": 5, "title": "Applying for PM-KISAN online", "category": "Govt Schemes", "duration": "~5 min", "query": "PM KISAN online apply tutorial"},
        {"id": 6, "title": "Making vermicompost at home", "category": "Soil", "duration": "~6 min", "query": "how to make vermicompost at home farming"},
        {"id": 7, "title": "Kisan Credit Card application process", "category": "Finance", "duration": "~5 min", "query": "Kisan Credit Card application process explained"},
    ],
    "hi": [
        {"id": 1, "title": "घर पर मिट्टी का pH कैसे जांचें", "category": "मिट्टी", "duration": "~4 मिनट", "query": "ghar par mitti ka pH kaise jaanche"},
        {"id": 2, "title": "धान रोपाई की सही दूरी", "category": "धान", "duration": "~6 मिनट", "query": "dhan ropai sahi doori tarika"},
        {"id": 3, "title": "टमाटर में Early Blight कैसे पहचानें", "category": "कीट नियंत्रण", "duration": "~4 मिनट", "query": "tamatar early blight kaise pahchane"},
        {"id": 4, "title": "कम खर्च में ड्रिप सिंचाई कैसे लगाएं", "category": "पानी", "duration": "~7 मिनट", "query": "kam kharch drip sinchai kaise lagayen"},
        {"id": 5, "title": "PM-KISAN ऑनलाइन आवेदन कैसे करें", "category": "सरकारी योजना", "duration": "~5 मिनट", "query": "PM KISAN online avedan kaise kare"},
        {"id": 6, "title": "घर पर वर्मीकम्पोस्ट कैसे बनाएं", "category": "मिट्टी", "duration": "~6 मिनट", "query": "ghar par vermicompost kaise banaye"},
        {"id": 7, "title": "किसान क्रेडिट कार्ड आवेदन प्रक्रिया", "category": "वित्त", "duration": "~5 मिनट", "query": "Kisan Credit Card avedan prakriya"},
    ],
}


@app.route("/api/video_tutorials", methods=["GET"])
def video_tutorials():
    lang = request.args.get("lang", "en")
    category = request.args.get("category", "").strip()
    videos = VIDEO_TUTORIALS.get(lang, VIDEO_TUTORIALS["en"])
    if category:
        videos = [v for v in videos if v["category"] == category] or videos
    out = []
    for v in videos:
        out.append({**v, "search_url": f"https://www.youtube.com/results?search_query={_urlquote(v['query'])}"})
    categories = sorted(set(v["category"] for v in VIDEO_TUTORIALS.get(lang, VIDEO_TUTORIALS["en"])))
    return jsonify({"videos": out, "categories": categories, "lang": lang})


# ---------------------------------------------------------------------------
# NEW: Pashu (Dairy/Livestock) Basic Health Tracker — no API route needed.
# Records live in the browser's localStorage (see templates/feature_pashu_health.html),
# same offline-first pattern as Farm Reminders and the Farm Ledger.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# NEW: Cold Storage Finder + Slot Booking — helps cut post-harvest loss by
# showing nearby cold storage capacity/availability and letting a farmer
# book a slot directly, instead of produce sitting out and spoiling while
# they search around by phone.
# ---------------------------------------------------------------------------
COLD_STORAGES = [
    {"id": 1, "name": "Odisha State Cold Storage, Nayapalli", "location": "Nayapalli", "crops": ["Potato", "Onion", "Tomato"], "rate_per_quintal_per_day": 3.5, "capacity_quintal": 500, "booked_quintal": 320},
    {"id": 2, "name": "Cuttack Agro Cold Chain", "location": "Cuttack", "crops": ["Potato", "Vegetables", "Fruits"], "rate_per_quintal_per_day": 4.0, "capacity_quintal": 800, "booked_quintal": 410},
    {"id": 3, "name": "Khordha Farmers' Cold Store", "location": "Khordha", "crops": ["Onion", "Tomato", "Groundnut"], "rate_per_quintal_per_day": 3.2, "capacity_quintal": 300, "booked_quintal": 290},
    {"id": 4, "name": "Puri Coastal Cold Storage", "location": "Puri", "crops": ["Fish/Seafood", "Vegetables"], "rate_per_quintal_per_day": 4.5, "capacity_quintal": 400, "booked_quintal": 150},
    {"id": 5, "name": "Jatani Multi-Purpose Cold Store", "location": "Jatani", "crops": ["Potato", "Onion", "Fruits"], "rate_per_quintal_per_day": 3.0, "capacity_quintal": 600, "booked_quintal": 200},
]
COLD_STORAGE_BOOKINGS = []  # in-memory demo store


@app.route("/api/cold_storages", methods=["GET"])
def cold_storages():
    location = request.args.get("location", "").strip()
    crop = request.args.get("crop", "").strip().lower()
    rows = COLD_STORAGES
    if location:
        filtered = [c for c in rows if c["location"] == location]
        rows = filtered if filtered else rows
    if crop:
        rows = [c for c in rows if any(crop in x.lower() for x in c["crops"])] or rows
    out = []
    for c in rows:
        available = c["capacity_quintal"] - c["booked_quintal"]
        out.append({**c, "available_quintal": available,
                    "status": "Full" if available <= 0 else "Limited Space" if available < c["capacity_quintal"] * 0.2 else "Available"})
    return jsonify({"storages": out})


@app.route("/api/cold_storage_booking", methods=["POST"])
def cold_storage_booking():
    data = request.get_json(force=True, silent=True) or {}
    storage_id = data.get("storage_id")
    crop = data.get("crop", "").strip()
    quantity_quintal = data.get("quantity_quintal")
    days = data.get("days")
    if not storage_id or not crop or not quantity_quintal or not days:
        return jsonify({"status": "error", "message": "Sabhi fields bharein"}), 400
    try:
        storage_id = int(storage_id)
        quantity_quintal = float(quantity_quintal)
        days = int(days)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Please enter valid numbers"}), 400

    storage = next((c for c in COLD_STORAGES if c["id"] == storage_id), None)
    if not storage:
        return jsonify({"status": "error", "message": "Cold storage not found"}), 404

    available = storage["capacity_quintal"] - storage["booked_quintal"]
    if quantity_quintal > available:
        return jsonify({"status": "error", "message": f"Sirf {available} quintal jagah bachi hai is storage me"}), 400

    storage["booked_quintal"] += quantity_quintal
    user = session.get("user", {"name": "Farmer"})
    booking = {
        "id": len(COLD_STORAGE_BOOKINGS) + 1,
        "farmer": user.get("name", "Farmer"),
        "storage_name": storage["name"],
        "crop": crop,
        "quantity_quintal": quantity_quintal,
        "days": days,
        "total_cost": round(quantity_quintal * days * storage["rate_per_quintal_per_day"]),
        "booked_at": datetime.now().strftime("%d %b %Y"),
    }
    COLD_STORAGE_BOOKINGS.append(booking)
    return jsonify({"status": "booked", "booking": booking})


@app.route("/api/cold_storage_bookings", methods=["GET"])
def cold_storage_bookings():
    return jsonify({"bookings": sorted(COLD_STORAGE_BOOKINGS, key=lambda b: -b["id"])})


# ---------------------------------------------------------------------------
# NEW: FPO / Group Selling — several small farmers pool the same crop
# together to sell in bulk, which fetches a noticeably better price than
# any one of them would get selling small quantities alone.
# ---------------------------------------------------------------------------
GROUP_SELLING_POOLS = [
    {"id": 1, "crop": "Rice", "location": "Cuttack", "target_quintal": 200, "current_quintal": 130, "solo_price": 2150, "pooled_price": 2340, "days_left": 4,
     "members": [{"farmer": "Ganesh Swain", "quantity_quintal": 60}, {"farmer": "Ratnakar Behera", "quantity_quintal": 70}]},
    {"id": 2, "crop": "Onion", "location": "Khordha", "target_quintal": 100, "current_quintal": 45, "solo_price": 1850, "pooled_price": 2050, "days_left": 6,
     "members": [{"farmer": "Sarita Mohanty", "quantity_quintal": 25}, {"farmer": "Debendra Sahu", "quantity_quintal": 20}]},
    {"id": 3, "crop": "Groundnut", "location": "Puri", "target_quintal": 80, "current_quintal": 80, "solo_price": 5600, "pooled_price": 6100, "days_left": 0,
     "members": [{"farmer": "Bijay Nayak", "quantity_quintal": 40}, {"farmer": "Ashok Jena", "quantity_quintal": 40}]},
]


@app.route("/api/group_selling_pools", methods=["GET"])
def group_selling_pools():
    crop = request.args.get("crop", "").strip().lower()
    pools = GROUP_SELLING_POOLS
    if crop:
        pools = [p for p in pools if crop in p["crop"].lower()] or pools
    out = []
    for p in pools:
        out.append({**p, "percent_filled": round(min(100, p["current_quintal"] / p["target_quintal"] * 100)),
                    "status": "Ready to Sell" if p["current_quintal"] >= p["target_quintal"] else "Filling Up"})
    return jsonify({"pools": sorted(out, key=lambda p: -p["id"])})


@app.route("/api/group_selling_join", methods=["POST"])
def group_selling_join():
    data = request.get_json(force=True, silent=True) or {}
    pool_id = data.get("pool_id")
    quantity_quintal = data.get("quantity_quintal")
    if not pool_id or not quantity_quintal:
        return jsonify({"status": "error", "message": "Pool aur quantity bharein"}), 400
    try:
        pool_id = int(pool_id)
        quantity_quintal = float(quantity_quintal)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Please enter valid numbers"}), 400

    pool = next((p for p in GROUP_SELLING_POOLS if p["id"] == pool_id), None)
    if not pool:
        return jsonify({"status": "error", "message": "Pool not found"}), 404

    user = session.get("user", {"name": "Farmer"})
    pool["current_quintal"] += quantity_quintal
    pool["members"].append({"farmer": user.get("name", "Farmer"), "quantity_quintal": quantity_quintal})
    return jsonify({"status": "joined", "pool": pool})


@app.route("/api/group_selling_create", methods=["POST"])
def group_selling_create():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "").strip()
    location = data.get("location", "Bhubaneswar (City Centre)")
    target_quintal = data.get("target_quintal")
    quantity_quintal = data.get("quantity_quintal")
    if not crop or not target_quintal or not quantity_quintal:
        return jsonify({"status": "error", "message": "Crop, target aur apni quantity bharein"}), 400
    try:
        target_quintal = float(target_quintal)
        quantity_quintal = float(quantity_quintal)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Please enter valid numbers"}), 400
    if target_quintal <= 0:
        return jsonify({"status": "error", "message": "Target quintal 0 se zyada hona chahiye"}), 400

    solo_price = {"Rice": 2150, "Wheat": 2000, "Maize": 1980, "Onion": 1850, "Groundnut": 5600, "Cotton": 6200, "Tomato": 1450, "Sugarcane": 340}.get(crop, 1800)
    user = session.get("user", {"name": "Farmer"})
    pool = {
        "id": len(GROUP_SELLING_POOLS) + 1,
        "crop": crop,
        "location": location,
        "target_quintal": target_quintal,
        "current_quintal": quantity_quintal,
        "solo_price": solo_price,
        "pooled_price": round(solo_price * 1.09),  # bulk selling typically fetches ~9% more
        "days_left": 7,
        "members": [{"farmer": user.get("name", "Farmer"), "quantity_quintal": quantity_quintal}],
    }
    GROUP_SELLING_POOLS.append(pool)
    return jsonify({"status": "created", "pool": pool})


# ---------------------------------------------------------------------------
# NEW: Season Budget Planner — before sowing, a farmer can see the full
# expected cost breakdown (seeds, fertilizer, pesticide, labour, irrigation)
# for a crop + area, alongside expected revenue and profit, instead of
# discovering the real cost only after it's already spent.
# ---------------------------------------------------------------------------
CROP_BUDGET_PER_ACRE = {
    # values: cost components in ₹ per acre, expected_yield in quintal/acre, market_price in ₹/quintal
    "Rice":      {"seeds": 1200, "fertilizer": 3500, "pesticide": 1800, "labour": 6000, "irrigation": 2500, "expected_yield_quintal": 22, "market_price": 2150},
    "Wheat":     {"seeds": 1600, "fertilizer": 3200, "pesticide": 1200, "labour": 4500, "irrigation": 1800, "expected_yield_quintal": 18, "market_price": 2000},
    "Maize":     {"seeds": 1400, "fertilizer": 2800, "pesticide": 1000, "labour": 4000, "irrigation": 1600, "expected_yield_quintal": 20, "market_price": 1980},
    "Sugarcane": {"seeds": 5000, "fertilizer": 6000, "pesticide": 2200, "labour": 9000, "irrigation": 4000, "expected_yield_quintal": 350, "market_price": 340},
    "Groundnut": {"seeds": 3500, "fertilizer": 2500, "pesticide": 1500, "labour": 5000, "irrigation": 2000, "expected_yield_quintal": 9, "market_price": 5600},
    "Cotton":    {"seeds": 2200, "fertilizer": 3800, "pesticide": 3500, "labour": 6500, "irrigation": 2200, "expected_yield_quintal": 7, "market_price": 6200},
    "Tomato":    {"seeds": 2800, "fertilizer": 3200, "pesticide": 2500, "labour": 7000, "irrigation": 2800, "expected_yield_quintal": 60, "market_price": 1450},
    "Onion":     {"seeds": 3200, "fertilizer": 2800, "pesticide": 1800, "labour": 6000, "irrigation": 2200, "expected_yield_quintal": 45, "market_price": 1850},
}


@app.route("/api/budget_planner", methods=["POST"])
def budget_planner():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "Rice")
    try:
        area_acres = float(data.get("area_acres", 1))
    except (TypeError, ValueError):
        area_acres = 1.0

    base = CROP_BUDGET_PER_ACRE.get(crop, CROP_BUDGET_PER_ACRE["Rice"])
    cost_items = {
        "Seeds": round(base["seeds"] * area_acres),
        "Fertilizer": round(base["fertilizer"] * area_acres),
        "Pesticide": round(base["pesticide"] * area_acres),
        "Labour": round(base["labour"] * area_acres),
        "Irrigation": round(base["irrigation"] * area_acres),
    }
    total_cost = sum(cost_items.values())
    expected_yield = round(base["expected_yield_quintal"] * area_acres, 1)
    expected_revenue = round(expected_yield * base["market_price"])
    expected_profit = expected_revenue - total_cost

    return jsonify({
        "crop": crop,
        "area_acres": area_acres,
        "cost_items": cost_items,
        "total_cost": total_cost,
        "expected_yield_quintal": expected_yield,
        "market_price_per_quintal": base["market_price"],
        "expected_revenue": expected_revenue,
        "expected_profit": expected_profit,
    })


# ---------------------------------------------------------------------------
# NEW: Push Notifications — real browser Web Push for price alerts, weather
# warnings, and disease outbreak alerts, so a farmer gets notified even when
# the app isn't open. Uses the standard VAPID Web Push protocol.
#
# Works in two modes:
#   1. Real push — if VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY are set (see
#      .env.example) AND the `pywebpush` package is installed, notifications
#      are actually delivered to the browser/OS notification tray.
#   2. Demo mode — if either is missing, subscriptions are still stored and
#      every "sent" notification is logged to an in-memory history that the
#      Alerts page can show, so the feature is fully demoable without any
#      paid/real infra.
# ---------------------------------------------------------------------------
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:support@agrostar.example")

PUSH_SUBSCRIPTIONS = []   # [{id, farmer, subscription, topics}]
NOTIFICATION_HISTORY = []  # in-memory log of every notification "sent" (real or demo)
PRICE_ALERTS = []          # [{id, farmer, crop, location, threshold_price, direction, triggered}]


def send_push_notification(subscription_info, title, body, url="/"):
    """Attempts a real Web Push delivery. Returns True only if it was
    actually sent; False (never raises) if pywebpush isn't installed, VAPID
    keys aren't configured, or the push fails for any reason (expired
    subscription, network issue, etc.) — caller should treat False as
    'logged in demo mode' rather than an error."""
    if not (VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY):
        return False
    try:
        from pywebpush import webpush, WebPushException  # optional dependency
    except ImportError:
        app.logger.info("pywebpush not installed — push notification logged in demo mode only.")
        return False
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_CLAIM_EMAIL},
        )
        return True
    except Exception as e:
        app.logger.warning(f"Push notification failed, logged in demo mode instead: {e}")
        return False


def broadcast_push_topic(topic, title, body, url="/"):
    """Sends (or demo-logs) a notification to every subscriber of a topic
    ('price', 'weather', or 'disease'). Always records to
    NOTIFICATION_HISTORY regardless of whether real delivery succeeded, so
    the in-app Alerts/notification history stays accurate either way."""
    sent_count = 0
    for sub in PUSH_SUBSCRIPTIONS:
        if topic not in sub.get("topics", []):
            continue
        delivered = send_push_notification(sub["subscription"], title, body, url)
        if delivered:
            sent_count += 1

    NOTIFICATION_HISTORY.insert(0, {
        "id": len(NOTIFICATION_HISTORY) + 1,
        "topic": topic,
        "title": title,
        "body": body,
        "sent_at": datetime.now().strftime("%d %b, %I:%M %p"),
        "delivered_real_push": sent_count > 0,
        "recipient_count": max(sent_count, 1),  # at least 1 for demo visibility
    })
    return sent_count


@app.route("/api/push_public_key", methods=["GET"])
def push_public_key():
    return jsonify({"public_key": VAPID_PUBLIC_KEY, "configured": bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)})


@app.route("/api/push_subscribe", methods=["POST"])
def push_subscribe():
    data = request.get_json(force=True, silent=True) or {}
    subscription = data.get("subscription")
    topics = data.get("topics") or ["price", "weather", "disease"]
    if not subscription or not subscription.get("endpoint"):
        return jsonify({"status": "error", "message": "Invalid subscription"}), 400

    user = session.get("user", {"name": "Farmer"})
    # Replace any existing subscription with the same endpoint (re-subscribe / topic update)
    endpoint = subscription["endpoint"]
    global PUSH_SUBSCRIPTIONS
    PUSH_SUBSCRIPTIONS = [s for s in PUSH_SUBSCRIPTIONS if s["subscription"]["endpoint"] != endpoint]
    PUSH_SUBSCRIPTIONS.append({
        "id": len(PUSH_SUBSCRIPTIONS) + 1,
        "farmer": user.get("name", "Farmer"),
        "subscription": subscription,
        "topics": topics,
    })
    return jsonify({"status": "subscribed", "topics": topics, "real_push_configured": bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)})


@app.route("/api/push_unsubscribe", methods=["POST"])
def push_unsubscribe():
    data = request.get_json(force=True, silent=True) or {}
    endpoint = data.get("endpoint")
    global PUSH_SUBSCRIPTIONS
    PUSH_SUBSCRIPTIONS = [s for s in PUSH_SUBSCRIPTIONS if s["subscription"]["endpoint"] != endpoint]
    return jsonify({"status": "unsubscribed"})


@app.route("/api/push_test", methods=["POST"])
def push_test():
    data = request.get_json(force=True, silent=True) or {}
    title = data.get("title", "AgroStar Test Alert")
    body = data.get("body", "This is a test notification — push alerts are working! 🌾")
    sent = broadcast_push_topic("test", title, body, "/feature/notification-alerts")
    return jsonify({
        "status": "sent",
        "real_push_delivered": sent > 0,
        "message": "Real push sent!" if sent > 0 else "Logged in demo mode (no VAPID keys / no real subscribers) — check notification history below.",
    })


@app.route("/api/notifications", methods=["GET"])
def get_notifications():
    return jsonify({"notifications": NOTIFICATION_HISTORY[:30]})


# ---------------------------------------------------------------------------
# NEW: Price Alerts — farmer sets a crop + threshold price; checking (button
# or periodic client-side poll) compares against current mandi price and
# pushes a notification the moment it crosses the threshold.
# ---------------------------------------------------------------------------
@app.route("/api/price_alerts", methods=["GET"])
def get_price_alerts():
    return jsonify({"alerts": sorted(PRICE_ALERTS, key=lambda a: -a["id"])})


@app.route("/api/price_alert", methods=["POST"])
def create_price_alert():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "").strip()
    location = data.get("location", "Bhubaneswar (City Centre)")
    threshold_price = data.get("threshold_price")
    direction = data.get("direction", "above")  # "above" or "below"
    if not crop or not threshold_price:
        return jsonify({"status": "error", "message": "Crop aur threshold price bharein"}), 400
    try:
        threshold_price = float(threshold_price)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Please enter a valid threshold price"}), 400

    user = session.get("user", {"name": "Farmer"})
    alert = {
        "id": len(PRICE_ALERTS) + 1,
        "farmer": user.get("name", "Farmer"),
        "crop": crop,
        "location": location,
        "threshold_price": threshold_price,
        "direction": direction,
        "triggered": False,
    }
    PRICE_ALERTS.append(alert)
    return jsonify({"status": "created", "alert": alert})


@app.route("/api/price_alert/<int:alert_id>", methods=["DELETE"])
def delete_price_alert(alert_id):
    global PRICE_ALERTS
    PRICE_ALERTS = [a for a in PRICE_ALERTS if a["id"] != alert_id]
    return jsonify({"status": "deleted"})


@app.route("/api/check_price_alerts", methods=["POST"])
def check_price_alerts():
    """Fetches current mock mandi prices and checks every un-triggered
    alert against them, pushing a notification for any that just crossed
    their threshold. Meant to be called periodically from the client (or
    tapped manually) since this demo has no background job scheduler."""
    triggered_now = []
    # Group alerts by (crop, location) so we only "fetch" price once per pair
    price_cache = {}
    for alert in PRICE_ALERTS:
        if alert["triggered"]:
            continue
        key = (alert["crop"], alert["location"])
        if key not in price_cache:
            price_cache[key] = random.randint(1800, 4500)  # same mock model as /api/market_prices
        current_price = price_cache[key]

        crossed = (alert["direction"] == "above" and current_price >= alert["threshold_price"]) or \
                  (alert["direction"] == "below" and current_price <= alert["threshold_price"])
        if crossed:
            alert["triggered"] = True
            alert["current_price"] = current_price
            direction_word = "crossed above" if alert["direction"] == "above" else "dropped below"
            title = f"💰 {alert['crop']} price alert"
            body = f"{alert['crop']} in {alert['location']} has {direction_word} ₹{alert['threshold_price']}/quintal (now ₹{current_price})."
            broadcast_push_topic("price", title, body, "/feature/agri-shop-prices")
            triggered_now.append(alert)

    return jsonify({"checked": len([a for a in PRICE_ALERTS if not a["triggered"]]) + len(triggered_now), "triggered": triggered_now})


# ---------------------------------------------------------------------------
# NEW: Weather Alert Check — reuses the same forecast pipeline as
# /api/irrigation_schedule; pushes a notification if heavy rain or extreme
# heat is expected, so a farmer knows even without opening the app.
# ---------------------------------------------------------------------------
@app.route("/api/check_weather_alerts", methods=["POST"])
def check_weather_alerts():
    data = request.get_json(force=True, silent=True) or {}
    location = data.get("location", "Bhubaneswar (City Centre)")
    lat, lon = LOCATION_COORDS.get(location, (20.2961, 85.8245))

    try:
        fdata = _fetch_open_meteo(lat, lon)
        precip_sums = (fdata.get("daily") or {}).get("precipitation_sum") or []
        max_temps = (fdata.get("daily") or {}).get("temperature_2m_max") or []
        rain_next_2_days = sum(precip_sums[:2]) if precip_sums else 0
        peak_temp = max(max_temps[:2]) if max_temps else 0
    except Exception:
        days, _ = _mock_weather_forecast()
        rain_next_2_days = sum(d["rain_mm"] for d in days[:2])
        peak_temp = max((d.get("temp_max", 32) for d in days[:2]), default=32)

    alerts_sent = []
    if rain_next_2_days >= 40:
        title = "🌧️ Heavy Rain Warning"
        body = f"{location}: ~{round(rain_next_2_days)}mm rain expected in the next 2 days — protect stored produce and check drainage."
        broadcast_push_topic("weather", title, body, "/feature/irrigation-scheduler")
        alerts_sent.append({"type": "heavy_rain", "value_mm": round(rain_next_2_days, 1)})
    if peak_temp >= 42:
        title = "🌡️ Extreme Heat Warning"
        body = f"{location}: temperatures near {round(peak_temp)}°C expected — irrigate early morning/evening and avoid midday fieldwork."
        broadcast_push_topic("weather", title, body, "/feature/irrigation-scheduler")
        alerts_sent.append({"type": "extreme_heat", "value_c": round(peak_temp, 1)})

    return jsonify({"location": location, "rain_next_2_days_mm": round(rain_next_2_days, 1), "peak_temp_c": round(peak_temp, 1), "alerts_sent": alerts_sent})


# ---------------------------------------------------------------------------
# NEW: Farmer Reels — short (30-60s) videos farmers upload themselves to
# share tips or success stories, not just watch. Videos are saved to
# static/uploads/reels/ and served back as normal static files; metadata
# (caption, crop tag, likes, views) lives in memory for the demo.
# ---------------------------------------------------------------------------
REELS_UPLOAD_DIR = os.path.join(app.root_path, "static", "uploads", "reels")
os.makedirs(REELS_UPLOAD_DIR, exist_ok=True)

ALLOWED_VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "m4v"}
MAX_REEL_SIZE_BYTES = 60 * 1024 * 1024  # 60MB — enough for a 30-60s clip at reasonable quality

REELS = [
    {"id": 1, "farmer": "Ganesh Swain, Cuttack", "caption": "Vermicompost banane ka aasan tarika — 3 hafte me tayyar!",
     "crop_tag": "Soil", "video_url": None, "likes": 42, "views": 310, "posted": "2 days ago"},
    {"id": 2, "farmer": "Sarita Mohanty, Khordha", "caption": "Is saal tomato me early blight ko aise pehchana aur bachaya.",
     "crop_tag": "Pest Control", "video_url": None, "likes": 28, "views": 205, "posted": "4 days ago"},
    {"id": 3, "farmer": "Bijay Nayak, Puri", "caption": "Drip irrigation lagane ke baad paani ka kharcha aadha ho gaya.",
     "crop_tag": "Water", "video_url": None, "likes": 55, "views": 480, "posted": "1 week ago"},
]
REEL_LIKED_BY_SESSION = {}  # reel_id -> set of session ids that already liked it (demo like-toggle)


def _allowed_video_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS


@app.route("/api/reels", methods=["GET"])
def get_reels():
    crop_tag = request.args.get("crop_tag", "").strip()
    reels = REELS
    if crop_tag:
        reels = [r for r in reels if r["crop_tag"] == crop_tag] or reels
    return jsonify({"reels": sorted(reels, key=lambda r: -r["id"]),
                     "crop_tags": sorted(set(r["crop_tag"] for r in REELS))})


@app.route("/api/reel_upload", methods=["POST"])
def reel_upload():
    caption = request.form.get("caption", "").strip()
    crop_tag = request.form.get("crop_tag", "General")
    video_file = request.files.get("video")

    if not caption:
        return jsonify({"status": "error", "message": "Ek chhota caption likhein"}), 400
    if not video_file or not video_file.filename:
        return jsonify({"status": "error", "message": "Video file chunein (30-60 second)"}), 400
    if not _allowed_video_file(video_file.filename):
        return jsonify({"status": "error", "message": "Sirf MP4, WebM, MOV, ya M4V video allowed hain"}), 400

    # Enforce size limit by reading length (small demo-scale check; a
    # production app would stream/validate this at the proxy layer instead).
    video_file.stream.seek(0, os.SEEK_END)
    size = video_file.stream.tell()
    video_file.stream.seek(0)
    if size > MAX_REEL_SIZE_BYTES:
        return jsonify({"status": "error", "message": "Video 60MB se bada hai — chhota video try karein"}), 400

    ext = video_file.filename.rsplit(".", 1)[1].lower()
    safe_name = f"{uuid.uuid4().hex}.{ext}"
    safe_name = secure_filename(safe_name)
    video_file.save(os.path.join(REELS_UPLOAD_DIR, safe_name))

    user = session.get("user", {"name": "Farmer"})
    reel = {
        "id": len(REELS) + 1,
        "farmer": user.get("name", "Farmer"),
        "caption": caption,
        "crop_tag": crop_tag,
        "video_url": url_for("static", filename=f"uploads/reels/{safe_name}"),
        "likes": 0,
        "views": 0,
        "posted": "Just now",
    }
    REELS.append(reel)
    return jsonify({"status": "uploaded", "reel": reel})


@app.route("/api/reel_like", methods=["POST"])
def reel_like():
    data = request.get_json(force=True, silent=True) or {}
    reel_id = data.get("reel_id")
    reel = next((r for r in REELS if r["id"] == reel_id), None)
    if not reel:
        return jsonify({"status": "error", "message": "Reel not found"}), 404

    session_id = session.get("_id") or session.get("user", {}).get("name", "anon")
    liked_set = REEL_LIKED_BY_SESSION.setdefault(reel_id, set())
    if session_id in liked_set:
        liked_set.discard(session_id)
        reel["likes"] = max(0, reel["likes"] - 1)
        liked = False
    else:
        liked_set.add(session_id)
        reel["likes"] += 1
        liked = True
    return jsonify({"status": "ok", "likes": reel["likes"], "liked": liked})


@app.route("/api/reel_view", methods=["POST"])
def reel_view():
    data = request.get_json(force=True, silent=True) or {}
    reel_id = data.get("reel_id")
    reel = next((r for r in REELS if r["id"] == reel_id), None)
    if not reel:
        return jsonify({"status": "error", "message": "Reel not found"}), 404
    reel["views"] += 1
    return jsonify({"status": "ok", "views": reel["views"]})


# ---------------------------------------------------------------------------
# NEW: Leaderboard — expands the existing points/badges gamification (see
# /api/gamification above) into a community-wide ranking across three
# categories (sustainability, app usage, community help), so farmers see
# where they stand against others, not just their own progress.
# ---------------------------------------------------------------------------
LEADERBOARD_FARMERS = [
    {"name": "Ganesh Swain", "location": "Cuttack", "sustainability_points": 420, "usage_points": 310, "community_points": 260},
    {"name": "Sarita Mohanty", "location": "Khordha", "sustainability_points": 380, "usage_points": 290, "community_points": 340},
    {"name": "Bijay Nayak", "location": "Puri", "sustainability_points": 510, "usage_points": 180, "community_points": 220},
    {"name": "Ratnakar Behera", "location": "Cuttack", "sustainability_points": 250, "usage_points": 400, "community_points": 150},
    {"name": "Debendra Sahu", "location": "Puri", "sustainability_points": 300, "usage_points": 220, "community_points": 410},
    {"name": "Ashok Jena", "location": "Pipili", "sustainability_points": 190, "usage_points": 260, "community_points": 180},
]

LEADERBOARD_BADGES = ALL_BADGES + [
    {"id": "sustainability-star", "name": "Sustainability Star", "desc": "Scored 400+ on the Sustainability Score feature"},
    {"id": "community-helper", "name": "Community Helper", "desc": "Got 20+ likes across Community posts"},
    {"id": "reel-creator", "name": "Reel Creator", "desc": "Uploaded a Farmer Reel"},
    {"id": "recruiter", "name": "Recruiter", "desc": "Referred a friend who joined AgroStar"},
]


@app.route("/api/leaderboard", methods=["GET"])
def leaderboard():
    category = request.args.get("category", "total")  # total, sustainability, usage, community
    user = session.get("user", {"name": "Farmer"})

    rows = []
    for f in LEADERBOARD_FARMERS:
        total = f["sustainability_points"] + f["usage_points"] + f["community_points"]
        rows.append({**f, "total_points": total})

    # Insert the current session's own farmer using their real gamification progress,
    # so they can see their own rank alongside the seeded community.
    my_total = FARMER_PROGRESS["points"]
    rows.append({
        "name": user.get("name", "You"), "location": "You", "is_you": True,
        "sustainability_points": my_total // 3, "usage_points": my_total // 3,
        "community_points": my_total - 2 * (my_total // 3), "total_points": my_total,
    })

    sort_key = {
        "sustainability": "sustainability_points", "usage": "usage_points",
        "community": "community_points", "total": "total_points",
    }.get(category, "total_points")
    rows.sort(key=lambda r: -r[sort_key])
    for i, r in enumerate(rows):
        r["rank"] = i + 1

    my_rank = next((r["rank"] for r in rows if r.get("is_you")), None)
    return jsonify({"category": category, "leaderboard": rows[:10], "my_rank": my_rank, "total_farmers": len(rows)})


# ---------------------------------------------------------------------------
# NEW: Referral Program — a farmer shares their own code/link with a
# friend; when the friend "redeems" it, both get reward points. Since this
# demo has no multi-user backend, the redeem endpoint simulates a friend
# using the code so the full reward flow can still be shown end-to-end.
# ---------------------------------------------------------------------------
REFERRAL_PROGRESS = {"invites_sent": 0, "successful_referrals": 0, "reward_points": 0}
REFERRAL_LOG = []  # [{id, code, redeemed_by, reward_points, redeemed_at}]
REWARD_POINTS_PER_REFERRAL = 100


def _get_referral_code():
    user = session.get("user", {"name": "Farmer"})
    name = user.get("name", "Farmer")
    # Deterministic short code from the farmer's name so it's stable across requests in this session.
    base = re.sub(r"[^A-Z0-9]", "", name.upper())[:5] or "FARM"
    digest = str(abs(hash(name)) % 10000).zfill(4)
    return f"AGRO-{base}{digest}"


@app.route("/api/referral_info", methods=["GET"])
def referral_info():
    code = _get_referral_code()
    return jsonify({
        "code": code,
        "share_link": f"https://agrostar.example/join?ref={code}",
        "invites_sent": REFERRAL_PROGRESS["invites_sent"],
        "successful_referrals": REFERRAL_PROGRESS["successful_referrals"],
        "reward_points": REFERRAL_PROGRESS["reward_points"],
        "reward_per_referral": REWARD_POINTS_PER_REFERRAL,
    })


@app.route("/api/referral_share", methods=["POST"])
def referral_share():
    """Called when the farmer taps a share button (WhatsApp/SMS/copy link) —
    just tracks that an invite went out, for their own stats."""
    REFERRAL_PROGRESS["invites_sent"] += 1
    return jsonify({"status": "ok", "invites_sent": REFERRAL_PROGRESS["invites_sent"]})


@app.route("/api/referral_redeem", methods=["POST"])
def referral_redeem():
    data = request.get_json(force=True, silent=True) or {}
    code = data.get("code", "").strip().upper()
    friend_name = data.get("friend_name", "A friend").strip() or "A friend"

    if not code:
        return jsonify({"status": "error", "message": "Referral code bharein"}), 400
    if code != _get_referral_code():
        return jsonify({"status": "error", "message": "Ye code match nahi karta"}), 400

    REFERRAL_PROGRESS["successful_referrals"] += 1
    REFERRAL_PROGRESS["reward_points"] += REWARD_POINTS_PER_REFERRAL
    FARMER_PROGRESS["points"] += REWARD_POINTS_PER_REFERRAL
    if "recruiter" not in FARMER_PROGRESS["badges_earned"]:
        FARMER_PROGRESS["badges_earned"].append("recruiter")

    entry = {
        "id": len(REFERRAL_LOG) + 1,
        "code": code,
        "redeemed_by": friend_name,
        "reward_points": REWARD_POINTS_PER_REFERRAL,
        "redeemed_at": datetime.now().strftime("%d %b, %I:%M %p"),
    }
    REFERRAL_LOG.append(entry)
    return jsonify({
        "status": "redeemed",
        "entry": entry,
        "your_new_points": FARMER_PROGRESS["points"],
        "message": f"🎉 {friend_name} joined using your code! You both earned {REWARD_POINTS_PER_REFERRAL} points.",
    })


@app.route("/api/referral_log", methods=["GET"])
def referral_log():
    return jsonify({"log": sorted(REFERRAL_LOG, key=lambda e: -e["id"])})


# ---------------------------------------------------------------------------
# NEW: Insurance Claim Filing & Tracking — extends the existing PMFBY
# premium calculator (/api/insurance_check above) with the part that
# actually matters after a crop loss: filing a claim and tracking its
# status, instead of a farmer only ever seeing the premium estimate.
# ---------------------------------------------------------------------------
INSURANCE_CLAIMS = []  # [{id, farmer, crop, area, loss_type, loss_percent, sum_insured, estimated_payout, status, filed_at, status_log}]

CLAIM_STATUS_SEQUENCE = ["Filed", "Under Review", "Approved", "Payout Processed"]
CLAIM_STATUS_NOTES = {
    "Filed": "Your claim has been submitted to the insurance company via the PMFBY portal.",
    "Under Review": "A local surveyor is assessing crop loss for your area based on satellite/field data.",
    "Approved": "Loss assessment complete — your claim has been approved for payout.",
    "Payout Processed": "Payout has been credited to your linked bank account.",
}


@app.route("/api/insurance_claim", methods=["POST"])
def file_insurance_claim():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "Rice")
    try:
        area = float(data.get("area", 1))
        loss_percent = float(data.get("loss_percent", 30))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Please enter valid numbers"}), 400
    loss_type = data.get("loss_type", "Drought")

    sum_insured_per_ha = {"Rice": 40000, "Wheat": 35000, "Maize": 30000, "Sugarcane": 90000}.get(crop, 30000)
    sum_insured = round(sum_insured_per_ha * area)
    estimated_payout = round(sum_insured * min(loss_percent, 100) / 100)

    user = session.get("user", {"name": "Farmer"})
    claim = {
        "id": len(INSURANCE_CLAIMS) + 1,
        "farmer": user.get("name", "Farmer"),
        "crop": crop,
        "area": area,
        "loss_type": loss_type,
        "loss_percent": loss_percent,
        "sum_insured": sum_insured,
        "estimated_payout": estimated_payout,
        "status": "Filed",
        "filed_at": datetime.now().strftime("%d %b %Y"),
        "status_log": [{"status": "Filed", "date": datetime.now().strftime("%d %b %Y"), "note": CLAIM_STATUS_NOTES["Filed"]}],
    }
    INSURANCE_CLAIMS.append(claim)
    return jsonify({"status": "filed", "claim": claim})


@app.route("/api/insurance_claims", methods=["GET"])
def get_insurance_claims():
    return jsonify({"claims": sorted(INSURANCE_CLAIMS, key=lambda c: -c["id"])})


@app.route("/api/insurance_claim/<int:claim_id>/advance", methods=["POST"])
def advance_insurance_claim(claim_id):
    """Demo-only: since there's no real insurer backend to poll, this
    simulates the claim moving to its next real-world status one step at a
    time (Filed -> Under Review -> Approved -> Payout Processed)."""
    claim = next((c for c in INSURANCE_CLAIMS if c["id"] == claim_id), None)
    if not claim:
        return jsonify({"status": "error", "message": "Claim not found"}), 404

    current_idx = CLAIM_STATUS_SEQUENCE.index(claim["status"])
    if current_idx >= len(CLAIM_STATUS_SEQUENCE) - 1:
        return jsonify({"status": "no_change", "claim": claim, "message": "Claim is already fully processed."})

    next_status = CLAIM_STATUS_SEQUENCE[current_idx + 1]
    claim["status"] = next_status
    claim["status_log"].append({
        "status": next_status,
        "date": datetime.now().strftime("%d %b %Y"),
        "note": CLAIM_STATUS_NOTES[next_status],
    })
    return jsonify({"status": "advanced", "claim": claim})


# ---------------------------------------------------------------------------
# NEW: Nearest Mandi Locator — finds the closest mandis to the farmer
# (using browser GPS if available, else their selected location) with
# distance and an estimated travel time, instead of Market Prices only
# showing bhav with no sense of which mandi is actually reachable.
# ---------------------------------------------------------------------------
MANDIS = [
    {"name": "Bhubaneswar Regulated Market (Unit-4)", "location": "Bhubaneswar (City Centre)", "crops": ["Rice", "Vegetables", "Groundnut"]},
    {"name": "Cuttack Main Mandi", "location": "Cuttack", "crops": ["Rice", "Onion", "Wheat"]},
    {"name": "Khordha Agri Market Yard", "location": "Khordha", "crops": ["Onion", "Tomato", "Groundnut"]},
    {"name": "Puri Wholesale Market", "location": "Puri", "crops": ["Groundnut", "Vegetables", "Fish/Seafood"]},
    {"name": "Jatani Farmers' Mandi", "location": "Jatani", "crops": ["Maize", "Rice", "Vegetables"]},
    {"name": "Banki Grain Market", "location": "Banki", "crops": ["Rice", "Maize", "Pulses"]},
    {"name": "Pipili Vegetable Market", "location": "Pipili", "crops": ["Vegetables", "Onion", "Tomato"]},
    {"name": "Tangi Regulated Market", "location": "Tangi", "crops": ["Rice", "Groundnut"]},
]

AVG_RURAL_ROAD_SPEED_KMPH = 35  # used only to estimate travel time for the demo


@app.route("/api/nearest_mandis", methods=["POST"])
def nearest_mandis():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "").strip()
    lat, lng = data.get("lat"), data.get("lng")

    if lat is not None and lng is not None:
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            lat = lng = None

    if lat is None or lng is None:
        location = data.get("location", "Bhubaneswar (City Centre)")
        lat, lng = LOCATION_COORDS.get(location, (20.2961, 85.8245))

    rows = MANDIS
    if crop:
        rows = [m for m in rows if crop in m["crops"]] or rows

    out = []
    for m in rows:
        mlat, mlng = LOCATION_COORDS.get(m["location"], (20.2961, 85.8245))
        dist = _haversine_km(lat, lng, mlat, mlng)
        eta_minutes = round((dist / AVG_RURAL_ROAD_SPEED_KMPH) * 60)
        out.append({**m, "distance_km": round(dist, 1), "eta_minutes": eta_minutes})

    out.sort(key=lambda m: m["distance_km"])
    return jsonify({"mandis": out})


# ---------------------------------------------------------------------------
# NEW: Labour / Mazdoor On-Demand — same idea as Equipment Rental, but for
# people: a farmer can find nearby workers for harvest/sowing/weeding, or
# register themselves as available for wage work on other farms (very
# common as supplementary income), and post open labour requirements.
# ---------------------------------------------------------------------------
LABOUR_WORKERS = [
    {"id": 1, "name": "Suresh Nayak", "skill": "Harvesting", "daily_rate": 450, "location": "Cuttack", "contact": "+91 98111 22001", "available": True},
    {"id": 2, "name": "Meena Behera", "skill": "Transplanting", "daily_rate": 400, "location": "Khordha", "contact": "+91 97222 33002", "available": True},
    {"id": 3, "name": "Ramesh Patra", "skill": "Weeding", "daily_rate": 380, "location": "Jatani", "contact": "+91 96333 44003", "available": True},
    {"id": 4, "name": "Sabitri Jena", "skill": "Harvesting", "daily_rate": 450, "location": "Puri", "contact": "+91 95444 55004", "available": False},
    {"id": 5, "name": "Dilip Sahu", "skill": "Spraying / Pesticide", "daily_rate": 500, "location": "Banki", "contact": "+91 94555 66005", "available": True},
    {"id": 6, "name": "Anita Swain", "skill": "Sowing", "daily_rate": 420, "location": "Pipili", "contact": "+91 93666 77006", "available": True},
]
LABOUR_REQUIREMENTS = [
    {"id": 1, "farmer": "Ganesh Swain", "work_type": "Harvesting", "workers_needed": 4, "date_needed": "3 days from now", "location": "Cuttack", "rate_offered": 450, "contact": "+91 98111 22334"},
    {"id": 2, "farmer": "Bijay Nayak", "work_type": "Transplanting", "workers_needed": 6, "date_needed": "This week", "location": "Puri", "rate_offered": 400, "contact": "+91 94555 66778"},
]


@app.route("/api/labour_workers", methods=["GET"])
def labour_workers():
    location = request.args.get("location", "").strip()
    skill = request.args.get("skill", "").strip()
    rows = LABOUR_WORKERS
    if location:
        filtered = [w for w in rows if w["location"] == location]
        rows = filtered if filtered else rows
    if skill:
        rows = [w for w in rows if w["skill"] == skill] or rows
    return jsonify({"workers": sorted(rows, key=lambda w: -w["id"]),
                     "skills": sorted(set(w["skill"] for w in LABOUR_WORKERS))})


@app.route("/api/labour_worker_register", methods=["POST"])
def labour_worker_register():
    data = request.get_json(force=True, silent=True) or {}
    skill = data.get("skill", "").strip()
    daily_rate = data.get("daily_rate")
    location = data.get("location", "Bhubaneswar (City Centre)")
    contact = data.get("contact", "").strip()
    if not skill or not daily_rate:
        return jsonify({"status": "error", "message": "Skill aur daily rate bharein"}), 400
    try:
        daily_rate = float(daily_rate)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Please enter a valid daily rate"}), 400

    user = session.get("user", {"name": "Farmer"})
    worker = {
        "id": len(LABOUR_WORKERS) + 1,
        "name": user.get("name", "Farmer"),
        "skill": skill,
        "daily_rate": daily_rate,
        "location": location,
        "contact": contact or "—",
        "available": True,
    }
    LABOUR_WORKERS.append(worker)
    return jsonify({"status": "registered", "worker": worker})


@app.route("/api/labour_requirements", methods=["GET"])
def get_labour_requirements():
    location = request.args.get("location", "").strip()
    rows = LABOUR_REQUIREMENTS
    if location:
        filtered = [r for r in rows if r["location"] == location]
        rows = filtered if filtered else rows
    return jsonify({"requirements": sorted(rows, key=lambda r: -r["id"])})


@app.route("/api/labour_requirement", methods=["POST"])
def add_labour_requirement():
    data = request.get_json(force=True, silent=True) or {}
    work_type = data.get("work_type", "").strip()
    workers_needed = data.get("workers_needed")
    date_needed = data.get("date_needed", "").strip()
    location = data.get("location", "Bhubaneswar (City Centre)")
    rate_offered = data.get("rate_offered")
    if not work_type or not workers_needed or not rate_offered:
        return jsonify({"status": "error", "message": "Work type, workers needed aur rate bharein"}), 400
    try:
        workers_needed = int(workers_needed)
        rate_offered = float(rate_offered)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Please enter valid numbers"}), 400

    user = session.get("user", {"name": "Farmer"})
    requirement = {
        "id": len(LABOUR_REQUIREMENTS) + 1,
        "farmer": user.get("name", "Farmer"),
        "work_type": work_type,
        "workers_needed": workers_needed,
        "date_needed": date_needed or "Flexible",
        "location": location,
        "rate_offered": rate_offered,
        "contact": data.get("contact", "—"),
    }
    LABOUR_REQUIREMENTS.append(requirement)
    return jsonify({"status": "posted", "requirement": requirement})


# ---------------------------------------------------------------------------
# NEW: KVK (Krishi Vigyan Kendra) Expert Call Booking — when the Aasha
# chatbot or a video tutorial isn't enough, a farmer can book a real voice/
# video call slot with a KVK subject-matter expert (soil science, plant
# pathology, entomology, horticulture, animal husbandry) instead of having
# no path to a human expert at all.
# ---------------------------------------------------------------------------
KVK_EXPERTS = [
    {"id": 1, "name": "Dr. Priyanka Rout", "specialization": "Plant Pathology", "kvk_name": "KVK Khordha",
     "location": "Khordha", "slots": ["Tomorrow 10:00 AM", "Tomorrow 3:00 PM", "Day after 11:00 AM"]},
    {"id": 2, "name": "Dr. Manas Mohapatra", "specialization": "Soil Science", "kvk_name": "KVK Cuttack",
     "location": "Cuttack", "slots": ["Tomorrow 11:00 AM", "Day after 10:00 AM", "Day after 2:00 PM"]},
    {"id": 3, "name": "Dr. Snehalata Nayak", "specialization": "Entomology (Pest Control)", "kvk_name": "KVK Puri",
     "location": "Puri", "slots": ["Today 4:00 PM", "Tomorrow 10:00 AM", "Tomorrow 1:00 PM"]},
    {"id": 4, "name": "Dr. Rajesh Pradhan", "specialization": "Horticulture", "kvk_name": "KVK Khordha",
     "location": "Khordha", "slots": ["Tomorrow 2:00 PM", "Day after 10:00 AM"]},
    {"id": 5, "name": "Dr. Anita Behera", "specialization": "Animal Husbandry", "kvk_name": "KVK Cuttack",
     "location": "Cuttack", "slots": ["Today 5:00 PM", "Tomorrow 9:00 AM", "Tomorrow 4:00 PM"]},
]
KVK_BOOKINGS = []  # [{id, farmer, expert_name, specialization, kvk_name, slot, topic, contact, booked_at}]


@app.route("/api/kvk_experts", methods=["GET"])
def kvk_experts():
    location = request.args.get("location", "").strip()
    specialization = request.args.get("specialization", "").strip()
    rows = KVK_EXPERTS
    if location:
        filtered = [e for e in rows if e["location"] == location]
        rows = filtered if filtered else rows
    if specialization:
        rows = [e for e in rows if e["specialization"] == specialization] or rows
    return jsonify({"experts": rows, "specializations": sorted(set(e["specialization"] for e in KVK_EXPERTS))})


@app.route("/api/kvk_booking", methods=["POST"])
def kvk_booking():
    data = request.get_json(force=True, silent=True) or {}
    expert_id = data.get("expert_id")
    slot = data.get("slot", "").strip()
    topic = data.get("topic", "").strip()
    contact = data.get("contact", "").strip()

    try:
        expert_id = int(expert_id) if expert_id is not None else 0
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid expert"}), 400

    expert = next((e for e in KVK_EXPERTS if e["id"] == expert_id), None)
    if not expert:
        return jsonify({"status": "error", "message": "Expert not found"}), 404
    if not slot or slot not in expert["slots"]:
        return jsonify({"status": "error", "message": "Ye slot ab available nahi hai — dusra slot chunein"}), 400
    if not topic:
        return jsonify({"status": "error", "message": "Apna sawaal/topic likhein"}), 400

    expert["slots"].remove(slot)  # slot no longer available once booked
    user = session.get("user", {"name": "Farmer"})
    booking = {
        "id": len(KVK_BOOKINGS) + 1,
        "farmer": user.get("name", "Farmer"),
        "expert_name": expert["name"],
        "specialization": expert["specialization"],
        "kvk_name": expert["kvk_name"],
        "slot": slot,
        "topic": topic,
        "contact": contact or "—",
        "booked_at": datetime.now().strftime("%d %b, %I:%M %p"),
    }
    KVK_BOOKINGS.append(booking)
    return jsonify({"status": "booked", "booking": booking})


@app.route("/api/kvk_bookings", methods=["GET"])
def get_kvk_bookings():
    return jsonify({"bookings": sorted(KVK_BOOKINGS, key=lambda b: -b["id"])})


# ---------------------------------------------------------------------------
# NEW: Nearby Vet Finder + Emergency Helpline — when livestock falls sick
# right now, a farmer needs a vet's phone number and distance immediately,
# not just a health record (which the existing Pashu Health Tracker keeps).
# Reuses the same haversine-distance approach as Nearest Mandi Locator.
# ---------------------------------------------------------------------------
NATIONAL_LIVESTOCK_HELPLINE = "1962"  # Government of India's toll-free Kisan Call Centre / livestock emergency line

VETS = [
    {"name": "Dr. Subrat Das — Govt Veterinary Hospital", "location": "Cuttack", "specialization": "Large Animal (Cattle/Buffalo)", "contact": "+91 98111 22770", "emergency_24x7": True},
    {"name": "Dr. Lipika Panda — Khordha Veterinary Dispensary", "location": "Khordha", "specialization": "Cattle/Goat", "contact": "+91 97222 33881", "emergency_24x7": False},
    {"name": "Dr. Prakash Swain — Puri Animal Health Centre", "location": "Puri", "specialization": "Poultry/Goat", "contact": "+91 96333 44992", "emergency_24x7": True},
    {"name": "Dr. Nirupama Sahu — Jatani Veterinary Clinic", "location": "Jatani", "specialization": "Large Animal (Cattle/Buffalo)", "contact": "+91 95444 55103", "emergency_24x7": False},
    {"name": "Dr. Bikash Rout — Banki Govt Vet Hospital", "location": "Banki", "specialization": "Cattle/Goat/Poultry", "contact": "+91 94555 66214", "emergency_24x7": True},
]


@app.route("/api/nearby_vets", methods=["POST"])
def nearby_vets():
    data = request.get_json(force=True, silent=True) or {}
    lat, lng = data.get("lat"), data.get("lng")

    if lat is not None and lng is not None:
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            lat = lng = None

    if lat is None or lng is None:
        location = data.get("location", "Bhubaneswar (City Centre)")
        lat, lng = LOCATION_COORDS.get(location, (20.2961, 85.8245))

    out = []
    for v in VETS:
        vlat, vlng = LOCATION_COORDS.get(v["location"], (20.2961, 85.8245))
        dist = _haversine_km(lat, lng, vlat, vlng)
        out.append({**v, "distance_km": round(dist, 1)})

    out.sort(key=lambda v: v["distance_km"])
    return jsonify({"vets": out, "emergency_helpline": NATIONAL_LIVESTOCK_HELPLINE})


# ---------------------------------------------------------------------------
# NEW: Seed Variety Comparison — for a given crop, compares real named
# varieties on yield, duration, disease resistance, water need, and price,
# so a farmer picks a variety deliberately instead of just buying whatever
# the local shop has in stock.
# ---------------------------------------------------------------------------
SEED_VARIETIES = {
    "Rice": [
        {"name": "Swarna (MTU 7029)", "duration_days": 145, "yield_quintal_per_acre": 22, "disease_resistance": "Medium", "water_requirement": "High", "season": "Kharif", "price_per_kg": 45},
        {"name": "MTU 1010", "duration_days": 125, "yield_quintal_per_acre": 24, "disease_resistance": "Good", "water_requirement": "Medium", "season": "Kharif", "price_per_kg": 50},
        {"name": "Naveen", "duration_days": 150, "yield_quintal_per_acre": 20, "disease_resistance": "High (Blast resistant)", "water_requirement": "High", "season": "Kharif", "price_per_kg": 42},
        {"name": "Lalat", "duration_days": 130, "yield_quintal_per_acre": 21, "disease_resistance": "Good (Blast tolerant)", "water_requirement": "Medium", "season": "Kharif", "price_per_kg": 44},
    ],
    "Wheat": [
        {"name": "HD 2967", "duration_days": 145, "yield_quintal_per_acre": 18, "disease_resistance": "Good (Rust resistant)", "water_requirement": "Medium", "season": "Rabi", "price_per_kg": 32},
        {"name": "PBW 550", "duration_days": 150, "yield_quintal_per_acre": 19, "disease_resistance": "Medium", "water_requirement": "Medium", "season": "Rabi", "price_per_kg": 30},
        {"name": "DBW 187", "duration_days": 135, "yield_quintal_per_acre": 20, "disease_resistance": "High (Rust + Blight resistant)", "water_requirement": "Low-Medium", "season": "Rabi", "price_per_kg": 35},
    ],
    "Maize": [
        {"name": "DHM 117", "duration_days": 105, "yield_quintal_per_acre": 22, "disease_resistance": "Good", "water_requirement": "Medium", "season": "Kharif/Rabi", "price_per_kg": 180},
        {"name": "Pioneer 30V92", "duration_days": 100, "yield_quintal_per_acre": 25, "disease_resistance": "Good", "water_requirement": "Medium", "season": "Kharif/Rabi", "price_per_kg": 220},
        {"name": "Ganga Safed-2", "duration_days": 110, "yield_quintal_per_acre": 18, "disease_resistance": "Medium", "water_requirement": "Low-Medium", "season": "Kharif", "price_per_kg": 150},
    ],
    "Groundnut": [
        {"name": "TAG 24", "duration_days": 100, "yield_quintal_per_acre": 9, "disease_resistance": "Medium", "water_requirement": "Low-Medium", "season": "Kharif", "price_per_kg": 90},
        {"name": "Devi (K-6)", "duration_days": 105, "yield_quintal_per_acre": 10, "disease_resistance": "Good (Leaf spot tolerant)", "water_requirement": "Medium", "season": "Kharif", "price_per_kg": 95},
        {"name": "Dharani", "duration_days": 110, "yield_quintal_per_acre": 11, "disease_resistance": "High (Rust + Leaf spot resistant)", "water_requirement": "Medium", "season": "Kharif", "price_per_kg": 100},
    ],
    "Cotton": [
        {"name": "Bt Cotton RCH 2", "duration_days": 160, "yield_quintal_per_acre": 8, "disease_resistance": "Good (Bollworm resistant)", "water_requirement": "Medium", "season": "Kharif", "price_per_kg": 850},
        {"name": "Bunny Bt", "duration_days": 155, "yield_quintal_per_acre": 7, "disease_resistance": "Medium", "water_requirement": "Medium", "season": "Kharif", "price_per_kg": 800},
    ],
}


def _score_seed_variety(v):
    """Simple composite score to suggest a 'recommended' variety: rewards
    higher yield and stronger disease resistance, and shorter duration
    (faster turnaround, less exposure to weather risk)."""
    resistance_score = 3 if "High" in v["disease_resistance"] else 2 if "Good" in v["disease_resistance"] else 1
    duration_score = max(0, (160 - v["duration_days"]) / 10)
    return v["yield_quintal_per_acre"] * 2 + resistance_score * 5 + duration_score


@app.route("/api/seed_varieties", methods=["GET"])
def seed_varieties():
    crop = request.args.get("crop", "Rice")
    varieties = SEED_VARIETIES.get(crop, SEED_VARIETIES["Rice"])
    scored = sorted(varieties, key=_score_seed_variety, reverse=True)
    out = [{**v, "recommended": i == 0} for i, v in enumerate(scored)]
    return jsonify({"crop": crop, "varieties": out, "crops_available": list(SEED_VARIETIES.keys())})


# ---------------------------------------------------------------------------
# NEW: Rainfall-based Sowing Calendar — combines the typical monsoon/season
# window for a crop with the live rain forecast (same pipeline as
# /api/irrigation_schedule) to tell a farmer not just "when season starts"
# but "should you actually sow this week", which plain calendars can't.
# ---------------------------------------------------------------------------
CROP_SOWING_WINDOWS = {
    # (start_month, start_day) to (end_month, end_day) — ideal sowing window for Odisha's climate
    "Rice": {"season": "Kharif", "start": (6, 15), "end": (7, 15), "min_weekly_rain_mm": 50},
    "Maize": {"season": "Kharif", "start": (6, 15), "end": (7, 31), "min_weekly_rain_mm": 35},
    "Groundnut": {"season": "Kharif", "start": (6, 10), "end": (7, 10), "min_weekly_rain_mm": 40},
    "Cotton": {"season": "Kharif", "start": (5, 25), "end": (6, 20), "min_weekly_rain_mm": 30},
    "Wheat": {"season": "Rabi", "start": (10, 15), "end": (11, 30), "min_weekly_rain_mm": 0},
    "Sugarcane": {"season": "Year-round (best window)", "start": (2, 1), "end": (3, 31), "min_weekly_rain_mm": 0},
}


def _current_or_next_window(start_md, end_md, today):
    """Returns (window_start, window_end, status) for a crop's sowing
    window relative to today:
      - 'too_early'  -> window_start/end is the upcoming (not yet started) window
      - 'in_window'  -> window_start/end is the currently active window
      - 'missed'     -> window_start/end is the window that just passed this
                        year (so the farmer sees the actual dates they missed,
                        not a confusing jump straight to next year)
    Handles windows that wrap across the year boundary (e.g. Nov-Jan)."""
    start_month, start_day = start_md
    end_month, end_day = end_md
    year = today.year

    this_start = datetime(year, start_month, start_day)
    this_end = datetime(year, end_month, end_day)
    if this_end < this_start:  # window wraps across the year boundary
        this_end = datetime(year + 1, end_month, end_day)

    if today < this_start:
        return this_start, this_end, "too_early"
    if this_start <= today <= this_end:
        return this_start, this_end, "in_window"
    return this_start, this_end, "missed"  # this year's window already passed


@app.route("/api/sowing_calendar", methods=["POST"])
def sowing_calendar():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "Rice")
    location = data.get("location", "Bhubaneswar (City Centre)")
    window = CROP_SOWING_WINDOWS.get(crop, CROP_SOWING_WINDOWS["Rice"])

    today = datetime.now()
    window_start, window_end, calendar_status = _current_or_next_window(window["start"], window["end"], today)

    if calendar_status == "in_window":
        days_left_in_window = (window_end - today).days
        calendar_message = f"You're inside the ideal sowing window for {crop} ({window['season']}) — {days_left_in_window} day(s) left in this window."
    elif calendar_status == "too_early":
        days_until_window = (window_start - today).days
        calendar_message = f"Ideal sowing window for {crop} ({window['season']}) starts in {days_until_window} day(s), on {window_start.strftime('%d %b')}."
    else:  # missed
        next_start = datetime(window_start.year + 1, window["start"][0], window["start"][1])
        calendar_message = f"This season's window ({window_start.strftime('%d %b')} – {window_end.strftime('%d %b %Y')}) has passed — next {crop} ({window['season']}) window opens around {next_start.strftime('%d %b %Y')}."

    # Live rain check for locations currently inside (or about to enter) the window
    lat, lon = LOCATION_COORDS.get(location, (20.2961, 85.8245))
    try:
        fdata = _fetch_open_meteo(lat, lon)
        precip_sums = (fdata.get("daily") or {}).get("precipitation_sum") or []
        rain_next_7_days = sum(precip_sums[:7]) if precip_sums else 0
    except Exception:
        days, _ = _mock_weather_forecast()
        rain_next_7_days = sum(d["rain_mm"] for d in days[:7])

    moisture_ready = rain_next_7_days >= window["min_weekly_rain_mm"]
    if window["min_weekly_rain_mm"] == 0:
        moisture_message = "This crop doesn't depend on monsoon rain for sowing — irrigation-based timing applies instead."
    elif moisture_ready:
        moisture_message = f"Expected rain (~{round(rain_next_7_days)}mm over 7 days) looks sufficient for sowing moisture."
    else:
        moisture_message = f"Expected rain (~{round(rain_next_7_days)}mm over 7 days) may not be enough yet — consider waiting a few more days or irrigating before sowing."

    return jsonify({
        "crop": crop,
        "location": location,
        "season": window["season"],
        "window_start": window_start.strftime("%d %b %Y"),
        "window_end": window_end.strftime("%d %b %Y"),
        "calendar_status": calendar_status,
        "calendar_message": calendar_message,
        "rain_next_7_days_mm": round(rain_next_7_days, 1),
        "moisture_ready": moisture_ready,
        "moisture_message": moisture_message,
        "crops_available": list(CROP_SOWING_WINDOWS.keys()),
    })


# ---------------------------------------------------------------------------
# NEW: Crop Rotation Planner — recommends what to grow NEXT based on what
# was grown last, so a farmer replenishes soil nutrients (e.g. a legume
# after a nitrogen-hungry cereal) and breaks pest/disease cycles that build
# up when the same crop family is grown repeatedly, rather than just
# repeating the same crop every season out of habit.
# ---------------------------------------------------------------------------
CROP_ROTATION_DATA = {
    "Rice": {"family": "Cereal", "nutrient_drain": "High Nitrogen",
             "recommended_next": [
                 {"crop": "Groundnut", "reason": "A legume that fixes nitrogen back into the soil after rice's heavy nitrogen use."},
                 {"crop": "Pulses (Moong/Urad)", "reason": "Short-duration legume — restores soil nitrogen and fits well before the next rice season."},
                 {"crop": "Mustard", "reason": "Different family and root depth — breaks the cereal pest/disease cycle."},
             ],
             "avoid_next": ["Maize", "Wheat"], "avoid_reason": "Another cereal would deplete the same nutrients further and let rice-specific pests/diseases carry over."},
    "Wheat": {"family": "Cereal", "nutrient_drain": "High Nitrogen",
              "recommended_next": [
                  {"crop": "Groundnut", "reason": "Legume rotation restores nitrogen wheat has drawn down."},
                  {"crop": "Mustard", "reason": "Common, well-suited rotation after wheat in this climate — different pest profile."},
                  {"crop": "Pulses (Moong/Urad)", "reason": "Fixes nitrogen and has a short duration before the next main season."},
              ],
              "avoid_next": ["Rice", "Maize"], "avoid_reason": "Repeating a cereal keeps nitrogen demand high and lets wheat pests persist in the soil."},
    "Maize": {"family": "Cereal", "nutrient_drain": "High Nitrogen",
              "recommended_next": [
                  {"crop": "Groundnut", "reason": "Legume rotation rebuilds nitrogen after maize."},
                  {"crop": "Pulses (Moong/Urad)", "reason": "Fast-growing legume that fits well as a rotation crop."},
              ],
              "avoid_next": ["Rice", "Wheat"], "avoid_reason": "Another cereal repeats the same nutrient drain and pest pressure."},
    "Groundnut": {"family": "Legume", "nutrient_drain": "Low Nitrogen (fixes nitrogen)",
                  "recommended_next": [
                      {"crop": "Rice", "reason": "Cereal makes good use of the nitrogen groundnut leaves behind in the soil."},
                      {"crop": "Maize", "reason": "Benefits from the nitrogen boost after a legume season."},
                  ],
                  "avoid_next": ["Groundnut", "Cotton"], "avoid_reason": "Repeating groundnut (or another crop prone to similar soil-borne diseases) builds up disease pressure."},
    "Cotton": {"family": "Fiber crop", "nutrient_drain": "High Potassium/Nitrogen",
               "recommended_next": [
                   {"crop": "Groundnut", "reason": "Legume rotation restores soil nitrogen after cotton."},
                   {"crop": "Maize", "reason": "Different pest profile — helps break the bollworm cycle cotton attracts."},
               ],
               "avoid_next": ["Cotton"], "avoid_reason": "Continuous cotton lets bollworm and soil-borne disease pressure build up season after season."},
    "Sugarcane": {"family": "Cash crop (Cereal-related)", "nutrient_drain": "Very High (N, P, K)",
                  "recommended_next": [
                      {"crop": "Pulses (Moong/Urad)", "reason": "Legume rotation is important after sugarcane's heavy nutrient draw."},
                      {"crop": "Groundnut", "reason": "Restores nitrogen before the next high-demand crop."},
                  ],
                  "avoid_next": ["Sugarcane"], "avoid_reason": "Back-to-back sugarcane exhausts soil nutrients and increases ratoon-related pest/disease buildup."},
}

CROP_ROTATION_HISTORY = []  # [{id, crop, season, logged_at}] — simple per-session rotation log


@app.route("/api/crop_rotation_advice", methods=["GET"])
def crop_rotation_advice():
    current_crop = request.args.get("current_crop", "Rice")
    info = CROP_ROTATION_DATA.get(current_crop, CROP_ROTATION_DATA["Rice"])

    # Warn if the farmer's own logged history shows the same crop grown repeatedly
    recent_crops = [h["crop"] for h in CROP_ROTATION_HISTORY[-3:]]
    repeat_warning = None
    if recent_crops.count(current_crop) >= 2:
        repeat_warning = f"You've logged {current_crop} {recent_crops.count(current_crop)} season(s) in a row recently — rotating now will help soil health."

    return jsonify({
        "current_crop": current_crop,
        "family": info["family"],
        "nutrient_drain": info["nutrient_drain"],
        "recommended_next": info["recommended_next"],
        "avoid_next": info["avoid_next"],
        "avoid_reason": info["avoid_reason"],
        "repeat_warning": repeat_warning,
        "crops_available": list(CROP_ROTATION_DATA.keys()),
    })


@app.route("/api/crop_rotation_log", methods=["POST"])
def crop_rotation_log():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "").strip()
    season = data.get("season", "").strip()
    if not crop or not season:
        return jsonify({"status": "error", "message": "Crop aur season bharein"}), 400

    entry = {"id": len(CROP_ROTATION_HISTORY) + 1, "crop": crop, "season": season,
              "logged_at": datetime.now().strftime("%d %b %Y")}
    CROP_ROTATION_HISTORY.append(entry)
    return jsonify({"status": "logged", "entry": entry})


@app.route("/api/crop_rotation_history", methods=["GET"])
def crop_rotation_history():
    return jsonify({"history": sorted(CROP_ROTATION_HISTORY, key=lambda h: -h["id"])})


# ---------------------------------------------------------------------------
# NEW: QR Code Farm-to-Table Traceability — a farmer generates a QR code
# for a batch of produce (crop, harvest date, location, practices used);
# a buyer scans it and lands on a public page confirming who grew it and
# how, which supports a premium price for traceable/organic produce.
# Uses the free QR Server API to render the QR image — no new dependency.
# ---------------------------------------------------------------------------
TRACE_RECORDS = {}  # trace_id -> {farmer, crop, location, harvest_date, practices, created_at}


@app.route("/api/trace_record", methods=["POST"])
def create_trace_record():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "").strip()
    location = data.get("location", "Bhubaneswar (City Centre)")
    harvest_date = data.get("harvest_date", "").strip()
    practices = data.get("practices", [])
    if not crop or not harvest_date:
        return jsonify({"status": "error", "message": "Crop aur harvest date bharein"}), 400

    user = session.get("user", {"name": "Farmer"})
    trace_id = uuid.uuid4().hex[:10]
    record = {
        "trace_id": trace_id,
        "farmer": user.get("name", "Farmer"),
        "crop": crop,
        "location": location,
        "harvest_date": harvest_date,
        "practices": practices,
        "created_at": datetime.now().strftime("%d %b %Y"),
    }
    TRACE_RECORDS[trace_id] = record

    public_url = url_for("trace_view", trace_id=trace_id, _external=True)
    qr_image_url = f"https://api.qrserver.com/v1/create-qr-code/?size=240x240&data={requests.utils.quote(public_url)}"
    return jsonify({"status": "created", "record": record, "public_url": public_url, "qr_image_url": qr_image_url})


@app.route("/api/trace_records", methods=["GET"])
def list_trace_records():
    user = session.get("user", {"name": "Farmer"})
    mine = [r for r in TRACE_RECORDS.values() if r["farmer"] == user.get("name", "Farmer")]
    out = []
    for r in sorted(mine, key=lambda x: -int(x["trace_id"], 16) if x["trace_id"].isalnum() else 0):
        public_url = url_for("trace_view", trace_id=r["trace_id"], _external=True)
        out.append({**r, "public_url": public_url,
                     "qr_image_url": f"https://api.qrserver.com/v1/create-qr-code/?size=160x160&data={requests.utils.quote(public_url)}"})
    return jsonify({"records": out})


@app.route("/trace/<trace_id>")
def trace_view(trace_id):
    record = TRACE_RECORDS.get(trace_id)
    if not record:
        return render_template("trace_not_found.html"), 404
    return render_template("trace_public.html", record=record)


# ---------------------------------------------------------------------------
# NEW: Weather-based Spray Advisory — checks rain chance and wind speed for
# the next several hours before a farmer sprays pesticide/fertilizer, so
# they don't waste it right before rain washes it off, or in wind strong
# enough to cause drift.
# ---------------------------------------------------------------------------
def _fetch_hourly_weather(lat, lon):
    """Live next-24h hourly forecast from Open-Meteo (separate from the
    daily-only _fetch_open_meteo used elsewhere, since spray timing needs
    hour-level precipitation probability and wind, not just daily totals)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat, "longitude": lon,
        "hourly": "precipitation_probability,precipitation,wind_speed_10m",
        "timezone": "Asia/Kolkata", "forecast_days": 2,
    }
    r = requests.get(url, params=params, timeout=8)
    r.raise_for_status()
    return r.json()


def _mock_hourly_weather():
    """Fallback hourly forecast for the next 12 hours when offline."""
    return [{"hour": i, "rain_chance": random.randint(0, 70), "wind_kmph": round(random.uniform(3, 20), 1)} for i in range(12)]


@app.route("/api/spray_advisory", methods=["POST"])
def spray_advisory():
    data = request.get_json(force=True, silent=True) or {}
    location = data.get("location", "Bhubaneswar (City Centre)")
    lat, lon = LOCATION_COORDS.get(location, (20.2961, 85.8245))

    try:
        hdata = _fetch_hourly_weather(lat, lon)
        hourly = hdata.get("hourly", {})
        rain_probs = (hourly.get("precipitation_probability") or [])[:12]
        winds = (hourly.get("wind_speed_10m") or [])[:12]
        max_rain_chance = max(rain_probs) if rain_probs else 0
        max_wind = max(winds) if winds else 0
    except Exception:
        mock = _mock_hourly_weather()
        max_rain_chance = max(h["rain_chance"] for h in mock)
        max_wind = max(h["wind_kmph"] for h in mock)

    RAIN_THRESHOLD = 40   # % chance — above this, spray would likely wash off
    WIND_THRESHOLD = 15   # km/h — above this, spray drift risk is high

    if max_rain_chance >= RAIN_THRESHOLD:
        verdict, message = "wait_rain", f"Rain is likely in the next 12 hours ({max_rain_chance}% chance) — spray would wash off. Wait until after the rain passes."
    elif max_wind >= WIND_THRESHOLD:
        verdict, message = "wait_wind", f"Wind speeds up to {round(max_wind)} km/h are expected — too strong for accurate spraying (drift risk). Wait for calmer conditions, ideally early morning."
    else:
        verdict, message = "safe_to_spray", f"Low rain chance ({max_rain_chance}%) and manageable wind (up to {round(max_wind)} km/h) — good conditions to spray in the next 12 hours. Early morning or evening is still best for effectiveness."

    return jsonify({
        "location": location,
        "max_rain_chance_percent": max_rain_chance,
        "max_wind_kmph": round(max_wind, 1),
        "verdict": verdict,
        "message": message,
    })


# ---------------------------------------------------------------------------
# NEW: Bill/Receipt Photo Scanner — OCRs a photo of a khaad/beej/pesticide
# receipt (via pytesseract) and extracts a rupee amount + guesses the
# expense category, so a farmer can add a Farm Ledger entry from a photo
# instead of typing every purchase by hand.
# ---------------------------------------------------------------------------
try:
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

RECEIPT_CATEGORY_KEYWORDS = {
    "Seeds (Beej)": ["seed", "beej", "bihan"],
    "Fertilizer (Khaad)": ["urea", "dap", "npk", "fertilizer", "fertiliser", "khad", "khaad", "compost"],
    "Pesticide": ["pesticide", "insecticide", "fungicide", "chlorpyrifos", "spray"],
    "Labour (Mazdoor)": ["labour", "labor", "mazdoor", "wages"],
    "Irrigation / Fuel": ["diesel", "petrol", "fuel", "pump"],
    "Equipment Rent": ["rent", "tractor", "harvester", "rotavator"],
}


@app.route("/api/scan_receipt", methods=["POST"])
def scan_receipt():
    photo = request.files.get("photo")
    if not photo or not photo.filename:
        return jsonify({"status": "error", "message": "Receipt ki photo chunein"}), 400
    if not PIL_AVAILABLE:
        return jsonify({"status": "error", "message": "Image processing not available on this server"}), 500

    try:
        img = Image.open(photo.stream).convert("RGB")
    except Exception:
        return jsonify({"status": "error", "message": "Ye photo padhi nahi ja saki — dusri photo try karein"}), 400

    extracted_text = ""
    ocr_worked = False
    if OCR_AVAILABLE:
        try:
            extracted_text = pytesseract.image_to_string(img)
            ocr_worked = True
        except Exception as e:
            # Covers pytesseract.TesseractNotFoundError (binary missing on
            # this system) as well as any other OCR failure — either way,
            # degrade gracefully to manual entry rather than erroring out.
            app.logger.warning(f"OCR failed: {e}")
            extracted_text = ""

    # Extract the most likely rupee amount: look for ₹/Rs figures first,
    # then fall back to the largest plain number found (often the total).
    amount_matches = re.findall(r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d{1,2})?)", extracted_text, re.IGNORECASE)
    if not amount_matches:
        amount_matches = re.findall(r"\b(\d{2,6}(?:\.\d{1,2})?)\b", extracted_text)

    suggested_amount = None
    if amount_matches:
        cleaned = [float(m.replace(",", "")) for m in amount_matches]
        suggested_amount = max(cleaned)  # usually the total is the largest figure on a receipt

    text_lower = extracted_text.lower()
    suggested_category = "Other"
    for category, keywords in RECEIPT_CATEGORY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            suggested_category = category
            break

    return jsonify({
        "status": "scanned",
        "ocr_available": ocr_worked,
        "extracted_text": extracted_text.strip()[:500],
        "suggested_amount": suggested_amount,
        "suggested_category": suggested_category,
    })


# ---------------------------------------------------------------------------
# NEW: Digital Farm Diary (Voice Journal) — a quick daily log of what was
# actually done in the field (sprayed, weeded, irrigated...), as a voice
# note or short text, separate from the Farm Ledger which only tracks
# money. Audio is saved to static/uploads/diary/ like Farmer Reels.
# ---------------------------------------------------------------------------
DIARY_UPLOAD_DIR = os.path.join(app.root_path, "static", "uploads", "diary")
os.makedirs(DIARY_UPLOAD_DIR, exist_ok=True)
ALLOWED_AUDIO_EXTENSIONS = {"webm", "ogg", "mp3", "wav", "m4a"}
MAX_DIARY_AUDIO_BYTES = 15 * 1024 * 1024  # 15MB — plenty for a short voice note

DIARY_ENTRIES = []  # [{id, farmer, activity_type, text_note, audio_url, entry_date, created_at}]


def _allowed_audio_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_AUDIO_EXTENSIONS


@app.route("/api/diary_entry", methods=["POST"])
def diary_entry():
    activity_type = request.form.get("activity_type", "Other")
    text_note = request.form.get("text_note", "").strip()
    entry_date = request.form.get("entry_date", "").strip() or datetime.now().strftime("%Y-%m-%d")
    audio_file = request.files.get("audio")

    audio_url = None
    if audio_file and audio_file.filename:
        if not _allowed_audio_file(audio_file.filename):
            return jsonify({"status": "error", "message": "Sirf webm, ogg, mp3, wav, ya m4a audio allowed hai"}), 400
        audio_file.stream.seek(0, os.SEEK_END)
        size = audio_file.stream.tell()
        audio_file.stream.seek(0)
        if size > MAX_DIARY_AUDIO_BYTES:
            return jsonify({"status": "error", "message": "Audio 15MB se bada hai"}), 400
        ext = audio_file.filename.rsplit(".", 1)[1].lower()
        safe_name = secure_filename(f"{uuid.uuid4().hex}.{ext}")
        audio_file.save(os.path.join(DIARY_UPLOAD_DIR, safe_name))
        audio_url = url_for("static", filename=f"uploads/diary/{safe_name}")

    if not text_note and not audio_url:
        return jsonify({"status": "error", "message": "Voice note record karein ya kuch likhein"}), 400

    user = session.get("user", {"name": "Farmer"})
    entry = {
        "id": len(DIARY_ENTRIES) + 1,
        "farmer": user.get("name", "Farmer"),
        "activity_type": activity_type,
        "text_note": text_note,
        "audio_url": audio_url,
        "entry_date": entry_date,
        "created_at": datetime.now().strftime("%d %b %Y, %I:%M %p"),
    }
    DIARY_ENTRIES.append(entry)
    return jsonify({"status": "logged", "entry": entry})


@app.route("/api/diary_entries", methods=["GET"])
def get_diary_entries():
    activity_type = request.args.get("activity_type", "").strip()
    rows = DIARY_ENTRIES
    if activity_type:
        rows = [e for e in rows if e["activity_type"] == activity_type] or rows
    return jsonify({"entries": sorted(rows, key=lambda e: -e["id"])})


# ---------------------------------------------------------------------------
# NEW: Crop Yield Benchmarking — compares a farmer's own yield against an
# anonymized area average (and top performers) for the same crop, so they
# see concretely where they stand rather than just an absolute number with
# no reference point.
# ---------------------------------------------------------------------------
CROP_YIELD_BENCHMARKS = {
    "Rice": {"area_average_quintal_per_acre": 20, "top_10_percent_quintal_per_acre": 26, "std_dev": 4},
    "Wheat": {"area_average_quintal_per_acre": 17, "top_10_percent_quintal_per_acre": 22, "std_dev": 3},
    "Maize": {"area_average_quintal_per_acre": 19, "top_10_percent_quintal_per_acre": 25, "std_dev": 4},
    "Groundnut": {"area_average_quintal_per_acre": 8.5, "top_10_percent_quintal_per_acre": 11, "std_dev": 1.5},
    "Cotton": {"area_average_quintal_per_acre": 6.5, "top_10_percent_quintal_per_acre": 9, "std_dev": 1.2},
    "Sugarcane": {"area_average_quintal_per_acre": 300, "top_10_percent_quintal_per_acre": 380, "std_dev": 40},
}

YIELD_GAP_TIPS = {
    "Rice": "Consider comparing seed varieties or checking your irrigation timing — both often explain the gap.",
    "Wheat": "A higher-yield variety or adjusting your sowing window could help close the gap.",
    "Maize": "Check spacing and fertilizer timing — these are common gaps for maize yield.",
    "Groundnut": "Disease-resistant varieties and proper spacing tend to make the biggest difference for groundnut.",
    "Cotton": "Bollworm control and irrigation consistency are the usual gaps for cotton yield.",
    "Sugarcane": "Ratoon management and balanced NPK application are common places to close the gap.",
}


@app.route("/api/yield_benchmark", methods=["POST"])
def yield_benchmark():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "Rice")
    try:
        my_yield = float(data.get("my_yield_quintal_per_acre", 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Please enter a valid yield number"}), 400

    bench = CROP_YIELD_BENCHMARKS.get(crop, CROP_YIELD_BENCHMARKS["Rice"])
    avg = bench["area_average_quintal_per_acre"]
    top10 = bench["top_10_percent_quintal_per_acre"]
    std_dev = bench["std_dev"]
    delta = round(my_yield - avg, 1)

    if my_yield >= top10:
        tier, message = "top_10", f"You're in the top 10% of {crop} farmers in your area!"
    elif my_yield >= avg + std_dev:
        tier, message = "above_average", f"You're above average for {crop} — solidly ahead of most farmers in your area."
    elif my_yield >= avg - std_dev * 0.5:
        tier, message = "average", f"You're around the area average for {crop}."
    else:
        tier, message = "below_average", f"You're below the area average for {crop} — there's room to close the gap."

    return jsonify({
        "crop": crop,
        "my_yield_quintal_per_acre": my_yield,
        "area_average_quintal_per_acre": avg,
        "top_10_percent_quintal_per_acre": top10,
        "delta_vs_average": delta,
        "tier": tier,
        "message": message,
        "improvement_tip": YIELD_GAP_TIPS.get(crop) if tier == "below_average" else None,
        "crops_available": list(CROP_YIELD_BENCHMARKS.keys()),
    })


# ---------------------------------------------------------------------------
# NEW: Nearby Govt Camp / Krishi Mela Alerts — upcoming agricultural fairs,
# subsidy camps, soil-testing camps, and training sessions near the
# farmer, so they know what's happening locally instead of missing camps
# they'd have wanted to attend.
# ---------------------------------------------------------------------------
GOVT_CAMPS = [
    {"id": 1, "name": "Rabi Season Krishi Mela", "camp_type": "Krishi Mela", "location": "Cuttack",
     "date": "05 Sep 2026", "description": "Seed, fertilizer, and equipment stalls from govt and private suppliers, plus expert talks on Rabi crop planning."},
    {"id": 2, "name": "PMFBY Crop Insurance Camp", "camp_type": "Subsidy Camp", "location": "Khordha",
     "date": "10 Sep 2026", "description": "On-the-spot help enrolling in PMFBY crop insurance and checking existing claim status."},
    {"id": 3, "name": "Free Soil Testing Camp", "camp_type": "Soil Testing", "location": "Puri",
     "date": "15 Sep 2026", "description": "Bring a soil sample for free NPK and pH testing, with fertilizer recommendations on the spot."},
    {"id": 4, "name": "KCC Loan Awareness Camp", "camp_type": "Subsidy Camp", "location": "Jatani",
     "date": "18 Sep 2026", "description": "Bank officials on-site to help with new Kisan Credit Card applications and renewals."},
    {"id": 5, "name": "Organic Farming Training", "camp_type": "Training", "location": "Bhubaneswar (City Centre)",
     "date": "22 Sep 2026", "description": "Hands-on training on vermicomposting, natural pest control, and organic certification basics."},
    {"id": 6, "name": "PM-KUSUM Solar Pump Camp", "camp_type": "Subsidy Camp", "location": "Banki",
     "date": "28 Sep 2026", "description": "Details and on-the-spot application help for solar irrigation pump subsidies under PM-KUSUM."},
]


@app.route("/api/govt_camps", methods=["GET"])
def govt_camps():
    location = request.args.get("location", "").strip()
    camp_type = request.args.get("camp_type", "").strip()
    rows = GOVT_CAMPS
    if location:
        filtered = [c for c in rows if c["location"] == location]
        rows = filtered if filtered else rows
    if camp_type:
        rows = [c for c in rows if c["camp_type"] == camp_type] or rows
    return jsonify({"camps": rows, "camp_types": sorted(set(c["camp_type"] for c in GOVT_CAMPS))})


# ---------------------------------------------------------------------------
# NEW: Water Quality Checker — the existing Groundwater Tracker only shows
# water TABLE LEVEL; this checks water QUALITY (salinity/EC and pH) for a
# borewell/canal/pond source, since water that's plentiful can still be
# unsuitable for irrigation if too saline or too acidic/alkaline.
# ---------------------------------------------------------------------------
SALT_TOLERANT_CROPS = ["Barley", "Cotton", "Sugar beet", "Date palm", "Coconut"]


@app.route("/api/water_quality_check", methods=["POST"])
def water_quality_check():
    data = request.get_json(force=True, silent=True) or {}
    location = data.get("location", "Bhubaneswar (City Centre)")
    source_type = data.get("source_type", "Borewell")

    # Deterministic per (location, source_type) so repeated checks are
    # consistent — canals tend to run less saline than borewells in this mock.
    rng = random.Random(f"{location}|{source_type}")
    base_ec = 0.8 if source_type == "Canal" else 1.6 if source_type == "Pond" else 2.0
    ec = round(rng.uniform(base_ec * 0.6, base_ec * 1.6), 2)  # dS/m (electrical conductivity — salinity proxy)
    ph = round(rng.uniform(6.0, 8.8), 1)

    if ec < 1:
        salinity_status, salinity_note = "Excellent", "Low salinity — safe for all crops."
    elif ec < 2:
        salinity_status, salinity_note = "Good", "Slightly saline but safe for most crops with normal irrigation practices."
    elif ec < 3:
        salinity_status, salinity_note = "Use with Caution", "Moderately saline — safe mainly for salt-tolerant crops; sensitive crops may show reduced yield."
    else:
        salinity_status, salinity_note = "Not Suitable", "High salinity — risks long-term soil damage and poor yield for most crops without treatment (e.g. gypsum, better drainage)."

    if 6.5 <= ph <= 8.4:
        ph_status, ph_note = "Normal", "pH is in the safe range for irrigation."
    elif ph < 6.5:
        ph_status, ph_note = "Acidic", "Somewhat acidic — may affect nutrient availability over time; consider periodic soil testing."
    else:
        ph_status, ph_note = "Alkaline", "Somewhat alkaline — can reduce micronutrient availability (iron, zinc) over time."

    overall_safe = salinity_status in ("Excellent", "Good") and ph_status == "Normal"

    return jsonify({
        "location": location,
        "source_type": source_type,
        "ec_ds_per_m": ec,
        "ph": ph,
        "salinity_status": salinity_status,
        "salinity_note": salinity_note,
        "ph_status": ph_status,
        "ph_note": ph_note,
        "overall_safe": overall_safe,
        "salt_tolerant_crops": SALT_TOLERANT_CROPS if salinity_status in ("Use with Caution", "Not Suitable") else [],
    })


# ---------------------------------------------------------------------------
# NEW: PM-KUSUM Solar Pump Scheme Info + Subsidy Calculator — explains the
# real scheme structure (per MNRE: 30% central + at least 30% state + up to
# 40% farmer share, with a bank-loan option so the farmer pays only ~10%
# upfront) and estimates the actual rupee breakdown for a chosen pump size.
# ---------------------------------------------------------------------------
SOLAR_PUMP_COST_BY_HP = {
    2: 180000, 3: 220000, 5: 320000, 7.5: 450000, 10: 580000,
}


@app.route("/api/solar_pump_subsidy", methods=["POST"])
def solar_pump_subsidy():
    data = request.get_json(force=True, silent=True) or {}
    try:
        hp = float(data.get("pump_hp", 5))
    except (TypeError, ValueError):
        hp = 5.0
    state_subsidy_percent = data.get("state_subsidy_percent", 30)
    try:
        state_subsidy_percent = float(state_subsidy_percent)
    except (TypeError, ValueError):
        state_subsidy_percent = 30.0
    use_bank_loan = bool(data.get("use_bank_loan", True))

    closest_hp = min(SOLAR_PUMP_COST_BY_HP.keys(), key=lambda h: abs(h - hp))
    total_cost = SOLAR_PUMP_COST_BY_HP[closest_hp]

    central_share = round(total_cost * 0.30)
    state_share = round(total_cost * (state_subsidy_percent / 100))
    farmer_share = total_cost - central_share - state_share
    farmer_share = max(0, farmer_share)

    if use_bank_loan:
        # Farmer typically pays ~10% upfront and takes a bank loan for the rest of their share
        farmer_upfront = round(total_cost * 0.10)
        loan_amount = max(0, farmer_share - farmer_upfront)
    else:
        farmer_upfront = farmer_share
        loan_amount = 0

    return jsonify({
        "pump_hp": closest_hp,
        "total_cost": total_cost,
        "central_subsidy": central_share,
        "state_subsidy": state_share,
        "farmer_share_total": farmer_share,
        "farmer_upfront_payment": farmer_upfront,
        "bank_loan_amount": loan_amount,
        "note": "Central CFA is fixed at 30% nationwide; state subsidy varies by state (often 30%+) — check your state renewable energy portal for the exact figure before applying.",
    })


# ---------------------------------------------------------------------------
# NEW: Post-Harvest Storage Loss Calculator — estimates how much grain
# (and money) is lost to poor storage, and what an improved method (e.g.
# hermetic bags) would save instead — since post-harvest loss silently
# costs many farmers more than any single input decision.
# ---------------------------------------------------------------------------
STORAGE_LOSS_PERCENT = {
    "Open/Traditional storage (gunny bags, open floor)": 9.0,
    "Basic covered storage (tarpaulin, mud bins)": 6.0,
    "Improved storage (hermetic/PICS bags)": 2.5,
    "Modern warehouse / silo": 1.0,
}


@app.route("/api/storage_loss_calculator", methods=["POST"])
def storage_loss_calculator():
    data = request.get_json(force=True, silent=True) or {}
    crop = data.get("crop", "Rice")
    try:
        quantity_quintal = float(data.get("quantity_quintal", 10))
        price_per_quintal = float(data.get("price_per_quintal", 2000))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Please enter valid numbers"}), 400
    current_method = data.get("current_method", "Open/Traditional storage (gunny bags, open floor)")

    current_loss_percent = STORAGE_LOSS_PERCENT.get(current_method, 9.0)
    current_loss_quintal = round(quantity_quintal * current_loss_percent / 100, 2)
    current_loss_value = round(current_loss_quintal * price_per_quintal)

    comparisons = []
    for method, loss_pct in STORAGE_LOSS_PERCENT.items():
        loss_qtl = round(quantity_quintal * loss_pct / 100, 2)
        loss_val = round(loss_qtl * price_per_quintal)
        savings_vs_current = round(current_loss_value - loss_val)
        comparisons.append({
            "method": method, "loss_percent": loss_pct,
            "loss_quintal": loss_qtl, "loss_value": loss_val,
            "savings_vs_current": savings_vs_current, "is_current": method == current_method,
        })

    return jsonify({
        "crop": crop,
        "quantity_quintal": quantity_quintal,
        "current_method": current_method,
        "current_loss_quintal": current_loss_quintal,
        "current_loss_value": current_loss_value,
        "comparisons": comparisons,
        "methods_available": list(STORAGE_LOSS_PERCENT.keys()),
    })


# ---------------------------------------------------------------------------
# NEW: Multi-Farm / Field Profiles — no API route needed. Field records
# (name, crop, area, location, sown date) live in the browser's
# localStorage (see templates/feature_my_fields.html), same offline-first
# pattern as Farm Ledger and Pashu Health Tracker, since this is private
# per-farmer data with no need for a server round-trip.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# NEW: Market Price Trend Charts — extends the existing Market Prices
# feature (which only shows today's snapshot) with a 30/90-day price
# history, so a farmer can see whether now is actually a good time to
# sell relative to the recent trend, not just today's number in isolation.
# ---------------------------------------------------------------------------
@app.route("/api/market_price_trend", methods=["GET"])
def market_price_trend():
    crop = request.args.get("crop", "Rice")
    location = request.args.get("location", "Bhubaneswar (City Centre)")
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30
    days = 90 if days >= 60 else 30  # only two supported windows, snap to nearest

    rng = random.Random(f"{crop}|{location}")  # deterministic per crop+mandi, so repeated views are consistent
    price = rng.randint(1800, 4500)
    history = []
    for i in range(days):
        d = datetime.now() - timedelta(days=(days - 1 - i))
        price = max(500, round(price + rng.uniform(-price * 0.02, price * 0.02)))
        history.append({"date": d.strftime("%d %b"), "price": price})

    current_price = history[-1]["price"]
    period_start_price = history[0]["price"]
    change_percent = round((current_price - period_start_price) / period_start_price * 100, 1)
    min_price = min(h["price"] for h in history)
    max_price = max(h["price"] for h in history)
    avg_price = round(sum(h["price"] for h in history) / len(history))

    if current_price >= avg_price * 1.03:
        sell_advice = "Price is above the period average — a reasonable time to sell if your stock is ready."
    elif current_price <= avg_price * 0.97:
        sell_advice = "Price is below the period average — consider waiting a bit if you can afford to hold."
    else:
        sell_advice = "Price is close to the period average — no strong signal to rush or delay selling."

    # Downsample to ~15 evenly-spaced points for a readable chart, while all
    # stats above are computed from the full daily series for accuracy.
    step = max(1, len(history) // 15)
    chart_points = history[::step]
    if chart_points[-1] != history[-1]:
        chart_points.append(history[-1])

    return jsonify({
        "crop": crop, "location": location, "days": days,
        "chart_points": chart_points, "current_price": current_price,
        "change_percent": change_percent, "min_price": min_price,
        "max_price": max_price, "avg_price": avg_price, "sell_advice": sell_advice,
    })


# ---------------------------------------------------------------------------
# NEW: e-NAM Integration Info — explains what the National Agriculture
# Market (e-NAM) actually is, whether a farmer's own mandi is on it, and
# how to register, since many farmers have heard the name but not the
# concrete registration steps.
# ---------------------------------------------------------------------------
ENAM_REGISTERED_MANDIS = {
    "Bhubaneswar (City Centre)": True, "Cuttack": True, "Khordha": True,
    "Puri": True, "Jatani": False, "Banki": False, "Pipili": False, "Tangi": False,
}


@app.route("/api/enam_status", methods=["GET"])
def enam_status():
    location = request.args.get("location", "Bhubaneswar (City Centre)")
    is_registered = ENAM_REGISTERED_MANDIS.get(location, False)
    return jsonify({
        "location": location,
        "is_enam_registered": is_registered,
        "national_stats": {
            "mandis_integrated": 1656, "states_uts_covered": 27,
            "farmers_registered_crore": 1.80, "traders_registered_lakh": 2.73,
            "launched_year": 2016,
        },
    })


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
