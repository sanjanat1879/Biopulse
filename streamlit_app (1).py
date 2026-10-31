"""MedMinder - Smart Medication Reminder (Streamlit + SQLite). Run: streamlit run streamlit_app.py

Accounts: a patient signs in with name + phone number / email. A caregiver signs in with the same
details plus the 4-digit caregiver PIN. Every record belongs to one patient, so nobody sees another
patient's medicines. Only the caregiver adds / removes medicines and chooses their times.

The caregiver sets a "robot reminder time" (minutes): after a dose time the robot repeats the spoken reminder about
every minute for that long, or until the patient marks the dose.

Times are chosen and shown in 12-hour AM/PM format. If the patient has not confirmed a dose (taken / missed)
within ALERT_AFTER minutes, a red alert appears at the top of the caregiver dashboard until the caregiver
presses "Got it" (or the patient confirms the dose).
"""
import base64, hashlib, hmac, json, os, re, sqlite3
from html import escape as esc
import datetime as dt
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
REMIND_DEFAULT = 5   # minutes the robot keeps repeating a dose reminder (caregiver can change it: 1 .. GRACE)
REPEAT_EVERY = 60    # seconds between repeated spoken reminders
ALERT_AFTER = 15    # minutes after the dose time with no response -> alert on caregiver dashboard (must be < GRACE)
DB_PATH = "medminder_accounts.db"
LANGS = {"English": ("en", "en-IN"), "हिन्दी": ("hi", "hi-IN"), "தமிழ்": ("ta", "ta-IN"),
         "తెలుగు": ("te", "te-IN"), "മലയാളം": ("ml", "ml-IN"), "ಕನ್ನಡ": ("kn", "kn-IN")}

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

T["en"].update(dash_alert="No response from patient", ack="Got it", min="min late")
T["hi"].update(dash_alert="मरीज़ की ओर से कोई जवाब नहीं", ack="ठीक है", min="मिनट देर")
T["ta"].update(dash_alert="நோயாளியிடமிருந்து பதில் இல்லை", ack="சரி", min="நிமிடம் தாமதம்")

T["te"] = dict(title="మెడ్\u200cమైండర్", sub="మీ మందులు, సరైన సమయానికి", today="ఈరోజు", meds="మందులు",
  appts="అపాయింట్\u200cమెంట్లు", patient="రోగి", caregiver="సంరక్షకులు", taken="తీసుకున్నాను",
  missed="తప్పిపోయింది", pending="మిగిలి ఉంది", add_med="మందును జోడించండి", name="మందు పేరు",
  dose="మోతాదు (డాక్టర్ చెప్పినట్లు)", food="ఆహారం", before="భోజనానికి ముందు", after="భోజనం తర్వాత",
  anytime="ఎప్పుడైనా", times="సమయాలు", time="సమయం", save="సేవ్ చేయండి", add="జోడించండి", delete="తొలగించండి",
  doctor="డాక్టర్", date="తేదీ", note="గమనిక", pin="సంరక్షకుల పిన్ (4 అంకెలు)", history="గత 7 రోజులు",
  attention="శ్రద్ధ అవసరం", due="మందు వేసుకునే సమయం అయింది", none_m="ఈరోజు మోతాదులు లేవు.",
  none_a="రాబోయే అపాయింట్\u200cమెంట్లు లేవు.", done="మోతాదులు తీసుకున్నారు", nomiss="ఈరోజు ఏదీ తప్పిపోలేదు.",
  remind="మందు వేసుకునే సమయం:",
  voice="ఈ పేజీ తెరిచి ఉన్నంతసేపు హెచ్చరికలు ప్రతి 30 సెకన్లకు నవీకరించబడతాయి.",
  signin="సైన్ ఇన్", create="ఖాతా సృష్టించండి", pname="రోగి పేరు", contact="ఫోన్ నంబర్ లేదా ఇమెయిల్",
  signin_as="ఇలా సైన్ ఇన్ చేయండి", invalid="వివరాలు సరిపోలలేదు. దయచేసి సరిచూసి మళ్లీ ప్రయత్నించండి.",
  bad_contact="సరైన 10 అంకెల ఫోన్ నంబర్ లేదా ఇమెయిల్ నమోదు చేయండి.",
  exists="ఈ ఫోన్ నంబర్ లేదా ఇమెయిల్ ఉన్న ఖాతా ఇప్పటికే ఉంది. దయచేసి సైన్ ఇన్ చేయండి.",
  need_name="దయచేసి రోగి పేరు నమోదు చేయండి.", bad_pin="పిన్ సరిగ్గా 4 అంకెలు ఉండాలి.",
  signout="సైన్ అవుట్", signed="సైన్ ఇన్ అయినవారు:", cg_for="సంరక్షకుల వీక్షణ:",
  how_many="రోజుకు ఎన్ని మోతాదులు", edit_times="సమయాలను మార్చండి", keep_times="ఉంచాల్సిన సమయాలు",
  add_time="మరొక సమయం జోడించండి", need_time="కనీసం ఒక సమయం ఉంచండి.", updated="నవీకరించబడింది",
  confirm="ఈ మందును, దాని మోతాదు చరిత్రను తొలగించాలా?", yes="అవును, తొలగించు", cancel="రద్దు చేయి",
  none_med="ఇంకా మందులు జోడించలేదు.", need_med="దయచేసి మందు పేరు నమోదు చేయండి.",
  ro_note="మీ సంరక్షకులు మందులను జోడించి, సమయాలను నిర్ణయిస్తారు.", new_pin="కొత్త సంరక్షకుల పిన్",
  change_pin="పిన్ మార్చండి", pin_saved="పిన్ నవీకరించబడింది", overview="సారాంశం", account="ఖాతా",
  disc="ఈ యాప్ మీ డాక్టర్ సూచనల ప్రకారం నమోదు చేసిన షెడ్యూల్ను మాత్రమే అనుసరిస్తుంది. ఇది మందులను సూచించదు, మోతాదులను మార్చదు.",
  dash_alert="రోగి నుండి స్పందన లేదు", ack="సరే", min="నిమిషాలు ఆలస్యం")

