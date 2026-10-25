import os, requests, datetime, time, unicodedata, json, re, math, random
from flask import Flask, request, jsonify
from datetime import timezone, timedelta
try:
    from tavily import TavilyClient
except:
    TavilyClient = None
try:
    import gspread
    from google.oauth2.service_account import Credentials
except:
    gspread = None

app = Flask(__name__)

APISPORTS_KEY = os.getenv("APISPORTS_KEY") or os.getenv("SPORTS_API_KEY")
GROQ_KEY = os.getenv("GROQ_API_KEY")
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELE_CHAT = os.getenv("TELEGRAM_CHAT_ID")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": APISPORTS_KEY} if APISPORTS_KEY else {}
MAROUA_TZ = timezone(timedelta(hours=1))

TRACKED_FILE = "/tmp/tracked_v25plus_final.json"
QUOTA_FILE = "/tmp/quota_v25plus_final.json"
TAVILY_CACHE_FILE = "/tmp/tavily_v25plus_final.json"
FIXTURE_CACHE_FILE = "/tmp/fixture_v25plus_final.json"
LAST_CALL_FILE = "/tmp/last_call_v25plus_final.json"
TRACKED_MATCHES = []

PRIORITY_LEAGUES = ["Premier League","La Liga","Serie A","Bundesliga","Ligue 1","Eredivisie","Primeira Liga","Championship","Champions League","Europa League"]

def get_today_maroua():
    return datetime.datetime.now(MAROUA_TZ).date().isoformat()

def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

def send_telegram(text, chat_id=None):
    cid = chat_id or TELE_CHAT
    if not TELE_TOKEN or not cid:
        print(text)
        return
    try:
        url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
        for chunk in [text[i:i+3900] for i in range(0, len(text), 3900)]:
            requests.post(url, json={"chat_id": cid, "text": chunk, "parse_mode": "Markdown"}, timeout=15)
    except:
        pass

def load_json(p,d):
    try:
        with open(p,'r') as f: return json.load(f)
    except: return d
def save_json(p,d):
    try:
        with open(p,'w') as f: json.dump(d,f)
    except: pass

def get_quota():
    q=load_json(QUOTA_FILE,{"used":0,"remaining":100,"date":get_today_maroua()})
    if q["date"]!=get_today_maroua():
        q={"used":0,"remaining":100,"date":get_today_maroua()}
        save_json(QUOTA_FILE,q)
    return q

def call_api_throttled(endpoint, min_interval=7, max_retry=3):
    q=get_quota()
    if q["remaining"]<=12:
        print(f"QUOTA STOP {q}"); return []
    last=load_json(LAST_CALL_FILE,{"t":0})
    diff=time.time()-last.get("t",0)
    if diff<min_interval: time.sleep(min_interval-diff)
    if not APISPORTS_KEY: return []
    for attempt in range(max_retry):
        try:
            res=requests.get(f"{BASE}/{endpoint}", headers=HEADERS, timeout=25)
            save_json(LAST_CALL_FILE,{"t":time.time()})
            try:
                rem=res.headers.get('x-ratelimit-requests-remaining')
                lim=res.headers.get('x-ratelimit-requests-limit')
                if rem is not None:
                    q["remaining"]=int(rem)
                    q["used"]=(int(lim) if lim else 100)-int(rem)
                    q["date"]=get_today_maroua()
                    save_json(QUOTA_FILE,q)
            except: pass
            if res.status_code==429:
                wait=15*(2**attempt)+random.uniform(0,3)
                print(f"429 sur {endpoint} - backoff {wait}s tentative {attempt+1}/{max_retry}")
                send_telegram(f"⚠️ 429 API {endpoint} - pause {int(wait)}s (tentative {attempt+1})",None)
                time.sleep(wait)
                continue
            j=res.json()
            if j.get('errors'):
                print(f"API ERR {endpoint} {j.get('errors')}")
                return []
            return j.get('response',[])
        except Exception as e:
            print(f"call_api {e} attempt {attempt}")
            time.sleep(2+attempt*2)
    return []

def get_fixtures_cached():
    today=get_today_maroua()
    cache=load_json(FIXTURE_CACHE_FILE,{})
    if cache.get("date")==today and cache.get("data"): return cache["data"]
    data=call_api_throttled(f"fixtures?date={today}",7)
    if data: save_json(FIXTURE_CACHE_FILE,{"date":today,"data":data})
    return data

def get_all_pinnacle_odds_full(fid):
    odds=call_api_throttled(f"odds?fixture={fid}",7)
    if not odds: return {}, "Pinnacle scan"
    try:
        for fx in odds:
            for book in fx.get('bookmakers',[]):
                if 'Pinnacle' in book['name']:
                    od={}; full_txt=""
                    for bet in book.get('bets',[]):
                        name=bet.get('name',''); vals=[]
                        for v in bet.get('values',[]):
                            try:
                                odd_val=float(v['odd'])
                                od[f"{name}:{v['value']}"]=odd_val
                                od[v['value']]=odd_val
                                od[f"{name} {v['value']}"]=odd_val
                                if name=='Match Winner':
                                    if 'Home' in v['value']: od['Home']=odd_val
                                    if 'Draw' in v['value']: od['Draw']=odd_val
                                    if 'Away' in v['value']: od['Away']=odd_val
                                vals.append(f"{v['value']}@{v['odd']}")
                            except: pass
                        if vals: full_txt+=f"{name}: {' | '.join(vals)} || "
                    return od, full_txt[:7000]
    except: pass
    return {}, "Pinnacle non dispo"

def get_lineups_compo(fid):
    lin=call_api_throttled(f"fixtures/lineups?fixture={fid}",7)
    if not lin: return ""
    try:
        txt="COMPOS OFFICIELLES: "
        for team in lin:
            tname=team['team']['name']
            starters=[p['player']['name'] for p in team.get('start XI',[])[:5]]
            txt+=f"{tname} {','.join(starters)} | "
        return txt[:800]
    except:
        return ""
