"""MedMinder - Smart Medication Reminder (Streamlit + SQLite). Run: streamlit run streamlit_app.py

Accounts: a patient signs in with name + phone number / email. A caregiver signs in with the same
details plus the 4-digit caregiver PIN. Every record belongs to one patient, so nobody sees another
patient's medicines. Only the caregiver adds / removes medicines and chooses their times.

Times are chosen and shown in 12-hour AM/PM format. If the patient has not confirmed a dose (taken / missed)
within ALERT_AFTER minutes, an alert is sent to the contact entered at sign-in: an SMS if it is a phone number,
an email if it is an email address. A background thread checks every 30 s, so alerts go out even when nobody
has the page open (the Streamlit server itself must be running).

ALERT SETUP (environment variables or .streamlit/secrets.toml - never hard-code passwords here):
  Email : SMTP_HOST, SMTP_PORT (587 or 465), SMTP_USER, SMTP_PASSWORD, SMTP_FROM (optional)
          e.g. Gmail: smtp.gmail.com / 587 / your address / a Google "App password"
  SMS   : TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM (your Twilio number, e.g. +1415...)
"""
import base64, hashlib, hmac, json, os, re, smtplib, sqlite3, threading, time, urllib.parse, urllib.request
import datetime as dt
from email.message import EmailMessage
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

TZ, GRACE = ZoneInfo("Asia/Kolkata"), 30  # minutes before an unconfirmed dose is marked missed
DEFAULT_TIMES = ["08:00", "13:00", "18:00", "21:00", "10:00", "15:00"]  # only starting suggestions; all editable
MAX_DOSES = 6
ALERT_AFTER = 15    # minutes after the dose time with no response -> alert the caregiver (must be < GRACE)
ALERT_WINDOW = 180  # never alert for doses older than this many minutes (avoids floods after downtime)
COUNTRY_CODE = "+91"  # prefix added to 10-digit phone numbers for SMS
DB_PATH = "medminder_accounts.db"
CFG_KEYS = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
            "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"]
LANGS = {"English": ("en", "en-IN"), "हिन्दी": ("hi", "hi-IN"), "தமிழ்": ("ta", "ta-IN")}