T["ml"] = dict(title="മെഡ്\u200cമൈൻഡർ", sub="നിങ്ങളുടെ മരുന്നുകൾ, കൃത്യസമയത്ത്", today="ഇന്ന്", meds="മരുന്നുകൾ",
  appts="അപ്പോയിന്റ്മെന്റുകൾ", patient="രോഗി", caregiver="പരിചരിക്കുന്നയാൾ", taken="കഴിച്ചു",
  missed="വിട്ടുപോയി", pending="ബാക്കി", add_med="മരുന്ന് ചേർക്കുക", name="മരുന്നിന്റെ പേര്",
  dose="അളവ് (ഡോക്ടർ പറഞ്ഞതുപോലെ)", food="ഭക്ഷണം", before="ഭക്ഷണത്തിന് മുമ്പ്", after="ഭക്ഷണത്തിന് ശേഷം",
  anytime="എപ്പോൾ വേണമെങ്കിലും", times="സമയങ്ങൾ", time="സമയം", save="സേവ് ചെയ്യുക", add="ചേർക്കുക",
  delete="നീക്കം ചെയ്യുക", doctor="ഡോക്ടർ", date="തീയതി", note="കുറിപ്പ്",
  pin="പരിചരിക്കുന്നയാളുടെ പിൻ (4 അക്കം)", history="കഴിഞ്ഞ 7 ദിവസം", attention="ശ്രദ്ധിക്കേണ്ടവ",
  due="മരുന്ന് കഴിക്കേണ്ട സമയം", none_m="ഇന്ന് ഡോസുകൾ ഇല്ല.", none_a="വരാനിരിക്കുന്ന അപ്പോയിന്റ്മെന്റുകൾ ഇല്ല.",
  done="ഡോസുകൾ കഴിച്ചു", nomiss="ഇന്ന് ഒന്നും വിട്ടുപോയില്ല.", remind="മരുന്ന് കഴിക്കേണ്ട സമയം:",
  voice="ഈ പേജ് തുറന്നിരിക്കുമ്പോൾ ഓരോ 30 സെക്കൻഡിലും അറിയിപ്പുകൾ പുതുക്കും.",
  signin="സൈൻ ഇൻ", create="അക്കൗണ്ട് ഉണ്ടാക്കുക", pname="രോഗിയുടെ പേര്", contact="ഫോൺ നമ്പർ അല്ലെങ്കിൽ ഇമെയിൽ",
  signin_as="ഇങ്ങനെ സൈൻ ഇൻ ചെയ്യുക", invalid="വിവരങ്ങൾ യോജിക്കുന്നില്ല. പരിശോധിച്ച് വീണ്ടും ശ്രമിക്കുക.",
  bad_contact="ശരിയായ 10 അക്ക ഫോൺ നമ്പറോ ഇമെയിലോ നൽകുക.",
  exists="ഈ ഫോൺ/ഇമെയിൽ ഉപയോഗിച്ച് അക്കൗണ്ട് ഇതിനകം ഉണ്ട്. സൈൻ ഇൻ ചെയ്യുക.", need_name="രോഗിയുടെ പേര് നൽകുക.",
  bad_pin="പിൻ കൃത്യം 4 അക്കങ്ങൾ ആയിരിക്കണം.", signout="സൈൻ ഔട്ട്", signed="സൈൻ ഇൻ ചെയ്തത്:",
  cg_for="പരിചരിക്കുന്നയാളുടെ കാഴ്ച:", how_many="ദിവസം എത്ര ഡോസ്", edit_times="സമയങ്ങൾ മാറ്റുക",
  keep_times="നിലനിർത്തേണ്ട സമയങ്ങൾ", add_time="മറ്റൊരു സമയം ചേർക്കുക", need_time="കുറഞ്ഞത് ഒരു സമയം വേണം.",
  updated="പുതുക്കി", confirm="ഈ മരുന്നും അതിന്റെ ഡോസ് ചരിത്രവും നീക്കം ചെയ്യണോ?",
  yes="അതെ, നീക്കം ചെയ്യുക", cancel="റദ്ദാക്കുക", none_med="മരുന്നുകൾ ഇതുവരെ ചേർത്തിട്ടില്ല.",
  need_med="മരുന്നിന്റെ പേര് നൽകുക.",
  ro_note="നിങ്ങളെ പരിചരിക്കുന്നയാൾ മരുന്നുകൾ ചേർക്കുകയും സമയം നിശ്ചയിക്കുകയും ചെയ്യും.",
  new_pin="പരിചരിക്കുന്നയാളുടെ പുതിയ പിൻ", change_pin="പിൻ മാറ്റുക", pin_saved="പിൻ പുതുക്കി",
  overview="സംഗ്രഹം", account="അക്കൗണ്ട്",
  disc="ഡോക്ടറുടെ നിർദ്ദേശപ്രകാരം നൽകിയ ഷെഡ്യൂൾ മാത്രമേ ഈ ആപ്പ് പിന്തുടരൂ. ഇത് മരുന്നുകൾ നിർദ്ദേശിക്കുകയോ ഡോസ് മാറ്റുകയോ ചെയ്യില്ല.",
  dash_alert="രോഗിയിൽ നിന്ന് പ്രതികരണമില്ല", ack="ശരി", min="മിനിറ്റ് വൈകി")

