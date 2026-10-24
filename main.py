import os, requests, datetime, time, unicodedata, json, re
from flask import Flask, request, jsonify
from datetime import timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials
try:
    from tavily import TavilyClient
except: TavilyClient = None

app = Flask(__name__)
APISPORTS_KEY = os.getenv("APISPORTS_KEY") or os.getenv("SPORTS_API_KEY")
GROQ_KEY = os.getenv("GROQ_API_KEY")
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELE_CHAT = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS_RAW = os.getenv("GOOGLE_CREDENTIALS_JSON")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": APISPORTS_KEY} if APISPORTS_KEY else {}
MAROUA_TZ = timezone(timedelta(hours=1))
TRACKED_FILE = "/tmp/tracked.json"
TRACKED_MATCHES = []
LAST_RUN_FILE = "/tmp/last_run.txt"
TAVILY_CACHE_FILE = "/tmp/tavily_cache.json"

def get_today_maroua():
    return datetime.datetime.now(MAROUA_TZ).date().isoformat()

def ecrire_dans_sheet(date_str, match, league, prono, cote, note):
    if not GOOGLE_CREDS_RAW or not GOOGLE_SHEET_ID: return
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(GOOGLE_CREDS_RAW)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(GOOGLE_SHEET_ID).sheet1
        sheet.append_row([date_str, match, league, prono, cote, note, "EN COURS"], value_input_option="USER_ENTERED")
    except Exception as e: print(f"Sheet: {e}")

def strip_accents(s): return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
def send_telegram(text, chat_id=None):
    cid = chat_id or TELE_CHAT
    if not TELE_TOKEN or not cid: print(text); return
    try:
        url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
        for chunk in [text[i:i+3900] for i in range(0, len(text), 3900)]:
            requests.post(url, json={"chat_id": cid, "text": chunk, "parse_mode":"Markdown"}, timeout=15)
    except Exception as e: print(f"Telegram: {e}")

def save_tracked():
    try:
        with open(TRACKED_FILE, 'w') as f: json.dump(TRACKED_MATCHES, f)
    except: pass
def load_tracked():
    global TRACKED_MATCHES
    try:
        with open(TRACKED_FILE, 'r') as f: TRACKED_MATCHES = json.load(f)
    except: TRACKED_MATCHES = []

def call_api(endpoint):
    if not APISPORTS_KEY: return []
    try:
        res = requests.get(f"{BASE}/{endpoint}", headers=HEADERS, timeout=15).json()
        if res.get('errors'): print(f"API Error {endpoint}: {res.get('errors')}"); return []
        return res.get('response', [])
    except Exception as e: print(f"call_api {e}"); return []

def get_all_pinnacle_odds(fid):
    odds = call_api(f"odds?fixture={fid}")
    if not odds: return {}, "Aucune cote encore"
    try:
        for fx in odds:
            for book in fx.get('bookmakers', []):
                if 'Pinnacle' in book['name']:
                    markets_text = ""
                    odds_dict = {}
                    for bet in book['bets']:
                        markets_text += f"{bet['name']}: "
                        for v in bet['values']:
                            markets_text += f"{v['value']}@{v['odd']} | "
                            if bet['name'] == 'Match Winner':
                                odds_dict[v['value']] = float(v['odd'])
                        markets_text += " || "
                    return odds_dict, markets_text[:4000]
    except: pass
    return {}, "Non dispo"

def get_tavily_cache():
    if not os.path.exists(TAVILY_CACHE_FILE): return {}
    try:
        with open(TAVILY_CACHE_FILE, 'r') as f: return json.load(f)
    except: return {}
def save_tavily_cache(c):
    try:
        with open(TAVILY_CACHE_FILE, 'w') as f: json.dump(c, f)
    except: pass

