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

def get_today_maroua(): return datetime.datetime.now(MAROUA_TZ).date().isoformat()
def ecrire_dans_sheet(d, m, l, p, c, n):
    if not GOOGLE_CREDS_RAW or not GOOGLE_SHEET_ID: return
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_RAW), scopes=scopes)
        gspread.authorize(creds).open_by_key(GOOGLE_SHEET_ID).sheet1.append_row([d,m,l,p,c,n,"EN COURS"], value_input_option="USER_ENTERED")
    except Exception as e: print(f"Sheet {e}")
def strip_accents(s): return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
def send_telegram(text, chat_id=None):
    cid = chat_id or TELE_CHAT
    if not TELE_TOKEN or not cid: print(text); return
    try:
        url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
        for chunk in [text[i:i+3900] for i in range(0, len(text), 3900)]:
            requests.post(url, json={"chat_id": cid, "text": chunk, "parse_mode":"Markdown"}, timeout=15)
    except Exception as e: print(f"TG {e}")
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
    if not odds: return {}, "Cotes en cours"
    try:
        for fx in odds:
            for book in fx.get('bookmakers', []):
                if 'Pinnacle' in book['name']:
                    txt=""; od={}
                    for bet in book['bets']:
                        txt+=f"{bet['name']}: "
                        for v in bet['values']:
                            txt+=f"{v['value']}@{v['odd']} | "
                            if bet['name']=='Match Winner': od[v['value']]=float(v['odd'])
                        txt+=" || "
                    return od, txt[:3500]
    except: pass
    return {}, "Cotes Pinnacle non dispo (plan gratuit)"

def get_tavily_cache():
    try:
        with open(TAVILY_CACHE_FILE,'r') as f: return json.load(f)
    except: return {}
def save_tavily_cache(c):
    try:
        with open(TAVILY_CACHE_FILE,'w') as f: json.dump(c,f)
    except: pass

def recherche_web_contextuelle(home, away):
    query = f"{home} {away} injuries lineup today"
    today = get_today_maroua()
    cache = get_tavily_cache()
    key = f"{today}_{home}_{away}"
    if key in cache: return cache[key]
    if TAVILY_API_KEY and TavilyClient:
        try:
            client = TavilyClient(api_key=TAVILY_API_KEY)
            res = client.search(query=query, search_depth="advanced", max_results=5, include_answer=True, time_range="day")
            txt = (res.get("answer","") + " " + " ".join([r["content"][:600] for r in res.get("results",[])[:3]]))[:3500]
            if len(txt)>50:
                cache[key]=txt; save_tavily_cache(cache); return txt
        except Exception as e: print(f"Tavily {e}")
    return "RAS web"

def arbitrage_ia(home, away, prono_natif, league, markets, prob=50):
    if not GROQ_KEY: return prono_natif, "Validation auto"
    web = recherche_web_contextuelle(home, away)
    prompt = f"SEHBA V23 FREE - Match {home} vs {away} - {league} - Natif {prono_natif} {prob}% - Web {web} - Marches {markets} - Donne DECISION: [prono] | JUSTIF: [15 mots FR]"
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type":"application/json"}, json={"model":"openai/gpt-oss-120b","messages":[{"role":"user","content":prompt}],"temperature":0.2,"max_tokens":300}, timeout=30).json()
        ia = r['choices'][0]['message']['content'].strip()
        m = re.search(r"DECISION:\s*(.*)", ia, re.IGNORECASE)
        final = m.group(1).split("|")[0].strip() if m else ia.split("\n")[0]
        return final, ia[:250]
    except: return prono_natif, "Defaut"