T["kn"] = dict(title="ಮೆಡ್\u200cಮೈಂಡರ್", sub="ನಿಮ್ಮ ಔಷಧಿಗಳು, ಸರಿಯಾದ ಸಮಯಕ್ಕೆ", today="ಇಂದು", meds="ಔಷಧಿಗಳು",
  appts="ವೈದ್ಯರ ಭೇಟಿಗಳು", patient="ರೋಗಿ", caregiver="ಆರೈಕೆದಾರರು", taken="ತೆಗೆದುಕೊಂಡೆ",
  missed="ತಪ್ಪಿಹೋಯಿತು", pending="ಬಾಕಿ", add_med="ಔಷಧಿ ಸೇರಿಸಿ", name="ಔಷಧಿಯ ಹೆಸರು",
  dose="ಪ್ರಮಾಣ (ವೈದ್ಯರು ಹೇಳಿದಂತೆ)", food="ಆಹಾರ", before="ಊಟಕ್ಕೆ ಮೊದಲು", after="ಊಟದ ನಂತರ",
  anytime="ಯಾವಾಗ ಬೇಕಾದರೂ", times="ಸಮಯಗಳು", time="ಸಮಯ", save="ಉಳಿಸಿ", add="ಸೇರಿಸಿ", delete="ತೆಗೆದುಹಾಕಿ",
  doctor="ವೈದ್ಯರು", date="ದಿನಾಂಕ", note="ಟಿಪ್ಪಣಿ", pin="ಆರೈಕೆದಾರರ ಪಿನ್ (4 ಅಂಕೆ)", history="ಕಳೆದ 7 ದಿನಗಳು",
  attention="ಗಮನ ಬೇಕಾದವು", due="ಔಷಧಿ ತೆಗೆದುಕೊಳ್ಳುವ ಸಮಯ", none_m="ಇಂದು ಯಾವುದೇ ಡೋಸ್ ಇಲ್ಲ.",
  none_a="ಮುಂಬರುವ ಭೇಟಿಗಳಿಲ್ಲ.", done="ಡೋಸ್ ತೆಗೆದುಕೊಳ್ಳಲಾಗಿದೆ", nomiss="ಇಂದು ಯಾವುದೂ ತಪ್ಪಿಲ್ಲ.",
  remind="ಔಷಧಿ ತೆಗೆದುಕೊಳ್ಳುವ ಸಮಯ:",
  voice="ಈ ಪುಟ ತೆರೆದಿರುವವರೆಗೆ ಎಚ್ಚರಿಕೆಗಳು ಪ್ರತಿ 30 ಸೆಕೆಂಡಿಗೆ ನವೀಕರಣಗೊಳ್ಳುತ್ತವೆ.",
  signin="ಸೈನ್ ಇನ್", create="ಖಾತೆ ರಚಿಸಿ", pname="ರೋಗಿಯ ಹೆಸರು", contact="ಫೋನ್ ಸಂಖ್ಯೆ ಅಥವಾ ಇಮೇಲ್",
  signin_as="ಹೀಗೆ ಸೈನ್ ಇನ್ ಮಾಡಿ", invalid="ವಿವರಗಳು ಹೊಂದಿಕೆಯಾಗುತ್ತಿಲ್ಲ. ಪರಿಶೀಲಿಸಿ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
  bad_contact="ಸರಿಯಾದ 10 ಅಂಕೆಯ ಫೋನ್ ಸಂಖ್ಯೆ ಅಥವಾ ಇಮೇಲ್ ನಮೂದಿಸಿ.",
  exists="ಈ ಫೋನ್ ಅಥವಾ ಇಮೇಲ್ ಹೊಂದಿರುವ ಖಾತೆ ಈಗಾಗಲೇ ಇದೆ. ದಯವಿಟ್ಟು ಸೈನ್ ಇನ್ ಮಾಡಿ.",
  need_name="ದಯವಿಟ್ಟು ರೋಗಿಯ ಹೆಸರು ನಮೂದಿಸಿ.", bad_pin="ಪಿನ್ ಸರಿಯಾಗಿ 4 ಅಂಕೆಗಳಿರಬೇಕು.",
  signout="ಸೈನ್ ಔಟ್", signed="ಸೈನ್ ಇನ್ ಆದವರು:", cg_for="ಆರೈಕೆದಾರರ ನೋಟ:",
  how_many="ದಿನಕ್ಕೆ ಎಷ್ಟು ಡೋಸ್", edit_times="ಸಮಯ ಬದಲಿಸಿ", keep_times="ಉಳಿಸಬೇಕಾದ ಸಮಯಗಳು",
  add_time="ಇನ್ನೊಂದು ಸಮಯ ಸೇರಿಸಿ", need_time="ಕನಿಷ್ಠ ಒಂದು ಸಮಯ ಇರಬೇಕು.", updated="ನವೀಕರಿಸಲಾಗಿದೆ",
  confirm="ಈ ಔಷಧಿ ಮತ್ತು ಅದರ ಡೋಸ್ ಇತಿಹಾಸವನ್ನು ತೆಗೆದುಹಾಕಬೇಕೇ?", yes="ಹೌದು, ತೆಗೆದುಹಾಕಿ", cancel="ರದ್ದುಮಾಡಿ",
  none_med="ಇನ್ನೂ ಔಷಧಿಗಳನ್ನು ಸೇರಿಸಿಲ್ಲ.", need_med="ದಯವಿಟ್ಟು ಔಷಧಿಯ ಹೆಸರು ನಮೂದಿಸಿ.",
  ro_note="ನಿಮ್ಮ ಆರೈಕೆದಾರರು ಔಷಧಿಗಳನ್ನು ಸೇರಿಸಿ ಸಮಯ ನಿಗದಿಪಡಿಸುತ್ತಾರೆ.", new_pin="ಹೊಸ ಆರೈಕೆದಾರರ ಪಿನ್",
  change_pin="ಪಿನ್ ಬದಲಿಸಿ", pin_saved="ಪಿನ್ ನವೀಕರಿಸಲಾಗಿದೆ", overview="ಸಾರಾಂಶ", account="ಖಾತೆ",
  disc="ಈ ಆ್ಯಪ್ ವೈದ್ಯರ ಸೂಚನೆಯಂತೆ ನಮೂದಿಸಿದ ವೇಳಾಪಟ್ಟಿಯನ್ನು ಮಾತ್ರ ಅನುಸರಿಸುತ್ತದೆ. ಇದು ಔಷಧಿಗಳನ್ನು ಸೂಚಿಸುವುದಿಲ್ಲ, ಡೋಸ್ ಬದಲಾಯಿಸುವುದಿಲ್ಲ.",
  dash_alert="ರೋಗಿಯಿಂದ ಪ್ರತಿಕ್ರಿಯೆ ಇಲ್ಲ", ack="ಸರಿ", min="ನಿಮಿಷ ತಡ")


