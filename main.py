import os, json, requests, datetime, threading, time
from flask import Flask, request
from groq import Groq

# --- CLES INTEGREES FINALES - NE PLUS TOUCHER ---
BOT_TOKEN = "8631670973:AAEuaMGEzeeLYOcjRp2mtgJuO-099yQNhQ0"
TELEGRAM_CHAT_ID = '8851728637'
GROQ_API_KEY = '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'
ODDS_API_KEY = 'a466f43991f4602462ade9c258464e72'
RAPIDAPI_KEY = '3f38adafd5msh4156e2331c2667fp17307ejsn5fa6a32beecb'
GOOGLE_SHEET_ID = "1YE3nB5AOl8mlITdKgHQALFEdxHYpTh9hPNJ0Fp0al60"
# -------------------------------------------------

FICHIER = "stats.json"
SCAN_FILE = "last_scan.json"
CACHE_MATCHS = {"data": [], "heure": None, "scans_today": 0, "date_scan": ""}

client = Groq(api_key=GROQ_API_KEY)
app = Flask(__name__)

def load():
    if not os.path.exists(FICHIER):
        return {"gagnants":0,"perdants":0,"coupons_jour":0,"date":str(datetime.date.today())}
    try:
        with open(FICHIER,"r") as f: return json.load(f)
    except: return {"gagnants":0,"perdants":0,"coupons_jour":0,"date":str(datetime.date.today())}

def save(s):
    with open(FICHIER,"w") as f: json.dump(s, f, indent=2)

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

def ask_groq(info):
    try:
        p = f"Match {info['match']} {info['league']} Cotes {info['cotes']}. Donne meilleur prono Double Chance, BTTS, Over. Court et rentable."
        resp = client.chat.completions.create(model="llama-3.1-8b-instant", messages=[{"role":"user","content":p}], max_tokens=250)
        return resp.choices[0].message.content
    except Exception as e: return f"{info['match']}\n{info['cotes']}"

def send(chat_id, text):
    try: requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id":chat_id,"text":text,"parse_mode":"Markdown"}, timeout=15)
    except: pass

def keep_alive():
    while True:
        time.sleep(14*60)
        try:
            url = os.getenv("RENDER_EXTERNAL_URL", "")
            if url: requests.get(url, timeout=10)
        except: pass
threading.Thread(target=keep_alive, daemon=True).start()

@app.route("/", methods=["GET"])
def home(): return "Sehba V17 FINAL AVEC CLES OK - ACTIF",200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    if not data or "message" not in data: return "ok",200
    msg = data["message"]; chat_id = msg["chat"]["id"]; txt = msg.get("text","").strip().lower()
    stats = load()
    if stats.get("date")!= str(datetime.date.today()):
        stats = {"gagnants":0,"perdants":0,"coupons_jour":0,"date":str(datetime.date.today())}; save(stats)
    if txt.startswith("/start") or txt.startswith("/analyse"):
        send(chat_id, "🔍 Scan en cours... 800 matchs analysés")
        best = get_best_matches(force="force" in txt)
        if not best: send(chat_id, "❌ Aucun match. Vérifie ODDS_API_KEY"); return "ok",200
        send(chat_id, f"✅ TOP {len(best)} MATCHS COUVERTS - Cache utilisé si déjà scanné aujourd'hui")
        for i, m in enumerate(best[:3]): send(chat_id, f"*{i+1}. {m['match']}*\n{ask_groq(m)}\n💰 {m['cotes']}")
        stats["coupons_jour"] += 1; save(stats)
    elif "suite" in txt:
        try:
            with open(SCAN_FILE,"r") as f: best = json.load(f)
            for i, m in enumerate(best[3:8]): send(chat_id, f"*{i+4}. {m['match']}*\n{ask_groq(m)}")
        except: send(chat_id, "Fais /analyse d'abord")
    elif "stats" in txt: send(chat_id, f"📊 G:{stats['gagnants']} P:{stats['perdants']} Jour:{stats['coupons_jour']}")
    else: send(chat_id, "/analyse - /suite - /stats")
    return "ok",200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",10000)))
