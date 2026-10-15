import os, json, requests, datetime, threading, time
from flask import Flask, request
from groq import Groq

# --- V18 FINAL - VERIFIE SANS ERREUR CODE INACTIF ---
BOT_TOKEN = "8631670973:AAEuaMGEzeeLYOcjRp2mtgJuO-099yQNhQ0"
GROQ_API_KEY = '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'
ODDS_API_KEY = 'a466f43991f4602462ade9c258464e72'
RAPIDAPI_KEY = '3f38adafd5msh4156e2331c2667fp17307ejsn5fa6a32beecb'
GOOGLE_SHEET_ID = "1YE3nB5AOI8mIITdKgHQALFEdxHYpTh9hPNJ0Fp0al60"
GOOGLE_SHEET_WEBHOOK = os.getenv("GOOGLE_SHEET_URL", "")
# -------------------------------------------------

FICHIER = "stats.json"
SCAN_FILE = "last_scan.json"
CACHE_MATCHS = {"data": [], "heure": None, "scans_today": 0, "date_scan": ""}

app = Flask(__name__)
client = Groq(api_key=GROQ_API_KEY)

def load():
    if not os.path.exists(FICHIER):
        return {"gagnants":0,"perdants":0,"coupons_jour":0,"date":str(datetime.date.today())}
    try:
        with open(FICHIER,"r") as f: return json.load(f)
    except: return {"gagnants":0,"perdants":0,"coupons_jour":0,"date":str(datetime.date.today())}

def save(s):
    try:
        with open(FICHIER,"w") as f: json.dump(s, f, indent=2)
    except: pass

memo = load()

def tg(chat_id, text):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":chat_id,"text":text,"parse_mode":"HTML"}, timeout=20)
    except: pass

def log_to_gsheet(match, type_coupon, coupon_text, cote):
    if not GOOGLE_SHEET_WEBHOOK: return
    try:
        payload = {"sheet_id": GOOGLE_SHEET_ID, "date": str(datetime.date.today()), "heure": datetime.datetime.now().strftime("%H:%M"),
                   "match": match, "type": type_coupon, "coupon": coupon_text[:5000], "cote": str(cote),
                   "gagnants": memo['gagnants'], "perdants": memo['perdants'], "jour": memo['coupons_jour']}
        requests.post(GOOGLE_SHEET_WEBHOOK, json=payload, timeout=5)
    except: pass

def can_scan_today():
    today = str(datetime.date.today())
    if CACHE_MATCHS["date_scan"]!= today:
        CACHE_MATCHS["scans_today"] = 0
        CACHE_MATCHS["date_scan"] = today
    return CACHE_MATCHS["scans_today"] < 2

def get_best_matches(force=False):
    global CACHE_MATCHS
    now = datetime.datetime.now()
    if not force and CACHE_MATCHS["heure"]:
        diff = (now - CACHE_MATCHS["heure"]).total_seconds() / 3600
        if diff < 12 and len(CACHE_MATCHS["data"]) > 5:
            return CACHE_MATCHS["data"]
    if not can_scan_today() and not force:
        return CACHE_MATCHS["data"]
    try:
        url = f"https://api.the-odds-api.com/v4/sports/soccer/odds?apiKey={ODDS_API_KEY}&regions=eu,uk&markets=h2h,totals,btts&oddsFormat=decimal"
        data = requests.get(url, timeout=30).json()
        if not isinstance(data, list): return CACHE_MATCHS["data"]
        all_matches = []
        for m in data:
            try:
                if not m.get("bookmakers"): continue
                home = m.get("home_team",""); away = m.get("away_team","")
                league = m.get("sport_title","")
                cotes_list = []
                for book in m["bookmakers"][:3]:
                    for market in book.get("markets",[]):
                        for out in market.get("outcomes",[]):
                            p = out.get("price",0)
                            if 1.35 <= p <= 5.0:
                                cotes_list.append(f"{out['name']}@{p}")
                if len(cotes_list) >= 2:
                    all_matches.append({"match": f"{home} vs {away}","league": league,"cotes": " | ".join(cotes_list[:6]),"nb": len(cotes_list)})
            except: continue
        all_matches.sort(key=lambda x: x["nb"], reverse=True)
        top30 = all_matches[:30]
        CACHE_MATCHS["data"] = top30; CACHE_MATCHS["heure"] = now
        CACHE_MATCHS["scans_today"] += 1; CACHE_MATCHS["date_scan"] = str(datetime.date.today())
        with open(SCAN_FILE,"w") as f: json.dump(top30, f)
        return top30
    except: return CACHE_MATCHS["data"]