T["en"].update(theme="Colours", th_navy="Navy & Snow", th_lav="Lavender & Pink", th_peach="Peach & Beach", repeat="Say it again")
T["hi"].update(theme="रंग", th_navy="नेवी ब्लू और स्नो व्हाइट", th_lav="लैवेंडर और गुलाबी", th_peach="पीच और बीच ब्लू", repeat="फिर से सुनें")
T["ta"].update(theme="வண்ணங்கள்", th_navy="நேவி நீலம் & பனி வெள்ளை", th_lav="லாவெண்டர் & இளஞ்சிவப்பு", th_peach="பீச் & கடற்கரை நீலம்", repeat="மீண்டும் சொல்")
T["te"].update(theme="రంగులు", th_navy="నేవీ బ్లూ & స్నో వైట్", th_lav="లావెండర్ & పింక్", th_peach="పీచ్ & బీచ్ బ్లూ", repeat="మళ్లీ చెప్పు")
T["ml"].update(theme="നിറങ്ങൾ", th_navy="നേവി ബ്ലൂ & സ്നോ വൈറ്റ്", th_lav="ലാവെൻഡർ & പിങ്ക്", th_peach="പീച്ച് & ബീച്ച് ബ്ലൂ", repeat="വീണ്ടും പറയൂ")
T["kn"].update(theme="ಬಣ್ಣಗಳು", th_navy="ನೇವಿ ಬ್ಲೂ & ಸ್ನೋ ವೈಟ್", th_lav="ಲ್ಯಾವೆಂಡರ್ & ಪಿಂಕ್", th_peach="ಪೀಚ್ & ಬೀಚ್ ಬ್ಲೂ", repeat="ಮತ್ತೆ ಹೇಳು")

