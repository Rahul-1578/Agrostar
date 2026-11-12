# Affordable Post-Harvest Quality Grading Unit — Hardware Design

Companion hardware spec for the **Quality Grading Unit** feature in AgroStar
(`/feature/postharvest-quality-grading`). The software module simulates
this unit's full workflow end-to-end; this document is the physical build
that would sit in a rural pack-house and feed the same API endpoints.

**Calibrated commodity for v1: Tomato** — high post-harvest loss rate in
India (est. 15–25%), fast-changing ripeness signals (color, ethylene,
softness), and a well-documented shelf-life science base to calibrate
against. The design generalizes to other commodities by swapping the
calibration table (see "Calibration procedure" below) — no hardware changes
needed.

---

## 1. Design goals (from the brief)

- **Affordable** — target under ₹6,000 per unit (commercial multispectral
  grading systems cost lakhs of rupees; this is a pack-house-friendly budget
  alternative, not a lab-grade replacement).
- **Visible/multispectral imaging** — grade by color, size, and surface
  defects.
- **Temperature, humidity, and gas sensing** — catch spoilage signals that
  aren't visible yet.
- **Shelf-life estimate + storage recommendation + traceable report** —
  actionable output, not just raw data.
- **Rural pack-house operation** — unreliable power and connectivity must
  not stop the unit from working.

---

## 2. Bill of Materials (BOM)

| Component | Purpose | Approx. cost (₹) |
|---|---|---|
| ESP32-CAM (with OV2640 camera) | Vision capture + onboard WiFi + compute | 550 |
| DHT22 temperature/humidity sensor | Cold-chain condition monitoring | 200 |
| MQ-135 gas sensor | VOC/ethylene-correlated spoilage proxy | 180 |
| 3× LED (red 630nm, blue 470nm, NIR 850nm) + resistors | Switched-illumination "multispectral-lite" capture (see §3) | 90 |
| 5V power regulator (buck converter) | Stable power from battery/solar input | 80 |
| 6V/2W solar panel | Off-grid daytime charging | 350 |
| 18650 Li-ion battery + charge controller (TP4056) | Power through the night / cloudy days | 300 |
| Weatherproof enclosure (ABS, IP54-rated) | Dust/moisture protection in pack-house conditions | 450 |
| Misc. (wiring, perfboard, mounting, diffuser for imaging chamber) | Assembly | 300 |
| **Total (approx.)** | | **≈ ₹2,500 per unit** |

Add ~₹1,000–1,500 for a small enclosure-mounted OLED status display and
a physical "capture" button if a no-phone-needed standalone mode is wanted
— optional for v1, since a paired phone/tablet running the AgroStar app
covers the UI.

**Estimated all-in cost: ₹2,500–4,000 per unit** at hobbyist/small-batch
pricing — orders-of-magnitude cheaper than commercial multispectral
sorters, which routinely cost ₹5–50 lakh.

---

## 3. "Multispectral-lite" on a budget

True multispectral imaging (dedicated narrow-band sensors across many
wavelengths) is expensive. This design approximates useful spectral
differentiation cheaply:

1. Capture one frame under **white light** (baseline visible image).
2. Capture a second frame under **630nm red LED only** — emphasizes
   ripeness-related redness and surface bruising contrast.
3. Capture a third frame under **850nm near-IR LED only** — near-IR
   reflectance correlates with internal bruising/water content in produce,
   even when it isn't visible under white light.
4. The three frames are compared (redness ratio, NIR reflectance delta) to
   flag defects a single white-light photo would miss.

This is the same principle used in low-cost precision-agriculture "poor
man's multispectral" rigs — swap a single camera between a few LED
wavelengths, rather than buying a real multispectral sensor array.

---

## 4. System block diagram

