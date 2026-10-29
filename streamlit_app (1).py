"""MedMinder - Smart Medication Reminder (Streamlit + SQLite). Run: streamlit run streamlit_app.py"""
import json, sqlite3, datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

TZ, GRACE = ZoneInfo("Asia/Kolkata"), 30  # minutes before an unconfirmed dose is marked missed
SLOTS = {"morning": "08:00", "afternoon": "13:00", "evening": "18:00", "night": "21:00"}
LANGS = {"English": ("en", "en-IN"), "हिन्दी": ("hi", "hi-IN"), "தமிழ்": ("ta", "ta-IN")}

T = {
 "en": dict(title="MedMinder", sub="Your medicines, on time", today="Today", meds="Medicines", appts="Appointments",
  sharing="Sharing", patient="Patient", caregiver="Caregiver", taken="Taken", missed="Missed", pending="Pending",
  add_med="Add a medicine", name="Medicine name", dose="Dose note (as written by your doctor)", food="Food",
  before="Before food", after="After food", anytime="Any time", times="Times", custom="Custom time (optional)",
  morning="Morning", afternoon="Afternoon", evening="Evening", night="Night", save="Save", add="Add",
  delete="Delete", doctor="Doctor", date="Date", time="Time", note="Note", share="Let my caregiver see my records",
  pin="Caregiver PIN (4 digits)", history="Last 7 days", attention="Needs attention", due="Time for your medicine",
  none_m="No doses today. Add a medicine in the Medicines tab.", none_a="No upcoming appointments.", wrong="Wrong PIN",
  notshared="The patient has not allowed sharing.", enter="Open patient records", done="doses taken", nomiss="Nothing missed today.",
  remind="Time to take", voice="Alerts refresh every 30 seconds while this page is open.",
  disc="This app only follows the schedule entered from your doctor's instructions. It does not suggest medicines or change doses."),
 "hi": dict(title="मेडमाइंडर", sub="आपकी दवाइयाँ, सही समय पर", today="आज", meds="दवाइयाँ", appts="अपॉइंटमेंट",
  sharing="साझा करना", patient="मरीज़", caregiver="देखभालकर्ता", taken="ले ली", missed="छूट गई", pending="बाकी",
  add_med="दवा जोड़ें", name="दवा का नाम", dose="खुराक (डॉक्टर के अनुसार)", food="भोजन", before="खाने से पहले",
  after="खाने के बाद", anytime="कभी भी", times="समय", custom="अपना समय (वैकल्पिक)", morning="सुबह", afternoon="दोपहर",
  evening="शाम", night="रात", save="सहेजें", add="जोड़ें", delete="हटाएँ", doctor="डॉक्टर", date="तारीख", time="समय",
  note="टिप्पणी", share="मेरे देखभालकर्ता को मेरा रिकॉर्ड देखने दें", pin="देखभालकर्ता पिन (4 अंक)", history="पिछले 7 दिन",
  attention="ध्यान देने योग्य", due="दवा का समय हो गया", none_m="आज कोई खुराक नहीं। दवाइयाँ टैब में दवा जोड़ें।",
  none_a="कोई आने वाला अपॉइंटमेंट नहीं।", wrong="गलत पिन", notshared="मरीज़ ने साझा करने की अनुमति नहीं दी है।",
  enter="मरीज़ का रिकॉर्ड खोलें", done="खुराक ली गईं", nomiss="आज कोई खुराक नहीं छूटी।", remind="दवा लेने का समय:",
  voice="यह पेज खुला रहने पर अलर्ट हर 30 सेकंड में अपडेट होते हैं।",
  disc="यह ऐप केवल आपके डॉक्टर के निर्देशों से दर्ज किए गए शेड्यूल का पालन करता है। यह दवा सुझाता या खुराक नहीं बदलता।"),
 "ta": dict(title="மெட்மைண்டர்", sub="உங்கள் மருந்துகள், சரியான நேரத்தில்", today="இன்று", meds="மருந்துகள்",
  appts="மருத்துவ சந்திப்புகள்", sharing="பகிர்வு", patient="நோயாளி", caregiver="பராமரிப்பாளர்", taken="எடுத்தேன்",
  missed="தவறியது", pending="நிலுவையில்", add_med="மருந்தைச் சேர்க்கவும்", name="மருந்தின் பெயர்",
  dose="அளவு (மருத்துவர் கூறியபடி)", food="உணவு", before="உணவுக்கு முன்", after="உணவுக்குப் பின்",
  anytime="எப்போது வேண்டுமானாலும்", times="நேரங்கள்", custom="வேறு நேரம் (விருப்பம்)", morning="காலை",
  afternoon="மதியம்", evening="மாலை", night="இரவு", save="சேமி", add="சேர்", delete="நீக்கு", doctor="மருத்துவர்",
  date="தேதி", time="நேரம்", note="குறிப்பு", share="என் பராமரிப்பாளர் என் பதிவுகளைப் பார்க்க அனுமதி",
  pin="பராமரிப்பாளர் பின் (4 இலக்கம்)", history="கடந்த 7 நாட்கள்", attention="கவனிக்க வேண்டியவை",
  due="மருந்து எடுக்கும் நேரம்", none_m="இன்று அளவுகள் இல்லை. மருந்துகள் தத்தலில் மருந்தைச் சேர்க்கவும்.",
  none_a="வரவிருக்கும் சந்திப்புகள் இல்லை.", wrong="தவறான பின்", notshared="நோயாளி பகிர அனுமதிக்கவில்லை.",
  enter="நோயாளியின் பதிவுகளைத் திற", done="அளவுகள் எடுக்கப்பட்டன", nomiss="இன்று எதுவும் தவறவில்லை.",
  remind="மருந்து எடுக்கும் நேரம்:", voice="இந்தப் பக்கம் திறந்திருக்கும் வரை அறிவிப்புகள் 30 விநாடிகளுக்கு ஒருமுறை புதுப்பிக்கப்படும்.",
  disc="இந்த செயலி மருத்துவரின் அறிவுறுத்தலின்படி உள்ளிட்ட அட்டவணையை மட்டுமே பின்பற்றும். மருந்துகளைப் பரிந்துரைக்காது, அளவை மாற்றாது."),
}

