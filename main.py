import os, requests, datetime, time, unicodedata, json
from flask import Flask, request, jsonify
from datetime import timezone, timedelta

app = Flask(__name__)

APISPORTS_KEY = os.getenv("APISPORTS_KEY") or os.getenv("SPORTS_API_KEY")
GROQ_KEY = os.getenv("GROQ_API_KEY")
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELE_CHAT = os.getenv("TELEGRAM_CHAT_ID")

BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": APISPORTS_KEY} if APISPORTS_KEY else {}
MAROUA_TZ = timezone(timedelta(hours=1))

TOP_LEAGUES = [307, 39, 140, 135, 78]
SECOND_LEAGUES = [308, 40, 203, 71, 72, 76]

# --- MEMOIRE DES 7 MATCHS POUR SUIVI SMART MONEY ---
TRACKED_FILE = "/tmp/tracked.json"
TRACKED_MATCHES = []  # [{fid, home, away, initial_odds, current_odds, marche, heure_match}]

def strip_accents(s): return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

def send_telegram(text, chat_id=None):
    cid = chat_id or TELE_CHAT
    if not TELE_TOKEN or not cid: print(text); return
    try:
        url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
        for chunk in [text[i:i+3900] for i in range(0, len(text), 3900)]:
            requests.post(url, json={"chat_id": cid, "text": chunk, "parse_mode":"Markdown"}, timeout=15)
    except Exception as e: print(e)

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
        r = requests.get(f"{BASE}/{endpoint}", headers=HEADERS, timeout=15).json()
        return r.get('response', [])
    except: return []

def get_current_pinnacle_odds(fid):
    odds = call_api(f"odds?fixture={fid}")
    try:
        for fx in odds:
            for book in fx.get('bookmakers', []):
                if 'Pinnacle' in book['name']:
                    for bet in book['bets']:
                        if bet['name'] == 'Match Winner':
                            return {v['value']: float(v['odd']) for v in bet['values']}
    except: return {}
    return {}

def recherche_web_reelle(home, away):
    try:
        q = f"{home} vs {away} injuries lineup"
        r = requests.get(f"https://api.duckduckgo.com/?q={q}&format=json&pretty=1", timeout=10).json()
        info = (r.get('AbstractText','') or "No major news")[:500]
        if GROQ_KEY:
            prompt = f"Match {home} vs {away}. Web: {info}. Dis en 2 lignes FR si blessure/explication pour fluctuation cote?"
            gr = requests.post("https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type":"application/json"},
                json={"model":"llama-3.1-8b-instant","messages":[{"role":"user","content":prompt}],"temperature":0.2}, timeout=15).json()
            return gr['choices'][0]['message']['content']
        return info
    except: return "Web OK"

def choisir_7_obligatoires():
    global TRACKED_MATCHES
    today = datetime.date.today().isoformat()
    fixtures = call_api(f"fixtures?date={today}")
    if not fixtures: return []
    scored = []
    for fx in fixtures[:200]:
        fid = fx['fixture']['id']; league_id = fx['league']['id']
        pred = call_api(f"predictions?fixture={fid}")
        if not pred: continue
        pred = pred[0]; odds = call_api(f"odds?fixture={fid}")
        try:
            conf = max(int(pred['predictions']['percent']['home'].replace('%','') or 0), int(pred['predictions']['percent']['draw'].replace('%','') or 0), int(pred['predictions']['percent']['away'].replace('%','') or 0))
            odds_count = len(odds[0]['bookmakers']) if odds and len(odds)>0 else 0
            scored.append({"fid":fid,"fixture":fx,"pred":pred,"odds":odds,"conf":conf,"avantage":conf+odds_count*2,"league_id":league_id,"type":"TOP" if league_id in TOP_LEAGUES else "SECOND"})
        except: continue
    top = sorted([s for s in scored if s['type']=="TOP"], key=lambda x: x['avantage'], reverse=True)[:3]
    second = sorted([s for s in scored if s['type']=="SECOND"], key=lambda x: x['avantage'], reverse=True)[:4]
    result = top + second
    if len(result) < 7:
        result += sorted([s for s in scored if s not in result], key=lambda x: x['avantage'], reverse=True)[:7-len(result)]
    result = result[:7]
    
    # SAUVEGARDE POUR SUIVI SMART MONEY TOUTES LES 30 MIN
    TRACKED_MATCHES = []
    for m in result:
        init_odds = get_current_pinnacle_odds(m['fid'])
        TRACKED_MATCHES.append({
            "fid": m['fid'], "home": m['fixture']['teams']['home']['name'], "away": m['fixture']['teams']['away']['name'],
            "heure": m['fixture']['fixture']['date'], "marche_initial": m['pred']['predictions']['advice'],
            "initial_odds": init_odds, "last_odds": init_odds, "statut": "ACTIF"
        })
    save_tracked()
    return result