T = {
 "en": dict(title="MedMinder", sub="Your medicines, on time", today="Today", meds="Medicines", appts="Appointments",
  patient="Patient", caregiver="Caregiver", taken="Taken", missed="Missed", pending="Pending",
  add_med="Add a medicine", name="Medicine name", dose="Dose note (as written by your doctor)", food="Food",
  before="Before food", after="After food", anytime="Any time", times="Times", time="Time",
  save="Save", add="Add", delete="Remove", doctor="Doctor", date="Date", note="Note",
  pin="Caregiver PIN (4 digits)", history="Last 7 days", attention="Needs attention", due="Time for your medicine",
  none_m="No doses today.", none_a="No upcoming appointments.", done="doses taken", nomiss="Nothing missed today.",
  remind="Time to take", voice="Alerts refresh every 30 seconds while this page is open.",
  signin="Sign in", create="Create account", pname="Patient name", contact="Phone number or email",
  signin_as="Sign in as", invalid="Details do not match. Please check and try again.",
  bad_contact="Enter a valid 10-digit phone number or an email address.",
  exists="An account with this phone/email already exists. Please sign in.", need_name="Please enter the patient name.",
  bad_pin="PIN must be exactly 4 digits.", signout="Sign out", signed="Signed in as", cg_for="Caregiver view for",
  how_many="Doses per day", edit_times="Change times", keep_times="Times to keep", add_time="Add another time",
  need_time="Keep at least one time.", updated="Updated", confirm="Remove this medicine and its dose history?",
  yes="Yes, remove", cancel="Cancel", none_med="No medicines added yet.", need_med="Please enter the medicine name.",
  ro_note="Your caregiver adds your medicines and sets the times.", new_pin="New caregiver PIN", change_pin="Change PIN",
  pin_saved="PIN updated", overview="Overview", account="Account",
  disc="This app only follows the schedule entered from your doctor's instructions. It does not suggest medicines or change doses."),
 "hi": dict(title="मेडमाइंडर", sub="आपकी दवाइयाँ, सही समय पर", today="आज", meds="दवाइयाँ", appts="अपॉइंटमेंट",
  patient="मरीज़", caregiver="देखभालकर्ता", taken="ले ली", missed="छूट गई", pending="बाकी",
  add_med="दवा जोड़ें", name="दवा का नाम", dose="खुराक (डॉक्टर के अनुसार)", food="भोजन", before="खाने से पहले",
  after="खाने के बाद", anytime="कभी भी", times="समय", time="समय", save="सहेजें", add="जोड़ें", delete="हटाएँ",
  doctor="डॉक्टर", date="तारीख", note="टिप्पणी", pin="देखभालकर्ता पिन (4 अंक)", history="पिछले 7 दिन",
  attention="ध्यान देने योग्य", due="दवा का समय हो गया", none_m="आज कोई खुराक नहीं।",
  none_a="कोई आने वाला अपॉइंटमेंट नहीं।", done="खुराक ली गईं", nomiss="आज कोई खुराक नहीं छूटी।",
  remind="दवा लेने का समय:", voice="यह पेज खुला रहने पर अलर्ट हर 30 सेकंड में अपडेट होते हैं।",
  signin="साइन इन", create="खाता बनाएँ", pname="मरीज़ का नाम", contact="फ़ोन नंबर या ईमेल",
  signin_as="इस रूप में साइन इन करें", invalid="विवरण मेल नहीं खाते। कृपया जाँचकर फिर कोशिश करें।",
  bad_contact="सही 10 अंकों का फ़ोन नंबर या ईमेल दर्ज करें।",
  exists="इस फ़ोन/ईमेल से खाता पहले से है। कृपया साइन इन करें।", need_name="कृपया मरीज़ का नाम लिखें।",
  bad_pin="पिन ठीक 4 अंकों का होना चाहिए।", signout="साइन आउट", signed="साइन इन:", cg_for="देखभालकर्ता दृश्य:",
  how_many="दिन में कितनी खुराक", edit_times="समय बदलें", keep_times="रखने वाले समय", add_time="एक और समय जोड़ें",
  need_time="कम से कम एक समय रखें।", updated="अपडेट हो गया", confirm="इस दवा और इसका खुराक इतिहास हटाएँ?",
  yes="हाँ, हटाएँ", cancel="रद्द करें", none_med="अभी कोई दवा नहीं जोड़ी गई।", need_med="कृपया दवा का नाम लिखें।",
  ro_note="आपके देखभालकर्ता दवाइयाँ जोड़ते हैं और समय तय करते हैं।", new_pin="नया देखभालकर्ता पिन",
  change_pin="पिन बदलें", pin_saved="पिन अपडेट हो गया", overview="सारांश", account="खाता",
  disc="यह ऐप केवल आपके डॉक्टर के निर्देशों से दर्ज किए गए शेड्यूल का पालन करता है। यह दवा सुझाता या खुराक नहीं बदलता।"),
 "ta": dict(title="மெட்மைண்டர்", sub="உங்கள் மருந்துகள், சரியான நேரத்தில்", today="இன்று", meds="மருந்துகள்",
  appts="மருத்துவ சந்திப்புகள்", patient="நோயாளி", caregiver="பராமரிப்பாளர்", taken="எடுத்தேன்",
  missed="தவறியது", pending="நிலுவையில்", add_med="மருந்தைச் சேர்க்கவும்", name="மருந்தின் பெயர்",
  dose="அளவு (மருத்துவர் கூறியபடி)", food="உணவு", before="உணவுக்கு முன்", after="உணவுக்குப் பின்",
  anytime="எப்போது வேண்டுமானாலும்", times="நேரங்கள்", time="நேரம்", save="சேமி", add="சேர்", delete="நீக்கு",
  doctor="மருத்துவர்", date="தேதி", note="குறிப்பு", pin="பராமரிப்பாளர் பின் (4 இலக்கம்)",
  history="கடந்த 7 நாட்கள்", attention="கவனிக்க வேண்டியவை", due="மருந்து எடுக்கும் நேரம்",
  none_m="இன்று அளவுகள் இல்லை.", none_a="வரவிருக்கும் சந்திப்புகள் இல்லை.", done="அளவுகள் எடுக்கப்பட்டன",
  nomiss="இன்று எதுவும் தவறவில்லை.", remind="மருந்து எடுக்கும் நேரம்:",
  voice="இந்தப் பக்கம் திறந்திருக்கும் வரை அறிவிப்புகள் 30 விநாடிகளுக்கு ஒருமுறை புதுப்பிக்கப்படும்.",
  signin="உள்நுழை", create="கணக்கை உருவாக்கு", pname="நோயாளியின் பெயர்", contact="தொலைபேசி எண் அல்லது மின்னஞ்சல்",
  signin_as="இவராக உள்நுழை", invalid="விவரங்கள் பொருந்தவில்லை. சரிபார்த்து மீண்டும் முயற்சிக்கவும்.",
  bad_contact="சரியான 10 இலக்க தொலைபேசி எண் அல்லது மின்னஞ்சலை உள்ளிடவும்.",
  exists="இந்த தொலைபேசி/மின்னஞ்சலில் ஏற்கனவே கணக்கு உள்ளது. உள்நுழையவும்.", need_name="நோயாளியின் பெயரை உள்ளிடவும்.",
  bad_pin="பின் சரியாக 4 இலக்கங்கள் இருக்க வேண்டும்.", signout="வெளியேறு", signed="உள்நுழைந்தவர்:",
  cg_for="பராமரிப்பாளர் பார்வை:", how_many="ஒரு நாளில் எத்தனை அளவு", edit_times="நேரங்களை மாற்று",
  keep_times="வைத்திருக்க வேண்டிய நேரங்கள்", add_time="இன்னொரு நேரத்தைச் சேர்", need_time="குறைந்தது ஒரு நேரம் வேண்டும்.",
  updated="புதுப்பிக்கப்பட்டது", confirm="இந்த மருந்தையும் அதன் அளவு வரலாற்றையும் நீக்கவா?",
  yes="ஆம், நீக்கு", cancel="ரத்து", none_med="இன்னும் மருந்துகள் சேர்க்கப்படவில்லை.",
  need_med="மருந்தின் பெயரை உள்ளிடவும்.", ro_note="உங்கள் பராமரிப்பாளர் மருந்துகளைச் சேர்த்து நேரத்தை அமைப்பார்.",
  new_pin="புதிய பராமரிப்பாளர் பின்", change_pin="பின்னை மாற்று", pin_saved="பின் புதுப்பிக்கப்பட்டது",
  overview="சுருக்கம்", account="கணக்கு",
  disc="இந்த செயலி மருத்துவரின் அறிவுறுத்தலின்படி உள்ளிட்ட அட்டவணையை மட்டுமே பின்பற்றும். மருந்துகளைப் பரிந்துரைக்காது, அளவை மாற்றாது."),
}

