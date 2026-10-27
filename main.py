# main.py V26 FINAL - COMPATIBLE ENV CAPTURE JOSMAN
import os, json, datetime, logging, requests, time
import numpy as np
from scipy.stats import poisson
from flask import Flask, request, jsonify
import gspread
from google.oauth2.service_account import Credentials
from groq import Groq

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# --- LECTURE ENV EXACT DE TA CAPTURE ---
FOOTBALL_KEY = os.getenv("FOOTBALL_DATA_KEY")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS_RAW = os.getenv("GOOGLE_CREDENTIALS_ID")
GROQ_KEY = os.getenv("GROQ_API_KEY")
ODDS_KEY = os.getenv("ODDS_API_KEY")
TAVILY_KEY = os.getenv("TAVILY_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

groq_client = Groq(api_key=GROQ_KEY) if GROQ_KEY else None
GPT_MODEL = "openai/gpt-oss-120b"

def ask_gpt_oss_120b(prompt):
    if not groq_client: return "IA Groq non configurée - fallback moteur physique."
    try:
        chat = groq_client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": "Tu es OneXpect V26. Analyse chirurgicale: derby, relégation, finale, fatigue, absences. Donne ticket 1X/X2/Over1.5 + fiabilité >84% + scénario 2 lignes."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2, max_tokens=600
        )
        return chat.choices[0].message.content
    except Exception as e:
        logging.error(f"GROQ ERR {e}")
        return f"Fallback moteur - IA erreur: {e}"

# VAULT LOCAL
class Vault:
    def __init__(self, d="predictive_vault"):
        self.d=d; os.makedirs(d, exist_ok=True)
    def read(self,k):
        fp=os.path.join(self.d,f"{k}.json")
        if not os.path.exists(fp): return None
        try:
            with open(fp,'r') as f: return json.load(f)
        except: return None
    def write(self,k,data):
        fp=os.path.join(self.d,f"{k}.json")
        with open(f"{fp}.tmp",'w') as f: json.dump(data,f)
        os.replace(f"{fp}.tmp", fp)
vault=Vault()

# SHEET LOG - compatible avec ton GOOGLE_CREDENTIALS_ID qui contient le JSON
def log_to_sheet(row):
    try:
        if not GOOGLE_SHEET_ID or not GOOGLE_CREDS_RAW: 
            logging.warning("SHEET ID ou CREDS manquant")
            return
        # GOOGLE_CREDENTIALS_ID doit contenir {"type":"service_account"...}
        creds_dict = json.loads(GOOGLE_CREDS_RAW)
        creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        sh.sheet1.append_row(row)
        logging.info(f"SHEET OK {row[1]} vs {row[2]}")
    except Exception as e:
        logging.error(f"SHEET FAIL {e}")

# MOTEUR NATIF rho=-0.06
class NativePredictiveEngine:
    def _dc(self,x,y,l1,l2,rho=-0.06):
        if x==0 and y==0: return 1 - (l1*l2*rho)
        if x==1 and y==0: return 1 + (l2*rho)
        if x==0 and y==1: return 1 + (l1*rho)
        if x==1 and y==1: return 1 - rho
        return 1.0
    def calculate(self, home, away, base, context, league_type="DOMESTIC_DIV_2"):
        ha=base.get('home_attack',1.5); aa=base.get('away_attack',1.1)
        hd=base.get('home_defence',1.0); ad=base.get('away_defence',1.15)
        val_mult=1.35 if league_type in ["DOMESTIC_DIV_2","BRAZIL_SAUDI"] else 1.0
        flam=ha*ad*1.14*val_mult; fmu=aa*hd
        flam*= (1-min(context.get('home_missing',0),0.40))
        fmu*= (1-min(context.get('away_missing',0),0.40))
        if context.get('fatigue')==2: flam*=0.75; fmu*=0.75
        pressure=context.get('pressure','STANDARD')
        if pressure=="DERBY": flam*=0.90; fmu*=0.90
        if pressure=="RELEGATION": flam*=1.15; fmu*=1.10
        if pressure=="FINALE": flam*=0.82; fmu*=0.82
        max_g=6; mat=np.zeros((max_g+1,max_g+1))
        for x in range(max_g+1):
            for y in range(max_g+1):
                mat[x,y]=poisson.pmf(x,flam)*poisson.pmf(y,fmu)*self._dc(x,y,flam,fmu)
        mat/=np.sum(mat)
        p1=float(np.sum(np.tril(mat,-1))); pn=float(np.diag(mat).sum()); p2=float(np.sum(np.triu(mat,1)))
        p1X=(p1+pn)*100; pX2=(p2+pn)*100; po15=(1-(mat[0,0]+mat[1,0]+mat[0,1]))*100
        return {"flam":flam,"fmu":fmu,"p1X":p1X,"pX2":pX2,"o15":po15,"mat":mat,"pressure":pressure}

engine=NativePredictiveEngine()

def safe_tavily(home,away):
    try:
        if not TAVILY_KEY: return {"home_missing":0,"away_missing":0,"fatigue":6,"pressure":"STANDARD","raw":"no key"}
        r=requests.post("https://api.tavily.com/search", json={"api_key":TAVILY_KEY,"query":f"{home} vs {away} composition blessures derby finale relégation","max_results":3}, timeout=10)
        txt=str(r.json()).lower()
        pressure="DERBY" if "derby" in txt else "RELEGATION" if "releg" in txt else "FINALE" if "finale" in txt or "final" in txt else "STANDARD"
        miss=0.15 if any(k in txt for k in ["blessé","forfait","absent","suspended"]) else 0
        fat=2 if "72h" in txt or "3 jours" in txt else 6
        return {"home_missing":miss,"away_missing":0,"fatigue":fat,"pressure":pressure,"raw":txt[:500]}
    except Exception as e:
        logging.error(f"TAVILY {e}")
        return {"home_missing":0,"away_missing":0,"fatigue":6,"pressure":"STANDARD","raw":"error"}