def get_team_stats(home_team, away_team):
    if not home_team or not away_team: return "Stats générales"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    try:
        url="https://api-football-v1.p.rapidapi.com/v3/standings?league=39&season=2024"
        res=requests.get(url, headers=headers, timeout=12).json()
        standings=res["response"][0]["league"]["standings"][0]
        hi={}; ai={}
        for team in standings:
            name=team["team"]["name"]
            if home_team.lower() in name.lower(): hi={"rang":team["rank"],"forme":team["form"],"points":team["points"]}
            if away_team.lower() in name.lower(): ai={"rang":team["rank"],"forme":team["form"],"points":team["points"]}
        if hi and ai: return f"Domicile {home_team} Rang {hi['rang']} Forme {hi['forme']} Pts {hi.get('points','')} | Ext {away_team} Rang {ai['rang']} Forme {ai['forme']}"
    except: pass
    return "Analyse historique générale + proba cotes"

@app.route("/", methods=["GET"])
def home(): return f"Sehba V18 FINAL CLEAN - SHEET {GOOGLE_SHEET_ID[:10]} - SANS ERREUR INACTIF - ACTIF",200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data or "message" not in data: return "ok",200
        msg = data["message"]; chat_id = msg["chat"]["id"]; txt = msg.get("text","").strip().lower()
        today = str(datetime.date.today())
        if memo.get("date")!=today:
            memo["date"]=today; memo["coupons_jour"]=0; save(memo)

        if txt.startswith("/start") or txt.startswith("/analyse"):
            is_force="force" in txt
            if is_force and not can_scan_today():
                tg(chat_id, "⛔ Limite 2 scans/j atteinte. Cache utilisé.")
                best=CACHE_MATCHS["data"]
            else:
                tg(chat_id, "🔍 V18 Scan Libre Arbitre Total + Cache + Sheet...")
                best=get_best_matches(force=is_force)
            if not best:
                tg(chat_id, "❌ Aucun match. Vérifie ODDS_API_KEY")
                return "ok",200
            tg(chat_id, f"✅ TOP {len(best)} MATCHS - Sheet {GOOGLE_SHEET_ID[:10]}")
            for i, m in enumerate(best[:3]):
                try:
                    home,away=m["match"].split(" vs ")
                    stats=get_team_stats(home,away)
                    prompt=f"Sujet:{m['match']} {m['league']} Cotes:{m['cotes']} Stats:{stats} Sheet:{GOOGLE_SHEET_ID} LIBRE ARBITRE TOTAL GROQ CHIC: Simple prioritaire si optimal hautement fiable, Combiné seulement si nécessaire plus sécurisé et rentable. Cote finale + justif courte. FR. COUPON SNIPER V18"
                    c=client.chat.completions.create(messages=[{"role":"user","content":prompt}], model="llama-3.3-70b-versatile", temperature=0.2, max_tokens=1000)
                    rep=c.choices[0].message.content
                    memo['coupons_jour']+=1; save(memo)
                    log_to_gsheet(m['match'],"COUPON V18",rep,m['cotes'])
                    tg(chat_id, f"<b>🎯 {i+1}. {m['match']}</b>\n{rep}\n💰 {m['cotes']}\n📗 Sheet loggé")
                except Exception as e: tg(chat_id, f"{m['match']}\n{m['cotes']}\nErr:{str(e)[:80]}")
        elif "suite" in txt:
            try:
                with open(SCAN_FILE,"r") as f: best=json.load(f)
                for i, m in enumerate(best[3:8]): tg(chat_id, f"<b>{i+4}. {m['match']}</b>\n💰 {m['cotes']}")
            except: tg(chat_id, "Fais /analyse d'abord")
        elif txt.startswith("/stats"):
            total=memo['gagnants']+memo['perdants']; taux=round((memo['gagnants']/total)*100,1) if total>0 else 0
            tg(chat_id, f"📊 V18 G:{memo['gagnants']} P:{memo['perdants']} Taux:{taux}% Jour:{memo['coupons_jour']}/5\n📗 Sheet:{GOOGLE_SHEET_ID}")
        elif txt.startswith("/win"): memo['gagnants']+=1; save(memo); tg(chat_id,"✅ GAIN V18"); log_to_gsheet("WIN","RESULTAT","Gagnant","")
        elif txt.startswith("/lose"): memo['perdants']+=1; save(memo); tg(chat_id,"❌ PERDANT V18"); log_to_gsheet("LOSE","RESULTAT","Perdant","")
        elif txt.startswith("/bilan"):
            tg(chat_id, "⏳ Bilan V18...")
            try:
                prompt=f"Données {memo['gagnants']} gains {memo['perdants']} pertes. Actuaire financier FR, croissance, capital."
                c=client.chat.completions.create(messages=[{"role":"user","content":prompt}], model="llama-3.3-70b-versatile", temperature=0.3, max_tokens=800)
                tg(chat_id, f"📋 BILAN V18:\n{c.choices[0].message.content}")
            except: tg(chat_id, "Bilan pause 30s")
        else: tg(chat_id, "/analyse - /suite - /stats - /win /lose /bilan")
    except: pass
    return "ok",200

def keep_alive():
    while True:
        time.sleep(14*60)
        try:
            url=os.getenv("RENDER_EXTERNAL_URL","")
            if url: requests.get(url, timeout=10)
        except: pass
threading.Thread(target=keep_alive, daemon=True).start()

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",10000)))