T["en"].update(alert_note="Missed-dose alerts are sent to this number (SMS) or email.", alerts="Caregiver alerts",
  alert_to="Alerts are sent to", alert_sms="SMS", alert_mail="email", alert_sent="alert sent", alert_failed="alert failed",
  alert_rule="If the patient does not confirm a dose within {m} minutes, an alert is sent.",
  alert_off="Alerts are not set up yet for this type of contact. Add the settings listed at the top of the app file and restart.",
  test_alert="Send test alert", test_ok="Test alert sent", test_fail="Could not send")
T["hi"].update(alert_note="छूटी खुराक के अलर्ट इस नंबर (SMS) या ईमेल पर भेजे जाएँगे।", alerts="देखभालकर्ता अलर्ट",
  alert_to="अलर्ट यहाँ भेजे जाते हैं", alert_sms="SMS", alert_mail="ईमेल", alert_sent="अलर्ट भेजा गया", alert_failed="अलर्ट विफल",
  alert_rule="अगर मरीज़ {m} मिनट में खुराक की पुष्टि नहीं करता, तो अलर्ट भेजा जाता है।",
  alert_off="इस तरह के संपर्क के लिए अलर्ट अभी सेट नहीं हैं। ऐप फ़ाइल के ऊपर दी गई सेटिंग जोड़ें और रीस्टार्ट करें।",
  test_alert="टेस्ट अलर्ट भेजें", test_ok="टेस्ट अलर्ट भेजा गया", test_fail="भेजा नहीं जा सका")
T["ta"].update(alert_note="தவறிய அளவு எச்சரிக்கைகள் இந்த எண்ணுக்கு (SMS) அல்லது மின்னஞ்சலுக்கு அனுப்பப்படும்.",
  alerts="பராமரிப்பாளர் எச்சரிக்கைகள்", alert_to="எச்சரிக்கைகள் அனுப்பப்படுவது", alert_sms="SMS", alert_mail="மின்னஞ்சல்",
  alert_sent="எச்சரிக்கை அனுப்பப்பட்டது", alert_failed="எச்சரிக்கை தோல்வி",
  alert_rule="நோயாளி {m} நிமிடங்களில் அளவை உறுதிப்படுத்தாவிட்டால் எச்சரிக்கை அனுப்பப்படும்.",
  alert_off="இந்த வகை தொடர்புக்கு எச்சரிக்கைகள் இன்னும் அமைக்கப்படவில்லை. செயலி கோப்பின் மேலே உள்ள அமைப்புகளைச் சேர்த்து மீண்டும் தொடங்கவும்.",
  test_alert="சோதனை எச்சரிக்கை அனுப்பு", test_ok="சோதனை எச்சரிக்கை அனுப்பப்பட்டது", test_fail="அனுப்ப முடியவில்லை")