def recherche_web_contextuelle(home, away):
    query = f"{home} {away} injuries suspensions lineup news today"
    today = get_today_maroua()
    cache = get_tavily_cache()
    key = f"{today}_{home}_{away}"
    if key in cache: return cache[key]
    if TAVILY_API_KEY and TavilyClient:
        try:
            client = TavilyClient(api_key=TAVILY_API_KEY)
            res = client.search(query=query, search_depth="advanced", max_results=5, include_answer=True, time_range="day")
            txt = (res.get("answer","") + " " + " ".join([r["content"][:600] for r in res.get("results",[])[:3]]))[:3500]
            if len(txt) > 50:
                cache[key]=txt; save_tavily_cache(cache)
                return txt
        except Exception as e: print(f"Tavily fail {e}")
    return "RAS - pas d'alerte web majeure"

def detect_smart_money(old_odds, new_odds):
    if not old_odds or not new_odds: return "RAS"
    try:
        for k in new_odds:
            if k in old_odds and old_odds[k] > 0:
                change = (new_odds[k] - old_odds[k]) / old_odds[k]
                if change > 0.12: return f"ALERTE SMART MONEY: {k} {old_odds[k]} -> {new_odds[k]} (+{int(change*100)}%)"
                if change < -0.12: return f"SMART MONEY CONFIRME: {k} {old_odds[k]} -> {new_odds[k]} (-{int(abs(change)*100)}%)"
    except: pass
    return "RAS"

def arbitrage_ia_contextuel_pro(home, away, prono_natif, league_name, all_markets_text, smart_money_signal, prob_pct=50):
    if not GROQ_KEY: return prono_natif, "Validation auto"
    contexte_web = recherche_web_contextuelle(home, away)
    prompt = f"""SEHBA V22 ALL LEAGUES - ARBITRE SMART.
Match: {home} vs {away} - {league_name}
Prono natif: {prono_natif} ({prob_pct}%)
WEB Tavily: {contexte_web}
Pinnacle TOUS MARCHES: {all_markets_text}
Signal: {smart_money_signal}
CONSIGNE: Tu as TOUS les marchés. Si SMART MONEY alerte, change de marché (ex 1X, Over, BTTS). Sinon garde natif mais choisis le marché le plus safe.
Réponds: DECISION: [Marché final] | JUSTIF: [15 mots FR]"""
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type":"application/json"},
            json={"model": "openai/gpt-oss-120b", "messages": [{"role":"user","content":prompt}], "temperature":0.2, "max_tokens":400}, timeout=30).json()
        ia = r['choices'][0]['message']['content'].strip()
        m = re.search(r"DECISION:\s*(.*)", ia, re.IGNORECASE)
        final = m.group(1).split("|")[0].strip() if m else ia.split("\n")[0]
        return final, ia[:250]
    except Exception as e:
        print(f"IA err {e}")
        return prono_natif, "Validation défaut"

def choisir_7_obligatoires():
    global TRACKED_MATCHES
    today = get_today_maroua()
    fixtures = call_api(f"fixtures?date={today}")
    if not fixtures: return []
    filtered = [fx for fx in fixtures if fx['fixture']['status']['short'] == 'NS']
    print(f"V22 ALL: {len(filtered)} matchs NS pour {today}")
    scored = []
    for fx in filtered[:50]:
        fid = fx['fixture']['id']
        pred_res = call_api(f"predictions?fixture={fid}")
        if not pred_res: continue
        pred = pred_res[0]
        try:
            pct = pred.get('predictions', {}).get('percent', {})
            conf = max(int(str(pct.get('home','0')).replace('%','') or 0), int(str(pct.get('draw','0')).replace('%','') or 0), int(str(pct.get('away','0')).replace('%','') or 0))
            scored.append({"fid": fid, "fixture": fx, "pred": pred, "conf": conf, "avantage": conf})
        except: continue
    result = sorted(scored, key=lambda x: x['avantage'], reverse=True)[:7]
    TRACKED_MATCHES = []
    for m in result:
        init_odds, _ = get_all_pinnacle_odds(m['fid'])
        TRACKED_MATCHES.append({"fid": m['fid'], "home": m['fixture']['teams']['home']['name'], "away": m['fixture']['teams']['away']['name'], "heure": m['fixture']['fixture']['date'], "initial_odds": init_odds, "last_odds": init_odds, "statut": "ATTENTE", "prono_envoye": "", "league": m['fixture']['league']['name']})
    save_tracked()
    return result