T["en"].update(rem_set="Robot reminder time (minutes)", rem_help="After each dose time, the robot keeps reminding the patient by voice for this many minutes, or until the dose is marked.", rem_left="min left")
T["hi"].update(rem_set="रोबोट रिमाइंडर समय (मिनट)", rem_help="हर खुराक के समय के बाद रोबोट इतने मिनट तक, या खुराक दर्ज होने तक, मरीज़ को आवाज़ से याद दिलाता रहेगा।", rem_left="मिनट शेष")
T["ta"].update(rem_set="ரோபோ நினைவூட்டல் நேரம் (நிமிடங்கள்)", rem_help="ஒவ்வொரு மருந்து நேரத்திற்குப் பிறகும் இத்தனை நிமிடங்கள், அல்லது அளவு குறிக்கப்படும் வரை, ரோபோ குரலில் நினைவூட்டிக்கொண்டே இருக்கும்.", rem_left="நிமிடம் மீதம்")
T["te"].update(rem_set="రోబో రిమైండర్ సమయం (నిమిషాలు)", rem_help="ప్రతి మందు సమయం తర్వాత రోబో ఇన్ని నిమిషాల పాటు, లేదా మోతాదు నమోదు చేసేవరకు, రోగికి గొంతుతో గుర్తుచేస్తూ ఉంటుంది.", rem_left="నిమిషాలు మిగిలాయి")
T["ml"].update(rem_set="റോബോട്ട് ഓർമ്മപ്പെടുത്തൽ സമയം (മിനിറ്റ്)", rem_help="ഓരോ മരുന്ന് സമയത്തിനു ശേഷവും ഇത്ര മിനിറ്റ്, അല്ലെങ്കിൽ ഡോസ് രേഖപ്പെടുത്തുന്നതുവരെ, റോബോട്ട് ശബ്ദത്തിൽ രോഗിയെ ഓർമ്മപ്പെടുത്തിക്കൊണ്ടിരിക്കും.", rem_left="മിനിറ്റ് ബാക്കി")
T["kn"].update(rem_set="ರೋಬೋಟ್ ಜ್ಞಾಪನೆ ಸಮಯ (ನಿಮಿಷ)", rem_help="ಪ್ರತಿ ಔಷಧಿ ಸಮಯದ ನಂತರ ರೋಬೋಟ್ ಇಷ್ಟು ನಿಮಿಷ, ಅಥವಾ ಡೋಸ್ ದಾಖಲಿಸುವವರೆಗೆ, ಧ್ವನಿಯಲ್ಲಿ ರೋಗಿಗೆ ನೆನಪಿಸುತ್ತಲೇ ಇರುತ್ತದೆ.", rem_left="ನಿಮಿಷ ಬಾಕಿ")

st.set_page_config(page_title="MedMinder", page_icon="💊", layout="centered")



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
    if "cg_ack" not in [r[1] for r in c.execute("pragma table_info(dose_log)")]:
        c.execute("alter table dose_log add column cg_ack integer default 0")  # caregiver pressed "Got it"
    if "remind_min" not in [r[1] for r in c.execute("pragma table_info(patients)")]:
        c.execute(f"alter table patients add column remind_min integer default {REMIND_DEFAULT}")  # robot reminder minutes
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


# ---------------- data ----------------
def check_patient(pid):
    """Create today's dose rows and auto-mark long-overdue unanswered doses as missed."""
    n = now(); today = n.date().isoformat()
    for m in db.execute("select * from medicines where patient_id=?", (pid,)).fetchall():
        for tm_ in m["times"].split(","):
            if m["created"] == today and tm_ < m["created_time"]:
                continue
            db.execute("insert or ignore into dose_log(med_id,patient_id,day,time) values(?,?,?,?)", (m["id"], pid, today, tm_))
    for r in db.execute("select id,day,time from dose_log where patient_id=? and status='pending'", (pid,)).fetchall():
        if n > dt.datetime.fromisoformat(f"{r['day']}T{r['time']}") + dt.timedelta(minutes=GRACE):
            db.execute("update dose_log set status='missed', auto=1 where id=?", (r["id"],))
    db.commit()


def get_remind_min(pid):
    r = db.execute("select remind_min from patients where id=?", (pid,)).fetchone()
    return int(r[0]) if r and r[0] else REMIND_DEFAULT


def data(pid):
    check_patient(pid)
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