st.set_page_config(page_title="MedMinder", page_icon="💊", layout="centered")
st.markdown("""<style>html,body,[class*="css"]{font-size:19px}
.stButton>button{min-height:3rem;font-weight:600;border-radius:12px}
.disc{border:2px dashed #4F6B72;border-radius:12px;padding:12px 16px;color:#4F6B72;margin-top:2rem}</style>""",
            unsafe_allow_html=True)


@st.cache_resource
def conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("pragma journal_mode=wal")
    c.executescript("""
    create table if not exists patients(id integer primary key, name text, contact text unique, pin_hash text);
    create table if not exists medicines(id integer primary key, patient_id integer, name text, dose text, food text,
        times text, created text, created_time text);
    create table if not exists dose_log(id integer primary key, med_id integer, patient_id integer, day text, time text,
        status text default 'pending', auto integer default 0, unique(med_id,day,time));
    create table if not exists appointments(id integer primary key, patient_id integer, doctor text, day text,
        time text, note text);
    """)
    if "alert_state" not in [r[1] for r in c.execute("pragma table_info(dose_log)")]:
        c.execute("alter table dose_log add column alert_state text default ''")  # '', sending, sent, failed:<why>
    c.commit()
    return c


db = conn()
now = lambda: dt.datetime.now(TZ).replace(tzinfo=None)


# ---------------- accounts ----------------
def hash_pin(pin, salt=None):
    salt = salt or os.urandom(16).hex()
    return f"{salt}${hashlib.pbkdf2_hmac('sha256', pin.encode(), bytes.fromhex(salt), 120_000).hex()}"


def check_pin(pin, stored):
    return hmac.compare_digest(hash_pin(pin, stored.split("$")[0]), stored)


def norm_name(s):
    return " ".join(s.split())


def norm_contact(s):
    """Return a normalised phone (10 digits) or lower-case email, else None."""
    s = s.strip()
    if "@" in s:
        return s.lower() if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else None
    d = re.sub(r"\D", "", s)
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    return d if len(d) == 10 else None


def find_patient(name, contact):
    c = norm_contact(contact)
    r = db.execute("select * from patients where contact=?", (c,)).fetchone() if c else None
    return r if r and r["name"].casefold() == norm_name(name).casefold() else None


def login(row, role):
    st.session_state.update(pid=row["id"], pname=row["name"], contact=row["contact"], role=role, spoken=set())
    st.rerun()


# ---------------- 12-hour time helpers ----------------
def fmt12(hhmm):
    """'13:05' -> '1:05 PM' (times are stored as 24-hour HH:MM, shown as AM/PM)."""
    if not hhmm:
        return ""
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def time_pick(label, key, default="08:00", optional=False):
    """Hour / minute / AM-PM picker. Returns 'HH:MM' (24h), or None if optional and hour is left on '--'."""
    st.markdown(label)
    h12, mm, ap = 8, 0, "AM"
    if default:
        h24, mm = int(default[:2]), int(default[3:5])
        h12, ap = (h24 % 12) or 12, "AM" if h24 < 12 else "PM"
    hours = (["--"] if optional else []) + [str(h) for h in range(1, 13)]
    c1, c2, c3 = st.columns(3)
    hs = c1.selectbox("hour", hours, index=hours.index(str(h12)) if (default or not optional) else 0,
                      key=f"{key}_h", label_visibility="collapsed")
    ms = c2.selectbox("minute", [f"{x:02d}" for x in range(60)], index=mm, key=f"{key}_m", label_visibility="collapsed")
    ps = c3.selectbox("am/pm", ["AM", "PM"], index=0 if ap == "AM" else 1, key=f"{key}_p", label_visibility="collapsed")
    if hs == "--":
        return None
    return f"{(int(hs) % 12) + (12 if ps == 'PM' else 0):02d}:{ms}"