def send_telegram(msg, chat_id=None):
    try:
        cid=chat_id or TELEGRAM_CHAT_ID
        if not TELEGRAM_TOKEN or not cid: logging.info(f"TG SKIP {msg[:50]}"); return
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id":cid,"text":msg,"parse_mode":"Markdown"}, timeout=15)
    except Exception as e: logging.error(f"TG {e}")

@app.route('/')
def home(): return jsonify({"status":"V26 FINAL ONLINE","model":GPT_MODEL,"env_keys":"FOOTBALL_DATA_KEY, GOOGLE_CREDENTIALS_ID, GOOGLE_SHEET_ID, GROQ_API_KEY, ODDS_API_KEY, TAVILY_API_KEY, TELEGRAM_TOKEN"})

@app.route('/cron-daily', methods=['GET','POST'])
def cron():
    matches=[
        {"home":"Leeds","away":"Sunderland","league":"Championship","type":"DOMESTIC_DIV_2"},
        {"home":"Flamengo","away":"Palmeiras","league":"Brazil Serie A","type":"BRAZIL_SAUDI"},
        {"home":"Fenerbahce","away":"Galatasaray","league":"Süper Lig","type":"BRAZIL_SAUDI"},
        {"home":"Al Hilal","away":"Al Nassr","league":"Saudi Pro League","type":"BRAZIL_SAUDI"},
        {"home":"PSV","away":"Ajax","league":"Eredivisie","type":"TRAP"},
        {"home":"Empoli","away":"Lecce","league":"Serie A","type":"TRAP"},
    ]
    top=[]
    for m in matches:
        base=vault.read(f"team_{m['home']}") or {"home_attack":1.5,"away_attack":1.1,"home_defence":1.0,"away_defence":1.15}
        ctx=safe_tavily(m['home'],m['away'])
        res=engine.calculate(m['home'],m['away'],base,ctx,league_type=m['type'])
        best=max(res['p1X'],res['pX2'],res['o15'])
        if best < 84: continue
        prompt=f"Match {m['home']} vs {m['away']} ({m['league']}) xG {res['flam']:.2f}-{res['fmu']:.2f} Proba 1X {res['p1X']:.1f}% X2 {res['pX2']:.1f}% O1.5 {res['o15']:.1f}% Pressure {res['pressure']} Contexte {ctx['raw']}. Donne TICKET | SCENARIO | FIABILITE"
        ia=ask_gpt_oss_120b(prompt)
        msg=f"🎫 *TICKET V26 IA - {m['league']}* 🎫\n⚽ {m['home']} vs {m['away']}\n📊 xG {res['flam']:.2f}-{res['fmu']:.2f} | {res['pressure']}\n🤖 *{GPT_MODEL}:*\n{ia}\n━━━━━━━━━━━━"
        top.append(msg)
        log_to_sheet([datetime.datetime.now().isoformat(), m['home'], m['away'], m['league'], f"{res['flam']:.2f}-{res['fmu']:.2f}", f"{best:.1f}%", ia[:150]])
    if top:
        send_telegram("🎯 *TOP 5 V26 FINAL - MIX D2/BRÉSIL/SAUDI/GROQ* 🎯\n\n" + "\n\n".join(top[:5]))
    return jsonify({"status":"ok","count":len(top)}),200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data=request.get_json(force=True,silent=True) or {}
        if 'message' not in data: return jsonify({"ok":True}),200
        text=data['message'].get('text',''); chat_id=str(data['message']['chat']['id'])
        if "vs" in text.lower():
            h,a=[x.strip().title() for x in text.lower().split("vs",1)]
            base=vault.read(f"team_{h}") or {"home_attack":1.5,"away_attack":1.1,"home_defence":1.0,"away_defence":1.15}
            ctx=safe_tavily(h,a)
            res=engine.calculate(h,a,base,ctx,league_type="TRAP")
            prompt=f"Manuel {h} vs {a} xG {res['flam']:.2f}-{res['fmu']:.2f} 1X {res['p1X']:.1f}% X2 {res['pX2']:.1f}% O1.5 {res['o15']:.1f}% Pressure {res['pressure']} Raw {ctx['raw']}. Ticket final + scenario 2 lignes."
            ia=ask_gpt_oss_120b(prompt)
            msg=f"🎫 *MANUEL V26*\n⚽ {h} vs {a}\n📊 xG {res['flam']:.2f}-{res['fmu']:.2f}\n🤖 {ia}"
            send_telegram(msg, chat_id)
            log_to_sheet([datetime.datetime.now().isoformat(), h, a, "MANUAL", f"{res['flam']:.2f}-{res['fmu']:.2f}", f"{max(res['p1X'],res['pX2'],res['o15']):.1f}%", ia[:150]])
        else:
            send_telegram("Envoie `Equipe vs Equipe` ex: `Flamengo vs Palmeiras`", chat_id)
        return jsonify({"ok":True}),200
    except Exception as e:
        logging.error(e); return jsonify({"ok":True}),200

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",10000)))