def analyser_et_envoyer_match(match_obj, index, chat_id=None, smart_signal="RAS"):
    fx = match_obj['fixture']; pred = match_obj['pred']; fid = match_obj['fid']
    if fx['fixture']['status']['short'] in ['1H','HT','2H','ET','P','LIVE']: return
    home = fx['teams']['home']['name']; away = fx['teams']['away']['name']
    try:
        dt = datetime.datetime.fromisoformat(fx['fixture']['date'].replace('Z','+00:00')).astimezone(MAROUA_TZ)
        heure_str = dt.strftime("%H:%M Maroua")
    except:
        heure_str = fx['fixture']['date']; dt = datetime.datetime.now(MAROUA_TZ)
    prono_natif = pred['predictions'].get('advice', 'Double Chance')
    pct = pred.get('predictions', {}).get('percent', {})
    try: prob = int(str(pct.get('home','50')).replace('%','') or 50)
    except: prob = 50
    odds_dict, markets_text = get_all_pinnacle_odds(fid)
    odds_str = ", ".join([f"{k}: {v}" for k, v in odds_dict.items()]) if odds_dict else markets_text[:200]
    prono_final, note_ia = arbitrage_ia_contextuel_pro(home, away, prono_natif, fx['league']['name'], markets_text, smart_signal, prob)
    msg = f"🔥 *TICKET V22 ALL {index}*\n⚽️ {home} vs {away} - {heure_str}\n🏆 {fx['league']['name']}\n\n📊 NATIF: {prono_natif} ({prob}%)\n📦 MARCHES: {markets_text[:400]}\n🎯 *FINAL:* {prono_final}\n💰 COTES: {odds_str}\n🧠 {note_ia}\n📈 {smart_signal}"
    send_telegram(msg, chat_id)
    ecrire_dans_sheet(dt.strftime("%Y-%m-%d %H:%M"), f"{home} - {away}", fx['league']['name'], prono_final, odds_str, note_ia + " | " + smart_signal)

@app.route('/')
def home_route(): return "SEHBA V22 ALL LEAGUES - ACTIF"
@app.route('/ping')
def ping_route(): return jsonify({"status":"alive", "version":"V22 ALL", "tracked": len(TRACKED_MATCHES), "date_maroua": get_today_maroua()})

@app.route('/run')
def run_daily():
    today = get_today_maroua()
    try:
        with open(LAST_RUN_FILE, 'r') as f:
            if f.read().strip() == today: return jsonify({"status":"Deja lance", "date":today})
    except: pass
    load_tracked()
    matches = choisir_7_obligatoires()
    if not matches: 
        send_telegram(f"ℹ️ V22 ALL: Aucun match trouvé pour {today}.")
        return jsonify({"error":"Aucun match", "date":today})
    with open(LAST_RUN_FILE, 'w') as f: f.write(today)
    msg = f"🚀 *V22 ALL - 7 matchs SUIVI pour {today} (tous championnats):*\n"
    for m in TRACKED_MATCHES:
        dt = datetime.datetime.fromisoformat(m['heure'].replace('Z','+00:00')).astimezone(MAROUA_TZ)
        msg += f"- {m['home']} vs {m['away']} ({m['league']}) à {dt.strftime('%H:%M')} -> H-90\n"
    send_telegram(msg)
    return jsonify({"status":"7 en ATTENTE", "date":today})