# ---------------- caregiver alerts (SMS / email) ----------------
def load_cfg():
    out = {}
    for k in CFG_KEYS:
        v = os.environ.get(k)
        if not v:
            try:
                v = st.secrets.get(k)
            except Exception:
                v = None
        out[k] = str(v) if v else ""
    return out


def alert_ready(cfg, contact):
    keys = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD") if "@" in contact else \
           ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")
    return all(cfg.get(k) for k in keys)


def notify(cfg, contact, subject, body):
    """Email if `contact` is an address, SMS if it is a phone number. Returns (ok, detail)."""
    if not alert_ready(cfg, contact):
        return False, "not configured"
    try:
        if "@" in contact:
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = subject, cfg["SMTP_FROM"] or cfg["SMTP_USER"], contact
            msg.set_content(body)
            port = int(cfg["SMTP_PORT"] or 587)
            if port == 465:
                with smtplib.SMTP_SSL(cfg["SMTP_HOST"], port, timeout=20) as s:
                    s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"]); s.send_message(msg)
            else:
                with smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=20) as s:
                    s.starttls(); s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"]); s.send_message(msg)
        else:
            sid = cfg["TWILIO_ACCOUNT_SID"]
            req = urllib.request.Request(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                data=urllib.parse.urlencode({"To": COUNTRY_CODE + contact, "From": cfg["TWILIO_FROM"], "Body": body}).encode())
            req.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{cfg['TWILIO_AUTH_TOKEN']}".encode()).decode())
            urllib.request.urlopen(req, timeout=20).close()
        return True, ""
    except Exception as e:
        return False, str(e)[:120]


# ---------------- data ----------------
def check_patient(c, p, cfg):
    """Create today's dose rows, alert the caregiver about unconfirmed doses, then auto-mark long-overdue ones missed.
    `c` is a sqlite connection, `p` a patients row. Safe to call from the page and from the background thread."""
    n = now(); today = n.date().isoformat(); pid = p["id"]
    for m in c.execute("select * from medicines where patient_id=?", (pid,)).fetchall():
        for tm_ in m["times"].split(","):
            if m["created"] == today and tm_ < m["created_time"]:
                continue
            c.execute("insert or ignore into dose_log(med_id,patient_id,day,time) values(?,?,?,?)", (m["id"], pid, today, tm_))
    c.commit()
    pending = c.execute("""select l.id,l.day,l.time,m.name from dose_log l join medicines m on m.id=l.med_id
                           where l.patient_id=? and l.status='pending'""", (pid,)).fetchall()
    for r in pending:
        due = dt.datetime.fromisoformat(f"{r['day']}T{r['time']}")
        late = n - due
        if dt.timedelta(minutes=ALERT_AFTER) <= late <= dt.timedelta(minutes=ALERT_WINDOW):
            # claim first so the page and the background thread never send the same alert twice
            claimed = c.execute("update dose_log set alert_state='sending' where id=? and coalesce(alert_state,'')=''",
                                (r["id"],)).rowcount
            c.commit()
            if claimed:
                ok, info = notify(cfg, p["contact"], f"MedMinder: {p['name']} has not confirmed a dose",
                                  f"MedMinder alert: {p['name']} has not confirmed {r['name']} "
                                  f"(due {fmt12(r['time'])}). Please check on them.")
                c.execute("update dose_log set alert_state=? where id=?", ("sent" if ok else f"failed:{info}", r["id"]))
                c.commit()
        if late > dt.timedelta(minutes=GRACE):
            c.execute("update dose_log set status='missed', auto=1 where id=? and status='pending'", (r["id"],))
    c.commit()


@st.cache_resource
def start_watcher():
    """Background thread: checks every patient every 30 s so alerts are sent even with no browser open."""
    cfg = load_cfg()

    def loop():
        c = sqlite3.connect(DB_PATH, timeout=10)
        c.row_factory = sqlite3.Row
        while True:
            try:
                for p in c.execute("select * from patients").fetchall():
                    check_patient(c, p, cfg)
            except Exception:
                pass
            time.sleep(30)

    threading.Thread(target=loop, daemon=True).start()
    return cfg