def choisir_7_obligatoires():
    global TRACKED_MATCHES
    today = get_today_maroua()
    fixtures = call_api(f"fixtures?date={today}")
    if not fixtures: return []
    filtered = [fx for fx in fixtures if fx['fixture']['status']['short']=='NS'][:15]
    scored=[]
    for fx in filtered:
        fid=fx['fixture']['id']
        pred_res=call_api(f"predictions?fixture={fid}")
        if not pred_res: continue
        pred=pred_res[0]
        try:
            pct=pred.get('predictions',{}).get('percent',{})
            conf=max(int(str(pct.get('home','0')).replace('%','') or 0), int(str(pct.get('draw','0')).replace('%','') or 0), int(str(pct.get('away','0')).replace('%','') or 0))
            scored.append({"fid":fid,"fixture":fx,"pred":pred,"conf":conf})
        except: continue
        if len(scored)>=7: break
        time.sleep(0.6)
    result=sorted(scored, key=lambda x: x['conf'], reverse=True)[:7]
    TRACKED_MATCHES=[]
    for m in result:
        TRACKED_MATCHES.append({"fid":m['fid'],"home":m['fixture']['teams']['home']['name'],"away":m['fixture']['teams']['away']['name'],"heure":m['fixture']['fixture']['date'],"statut":"ATTENTE","league":m['fixture']['league']['name']})
    save_tracked()
    return result

def analyser_et_envoyer(match_obj, index, chat_id=None):
    fx=match_obj['fixture']; pred=match_obj['pred']
    home=fx['teams']['home']['name']; away=fx['teams']['away']['name']
    try:
        dt=datetime.datetime.fromisoformat(fx['fixture']['date'].replace('Z','+00:00')).astimezone(MAROUA_TZ)
        heure_str=dt.strftime("%H:%M Maroua")
    except:
        heure_str=fx['fixture']['date']; dt=datetime.datetime.now(MAROUA_TZ)
    prono_natif=pred['predictions'].get('advice','Double Chance')
    pct=pred.get('predictions',{}).get('percent',{})
    try: prob=int(str(pct.get('home','50')).replace('%','') or 50)
    except: prob=50
    odds_dict, markets_text = get_all_pinnacle_odds(match_obj['fid'])
    odds_str=", ".join([f"{k}:{v}" for k,v in odds_dict.items()]) if odds_dict else markets_text[:200]
    prono_final, note_ia = arbitrage_ia(home, away, prono_natif, fx['league']['name'], markets_text, prob)
    msg = f"🔥 *TICKET V23 FREE {index}*\n⚽️ {home} vs {away} - {heure_str}\n🏆 {fx['league']['name']}\n\n📊 NATIF: {prono_natif} ({prob}%)\n🎯 *FINAL:* {prono_final}\n💰 {odds_str}\n🧠 {note_ia}"
    send_telegram(msg, chat_id)
    ecrire_dans_sheet(dt.strftime("%Y-%m-%d %H:%M"), f"{home} - {away}", fx['league']['name'], prono_final, odds_str, note_ia)

@app.route('/')
def home_route(): return "SEHBA V23 FREE - ACTIF"
@app.route('/ping')
def ping_route(): return jsonify({"status":"alive","version":"V23 FREE","tracked":len(TRACKED_MATCHES),"date":get_today_maroua()})
@app.route('/run')
def run_daily():
    today=get_today_maroua()
    try:
        with open(LAST_RUN_FILE,'r') as f:
            if f.read().strip()==today: return jsonify({"status":"Deja lance","date":today})
    except: pass
    load_tracked()
    matches=choisir_7_obligatoires()
    if not matches:
        send_telegram(f"ℹ️ V23 FREE: Aucun match pour {today}. API a renvoyé 0.")
        return jsonify({"error":"0 match","date":today})
    with open(LAST_RUN_FILE,'w') as f: f.write(today)
    msg=f"🚀 *V23 FREE - {len(TRACKED_MATCHES)} matchs SUIVI pour {today}:*\n"
    for m in TRACKED_MATCHES:
        dt=datetime.datetime.fromisoformat(m['heure'].replace('Z','+00:00')).astimezone(MAROUA_TZ)
        msg+=f"- {m['home']} vs {m['away']} à {dt.strftime('%H:%M')}\n"
    send_telegram(msg)
    return jsonify({"status":"OK","date":today})
