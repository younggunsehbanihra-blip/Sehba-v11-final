import os
import json
import requests
import datetime
import threading
import time
from flask import Flask, request
from groq import Groq

# Configuration avec tes clés d'origine - CORRIGE SEULEMENT SHEET ID
GROQ_API_KEY = "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"
TELEGRAM_TOKEN = "8631670973:AAHqmUWGKKr1dNeNO6fLOXwfngjygClOEqA"
TELEGRAM_CHAT_ID = "8851728637"
ODDS_API_KEY = "a466f43991f4602462ade9c258464e72"
RAPIDAPI_KEY = "3f38adafd5msh4156e2331c2667fp17307ejsn5fa6a32beecb"
GOOGLE_SHEET_ID = "1YE3nB5AOI8mIITdKgHQALFEdxHYpTh9hPNJ0Fp0al60" # CORRIGE AOl8ml -> AOI8mII

FICHIER_STATS = "/tmp/stats.json"
app = Flask(__name__)

def local_load():
    if os.path.exists(FICHIER_STATS):
        try:
            with open(FICHIER_STATS, "r") as f: return json.load(f)
        except Exception: pass
    return {"gagnants": 0, "perdants": 0, "coupons_jour": 0, "date": str(datetime.date.today())}

def local_save(d):
    try:
        with open(FICHIER_STATS, "w") as f: json.dump(d, f, indent=2)
    except Exception: pass

def log_to_google_sheet(status, detail="Pari Enregistré"):
    try:
        url_macro = os.getenv("GOOGLE_SHEET_URL", "https://google.com")
        payload = {"spreadsheet_id": GOOGLE_SHEET_ID, "date": str(datetime.date.today()), "statut": status, "details": detail}
        requests.post(url_macro, json=payload, timeout=10)
    except Exception: pass

def tg(txt):
    try:
        # URL CORRIGEE 1 : api.telegram.org/bot
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": txt, "parse_mode": "HTML"}, timeout=15)
    except Exception: pass

def web_search(query):
    """Recherche web intégrée gratuite via DuckDuckGo Lite - TON CODE ORIGINAL 100%"""
    try:
        # URL CORRIGEE 2
        url = f"https://lite.duckduckgo.com/lite/?q={requests.utils.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, 'html.parser')
            snippets = [t.text.strip() for t in soup.find_all('a', class_='result__snippet')[:3]]
            return " | ".join(snippets) if snippets else "Pas d'infos web."
    except Exception: pass
    return "Aucune info web supplémentaire trouvée."