def analyser_et_envoyer_match(match_obj, index, chat_id=None):
    fx = match_obj['fixture']; pred = match_obj['pred']; fid = match_obj['fid']
    home = fx['teams']['home']['name']; away = fx['teams']['away']['name']
    try: dt = datetime.datetime.fromisoformat(fx['fixture']['date'].replace('Z','+00:00')).astimezone(MAROUA_TZ); heure_str = dt.strftime("%H:%M Maroua")
    except: heure_str = fx['fixture']['date']; dt = datetime.datetime.now(MAROUA_TZ)
    web = recherche_web_reelle(home, away)
    msg = f"""🔥 **TICKET V18 PRO MAX {index}/7**
⚽️ {home} vs {away} - {heure_str}
🏆 {fx['league']['name']}

📊 API: {pred['predictions']['advice']} ({match_obj['conf']}%)
🎯 **MARCHÉ PRINCIPAL:** {pred['predictions']['advice']}
📦 COTES PINNACLE INITIALES: {get_current_pinnacle_odds(fid)}
🌐 WEB: {web}

💵 TICKET 100% - Suivi Smart Money activé (toutes les 30min jusqu'à T-90)"""
    send_telegram(msg, chat_id)

# --- ROUTES ---
@app.route('/')
def home_route(): return "SEHBA V18 PRO MAX SMART MONEY READY"

@app.route('/run')
@app.route('/auto')
def run_daily():
    load_tracked()
    matches = choisir_7_obligatoires()
    if not matches:
        send_telegram("⚠️ Aucun match - quota 0"); return jsonify({"error":"Aucun match"})
    send_telegram(f"🚀 V18 PRO MAX - 7 matchs choisis (200 analysés) - Suivi Smart Money lancé")
    for i, m in enumerate(matches, 1):
        analyser_et_envoyer_match(m, i); time.sleep(2)
    return jsonify({"status":"7 envoyes + tracked"})

