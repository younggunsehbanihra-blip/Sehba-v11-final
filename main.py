import requests, json, os, datetime
from flask import Flask, request
from groq import Groq

GROQ_API_KEY = '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'
TELEGRAM_TOKEN = '8631670973:AAHqmUWGKKr1dNeNO6fLOXwfngjygClOEqA'
TELEGRAM_CHAT_ID = '8851728637'
ODDS_API_KEY = 'a466f43991f4602462ade9c258464e72'
FICHIER = "stats.json"
client = Groq(api_key=GROQ_API_KEY)
app = Flask(__name__)

def load():
    if os.path.exists(FICHIER):
        try:
            with open(FICHIER,"r") as f: return json.load(f)
        except: pass
    return {"gagnants":0,"perdants":0,"coupons_jour":0,"date":str(datetime.date.today())}
def save(d):
    with open(FICHIER,"w") as f: json.dump(d,f)
memo = load()

def tg(txt):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id":TELEGRAM_CHAT_ID,"text":txt,"parse_mode":"HTML"}, timeout=15)
    except: pass

def get_live_odds():
    try:
        url = f"https://api.the-odds-api.com/v4/sports/soccer_epl/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h&oddsFormat=decimal"
        r = requests.get(url, timeout=10).json()
        return str(r[:2])[:1000]
    except: return "Matchs du jour"

@app.route('/')
def home(): return "V11 CRON READY"

@app.route(f'/{TELEGRAM_TOKEN}', methods=['POST'])
def webhook():
    data = request.get_json()
    txt = data.get("message",{}).get("text","").lower()
    today = str(datetime.date.today())
    if memo.get("date")!= today:
        memo["date"]=today; memo["coupons_jour"]=0; save(memo)
    if "/stats" in txt:
        tg(f"📊 STATS {memo['gagnants']}/50 | Aujourd'hui: {memo['coupons_jour']}/5")
    elif "/win" in txt:
        memo['gagnants']+=1; save(memo); tg(f"✅ WIN! {memo['gagnants']}/50")
    elif "/lose" in txt:
        memo['perdants']+=1; save(memo); tg(f"❌ {memo['gagnants']}/{memo['gagnants']+memo['perdants']}")
    elif "/analyse" in txt or "/coupon" in txt:
        if memo['coupons_jour'] >= 5:
            tg(f"⏳ Limite 5/jour atteinte. {memo['gagnants']}/50")
        else:
            tg("🔍 Scan LIVE...")
            live = get_live_odds()
            prompt = f"Données LIVE: {live}. Genere 1 COUPON SNIPER cote 2.20-2.80 analyse courte"
            c = client.chat.completions.create(messages=[{"role":"user","content":prompt}], model="openai/gpt-oss-120b", temperature=0.3, max_tokens=1000)
            memo['coupons_jour']+=1; save(memo)
            tg(f"🎯 COUPON {memo['coupons_jour']}/5\n\n{c.choices[0].message.content}\n\n👉 /win ou /lose")
    return "ok"

if __name__ == "__main__":
    try: requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url=https://sehba-v11-final.onrender.com/{TELEGRAM_TOKEN}")
    except: pass
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