def get_live_odds():
    try:
        # URL CORRIGEE 3
        url = f"https://api.the-odds-api.com/v4/sports/soccer/odds?apiKey={ODDS_API_KEY}&regions=eu,uk&markets=h2h,totals,btts&oddsFormat=decimal"
        r = requests.get(url, timeout=15).json()
        matchs_valides = []
        now = datetime.datetime.now(datetime.timezone.utc)
        if isinstance(r, list):
            for m in r:
                c_str = m.get("commence_time")
                if not c_str: continue
                c_time = datetime.datetime.strptime(c_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                ecart = (c_time - now).total_seconds() / 60
                if 0 <= ecart <= 45:
                    c_det = []
                    if m.get("bookmakers"):
                        for bk in m["bookmakers"][:2]:
                            for mk in bk.get("markets", []):
                                out = mk.get("outcomes", [])
                                details = ", ".join([f"{o['name']}: {o['price']}" for o in out])
                                c_det.append(f"[{bk.get('title')} - {mk.get('key').upper()}] {details}")
                    matchs_valides.append({
                        "home": m.get("home_team"),
                        "away": m.get("away_team"),
                        "league": m.get("sport_title"),
                        "cotes": " | ".join(c_det[:3])
                    })
            return matchs_valides
    except Exception: pass
    return []

def get_team_stats(home_team, away_team):
    """LIVRAISON TOTALE : Envoie l'intégralité absolue du JSON de prédiction de l'API à l'IA - TON CODE ORIGINAL"""
    if not home_team or home_team == "Équipe A": return "Données non disponibles."
    # URL CORRIGEE 4
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    try:
        today_str = datetime.date.today().isoformat()
        url_fix = f"https://api-football-v1.p.rapidapi.com/v3/fixtures?date={today_str}"
        res_fix = requests.get(url_fix, headers=headers, timeout=10).json()

        fixture_id = None
        if "response" in res_fix:
            for fix in res_fix["response"]:
                h_name = fix["teams"]["home"]["name"]
                a_name = fix["teams"]["away"]["name"]
                if home_team.lower() in h_name.lower() or away_team.lower() in a_name.lower():
                    fixture_id = fix["fixture"]["id"]
                    break

        if fixture_id:
            url_pred = f"https://api-football-v1.p.rapidapi.com/v3/predictions?fixture={fixture_id}"
            res_pred = requests.get(url_pred, headers=headers, timeout=10).json()

            if "response" in res_pred and len(res_pred["response"]) > 0:
                donnies_brutes = json.dumps(res_pred["response"][0], ensure_ascii=False)
                return donnies_brutes

    except Exception: pass
    return "Analyse algorithmique standard activée."

def keep_alive():
    while True:
        time.sleep(14 * 60)
        try:
            u = os.getenv("RENDER_EXTERNAL_URL", "")
            if u: requests.get(u, timeout=10)
        except Exception: pass

threading.Thread(target=keep_alive, daemon=True).start()

@app.route('/')
def home_page(): return "SERVEUR SEHBA V19 OPERATIONNEL", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    data = request.get_json(force=True, silent=True)
    if not data or "message" not in data: return "ok", 200
    msg = data.get("message", {})
    txt = msg.get("text", "").strip().lower()
    if not txt: return "ok", 200

    memo = local_load()
    today = str(datetime.date.today())
    if memo.get("date")!= today:
        memo["date"] = today; memo["coupons_jour"] = 0; local_save(memo)

    if txt == "/stats":
        tot = memo['gagnants'] + memo['perdants']
        tx = round((memo['gagnants']/tot)*100, 1) if tot > 0 else 0
        tg(f"📊 <b>BILAN</b>\n✅ G: {memo['gagnants']}\n❌ P: {memo['perdants']}\n📈 Réussite: {tx}%\n🔄 {tot}/50\n📆 Jour: {memo['coupons_jour']}/5")

    elif txt == "/win":
        memo['gagnants'] += 1; local_save(memo); log_to_google_sheet("GAIN"); tg("✅ OPTION VALIDÉE EN GAIN!")

    elif txt == "/lose":
        memo['perdants'] += 1; local_save(memo); log_to_google_sheet("PERTE"); tg("❌ OPTION ENREGISTRÉE EN PERTE.")

    elif txt == "/bilan":
        try:
            c = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                messages=[{"role":"user","content":f"G:{memo['gagnants']}, P:{memo['perdants']}. Actuaire financier. Note concise en français capital/sécurité."}],
                model="llama-3.3-70b-versatile",
                max_tokens=250
            )
            # CORRIGE 5 : choices[0]
            tg(f"📋 <b>FINANCES :</b>\n\n{c.choices[0].message.content}")
        except Exception: pass

    elif txt in ["/analyse", "/coupon"]:
        if memo['coupons_jour'] >= 5:
            tg("⏳ Limite de 5 coupons atteinte.")
        else:
            m_dispo = get_live_odds()
            if not m_dispo:
                tg("📭 Aucun match disponible débutant dans les 45 minutes.")
                return "ok", 200
            mat = []
            for m in m_dispo[:2]:
                det = get_team_stats(m["home"], m["away"])
                news = web_search(f"{m['home']} vs {m['away']} team news injury sports analytics")
                mat.append(f"Ligue: {m['league']} | {m['home']} vs {m['away']}\nCotes: {m['cotes']}\nDonnées Brutes RapidAPI Football: {det}\nInfos Web Direct: {news}")

            prmt = f"CONTEXTE ANALYTIQUE COMPLET H-45 :\n{chr(10).join(mat)}\n\nAgis en système expert, actuaire et mathématicien des marchés de paris sportifs. Tu as accès à l'intégralité brute de la réponse JSON de l'API de football (incluant toutes les métriques de probabilités, comparaisons détaillées d'attaque/défense, et l'historique mathématique complet) ainsi qu'aux rumeurs du web. Analyse TOUT sans exception. Donne le pari combiné principal ultra-sécurisé ET TOUTES LES AUTRES OPTIONS RENTABLES/ALTERNATIVES (Double chance, BTTS, Totaux alternatifs style 1xBet). Preuve mathématique / probabiliste courte. Titre: COUPON SNIPER MULTI-OPTIONS. En français."
            try:
                c = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                    messages=[{"role":"user","content":prmt}],
                    model="llama-3.3-70b-versatile",
                    temperature=0.1,
                    max_tokens=950
                )
                memo['coupons_jour'] += 1; local_save(memo)
                # CORRIGE 5 : choices[0]
                tg(f"<b>🎯 COUPON COMBINÉ & MULTI-OPTIONS {memo['coupons_jour']}/5</b>\n\n{c.choices[0].message.content}")
            except Exception: pass
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    if os.getenv("RENDER_EXTERNAL_URL"):
        try:
            # URL CORRIGEE 1
            webhook_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={os.getenv('RENDER_EXTERNAL_URL').rstrip('/')}/webhook"
            requests.get(webhook_url, timeout=10)
        except Exception: pass
    app.run(host="0.0.0.0", port=port)
            