```
        ┌─────────────────────┐
        │   Imaging chamber    │   White / Red / NIR LEDs (switched)
        │   (ESP32-CAM)        │   + diffuser for consistent lighting
        └──────────┬───────────┘
                    │
   ┌────────────────┼─────────────────┐
   │                │                 │
┌──▼───┐       ┌────▼─────┐     ┌─────▼─────┐
│ DHT22 │       │  MQ-135  │     │  ESP32     │
│ Temp/ │       │  Gas/VOC │     │  MCU +     │
│ Humid │       │  sensor  │     │  WiFi      │
└──────┘       └──────────┘     └─────┬──────┘
                                       │  HTTP POST (image + sensor JSON)
                                       ▼
                        ┌───────────────────────────┐
                        │   AgroStar Flask backend     │
                        │  /api/quality_grading/*    │
                        │  (grading logic, shelf-life│
                        │   model, report generation)│
                        └───────────┬───────────────┘
                                    │
                    ┌───────────────┼────────────────┐
                    ▼                                ▼
          Farmer's AgroStar app              Public traceable report
          (grade, shelf-life,               (/report/<batch_id>)
           storage actions)                 — QR code for aggregators/buyers
```

**Offline-first note:** the unit buffers readings locally (ESP32 flash) if
WiFi/internet is unavailable at the moment of capture, and syncs to the
Flask backend once connectivity returns — consistent with the rest of
AgroStar's offline-first design.

---

## 5. Power & connectivity for rural conditions

- **Power:** solar + 18650 battery combo keeps the unit running through
  grid outages, common in rural pack-houses. ESP32 in deep-sleep between
  captures keeps draw low enough for the small solar panel to sustain it.
- **Connectivity:** ESP32 WiFi connects to a phone hotspot or pack-house
  router when available. If offline, readings queue locally and sync later
  — the unit never blocks a grading session on having live internet.
- **Enclosure:** IP54-rated to handle dust and incidental moisture typical
  of an open-sided rural pack-house, without needing a climate-controlled
  room.

---

## 6. Calibration procedure (Tomato, v1)

1. **Collect a labeled reference set** — ~150–200 tomatoes spanning grades
   A (market-fresh), B (minor blemish/slightly over-ripe), and C (visible
   bruising/soft spots), graded manually by an experienced mandi grader as
   ground truth.
2. **Capture reference images + sensor readings** for each sample under
   controlled conditions (known temperature/humidity/gas exposure).
3. **Derive thresholds** for the redness ratio (§3) and NIR reflectance
   delta that best separate A/B/C in the reference set — these become the
   `visual_score` thresholds used by `/api/quality_grading/analyze`.
4. **Set the ideal storage baseline** — for tomato: 12–15°C, 90–95% RH,
   low ethylene exposure — sourced from post-harvest horticulture
   literature, used as the shelf-life model's anchor point (already
   encoded in `app.py` as `TOMATO_IDEAL_TEMP_RANGE` /
   `TOMATO_IDEAL_HUMIDITY_RANGE` / `TOMATO_BASELINE_SHELF_LIFE_DAYS`).
5. **Validate** — run held-out samples through the calibrated model, compare
   predicted grade + shelf-life against actual observed spoilage over the
   following days, and adjust thresholds if there's systematic bias.
6. **Recalibrating for a different commodity** — repeat steps 1–5 with a
   new reference set and update the constants; the imaging/sensor/software
   pipeline itself doesn't change.

---

## 7. What's simulated vs. real in the current software demo

To be transparent about the current state (important for judges):

| Component | Status in the live app |
|---|---|
| Grading UI, workflow, API contract | **Real** — fully working end-to-end |
| Shelf-life model (temperature/humidity/gas → days remaining) | **Real domain logic** — deterministic formula grounded in tomato post-harvest storage science, not a placeholder |
| Storage action recommendations | **Real rule-based logic**, not canned text |
| Traceable report + QR code + public verification link | **Real** — fully working, no login required to view |
| Live sensor readings | **Simulated** — realistic random values standing in for DHT22/MQ-135 GPIO reads, until physical hardware is built |
| Image-based defect/ripeness scoring | **Heuristic on real uploaded images** (color-balance analysis) standing in for a trained CV model — a genuine next step would be training a small CNN on the labeled reference set from the calibration procedure above |

This keeps the demo honest: the parts that require physical sensors are
clearly simulated, while the actual decision logic (shelf-life, storage
actions, traceability) is real and ready to run unmodified once real
sensor hardware is attached.