def data(pid):
    check_patient(db, db.execute("select * from patients where id=?", (pid,)).fetchone(), CFG)
    today = now().date().isoformat()
    doses = db.execute("""select l.*, m.name, m.dose, m.food from dose_log l join medicines m on m.id=l.med_id
        where l.patient_id=? and day=? order by time""", (pid, today)).fetchall()
    hist = db.execute("""select day as Day, sum(status='taken') as Taken, sum(status='missed') as Missed
        from dose_log where patient_id=? and day>=? group by day order by day desc""",
        (pid, (now().date() - dt.timedelta(days=6)).isoformat())).fetchall()
    appts = db.execute("select * from appointments where patient_id=? and day>=? order by day,time",
                       (pid, today)).fetchall()
    meds = db.execute("select * from medicines where patient_id=? order by name", (pid,)).fetchall()
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
    if appts is None:  # patient's Today tab: appointments have their own tab
        return
    st.subheader(t["appts"])
    for a in appts:
        st.write(f"**{a['doctor']}** · {a['day']} {fmt12(a['time'])} {('· ' + a['note']) if a['note'] else ''}")
    if not appts:
        st.caption(t["none_a"])


def speak(texts, voice, title):
    """Spoken + browser-notification alert (runs in the browser)."""
    components.html(f"""<script>
    const L={json.dumps(voice)},T={json.dumps(texts)},H={json.dumps(title)};
    try{{if('Notification' in window&&Notification.permission==='default')Notification.requestPermission()}}catch(e){{}}
    T.forEach(s=>{{try{{const u=new SpeechSynthesisUtterance(s);u.lang=L;u.rate=.9;speechSynthesis.speak(u)}}catch(e){{}}
      try{{if(Notification.permission==='granted')new Notification(H,{{body:s}})}}catch(e){{}}}});</script>""", height=0)


def disclaimer(t):
    st.markdown(f"<div class='disc'>{t['disc']}</div>", unsafe_allow_html=True)


CFG = start_watcher()

# ---------------- sidebar: language ----------------
lang_name = st.sidebar.selectbox("🌐", list(LANGS), key="lang")
code, voice = LANGS[lang_name]
t = T[code]
st.title(f"💊 {t['title']}")
st.caption(t["sub"])

# ---------------- sign in / create account ----------------
if "pid" in st.session_state and not db.execute(
        "select 1 from patients where id=?", (st.session_state["pid"],)).fetchone():
    st.session_state.clear(); st.rerun()

if "pid" not in st.session_state:
    tab_in, tab_new = st.tabs([t["signin"], t["create"]])
    with tab_in:
        role_label = st.radio(t["signin_as"], [t["patient"], t["caregiver"]], horizontal=True)
        with st.form("signin"):
            n_in = st.text_input(t["pname"])
            c_in = st.text_input(t["contact"])
            p_in = st.text_input(t["pin"], type="password", max_chars=4) if role_label == t["caregiver"] else ""
            if st.form_submit_button(t["signin"], use_container_width=True):
                row = find_patient(n_in, c_in)
                is_cg = role_label == t["caregiver"]
                if row and (not is_cg or (p_in and check_pin(p_in, row["pin_hash"]))):
                    login(row, "caregiver" if is_cg else "patient")
                else:
                    st.error(t["invalid"])
    with tab_new:
        with st.form("register"):
            n_new = st.text_input(t["pname"])
            c_new = st.text_input(t["contact"])
            st.caption(t["alert_note"])
            p_new = st.text_input(t["pin"], type="password", max_chars=4)
            if st.form_submit_button(t["create"], use_container_width=True):
                c_norm = norm_contact(c_new)
                if not norm_name(n_new):
                    st.error(t["need_name"])
                elif not c_norm:
                    st.error(t["bad_contact"])
                elif not (p_new.isdigit() and len(p_new) == 4):
                    st.error(t["bad_pin"])
                elif db.execute("select 1 from patients where contact=?", (c_norm,)).fetchone():
                    st.error(t["exists"])
                else:
                    cur = db.execute("insert into patients(name,contact,pin_hash) values(?,?,?)",
                                     (norm_name(n_new), c_norm, hash_pin(p_new)))
                    db.commit()
                    login(db.execute("select * from patients where id=?", (cur.lastrowid,)).fetchone(), "patient")
    disclaimer(t)
    st.stop()

# ---------------- signed in ----------------
pid, role, pname = st.session_state["pid"], st.session_state["role"], st.session_state["pname"]
st.sidebar.write(f"{t['signed']} **{pname}**")
st.sidebar.caption(t["caregiver"] if role == "caregiver" else t["patient"])
if st.sidebar.button(t["signout"], use_container_width=True):
    st.session_state.clear(); st.rerun()
if st_autorefresh:
    st_autorefresh(interval=30000, key="tick")

doses, hist, appts, meds = data(pid)