@app.route('/check_and_analyze')
def check_and_analyze():
    load_tracked()
    if not TRACKED_MATCHES: return jsonify({"status":"vide"})
    now=datetime.datetime.now(MAROUA_TZ)
    fait=0
    for m in TRACKED_MATCHES:
        try:
            mt=datetime.datetime.fromisoformat(m['heure'].replace('Z','+00:00')).astimezone(MAROUA_TZ)
            diff=(mt-now).total_seconds()/60
            if m.get("statut")=="ATTENTE" and 30 <= diff <= 90:
                fx=call_api(f"fixtures?id={m['fid']}")
                pr=call_api(f"predictions?fixture={m['fid']}")
                if fx and pr:
                    analyser_et_envoyer({"fid":m['fid'],"fixture":fx[0],"pred":pr[0]}, f"J-{int(diff)}min")
                    m['statut']="ANALYSE"; fait+=1; time.sleep(3)
        except Exception as e: print(e)
    save_tracked()
    return jsonify({"lancees":fait})

@app.route('/webhook', methods=['POST'])
def webhook_route():
    data=request.get_json()
    if not data or 'message' not in data: return jsonify({"ok":True})
    raw=data['message'].get('text','').strip()
    chat_id=data['message']['chat']['id']
    if not raw.lower().startswith('/vs'): return jsonify({"ok":True})
    clean_raw=raw[3:].strip()
    clean=strip_accents(clean_raw)
    lower=clean.lower()
    if ' - ' in clean: sep=' - '
    elif ' vs ' in lower:
        idx=lower.find(' vs '); clean=clean[:idx]+' - '+clean[idx+4:]; sep=' - '
    elif ' v ' in f" {lower} ":
        idx=lower.find(' v '); clean=clean[:idx]+' - '+clean[idx+3:]; sep=' - '
    else: sep=None
    if not sep or ' - ' not in clean:
        send_telegram("Format: /vs Real - Leipzig", chat_id); return jsonify({"ok":True})
    h_q,a_q=[x.strip() for x in clean.split(' - ',1)]
    send_telegram(f"🔍 *V23 FREE: {h_q} vs {a_q} (scan date gratuit)...*", chat_id)
    target=None
    for days in [0,1]:
        date_check=(datetime.datetime.now(MAROUA_TZ).date()+datetime.timedelta(days=days)).isoformat()
        fixtures=call_api(f"fixtures?date={date_check}")
        print(f"V23 FREE scan {date_check}: {len(fixtures)} fixtures")
        for f in fixtures:
            hn=strip_accents(f['teams']['home']['name'].lower())
            an=strip_accents(f['teams']['away']['name'].lower())
            if (h_q.lower() in hn and a_q.lower() in an) or (h_q.lower() in an and a_q.lower() in hn) or (h_q.lower() in hn or a_q.lower() in an or h_q.lower() in an or a_q.lower() in hn):
                if h_q.lower() in hn or h_q.lower() in an:
                    if a_q.lower() in hn or a_q.lower() in an:
                        target=f; break
                if not target and (h_q.lower() in hn or h_q.lower() in an or a_q.lower() in hn or a_q.lower() in an):
                    target=f
        if target and (h_q.lower() in strip_accents(target['teams']['home']['name'].lower()) or h_q.lower() in strip_accents(target['teams']['away']['name'].lower())): 
            pass
        if target and target['teams']['home']['name'].lower().count(h_q.lower()) or True:
            pass
        if target: break
    # version simple finale
    if not target:
        for days in [0,1]:
            date_check=(datetime.datetime.now(MAROUA_TZ).date()+datetime.timedelta(days=days)).isoformat()
            fixtures=call_api(f"fixtures?date={date_check}")
            for f in fixtures:
                hn=strip_accents(f['teams']['home']['name'].lower()); an=strip_accents(f['teams']['away']['name'].lower())
                if h_q.lower()[:4] in hn or h_q.lower()[:4] in an:
                    if a_q.lower()[:4] in hn or a_q.lower()[:4] in an:
                        target=f; break
            if target: break
    if not target:
        send_telegram(f"❌ {h_q} vs {a_q} introuvable sur {get_today_maroua()}. Vérifie orthographe.", chat_id)
        return jsonify({"ok":True})
    fid=target['fixture']['id']
    pred=call_api(f"predictions?fixture={fid}")
    if not pred:
        send_telegram(f"⚠️ {target['teams']['home']['name']} vs {target['teams']['away']['name']} trouvé mais pas de prono API.", chat_id)
        return jsonify({"ok":True})
    analyser_et_envoyer({"fid":fid,"fixture":target,"pred":pred[0]}, "MANUEL V23 FREE", chat_id)
    return jsonify({"ok":True})

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
