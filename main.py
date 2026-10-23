import os, requests, datetime, time, unicodedata, json
from flask import Flask, request, jsonify
from datetime import timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# --- ENV - V19 ---
APISPORTS_KEY = os.getenv("APISPORTS_KEY") or os.getenv("SPORTS_API_KEY")
GROQ_KEY = os.getenv("GROQ_API_KEY")
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELE_CHAT = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS_RAW = os.getenv("GOOGLE_CREDENTIALS_JSON")

BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": APISPORTS_KEY} if APISPORTS_KEY else {}
MAROUA_TZ = timezone(timedelta(hours=1))

TOP_LEAGUES = [39, 140, 135, 78, 61]
SECOND_LEAGUES = [40, 141, 136, 79, 62]
TRACKED_FILE = "/tmp/tracked.json"
TRACKED_MATCHES = []
LAST_RUN_FILE = "/tmp/last_run.txt"

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
        if res.get('errors'): return []
        return res.get('response', [])
    except: return []

def get_current_pinnacle_odds(fid):
    odds = call_api(f"odds?fixture={fid}")
    if not odds: return {}
    try:
        for fx in odds:
            for book in fx.get('bookmakers', []):
                if 'Pinnacle' in book['name']:
                    for bet in book['bets']:
                        if bet['name'] == 'Match Winner':
                            return {v['value']: float(v['odd']) for v in bet['values']}
    except: pass
    return {}

# --- V19 : GPT-OSS 120B INTÉGRÉ ICI ---
def arbitrage_ia_contextuel(home, away, prono_natif):
    if not GROQ_KEY: return prono_natif, "Validation auto (IA non configurée)"
    try:
        prompt = f"""Tu es l'arbitre IA PRO MAX du bot SEHBA V19.
Match: {home} vs {away}.
Prono statistique natif API: {prono_natif}.
Tâche: Analyse risque contextuel (blessures, forme, météo).
Si le prono est safe, réponds exactement: VALIDE
Sinon propose une couverture sécurisée type: Double Chance X2 ou Over 1.5 ou BTTS non.
Réponse en FR, 2 lignes max."""

        payload = {
            "model": "openai/gpt-oss-120b",  # TON MODELE OFFICIEL V19
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 350
        }
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type":"application/json"},
            json=payload, timeout=30).json()
        
        reponse_ia = r['choices'][0]['message']['content'].strip()
        if "VALIDE" in reponse_ia.upper():
            return prono_natif, "Confirmé par GPT-OSS 120B (Feu vert)"
        return reponse_ia, "⚠️ Ajusté par GPT-OSS 120B"
    except Exception as e:
        print(f"IA V19 Erreur: {e}")
        return prono_natif, "Validation défaut V19"

def choisir_7_obligatoires():
    global TRACKED_MATCHES
    today = datetime.date.today().isoformat()
    fixtures = call_api(f"fixtures?date={today}")
    if not fixtures: return []
    filtered = []
    for fx in fixtures:
        if fx['fixture']['status']['short'] == 'NS':
            lid = fx['league']['id']
            if lid in TOP_LEAGUES: filtered.append((fx, "TOP", 10))
            elif lid in SECOND_LEAGUES: filtered.append((fx, "SECOND", 5))
            else: filtered.append((fx, "OTHER", 1))
    filtered = sorted(filtered, key=lambda x: x[2], reverse=True)[:15]
    scored = []
    for fx, l_type, _ in filtered:
        fid = fx['fixture']['id']
        pred_res = call_api(f"predictions?fixture={fid}")
        if not pred_res: continue
        pred = pred_res[0]
        try:
            pct = pred.get('predictions', {}).get('percent', {})
            conf = max(int(str(pct.get('home','0')).replace('%','') or 0), int(str(pct.get('draw','0')).replace('%','') or 0), int(str(pct.get('away','0')).replace('%','') or 0))
            scored.append({"fid": fid, "fixture": fx, "pred": pred, "conf": conf, "avantage": conf + (10 if l_type=="TOP" else 5), "type": l_type})
        except: continue
    top = sorted([s for s in scored if s['type']=="TOP"], key=lambda x: x['avantage'], reverse=True)[:3]
    second = sorted([s for s in scored if s['type']=="SECOND"], key=lambda x: x['avantage'], reverse=True)[:4]
    result = top + second
    if len(result) < 7: result += sorted([s for s in scored if s not in result], key=lambda x: x['avantage'], reverse=True)[:7-len(result)]
    result = result[:7]
    TRACKED_MATCHES = []
    for m in result:
        init_odds = get_current_pinnacle_odds(m['fid'])
        TRACKED_MATCHES.append({"fid": m['fid'], "home": m['fixture']['teams']['home']['name'], "away": m['fixture']['teams']['away']['name'], "heure": m['fixture']['fixture']['date'], "initial_odds": init_odds, "last_odds": init_odds, "statut": "ACTIF"})
    save_tracked()
    return result