st.set_page_config(page_title="MedMinder", page_icon="💊", layout="centered")
st.markdown("""<style>html,body,[class*="css"]{font-size:19px}
.stButton>button{min-height:3rem;font-weight:600;border-radius:12px}
.disc{border:2px dashed #4F6B72;border-radius:12px;padding:12px 16px;color:#4F6B72;margin-top:2rem}</style>""",
            unsafe_allow_html=True)


@st.cache_resource
def conn():
    c = sqlite3.connect("medminder.db", check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.executescript("""
    create table if not exists settings(k text primary key, v text);
    create table if not exists medicines(id integer primary key, name text, dose text, food text,
        times text, created text, created_time text);
    create table if not exists dose_log(id integer primary key, med_id integer, day text, time text,
        status text default 'pending', auto integer default 0, unique(med_id,day,time));
    create table if not exists appointments(id integer primary key, doctor text, day text, time text, note text);
    """)
    return c


db = conn()
now = lambda: dt.datetime.now(TZ).replace(tzinfo=None)


def setting(k, default=""):
    r = db.execute("select v from settings where k=?", (k,)).fetchone()
    return r["v"] if r else default


def put(k, v):
    db.execute("insert or replace into settings values(?,?)", (k, v)); db.commit()


def sync():
    n = now(); today = n.date().isoformat()
    for m in db.execute("select * from medicines").fetchall():
        for t in m["times"].split(","):
            if m["created"] == today and t < m["created_time"]:
                continue
            db.execute("insert or ignore into dose_log(med_id,day,time) values(?,?,?)", (m["id"], today, t))
    for r in db.execute("select id,day,time from dose_log where status='pending'").fetchall():
        if n > dt.datetime.fromisoformat(f"{r['day']}T{r['time']}") + dt.timedelta(minutes=GRACE):
            db.execute("update dose_log set status='missed', auto=1 where id=?", (r["id"],))
    db.commit()


def data():
    sync(); today = now().date().isoformat()
    doses = db.execute("""select l.*, m.name, m.dose, m.food from dose_log l join medicines m on m.id=l.med_id
        where day=? order by time""", (today,)).fetchall()
    hist = db.execute("""select day as Day, sum(status='taken') as Taken, sum(status='missed') as Missed
        from dose_log where day>=? group by day order by day desc""",
        ((now().date() - dt.timedelta(days=6)).isoformat(),)).fetchall()
    appts = db.execute("select * from appointments where day>=? order by day,time", (today,)).fetchall()
    meds = db.execute("select * from medicines order by name").fetchall()
    return doses, hist, appts, meds


def food_label(t, f):
    return t[f] if f in ("before", "after") else t["anytime"]


def summary(t, doses, hist, appts):
    done = sum(d["status"] == "taken" for d in doses)
    st.metric(t["today"], f"{done} / {len(doses)} {t['done']}")
    if doses:
        st.progress(done / len(doses))
    st.subheader(t["history"])
    st.dataframe(pd.DataFrame([dict(h) for h in hist]), hide_index=True, use_container_width=True)
    st.subheader(t["appts"])
    for a in appts:
        st.write(f"**{a['doctor']}** · {a['day']} {a['time'] or ''} {('· ' + a['note']) if a['note'] else ''}")
    if not appts:
        st.caption(t["none_a"])


def speak(texts, voice, title):
    """Spoken + browser-notification alert (runs in the browser)."""
    components.html(f"""<script>
    const L={json.dumps(voice)},T={json.dumps(texts)},H={json.dumps(title)};
    try{{if('Notification' in window&&Notification.permission==='default')Notification.requestPermission()}}catch(e){{}}
    T.forEach(s=>{{try{{const u=new SpeechSynthesisUtterance(s);u.lang=L;u.rate=.9;speechSynthesis.speak(u)}}catch(e){{}}
      try{{if(Notification.permission==='granted')new Notification(H,{{body:s}})}}catch(e){{}}}});</script>""", height=0)


# ---------------- sidebar ----------------
lang_name = st.sidebar.selectbox("🌐", list(LANGS), key="lang")
code, voice = LANGS[lang_name]
t = T[code]
mode = st.sidebar.radio(" ", [t["patient"], t["caregiver"]], label_visibility="collapsed")
st.title(f"💊 {t['title']}")
st.caption(t["sub"])
if st_autorefresh:
    st_autorefresh(interval=30000, key="tick")

doses, hist, appts, meds = data()

# ---------------- caregiver ----------------
if mode == t["caregiver"]:
    if setting("share") != "1":
        st.warning(t["notshared"])
    else:
        pin = st.text_input(t["pin"], type="password", max_chars=4)
        if pin and pin != setting("pin"):
            st.error(t["wrong"])
        elif pin:
            st.subheader(t["attention"])
            bad = [d for d in doses if d["status"] == "missed"]
            for d in bad:
                st.error(f"{d['name']} · {d['time']} — {t['missed']}")
            if not bad:
                st.success(t["nomiss"])
            for d in doses:
                st.write(f"**{d['name']}** · {d['time']} · {food_label(t, d['food'])} — {t[d['status']]}")
            summary(t, doses, hist, appts)
            st.subheader(t["meds"])
            for m in meds:
                st.write(f"**{m['name']}** · {m['times']} {('· ' + m['dose']) if m['dose'] else ''}")
    st.markdown(f"<div class='disc'>{t['disc']}</div>", unsafe_allow_html=True)
    st.stop()

# ---------------- patient ----------------
tab_today, tab_meds, tab_appt, tab_share = st.tabs([t["today"], t["meds"], t["appts"], t["sharing"]])
hhmm = now().strftime("%H:%M")

with tab_today:
    spoken = st.session_state.setdefault("spoken", set())
    due = [d for d in doses if d["status"] == "pending" and d["time"] <= hhmm]
    fresh = [d for d in due if d["id"] not in spoken]
    if fresh:
        speak([f"{t['remind']} {d['name']} ({food_label(t, d['food'])})" for d in fresh], voice, t["due"])
        spoken.update(d["id"] for d in fresh)
    for d in due:
        st.warning(f"⏰ {t['due']}: **{d['name']}** · {d['time']}")
    if not doses:
        st.info(t["none_m"])
    for d in doses:
        with st.container(border=True):
            icon = {"taken": "🟢", "missed": "🔴", "pending": "🟠"}[d["status"]]
            st.markdown(f"### {icon} {d['name']} · {d['time']}")
            st.caption(f"{food_label(t, d['food'])}{(' · ' + d['dose']) if d['dose'] else ''} — {t[d['status']]}")
            c1, c2 = st.columns(2)
            if c1.button(f"✔ {t['taken']}", key=f"t{d['id']}", use_container_width=True):
                db.execute("update dose_log set status='taken', auto=0 where id=?", (d["id"],)); db.commit(); st.rerun()
            if c2.button(f"✖ {t['missed']}", key=f"m{d['id']}", use_container_width=True):
                db.execute("update dose_log set status='missed', auto=0 where id=?", (d["id"],)); db.commit(); st.rerun()
    summary(t, doses, hist, appts)
    if not st_autorefresh:
        st.caption("Tip: pip install streamlit-autorefresh for automatic 30-second reminders.")
    else:
        st.caption(t["voice"])

with tab_meds:
    st.subheader(t["add_med"])
    with st.form("med", clear_on_submit=True):
        name = st.text_input(t["name"])
        dose = st.text_input(t["dose"])
        food = st.selectbox(t["food"], ["any", "before", "after"], format_func=lambda f: food_label(t, f))
        slots = st.multiselect(t["times"], list(SLOTS), format_func=lambda s: f"{t[s]} ({SLOTS[s]})")
        custom = st.time_input(t["custom"], value=None)
        if st.form_submit_button(t["save"], use_container_width=True) and name.strip():
            times = [SLOTS[s] for s in slots] + ([custom.strftime("%H:%M")] if custom else [])
            if times:
                n = now()
                db.execute("insert into medicines(name,dose,food,times,created,created_time) values(?,?,?,?,?,?)",
                           (name.strip(), dose, food, ",".join(sorted(set(times))), n.date().isoformat(), n.strftime("%H:%M")))
                db.commit(); st.rerun()
    for m in meds:
        c1, c2 = st.columns([4, 1])
        c1.write(f"**{m['name']}** · {m['times']}")
        if c2.button(t["delete"], key=f"dm{m['id']}"):
            db.execute("delete from medicines where id=?", (m["id"],)); db.execute("delete from dose_log where med_id=?", (m["id"],))
            db.commit(); st.rerun()

with tab_appt:
    with st.form("appt", clear_on_submit=True):
        doc = st.text_input(t["doctor"])
        day = st.date_input(t["date"], value=now().date())
        tm = st.time_input(t["time"], value=None)
        nt = st.text_input(t["note"])
        if st.form_submit_button(t["add"], use_container_width=True) and doc.strip():
            db.execute("insert into appointments(doctor,day,time,note) values(?,?,?,?)",
                       (doc.strip(), day.isoformat(), tm.strftime("%H:%M") if tm else "", nt)); db.commit(); st.rerun()
    for a in appts:
        c1, c2 = st.columns([4, 1])
        c1.write(f"**{a['doctor']}** · {a['day']} {a['time'] or ''} {a['note'] or ''}")
        if c2.button(t["delete"], key=f"da{a['id']}"):
            db.execute("delete from appointments where id=?", (a["id"],)); db.commit(); st.rerun()

with tab_share:
    with st.form("share"):
        sh = st.checkbox(t["share"], value=setting("share") == "1")
        pin = st.text_input(t["pin"], value=setting("pin"), max_chars=4)
        if st.form_submit_button(t["save"], use_container_width=True):
            put("share", "1" if sh else "0")
            if pin.isdigit() and len(pin) == 4:
                put("pin", pin)
            st.success("✔")

st.markdown(f"<div class='disc'>{t['disc']}</div>", unsafe_allow_html=True)
