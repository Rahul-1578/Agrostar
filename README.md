# AgroStar 🌾 — Sow Smart, Reap Big

AI-powered crop yield prediction and farm management platform, built for Smart India Hackathon (Theme: Agriculture, Food Tech & Rural Development).

AgroStar gives farmers accurate yield forecasts, soil health insights, weather-smart irrigation guidance, and a 24/7 multilingual voice assistant (AASHA) — plus government scheme access, crop insurance, satellite crop health, and community support, in one simple mobile-first app.

## Features

| Category | Features |
|---|---|
| Predict & Plan | Yield Prediction, Crop Recommendation, Best Crops for You (location-based, ranked by grow-fit + market profit), Growth Stage Monitoring, Farm Reminders (with alarm notifications) |
| Soil & Inputs | Soil Health Analysis, Fertilizer & Pesticide Plan, Pest & Disease Detection, **Cover Crop Selection Guide** |
| Weather & Market | 7-Day Weather Forecast, Climate Adaptation Tips, Market Prices, Rainfall History |
| Crop Health from Space | Satellite NDVI Crop Health |
| Post-Harvest & Traceability | **Quality Grading Unit** — vision + sensor grading, shelf-life estimate, storage recommendations, QR-traceable public report for buyers/aggregators (calibrated for Tomato; see `HARDWARE_DESIGN.md` for the physical unit spec) |
| Government & Finance | Govt Scheme Eligibility (PM-KISAN, PMFBY, KCC), Insurance & Loan Checker, Sustainability Score |
| Community | Farmer Community Forum |
| Assistant | AASHA — voice + text chatbot in English, Hindi, and Odia |
| Trust & Accessibility | "Why this result?" explainable-AI panel, offline banner + SMS fallback, gamified streaks & badges |

## Future Innovations (Proposed R&D, in-app under "Future Innovations")

| Concept | Summary |
|---|---|
| **Autonomous Pest and Disease Sentinel Network with Precision Bio-Intervention Guidance** | Low-cost cameras/sensors + edge AI for early pest/disease detection in the field, hotspot mapping across farms, and localized bio-intervention guidance delivered as local-language alerts. |
| **Cooperative Autonomous Micro-Machine for Precision Weeding and Shared Rural Use** | Small autonomous row-navigating machine that tells crops apart from weeds and removes weeds mechanically (no herbicide), sized and priced for shared/cooperative ownership across small farms. |

These remain concept-only pages (Features → Future Innovations) with no backend API yet — see `Roadmap` below for productionizing them. The
post-harvest grading concept has since been built out into a working feature (see table above) with a real API, shelf-life model, and traceability report — plus its own hardware design doc, `HARDWARE_DESIGN.md`.

## Tech Stack

- **Backend:** Python, Flask (modular routes: page routes + JSON `/api/*` routes)
- **Frontend:** Jinja2 templates, vanilla CSS/JS (no framework — fast, dependency-light, easy to demo anywhere)
- **AI:** GPT-5 / Google Gemini for the chatbot (optional — falls back to a built-in rule-based responder automatically if no API key is set, so the app never breaks on stage)
- **Voice:** Browser Web Speech API for speech-to-text input and text-to-speech output — no extra service needed
- **Planned for production:** Supabase/Postgres for persistent storage, Google Earth Engine for real NDVI, Google Maps for location services

## Project Structure

```
agrostar/
├── app.py                  # All Flask routes + API endpoints
├── requirements.txt
├── .env.example             # API key template (optional, for real AI)
├── templates/
│   ├── base.html            # Shared shell + bottom nav
│   ├── welcome.html         # Page 1: Login / Signup / Guest
│   ├── features.html        # Page 2: Feature grid
│   ├── chatbot.html         # Page 3: AASHA voice assistant
│   ├── dashboard.html       # Page 4: Live overview
│   ├── profile.html         # Page 5: Settings, badges, SMS alerts
│   └── feature_*.html       # One template per feature (opened from the grid)
└── static/
    ├── css/style.css        # Design system (colors, type, components)
    └── js/app.js
```

