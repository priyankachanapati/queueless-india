import streamlit as st


from supabase import create_client
from datetime import datetime, timedelta
import uuid
import google.generativeai as genai
import os




# ---------------- CONFIG (FIXED FOR CLOUD RUN) ----------------

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("🚨 Backend configuration missing. Please contact admin.")
    st.stop()

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Gemini is optional
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("models/text-bison-001")
else:
    model = None

# -------------------------------------------------------------

st.set_page_config(page_title="Queueless India", layout="centered")

# ---------------- SESSION ----------------
if "user_id" not in st.session_state:
    st.session_state.user_id = str(uuid.uuid4())

if "last_signal_time" not in st.session_state:
    st.session_state.last_signal_time = None

def can_send_signal():
    if st.session_state.last_signal_time is None:
        return True
    return datetime.utcnow() - st.session_state.last_signal_time > timedelta(minutes=5)

# ---------------- HELPERS ----------------

def get_baseline(office_id, day, slot):
    res = supabase.table("baseline_wait_times") \
        .select("avg_wait_minutes") \
        .eq("office_id", office_id) \
        .eq("day_of_week", day) \
        .eq("time_slot", slot) \
        .execute()

    if res.data:
        return res.data[0]["avg_wait_minutes"]

    fallback = supabase.table("baseline_wait_times") \
        .select("avg_wait_minutes") \
        .eq("office_id", office_id) \
        .eq("time_slot", slot) \
        .execute()

    if fallback.data:
        vals = [r["avg_wait_minutes"] for r in fallback.data]
        return int(sum(vals) / len(vals))

    return None


def get_live_signals(office_id):
    since = datetime.utcnow() - timedelta(minutes=45)
    res = supabase.table("live_signals") \
        .select("*") \
        .eq("office_id", office_id) \
        .gte("timestamp", since.isoformat()) \
        .execute()
    return res.data


def classify_condition(entered, completed):
    if entered > completed:
        return "Heavier than usual"
    elif completed > entered:
        return "Lighter than usual"
    return "Normal"


def calculate_range(baseline, condition):
    if condition == "Heavier than usual":
        return int(baseline * 1.1), int(baseline * 1.3)
    elif condition == "Lighter than usual":
        return int(baseline * 0.7), int(baseline * 0.9)
    return int(baseline * 0.9), int(baseline * 1.1)


def confidence_level(signal_count):
    if signal_count >= 3:
        return "High"
    elif signal_count >= 1:
        return "Medium"
    return "Low"


def ai_explanation(day, slot, baseline, condition):
    if not model:
        return "This estimate is based on past data and recent visitor activity."
    try:
        return model.generate_content(
            f"Explain waiting time simply. Baseline {baseline} mins, condition {condition}."
        ).text
    except:
        return "This estimate is based on past data and recent visitor activity."


def best_time_today(office_id):
    today = datetime.now().weekday()
    best = None
    for h in range(9, 17):
        slot = f"{h:02d}:00-{h+1:02d}:00"
        b = get_baseline(office_id, today, slot)
        if b and (best is None or b < best[1]):
            best = (slot, b)
    return best

# ---------------- UI ----------------

st.title("🇮🇳 Queueless India")
st.markdown("### Plan your government office visit smartly")
st.divider()

# ---------------- LOCATION SELECTION ----------------

locations_res = supabase.table("locations").select("*").execute()

location_map = {
    f"{l['city']}, {l['state']}": l for l in locations_res.data
}

location_label = st.selectbox("📍 Select Location", location_map.keys())
location = location_map[location_label]

# ---------------- OFFICE SELECTION ----------------

offices_res = supabase.table("offices") \
    .select("*") \
    .eq("location_id", location["id"]) \
    .execute()

if not offices_res.data:
    st.warning("No offices found for this location.")
    st.stop()

office_map = {o["name"]: o for o in offices_res.data}

office_name = st.selectbox("🏢 Select Office", office_map.keys())
office = office_map[office_name]

# ---------------- PLAN VISIT ----------------

st.subheader("🗓️ Plan Your Visit")

day_map = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2,
    "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6
}

day_name = st.selectbox("Day of visit", day_map.keys())
day = day_map[day_name]

time_slots = [
    "09:00-10:00", "10:00-11:00", "11:00-12:00",
    "12:00-13:00", "14:00-15:00",
    "15:00-16:00", "16:00-17:00"
]

slot = st.selectbox("Time slot", time_slots)

baseline = get_baseline(office["id"], day, slot)

if baseline is None:
    st.warning("No historical data for this slot yet.")
    st.stop()

# ---------------- LIVE CALC ----------------

signals = get_live_signals(office["id"])
entered = sum(1 for s in signals if s["signal_type"] == "entered")
completed = sum(1 for s in signals if s["signal_type"] == "completed")

condition = classify_condition(entered, completed)
low, high = calculate_range(baseline, condition)
confidence = confidence_level(len(signals))

# ---------------- RESULTS ----------------

st.subheader("⏳ Expected Waiting Time")
st.markdown(f"## **{low} – {high} minutes**")
st.caption(f"Based on {len(signals)} recent check-ins")

c1, c2 = st.columns(2)
c1.metric("Condition", condition)
c2.metric("Confidence", confidence)

with st.expander("🤖 Explanation"):
    st.write(ai_explanation(day, slot, baseline, condition))

# ---------------- BEST TIME ----------------

best = best_time_today(office["id"])
if best:
    st.success(f"Best time today: **{best[0]}** (~{best[1]} mins)")

# ---------------- CHECK-IN ----------------

st.subheader("📍 Help others (optional)")

c1, c2 = st.columns(2)

with c1:
    if st.button("I just entered"):
        if can_send_signal():
            supabase.table("live_signals").insert({
                "office_id": office["id"],
                "signal_type": "entered",
                "user_id": st.session_state.user_id
            }).execute()
            st.session_state.last_signal_time = datetime.utcnow()
            st.success("Entry recorded!")

with c2:
    if st.button("I just completed"):
        if can_send_signal():
            supabase.table("live_signals").insert({
                "office_id": office["id"],
                "signal_type": "completed",
                "user_id": st.session_state.user_id
            }).execute()
            st.session_state.last_signal_time = datetime.utcnow()
            st.success("Completion recorded!")