@app.route('/check_and_analyze')
def check_and_analyze():
    load_tracked()
    if not TRACKED_MATCHES: return jsonify({"status":"vide", "date":get_today_maroua()})
    now = datetime.datetime.now(MAROUA_TZ)
    fait = 0
    for m in TRACKED_MATCHES:
        try:
            match_time = datetime.datetime.fromisoformat(m['heure'].replace('Z','+00:00')).astimezone(MAROUA_TZ)
            diff_min = (match_time - now).total_seconds() / 60
            smart_signal = "RAS"
            if diff_min < 180:
                new_odds, _ = get_all_pinnacle_odds(m['fid'])
                smart_signal = detect_smart_money(m.get('last_odds'), new_odds)
                if "ALERTE" in smart_signal and m.get("statut") == "ANALYSE":
                    fx = call_api(f"fixtures?id={m['fid']}")
                    pr = call_api(f"predictions?fixture={m['fid']}")
                    if fx and pr:
                        match_obj = {"fid": m['fid'], "fixture": fx[0], "pred": pr[0], "conf": 70}
                        analyser_et_envoyer_match(match_obj, f"RE-ANALYSE {m['home'][:5]}", None, smart_signal)
                        fait += 1
                if new_odds: m['last_odds'] = new_odds
            if m.get("statut") == "ATTENTE" and 30 <= diff_min <= 90:
                fx = call_api(f"fixtures?id={m['fid']}")
                pr = call_api(f"predictions?fixture={m['fid']}")
                if fx and pr:
                    match_obj = {"fid": m['fid'], "fixture": fx[0], "pred": pr[0], "conf": 70}
                    analyser_et_envoyer_match(match_obj, f"J-{int(diff_min)}min", None, smart_signal)
                    m['statut'] = "ANALYSE"
                    fait += 1
                    time.sleep(3)
        except Exception as e: print(f"Err {e}")
    save_tracked()
    return jsonify({"lancees": fait, "heure": now.strftime("%H:%M"), "date":now.strftime("%Y-%m-%d")})

def find_team_id(name):
    res = call_api(f"teams?search={name}")
    if res: return res[0]['team']['id']
    return None

@app.route('/webhook', methods=['POST'])
def webhook_route():
    data = request.get_json()
    if not data or 'message' not in data: return jsonify({"ok":True})
    raw = data['message'].get('text','').strip()
    chat_id = data['message']['chat']['id']
    if not raw.lower().startswith('/vs'): return jsonify({"ok":True})
    clean_raw = raw[3:].strip()
    clean = strip_accents(clean_raw)
    lower = clean.lower()
    if ' - ' in clean: sep = ' - '
    elif ' vs ' in lower:
        idx = lower.find(' vs '); clean = clean[:idx] + ' - ' + clean[idx+4:]; sep = ' - '
    elif ' v ' in f" {lower} ":
        idx = lower.find(' v '); clean = clean[:idx] + ' - ' + clean[idx+3:]; sep = ' - '
    else: sep = None
    if not sep or ' - ' not in clean:
        send_telegram("Format: /vs Real - Leipzig", chat_id)
        return jsonify({"ok":True})
    h_q, a_q = [x.strip() for x in clean.split(' - ', 1)]
    send_telegram(f"🔍 *V22 ALL: {h_q} vs {a_q} + Tavily + Smart...*", chat_id)
    target = None
    for name in [h_q, a_q]:
        tid = find_team_id(name)
        if tid:
            fixtures = call_api(f"fixtures?team={tid}&next=10")
            for f in fixtures:
                hn = strip_accents(f['teams']['home']['name'].lower())
                an = strip_accents(f['teams']['away']['name'].lower())
                if h_q.lower() in hn or h_q.lower() in an or a_q.lower() in hn or a_q.lower() in an:
                    target = f; break
        if target: break
    if not target:
        send_telegram(f"❌ {h_q} vs {a_q} introuvable. Essaie nom complet.", chat_id)
        return jsonify({"ok":True})
    fid = target['fixture']['id']
    pred = call_api(f"predictions?fixture={fid}")
    if not pred:
        send_telegram(f"⚠️ {target['teams']['home']['name']} vs {target['teams']['away']['name']} trouvé mais pas de prono API.", chat_id)
        return jsonify({"ok":True})
    match_obj = {"fid": fid, "fixture": target, "pred": pred[0], "conf": 70}
    analyser_et_envoyer_match(match_obj, "MANUEL V22 ALL", chat_id, "Analyse manuelle")
    return jsonify({"ok":True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