## Setup — Detailed Installation Steps

Follow these steps to run AgroStar on your own computer (Windows, macOS, or Linux).

### Prerequisites

| Requirement | Details |
|---|---|
| **Python** | Version **3.10 or higher** (3.11 / 3.12 recommended) |
| **pip** | Comes with Python; used to install packages |
| **Internet** | Needed once to download packages; app works offline after that for demo mode |
| **Browser** | Chrome / Edge / Firefox / Safari |

Check if Python is already installed:

```bash
python --version
# or on some systems:
python3 --version
```

If you see `Python 3.10.x` or higher, you are ready. If not, install it first.

---

### Step 1 — Install Python

#### Windows
1. Go to [https://www.python.org/downloads/](https://www.python.org/downloads/)
2. Download the latest **Python 3.12** (or 3.11) installer.
3. Run the installer.
4. **Important:** tick the box **"Add python.exe to PATH"** at the bottom of the first screen.
5. Click **Install Now** and wait until it finishes.
6. Open a new **Command Prompt** or **PowerShell** and verify:
   ```bash
   python --version
   pip --version
   ```

#### macOS
1. Install via [Homebrew](https://brew.sh/) (recommended):
   ```bash
   brew install python@3.12
   ```
2. Or download the official installer from [python.org](https://www.python.org/downloads/macos/).
3. Verify:
   ```bash
   python3 --version
   pip3 --version
   ```

#### Linux (Ubuntu / Debian)
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv -y
python3 --version
```

---

### Step 2 — Download / Open the Project

1. Unzip the project folder (e.g. `agrostar_AASHA.zip`) if you received it as a zip.
2. Open a terminal **inside the project folder** (the folder that contains `app.py` and `requirements.txt`).

```bash
# Example (adjust the path to where you extracted the zip)
cd path/to/agrostar
```

You should see files like:
```
app.py
requirements.txt
README.md
templates/
static/
.env.example
```

---

### Step 3 — Create a Virtual Environment (recommended)

A virtual environment keeps AgroStar’s packages separate from other Python projects.

#### Windows (Command Prompt / PowerShell)
```bash
python -m venv venv
venv\Scripts\activate
```

#### macOS / Linux
```bash
python3 -m venv venv
source venv/bin/activate
```

When activated, your terminal prompt usually shows `(venv)` at the start.

To deactivate later:
```bash
deactivate
```

---

### Step 4 — Install Dependencies

With the virtual environment activated:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

This installs:

| Package | Purpose |
|---|---|
| Flask 3.0.3 | Web server & routes |
| requests | HTTP calls (SMS / external APIs) |
| python-dotenv | Loads `.env` file for API keys |
| gunicorn | Production server (optional for local demo) |
| Pillow | Image checks (pest / quality features) |

If `pip` is not found, try `pip3` or `python -m pip install -r requirements.txt`.

---

### Step 5 — Configure Environment Variables (optional)

AgroStar **runs fully in demo mode without any keys**. Keys are only needed for real AI chat replies and real SMS OTP.

1. Copy the example file:
   ```bash
   # Windows
   copy .env.example .env

   # macOS / Linux
   cp .env.example .env
   ```

2. Open `.env` in a text editor and fill in only what you need:

```env
# AASHA chatbot — real AI replies (optional)
OPENAI_API_KEY=sk-your-key-here
# OR
GEMINI_API_KEY=your-gemini-key-here

# Real SMS OTP (optional — pick ONE provider)
MSG91_AUTH_KEY=
MSG91_SENDER_ID=AGROSTAR

TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
```

3. Save the file. `.env` is already listed in `.gitignore` so keys are never committed.

**Without keys:**
- AASHA uses built-in rule-based demo replies
- OTP login shows the code on screen (demo mode)
- All other features (yield, soil, cover crops, etc.) work normally

---

### Step 6 — Run the App

```bash
python app.py
# or
python3 app.py
```

You should see something like:

```
 * Running on http://0.0.0.0:5000
 * Debug mode: on
```

---

### Step 7 — Open in Browser

Go to:

**http://localhost:5000**

or on the same machine:

**http://127.0.0.1:5000**

To open from another device on the same Wi-Fi (phone/tablet), use your computer’s LAN IP, e.g.:

**http://192.168.x.x:5000**

---

### Step 8 — First Use

1. On the welcome screen, choose **Continue as Guest** (or Sign Up / Log In).
2. Explore **Features** — Soil Health, Fertilizer Plan, Cover Crop Selection Guide, AASHA chatbot, etc.
3. Language can be switched to **English / हिंदी / ଓଡ଼ିଆ** from Profile or the chatbot page.

---

### Troubleshooting

| Problem | Solution |
|---|---|
| `python` not found | Reinstall Python and tick **Add to PATH** (Windows), or use `python3` |
| `pip` not found | Run `python -m pip install -r requirements.txt` |
| Port 5000 already in use | Stop the other app, or change port in `app.py`: `app.run(..., port=5001)` |
| ModuleNotFoundError: flask | Activate the virtual environment, then re-run `pip install -r requirements.txt` |
| Pillow warning | Run `pip install Pillow` — only affects photo validity checks |
| Blank page / CSS missing | Make sure you run `python app.py` from the project root (where `app.py` lives) |
| Changes not showing | Hard-refresh the browser (`Ctrl+Shift+R` / `Cmd+Shift+R`) |

---

### Running with Gunicorn (optional, production-style)

```bash
pip install gunicorn
gunicorn -b 0.0.0.0:5000 app:app
```

---

### Deploy on Render (optional)

The repo includes `render.yaml`. Connect the GitHub repo to [Render](https://render.com), set environment variables in the dashboard, and deploy. The free web service plan is enough for demos.

## API Endpoints (all mocked with realistic sample data — swap for real models/DB later)

`/api/predict_yield` · `/api/soil_health` · `/api/weather` · `/api/crop_recommendation` · `/api/best_crops_for_location` · `/api/nearest_location` · `/api/fertilizer_plan` · `/api/cover_crops` · `/api/growth_stage` · `/api/pest_detection` · `/api/climate_adaptation` · `/api/market_prices` · `/api/chatbot` · `/api/rainfall_history` · `/api/satellite_health` · `/api/quality_grading/sensors` · `/api/quality_grading/analyze` · `/api/quality_grading/report` · `/report/<batch_id>` · `/api/govt_schemes` · `/api/scheme_eligibility` · `/api/insurance_check` · `/api/loan_eligibility` · `/api/gamification` · `/api/community_posts` · `/api/sustainability_score` · `/api/sms_subscribe` · `/api/locations`

## Fertilizer Plan Logic (`/api/fertilizer_plan`)

This feature is a **rule-based (static) system** — no ML model. It works as follows:

### Flow
1. Farmer selects a crop from the dropdown (Rice, Wheat, Maize, Sugarcane, Groundnut, Cotton).
2. Clicks **Generate Plan**.
3. Frontend sends `POST /api/fertilizer_plan` with `{ "crop": "Rice" }`.
4. Backend looks up a pre-written plan dictionary for that crop and returns:
   - Stage-wise fertilizer schedule (4 stages)
   - Next reminder text (e.g. “Urea top-dressing due in 4 days”)
   - Number of days until the next action
5. UI renders each stage as a card + shows a reminder banner.
6. **Set Reminder** button redirects to the Farm Reminders page with crop, task and days pre-filled.

### Data structure (in `app.py`)
```python
plans = {
    "Rice": [
        {"stage": "Sowing / Basal", "days": "Day 0", "fertilizer": "DAP 50kg/acre + Zinc Sulphate 10kg/acre"},
        {"stage": "Tillering", "days": "Day 20-25", "fertilizer": "Urea 40kg/acre"},
        {"stage": "Panicle Initiation", "days": "Day 45-50", "fertilizer": "Urea 30kg/acre + MOP 20kg/acre"},
        {"stage": "Flowering", "days": "Day 65-70", "fertilizer": "Foliar spray — 2% DAP solution"},
    ],
    # ... same pattern for Wheat, Maize, Sugarcane, Groundnut, Cotton
}
reminders = {
    "Rice": "Urea top-dressing due in 4 days",
    # ...
}
reminder_days = {"Rice": 4, "Wheat": 3, "Maize": 5, ...}
```

### Supported crops
| Crop       | Stages covered                          | Typical next action          |
|------------|-----------------------------------------|------------------------------|
| Rice       | Sowing → Tillering → Panicle → Flowering| Urea top-dressing (4 days)  |
| Wheat      | Sowing → CRI → Jointing → Grain Filling | Urea top-dressing (3 days)  |
| Maize      | Sowing → Knee-High → Tasseling → Silking| Urea top-dressing (5 days)  |
| Sugarcane  | Planting → Tillering → Grand Growth → Maturity | Urea (7 days)          |
| Groundnut  | Sowing → Flowering → Pegging → Pod Filling | Gypsum (6 days)         |
| Cotton     | Sowing → Squaring → Flowering → Boll development | Urea (5 days)        |

### How to add a new crop
1. Add the crop name to the `<select id="crop">` in `templates/feature_fertilizer.html`.
2. Add a new entry in the `plans`, `reminders` and `reminder_days` dictionaries inside the `fertilizer_plan()` function in `app.py`.

### Future improvement ideas
- Make dosages dynamic using soil-test NPK values + area.
- Factor in location / climate zone and previous applications.
- Pull real package-of-practices data from ICAR / state agriculture universities.

## Soil Health Metrics & Crop Rotation (`/api/soil_health`)

Enhanced soil analysis returns:

**Primary metrics**
- Soil pH, Nitrogen (N), Phosphorus (P), Potassium (K), Organic Carbon

**Additional metrics**
- Soil Moisture (%), Electrical Conductivity / EC (dS/m), Zinc (Zn), Sulphur (S)
- Overall **Soil Health Score** (0–100) calculated from the above levels

**Recommendations** — dynamic tips based on the generated values (e.g. zinc deficiency, sulphur status, pH advice).

**Crop Rotation Benefits** — four practical sequences for Indian conditions:
| Sequence | Benefit |
|---|---|
| Rice → Pulse (Moong/Urad) → Wheat | N-fixation, lower urea need, breaks pest cycles |
| Maize → Groundnut/Soybean → Wheat | Improves structure & residual N |
| Cotton → Chickpea → Sorghum/Pearl Millet | Residual N + better porosity + nematode control |
| Sugarcane → Green-manure → Rice/Vegetables | Recovers organic carbon after long-duration crop |

All of the above are currently mocked with realistic sample ranges; swap the generator for real lab / IoT sensor data when available.

## Roadmap (next steps beyond hackathon demo)

- Connect Supabase/Postgres for real user accounts and persistent field data
- Replace mocked yield/soil models with a trained ML pipeline
- Real Google Earth Engine NDVI integration
- SMS gateway (Twilio/MSG91) — **already wired up** for both offline alerts and real OTP delivery. Set `MSG91_AUTH_KEY` or the `TWILIO_*` vars (see `.env.example`) and OTP login will send real SMS instead of showing the code on-screen. No keys set → automatically falls back to on-screen demo mode.
- Human fallback support — toll-free call + WhatsApp chat button available on every page (including before login), plus the real Govt Kisan Call Centre number (1800-180-1551), so a farmer is never stuck even if the app itself breaks
- Google Maps integration for nearest-mandi routing
- Build real hardware/edge-AI pipelines for the two remaining Future Innovations concepts above (sentinel camera nodes, autonomous weeder) and wire them to live `/api/*` endpoints the same way existing features are mocked today — the post-harvest grading concept has already made this jump (see `HARDWARE_DESIGN.md`)

---
Built for SIH — Agriculture, Food Tech & Rural Development.