# ---------------- colour themes + robot ----------------
THEMES = {  # bg/soft = page colours, tx = text, ac = main accent (buttons), ac2 = pastel accent, g1/g2 = title gradient
    "navy":  dict(bg="#F7FAFF", soft="#E3ECFB", tx="#14213D", ac="#1F3A68", ac2="#9DB7E8", g1="#1F3A68", g2="#5B82C9", emoji="🌙"),
    "lav":   dict(bg="#F8F3FF", soft="#FBE6F1", tx="#47356B", ac="#7E5BC7", ac2="#F6A5C8", g1="#7E5BC7", g2="#EE82B1", emoji="🌸"),
    "peach": dict(bg="#FFF5EE", soft="#DDF1F7", tx="#33454F", ac="#1E7FA3", ac2="#FFB48E", g1="#1E7FA3", g2="#FF9A6B", emoji="🍑"),
}

THEME_CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Nunito:wght@500;700;800&display=swap');
html,body,[class*="css"]{font-size:19px}
.stApp{background:linear-gradient(180deg,@bg@ 0%,@soft@ 100%);color:@tx@;color-scheme:light}
.stApp h1,.stApp h2,.stApp h3,.stApp h4,.stApp p,.stApp label,.stApp li,.stApp button,.stApp input,.stApp textarea,
.stApp [data-baseweb="tab"]{font-family:'Nunito',sans-serif}
.stApp h1,.stApp h2,.stApp h3,.stApp h4,.stApp p,.stApp label,.stApp li,.stApp [data-testid="stMarkdownContainer"],
.stApp [data-testid="stCaptionContainer"],.stApp [data-testid="stWidgetLabel"] p{color:@tx@}
.stApp h1{background:linear-gradient(90deg,@g1@,@g2@);-webkit-background-clip:text;background-clip:text;
 -webkit-text-fill-color:transparent;font-weight:800}
[data-testid="stSidebar"]{background:@soft@}
.stApp .stButton>button,.stApp .stFormSubmitButton>button,.stApp [data-testid="stBaseButton-secondary"],
.stApp [data-testid="stBaseButton-secondaryFormSubmit"]{background:@ac@;color:#fff;border:0;border-radius:999px;min-height:3rem;
 font-weight:700;box-shadow:0 4px 0 rgba(0,0,0,.15);transition:transform .1s}