def read_only_meds():
    if not meds:
        st.info(t["none_med"])
    for m in meds:
        with st.container(border=True):
            st.markdown(f"**{m['name']}**")
            st.caption(f"{food_label(t, m['food'])}{(' · ' + m['dose']) if m['dose'] else ''}")
            st.write("🕒 " + ", ".join(fmt12(x) for x in m["times"].split(",")))


# ================= caregiver =================
if role == "caregiver":
    st.subheader(f"{t['cg_for']} {pname}")
    tab_over, tab_meds, tab_appt = st.tabs([t["overview"], t["meds"], t["appts"]])

    with tab_over:
        st.subheader(t["attention"])
        bad = [d for d in doses if d["status"] == "missed"]
        for d in bad:
            st.error(f"{d['name']} · {fmt12(d['time'])} — {t['missed']}")
        if not bad:
            st.success(t["nomiss"])
        if not doses:
            st.info(t["none_m"])
        for d in doses:
            a_state = d["alert_state"] or ""
            tag = f" · 🔔 {t['alert_sent']}" if a_state == "sent" else (
                  f" · ⚠ {t['alert_failed']}" if a_state.startswith("failed") else "")
            st.write(f"**{d['name']}** · {fmt12(d['time'])} · {food_label(t, d['food'])} — {t[d['status']]}{tag}")
        contact = st.session_state.get("contact", "")
        with st.expander(t["alerts"]):
            st.write(f"{t['alert_to']}: **{contact}** ({t['alert_mail'] if '@' in contact else t['alert_sms']})")
            st.caption(t["alert_rule"].format(m=ALERT_AFTER))
            if not alert_ready(CFG, contact):
                st.warning(t["alert_off"])
            if st.button(t["test_alert"], key="test_alert"):
                ok, info = notify(CFG, contact, "MedMinder test alert",
                                  f"MedMinder test: alerts for {pname} are working.")
                st.success(t["test_ok"]) if ok else st.error(f"{t['test_fail']}: {info}")
        summary(t, doses, hist, appts)

    with tab_meds:
        st.subheader(t["add_med"])
        n_doses = st.number_input(t["how_many"], 1, MAX_DOSES, 1, key="n_doses")  # outside the form so inputs update
        with st.form("med", clear_on_submit=True):
            name = st.text_input(t["name"])
            dose = st.text_input(t["dose"])
            food = st.selectbox(t["food"], ["any", "before", "after"], format_func=lambda f: food_label(t, f))
            picked = [time_pick(f"{t['time']} {i + 1}", f"nt{i}", DEFAULT_TIMES[i]) for i in range(int(n_doses))]
            if st.form_submit_button(t["save"], use_container_width=True):
                if not name.strip():
                    st.error(t["need_med"])
                else:
                    n = now()
                    times = sorted(set(picked))
                    db.execute("""insert into medicines(patient_id,name,dose,food,times,created,created_time)
                                  values(?,?,?,?,?,?,?)""",
                               (pid, name.strip(), dose, food, ",".join(times), n.date().isoformat(), n.strftime("%H:%M")))
                    db.commit(); st.rerun()

        st.divider()
        if not meds:
            st.info(t["none_med"])
        for m in meds:
            with st.container(border=True):
                c1, c2 = st.columns([4, 1])
                c1.markdown(f"**{m['name']}** · 🕒 {', '.join(fmt12(x) for x in m['times'].split(','))}")
                c1.caption(f"{food_label(t, m['food'])}{(' · ' + m['dose']) if m['dose'] else ''}")
                if c2.button(t["delete"], key=f"dm{m['id']}"):
                    st.session_state["del_med"] = m["id"]; st.rerun()
                if st.session_state.get("del_med") == m["id"]:
                    st.warning(t["confirm"])
                    y, n_ = st.columns(2)
                    if y.button(t["yes"], key=f"y{m['id']}", use_container_width=True):
                        db.execute("delete from dose_log where med_id=? and patient_id=?", (m["id"], pid))
                        db.execute("delete from medicines where id=? and patient_id=?", (m["id"], pid))
                        db.commit(); st.session_state.pop("del_med"); st.rerun()
                    if n_.button(t["cancel"], key=f"c{m['id']}", use_container_width=True):
                        st.session_state.pop("del_med"); st.rerun()
                with st.expander(t["edit_times"]):
                    with st.form(f"ed{m['id']}"):
                        cur_times = m["times"].split(",")
                        keep = st.multiselect(t["keep_times"], cur_times, default=cur_times, format_func=fmt12)
                        extra = time_pick(t["add_time"], f"ex{m['id']}", None, optional=True)
                        if st.form_submit_button(t["save"], use_container_width=True):
                            new = sorted(set(keep) | ({extra} if extra else set()))
                            if not new:
                                st.error(t["need_time"])
                            else:
                                n = now(); today = n.date().isoformat()
                                db.execute("""delete from dose_log where med_id=? and patient_id=? and day=?
                                              and status='pending' and time not in (%s)""" % ",".join("?" * len(new)),
                                           (m["id"], pid, today, *new))
                                db.execute("update medicines set times=?, created=?, created_time=? where id=? and patient_id=?",
                                           (",".join(new), today, n.strftime("%H:%M"), m["id"], pid))
                                db.commit(); st.rerun()

    with tab_appt:
        with st.form("appt", clear_on_submit=True):
            doc = st.text_input(t["doctor"])
            day = st.date_input(t["date"], value=now().date())
            tm = time_pick(t["time"], "ap_t", None, optional=True)
            nt = st.text_input(t["note"])
            if st.form_submit_button(t["add"], use_container_width=True) and doc.strip():
                db.execute("insert into appointments(patient_id,doctor,day,time,note) values(?,?,?,?,?)",
                           (pid, doc.strip(), day.isoformat(), tm or "", nt)); db.commit(); st.rerun()
        for a in appts:
            c1, c2 = st.columns([4, 1])
            c1.write(f"**{a['doctor']}** · {a['day']} {fmt12(a['time'])} {a['note'] or ''}")
            if c2.button(t["delete"], key=f"da{a['id']}"):
                db.execute("delete from appointments where id=? and patient_id=?", (a["id"], pid)); db.commit(); st.rerun()
        if not appts:
            st.caption(t["none_a"])
    disclaimer(t)
    st.stop()