def analyser_et_envoyer_match(match_obj, index, chat_id=None):
    fx = match_obj['fixture']; pred = match_obj['pred']; fid = match_obj['fid']
    if fx['fixture']['status']['short'] in ['1H','HT','2H','ET','P','LIVE']: return
    home = fx['teams']['home']['name']; away = fx['teams']['away']['name']
    try:
        dt = datetime.datetime.fromisoformat(fx['fixture']['date'].replace('Z','+00:00')).astimezone(MAROUA_TZ)
        heure_str = dt.strftime("%H:%M Maroua")
    except:
        heure_str = fx['fixture']['date']; dt = datetime.datetime.now(MAROUA_TZ)
    prono_natif = pred['predictions'].get('advice', 'Double Chance')
    prono_final, note_ia = arbitrage_ia_contextuel(home, away, prono_natif)
    odds_pinnacle = get_current_pinnacle_odds(fid)
    odds_str = ", ".join([f"{k}: {v}" for k, v in odds_pinnacle.items()]) if odds_pinnacle else "Non dispo"
    msg = f"🔥 *TICKET V19 GPT-OSS 120B {index}/7*\n⚽️ {home} vs {away} - {heure_str}\n🏆 {fx['league']['name']}\n\n📊 NATIF: {prono_natif}\n🎯 *MARCHE:* {prono_final}\n📦 COTES: {odds_str}\n🤖 NOTE: {note_ia}"
    send_telegram(msg, chat_id)
    ecrire_dans_sheet(dt.strftime("%Y-%m-%d %H:%M"), f"{home} - {away}", fx['league']['name'], prono_final, odds_str, note_ia)

@app.route('/')
def home_route(): return "SEHBA V19 GPT-OSS 120B - ACTIF"
@app.route('/ping')
def ping_route(): return jsonify({"status":"alive", "version":"V19 GPT-OSS 120B"})
@app.route('/run')
def run_daily():
    today = datetime.date.today().isoformat()
    try:
        with open(LAST_RUN_FILE, 'r') as f:
            if f.read().strip() == today: return jsonify({"status":"Deja lance"})
    except: pass
    load_tracked()
    matches = choisir_7_obligatoires()
    if not matches: return jsonify({"error":"Aucun match"})
    with open(LAST_RUN_FILE, 'w') as f: f.write(today)
    send_telegram("🚀 *Lancement V19 GPT-OSS 120B...*")
    for i, m in enumerate(matches, 1):
        analyser_et_envoyer_match(m, i)
        time.sleep(2)
    return jsonify({"status":"7 envoyes V19"})
@app.route('/check_odds')
def check_odds_route():
    load_tracked()
    if not TRACKED_MATCHES: return jsonify({"status":"Rien a suivre"})
    alerts = []
    for m in TRACKED_MATCHES:
        new_odds = get_current_pinnacle_odds(m['fid'])
        if new_odds and new_odds != m.get('last_odds'):
            m['last_odds'] = new_odds
            alerts.append(f"📈 {m['home']} vs {m['away']}: {new_odds}")
    save_tracked()
    if alerts: send_telegram("🔔 *Alerte Pinnacle V19:*\n" + "\n".join(alerts))
    return jsonify({"checked": len(TRACKED_MATCHES), "alerts": alerts})
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
    sep = None
    if ' - ' in clean: sep = ' - '
    elif ' vs ' in lower:
        idx = lower.find(' vs ')
        clean = clean[:idx] + ' - ' + clean[idx+4:]
        sep = ' - '
    elif ' v ' in f" {lower} ":
        idx = lower.find(' v ')
        clean = clean[:idx] + ' - ' + clean[idx+3:]
        sep = ' - '
    if not sep or ' - ' not in clean:
        send_telegram("Format: /vs Real - Leipzig ou /vs Real vs Leipzig", chat_id)
        return jsonify({"ok":True})
    h_q, a_q = [x.strip() for x in clean.split(' - ', 1)]
    send_telegram(f"🔍 *V19 Manuel: {h_q} vs {a_q} (GPT-OSS 120B)*", chat_id)
    today = datetime.date.today().isoformat()
    fixtures = call_api(f"fixtures?date={today}")
    if len(fixtures) < 5:
        for d in [1,2,3]:
            nd = (datetime.date.today() + datetime.timedelta(days=d)).isoformat()
            fixtures.extend(call_api(f"fixtures?date={nd}"))
    target = None
    for f in fixtures:
        hn = strip_accents(f['teams']['home']['name'].lower()); an = strip_accents(f['teams']['away']['name'].lower())
        if h_q.lower() in hn and a_q.lower() in an: target=f; break
        if a_q.lower() in hn and h_q.lower() in an: target=f; break
    if not target:
        send_telegram(f"❌ {h_q} vs {a_q} introuvable sur 3j.", chat_id); return jsonify({"ok":True})
    fid = target['fixture']['id']
    pred = call_api(f"predictions?fixture={fid}")
    if not pred:
        send_telegram(f"⚠️ {target['teams']['home']['name']} vs {target['teams']['away']['name']} trouvé mais pas de prediction.", chat_id)
        return jsonify({"ok":True})
    match_obj = {"fid": fid, "fixture": target, "pred": pred[0], "conf": 70}
    analyser_et_envoyer_match(match_obj, "MANUEL V19", chat_id)
    return jsonify({"ok":True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