.stApp .stButton>button:hover,.stApp .stFormSubmitButton>button:hover,.stApp [data-testid="stBaseButton-secondary"]:hover,
.stApp [data-testid="stBaseButton-secondaryFormSubmit"]:hover{background:@g2@;color:#fff;transform:translateY(-1px)}
.stApp .stButton>button:active,.stApp .stFormSubmitButton>button:active{transform:translateY(3px);box-shadow:none}
.stApp .stButton>button p,.stApp .stFormSubmitButton>button p,.stApp [data-testid^="stBaseButton-secondary"] p{color:#fff!important}
.stApp [data-baseweb="input"],.stApp [data-baseweb="select"]>div,.stApp [data-baseweb="textarea"]{background:#fff!important;
 border:2px solid @ac2@!important;border-radius:14px!important}
.stApp [data-baseweb="base-input"]{background:#fff!important}
.stApp input,.stApp textarea,.stApp [data-baseweb="select"] div{color:@tx@!important;-webkit-text-fill-color:@tx@}
[data-baseweb="popover"] ul,[data-baseweb="popover"] li{background:#fff!important;color:@tx@!important}
.stApp .stTabs [data-baseweb="tab"]{font-weight:700;color:@tx@}
.stApp .stTabs [aria-selected="true"]{color:@ac@}
.stApp .stTabs [data-baseweb="tab-highlight"]{background:@ac@!important;height:4px;border-radius:4px}
.stApp [data-testid="stVerticalBlockBorderWrapper"]{border-color:@ac2@!important;border-width:2px!important;border-radius:20px!important}
.stApp [data-testid="stForm"]{border:2px solid @ac2@;border-radius:20px;background:rgba(255,255,255,.65)}
.stApp [data-testid="stExpander"]{border:2px solid @ac2@;border-radius:16px;background:rgba(255,255,255,.55)}
.stApp [data-testid="stMetricValue"]{color:@ac@;font-weight:800}
.stApp [data-testid="stAlert"]{border-radius:16px}
.disc{border:2px dashed @ac@;border-radius:16px;padding:12px 16px;color:@tx@;margin-top:2rem;background:rgba(255,255,255,.55)}
.robot-row{display:flex;align-items:center;gap:4px;margin:6px 0 14px}
.robot-row img{flex:none;filter:drop-shadow(0 6px 8px rgba(0,0,0,.12))}
.bubble{position:relative;background:#fff;border:3px solid @ac2@;border-radius:22px;padding:12px 18px;font-weight:700;
 color:@tx@;box-shadow:0 4px 0 @ac2@;line-height:1.35}
.bubble:before{content:"";position:absolute;left:-13px;top:50%;margin-top:-10px;border:10px solid transparent;
 border-left:0;border-right-color:@ac2@}
.bubble div+div{margin-top:4px}
</style>"""

ROBOT_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 150" width="160" height="150">
<style>
@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}
@keyframes blink{0%,90%,100%{transform:scaleY(1)}95%{transform:scaleY(.1)}}
@keyframes talk{0%,100%{transform:scaleY(.3)}50%{transform:scaleY(1)}}
@keyframes wave{0%,100%{transform:rotate(0deg)}50%{transform:rotate(-40deg)}}
@keyframes ping{0%{opacity:0}35%{opacity:1}70%,100%{opacity:0}}
@keyframes glow{0%,100%{transform:scale(1)}50%{transform:scale(1.35)}}
.bob{animation:bob 1.8s ease-in-out infinite}
.eye{transform-box:fill-box;transform-origin:center;animation:blink 4s infinite}
.mouth{transform-box:fill-box;transform-origin:center;animation:talk .5s ease-in-out infinite}
.arm{transform-origin:98px 114px;animation:wave 1s ease-in-out infinite}
.bulb{transform-box:fill-box;transform-origin:center;animation:glow 1s ease-in-out infinite}
.w1{animation:ping 1.2s infinite}.w2{animation:ping 1.2s .2s infinite}.w3{animation:ping 1.2s .4s infinite}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
</style>
<ellipse cx="70" cy="146" rx="30" ry="3.5" fill="#000" opacity=".1"/>
<g class="bob">
<line x1="70" y1="30" x2="70" y2="17" stroke="@ac@" stroke-width="5" stroke-linecap="round"/>
<circle class="bulb" cx="70" cy="12" r="7" fill="@ac2@" stroke="@ac@" stroke-width="3"/>
<rect x="16" y="52" width="12" height="26" rx="6" fill="@ac@"/>
<rect x="112" y="52" width="12" height="26" rx="6" fill="@ac@"/>
<rect x="24" y="30" width="92" height="72" rx="28" fill="#fff" stroke="@ac@" stroke-width="5"/>
<rect x="35" y="41" width="70" height="50" rx="20" fill="@ac@"/>
<g class="eye"><circle cx="54" cy="60" r="7" fill="#fff"/></g>
<g class="eye"><circle cx="86" cy="60" r="7" fill="#fff"/></g>
<ellipse cx="47" cy="75" rx="6" ry="3.5" fill="@ac2@"/><ellipse cx="93" cy="75" rx="6" ry="3.5" fill="@ac2@"/>
<rect class="mouth" x="62" y="72" width="16" height="10" rx="5" fill="#fff"/>
<rect x="42" y="106" width="56" height="34" rx="14" fill="@ac2@" stroke="@ac@" stroke-width="4"/>
<path d="M70 132 l-8 -8 a4.5 4.5 0 0 1 8 -5.5 a4.5 4.5 0 0 1 8 5.5 z" fill="#fff"/>
<line x1="44" y1="114" x2="26" y2="126" stroke="@ac@" stroke-width="7" stroke-linecap="round"/>
<g class="arm"><line x1="98" y1="114" x2="118" y2="100" stroke="@ac@" stroke-width="7" stroke-linecap="round"/>
<circle cx="119" cy="99" r="5" fill="@ac2@" stroke="@ac@" stroke-width="2"/></g>
<g fill="none" stroke="@g2@" stroke-width="4" stroke-linecap="round">
<path class="w1" d="M128 56 q6 7 0 14"/><path class="w2" d="M134 50 q9 13 0 26"/><path class="w3" d="M140 44 q12 19 0 38"/></g>
</g>
</svg>"""


def tokens(text, c):
    for k, v in c.items():
        text = text.replace(f"@{k}@", v)
    return text


def apply_theme(key):
    st.markdown(tokens(THEME_CSS, THEMES[key]), unsafe_allow_html=True)


def robot(lines):
    """Cute talking robot with a speech bubble (shown while a spoken reminder is active)."""
    img = base64.b64encode(tokens(ROBOT_SVG, THEMES[st.session_state.get("theme", "lav")]).encode()).decode()
    bubble = "".join(f"<div>{esc(x)}</div>" for x in lines)
    st.markdown(f"<div class='robot-row'><img src='data:image/svg+xml;base64,{img}' width='130' alt='robot'>"
                f"<div class='bubble'>{bubble}</div></div>", unsafe_allow_html=True)


def reset_session():
    """Sign out / clear the session but remember the chosen language and colours."""
    prefs = st.session_state.get("_prefs")
    st.session_state.clear()
    if prefs:
        st.session_state["_prefs"] = prefs
    st.rerun()


def speak(texts, voice, title):
    """Spoken + browser-notification alert (runs in the browser)."""
    components.html(f"""<script>
    // {dt.datetime.now().isoformat()}
    const L={json.dumps(voice)},T={json.dumps(texts)},H={json.dumps(title)};
    try{{if('Notification' in window&&Notification.permission==='default')Notification.requestPermission()}}catch(e){{}}
    T.forEach(s=>{{try{{const u=new SpeechSynthesisUtterance(s);u.lang=L;u.rate=.9;speechSynthesis.speak(u)}}catch(e){{}}
      try{{if(Notification.permission==='granted')new Notification(H,{{body:s}})}}catch(e){{}}}});</script>""", height=0)


def disclaimer(t):
    st.markdown(f"<div class='disc'>{t['disc']}</div>", unsafe_allow_html=True)


# ---------------- sidebar: language ----------------
_prefs = st.session_state.get("_prefs", {})
st.session_state.setdefault("lang", _prefs.get("lang", "English"))
st.session_state.setdefault("theme", _prefs.get("theme", "lav"))
lang_name = st.sidebar.selectbox("🌐", list(LANGS), key="lang")
code, voice = LANGS[lang_name]
t = T[code]
theme_key = st.sidebar.selectbox("🎨 " + t["theme"], list(THEMES), key="theme",
                                 format_func=lambda k: f"{THEMES[k]['emoji']} {t['th_' + k]}")
st.session_state["_prefs"] = {"lang": lang_name, "theme": theme_key}
apply_theme(theme_key)
st.title(f"💊 {t['title']}")
st.caption(t["sub"])

# ---------------- sign in / create account ----------------
if "pid" in st.session_state and not db.execute(
        "select 1 from patients where id=?", (st.session_state["pid"],)).fetchone():
    reset_session()

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
    reset_session()
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

    # ---- live alerts: doses the patient has not answered ----
    spoken = st.session_state.setdefault("spoken", set())
    new_alerts = []
    _alerts = [d for d in doses if (d["status"] == "pending" or (d["status"] == "missed" and d["auto"])) and not d["cg_ack"]
               and int((now() - dt.datetime.fromisoformat(f"{d['day']}T{d['time']}")).total_seconds() // 60) >= ALERT_AFTER]
    if _alerts:
        robot([f"{pname}: {t['dash_alert']} - {d['name']}" for d in _alerts[:3]])
    for d in doses:
        late = int((now() - dt.datetime.fromisoformat(f"{d['day']}T{d['time']}")).total_seconds() // 60)
        no_reply = d["status"] == "pending" or (d["status"] == "missed" and d["auto"])
        if no_reply and late >= ALERT_AFTER and not d["cg_ack"]:
            c1, c2 = st.columns([4, 1])
            c1.error(f"🚨 {t['dash_alert']}: **{d['name']}** · {fmt12(d['time'])} ({late} {t['min']})")
            if c2.button(t["ack"], key=f"ack{d['id']}", use_container_width=True):
                db.execute("update dose_log set cg_ack=1 where id=? and patient_id=?", (d["id"], pid))
                db.commit(); st.rerun()
            if ("cg", d["id"]) not in spoken:
                new_alerts.append(d); spoken.add(("cg", d["id"]))
    if new_alerts:
        speak([f"{pname}: {t['dash_alert']} - {d['name']}" for d in new_alerts], voice, t["dash_alert"])
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
            st.write(f"**{d['name']}** · {fmt12(d['time'])} · {food_label(t, d['food'])} — {t[d['status']]}")
        summary(t, doses, hist, appts)

    with tab_meds:
        st.subheader("🤖 " + t["rem_set"])
        with st.form("remform"):
            rm = st.number_input(t["rem_set"], 1, GRACE, get_remind_min(pid))
            st.caption(t["rem_help"])
            if st.form_submit_button(t["save"], use_container_width=True):
                db.execute("update patients set remind_min=? where id=?", (int(rm), pid)); db.commit()
                st.success(t["updated"])
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
    last_said = st.session_state.setdefault("last_said", {})
    rmin, n_ = get_remind_min(pid), now()
    age = lambda d: (n_ - dt.datetime.fromisoformat(f"{d['day']}T{d['time']}")).total_seconds()
    line = lambda d: f"{t['remind']} {d['name']} ({food_label(t, d['food'])})"
    due = [d for d in doses if d["status"] == "pending" and d["time"] <= hhmm]
    fresh = [d for d in due if d["id"] not in spoken]
    # keep repeating the reminder (about once a minute) for as long as the caregiver set, until the dose is marked
    again = [d for d in due if d["id"] in spoken and age(d) < rmin * 60
             and (n_ - last_said.get(d["id"], dt.datetime.min)).total_seconds() >= REPEAT_EVERY - 5]
    say = fresh + again
    if say:
        speak([line(d) for d in say], voice, t["due"])
        spoken.update(d["id"] for d in fresh)
        last_said.update({d["id"]: n_ for d in say})
    said = {d["id"] for d in say}
    live = [d for d in due if age(d) < rmin * 60 or d["id"] in said]
    if live:
        left = max(0, -(-int(rmin * 60 - min(age(d) for d in live)) // 60))   # whole minutes left, rounded up
        robot([line(d) for d in live[:3]] + ([f"+{len(live) - 3}"] if len(live) > 3 else [])
              + ([f"⏳ {left} {t['rem_left']}"] if left else []))
        if st.button("🔊 " + t["repeat"], key="repeat_voice"):
            spoken.clear(); st.rerun()
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