# ================= patient =================
with st.sidebar.expander(t["change_pin"]):
    with st.form("pinform", clear_on_submit=True):
        np_ = st.text_input(t["new_pin"], type="password", max_chars=4)
        if st.form_submit_button(t["save"], use_container_width=True):
            if np_.isdigit() and len(np_) == 4:
                db.execute("update patients set pin_hash=? where id=?", (hash_pin(np_), pid)); db.commit()
                st.success(t["pin_saved"])
            else:
                st.error(t["bad_pin"])

tab_today, tab_meds, tab_appt = st.tabs([t["today"], t["meds"], t["appts"]])
hhmm = now().strftime("%H:%M")

with tab_today:
    spoken = st.session_state.setdefault("spoken", set())
    due = [d for d in doses if d["status"] == "pending" and d["time"] <= hhmm]
    fresh = [d for d in due if d["id"] not in spoken]
    if fresh:
        speak([f"{t['remind']} {d['name']} ({food_label(t, d['food'])})" for d in fresh], voice, t["due"])
        spoken.update(d["id"] for d in fresh)
    for d in due:
        st.warning(f"⏰ {t['due']}: **{d['name']}** · {fmt12(d['time'])}")
    if not doses:
        st.info(t["none_m"])
    for d in doses:
        with st.container(border=True):
            icon = {"taken": "🟢", "missed": "🔴", "pending": "🟠"}[d["status"]]
            st.markdown(f"### {icon} {d['name']} · {fmt12(d['time'])}")
            st.caption(f"{food_label(t, d['food'])}{(' · ' + d['dose']) if d['dose'] else ''} — {t[d['status']]}")
            c1, c2 = st.columns(2)
            if c1.button(f"✔ {t['taken']}", key=f"t{d['id']}", use_container_width=True):
                db.execute("update dose_log set status='taken', auto=0 where id=? and patient_id=?", (d["id"], pid)); db.commit(); st.rerun()
            if c2.button(f"✖ {t['missed']}", key=f"m{d['id']}", use_container_width=True):
                db.execute("update dose_log set status='missed', auto=0 where id=? and patient_id=?", (d["id"], pid)); db.commit(); st.rerun()
    summary(t, doses, hist, None)
    if not st_autorefresh:
        st.caption("Tip: pip install streamlit-autorefresh for automatic 30-second reminders.")
    else:
        st.caption(t["voice"])

with tab_meds:
    st.caption(t["ro_note"])
    read_only_meds()

with tab_appt:
    for a in appts:
        st.write(f"**{a['doctor']}** · {a['day']} {fmt12(a['time'])} {('· ' + a['note']) if a['note'] else ''}")
    if not appts:
        st.caption(t["none_a"])

disclaimer(t)