@app.route('/check_odds')
def check_odds_route():
    """ A APPELER TOUTES LES 30 MIN PAR CRON-JOB.ORG """
    load_tracked()
    if not TRACKED_MATCHES: return jsonify({"error":"Aucun match tracké, fais /run d'abord"})
    alertes = []
    now = datetime.datetime.now(MAROUA_TZ)
    for tm in TRACKED_MATCHES:
        if tm['statut'] != 'ACTIF': continue
        fid = tm['fid']
        try:
            match_time = datetime.datetime.fromisoformat(tm['heure'].replace('Z','+00:00')).astimezone(MAROUA_TZ)
            if match_time - now <= timedelta(minutes=90):
                # T-90 atteint, on ne touche plus sauf alerte grave
                minutes_left = int((match_time - now).total_seconds()/60)
                if minutes_left < 0: tm['statut'] = "TERMINE"; continue
        except: pass

        current = get_current_pinnacle_odds(fid)
        initial = tm.get('initial_odds', {})
        if not current or not initial: continue

        # DETECTION SMART MONEY - Hausse brutale favori
        for team, odd_init in initial.items():
            odd_now = current.get(team)
            if not odd_now: continue
            variation = ((odd_now - odd_init) / odd_init) * 100
            # Si cote favori monte >15% = Smart Money contre nous
            if variation > 15 and odd_init < 2.0:
                web_check = recherche_web_reelle(tm['home'], tm['away'])
                msg_alerte = f"""🚨 **SMART MONEY ALERTE - {tm['home']} vs {tm['away']}**
📉 Cote {team}: {odd_init} → {odd_now} (+{variation:.1f}%)
⏰ T-90 en cours - Signal fort

💡 **ACTION:** Changer de position!
Ancien marché: {tm['marche_initial']}
👉 **NOUVEAU MARCHÉ CONSEILLÉ: Double Chance - Couverture + Under 3.5**

🌐 Web check: {web_check}
💵 On sécurise le ticket!"""
                send_telegram(msg_alerte)
                tm['last_odds'] = current
                tm['marche_initial'] = "Double Chance (Ajustement Smart Money)"
                alertes.append(tm)
            # Baisse normale = confiance
            elif variation < -10:
                send_telegram(f"✅ Smart Money OK {tm['home']} vs {tm['away']} - Cote {team} {odd_init}→{odd_now} (-{abs(variation):.1f}%) - On reste sur {tm['marche_initial']}")

        tm['last_odds'] = current
    save_tracked()
    return jsonify({"checked": len(TRACKED_MATCHES), "alertes": len(alertes), "tracked": TRACKED_MATCHES})

@app.route('/setwebhook')
def setwebhook_route():
    host = request.host_url.rstrip('/') + '/webhook'
    r = requests.get(f"https://api.telegram.org/bot{TELE_TOKEN}/setWebhook?url={host}").json()
    return jsonify({"webhook": host, "result": r})

@app.route('/webhook', methods=['POST'])
def webhook_route():
    data = request.get_json()
    if not data or 'message' not in data: return jsonify({"ok":True})
    raw = data['message'].get('text','').strip()
    chat_id = data['message']['chat']['id']
    clean = raw
    if clean.lower().startswith('/vs'): clean = clean[3:].strip()
    if clean.startswith('/'): clean = clean[1:].strip()
    clean = strip_accents(clean)
    if '-' not in clean:
        send_telegram("Format: /vs Barcelone - Real Madrid", chat_id); return jsonify({"ok":True})
    h_q, a_q = [x.strip() for x in clean.split('-',1)]
    send_telegram(f"🔍 Analyse manuelle V18 PRO MAX: {h_q} vs {a_q}... API + WEB + Smart Money check...", chat_id)
    today = datetime.date.today().isoformat()
    fixtures = call_api(f"fixtures?date={today}")
    if not fixtures:
        web = recherche_web_reelle(h_q, a_q)
        send_telegram(f"🔥 TICKET SECOURS {h_q} vs {a_q}\n🌐 {web}\n🎯 Double Chance 1X + Suivi Smart Money T-90", chat_id)
        return jsonify({"ok":True})
    target = fixtures[0]
    for f in fixtures:
        if h_q.lower() in f['teams']['home']['name'].lower() or h_q.lower() in f['teams']['away']['name'].lower(): target = f; break
    fid = target['fixture']['id']; pred = call_api(f"predictions?fixture={fid}"); odds = call_api(f"odds?fixture={fid}")
    match_obj = {"fid": fid, "fixture": target, "pred": pred[0] if pred else {"predictions":{"advice":"Double Chance","percent":{"home":"50%","draw":"25%","away":"25%"}}}, "odds": odds, "conf": 60, "type": "MANUAL-TELEGRAM"}
    analyser_et_envoyer_match(match_obj, 1, chat_id)
    return jsonify({"ok":True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
