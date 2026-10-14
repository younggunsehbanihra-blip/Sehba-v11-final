import requests, json, os, datetime
from flask import Flask, request
from groq import Groq

# --- VOS CLÉS D'ACCÈS INTÉGRALES ---
GROQ_API_KEY = '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'
TELEGRAM_TOKEN = '8631670973:AAHqmUWGKKr1dNeNO6fLOXwfngjygClOEqA'
TELEGRAM_CHAT_ID = '8851728637'
ODDS_API_KEY = 'a466f43991f4602462ade9c258464e72'
RAPIDAPI_KEY = '3f38adafd5msh4156e2331c2667fp17307ejsn5fa6a32beecb' 

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
    try: requests.post(f"https://telegram.org{TELEGRAM_TOKEN}/sendMessage", json={"chat_id":TELEGRAM_CHAT_ID,"text":txt,"parse_mode":"HTML"}, timeout=15)
    except: pass

def get_live_odds():
    try:
        url = f"https://the-odds-api.com{ODDS_API_KEY}&regions=eu&markets=h2h,totals,btts&oddsFormat=decimal"
        r = requests.get(url, timeout=10).json()
        if r and len(r) > 0:
            match = r
            home_team = match.get("home_team")
            away_team = match.get("away_team")
            
            cotes_detaillees = []
            if match.get("bookmakers") and len(match["bookmakers"]) > 0:
                for market in match["bookmakers"].get("markets", []):
                    key = market.get("key")
                    outcomes = market.get("outcomes", [])
                    details = ", ".join([f"{o['name']}: {o['price']}" for o in outcomes])
                    cotes_detaillees.append(f"[{key.upper()}] -> {details}")
            
            return home_team, away_team, " | ".join(cotes_detaillees)
    except: pass
    return None, None, "Matchs du jour - Premier League"

def get_team_stats(home_team, away_team):
    if not home_team or not away_team:
        return "Pas de statistiques d'historique disponibles pour ce match."
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "://rapidapi.com"}
    try:
        url_standings = "https://://rapidapi.com/v3/standings?league=39&season=2026"
        res = requests.get(url_standings, headers=headers, timeout=10).json()
        standings_list = res["response"]["league"]["standings"]
        home_info, away_info = {}, {}
        for team in standings_list:
            name = team["team"]["name"]
            if home_team.lower() in name.lower() or name.lower() in home_team.lower():
                home_info = {"rang": team["rank"], "forme": team["form"], "points": team["points"], "buts_pour": team["all"]["goals"]["for"], "buts_contre": team["all"]["goals"]["against"]}
            if away_team.lower() in name.lower() or name.lower() in away_team.lower():
                away_info = {"rang": team["rank"], "forme": team["form"], "points": team["points"], "buts_pour": team["all"]["goals"]["for"], "buts_contre": team["all"]["goals"]["against"]}
        if home_info and away_info:
            return (f"--- STATISTIQUES TERRAIN ---\n"
                    f"Domicile ({home_team}) : Rang {home_info['rang']}, Forme : {home_info['forme']}, Pts : {home_info['points']}, Buts marqués : {home_info['buts_pour']}, Buts encaissés : {home_info['buts_contre']}.\n"
                    f"Extérieur ({away_team}) : Rang {away_info['rang']}, Forme : {away_info['forme']}, Pts : {away_info['points']}, Buts marqués : {away_info['buts_pour']}, Buts encaissés : {away_info['buts_contre']}.")
    except: pass
    return "Analyse basée sur l'historique général récent et les probabilités mathématiques des cotes."

@app.route('/')
def home(): return "V16 PRO - COMBINÉ FACULTATIF TACTIQUE READY"

@app.route(f'/{TELEGRAM_TOKEN}', methods=['POST'])
def webhook():
    data = request.get_json()
    message_obj = data.get("message", {})
    txt = message_obj.get("text", "").lower()
    
    if not txt:
        return "ok"

    today = str(datetime.date.today())
    if memo.get("date") != today:
        memo["date"] = today
        memo["coupons_jour"] = 0
        save(memo)

    if txt == "/stats":
        total_joues = memo['gagnants'] + memo['perdants']
        restant = max(0, 50 - total_joues)
        taux_reussite = round((memo['gagnants'] / total_joues) * 100, 1) if total_joues > 0 else 0
        
        recap = (
            f"📊 <b>TABLEAU DE BORD - STRATÉGIE PRO (ÉCHANTILLON 50)</b>\n\n"
            f"✅ Coupons Gagnants : <b>{memo['gagnants']}</b>\n"
            f"❌ Coupons Perdants : <b>{memo['perdants']}</b>\n"
            f"📈 Taux de réussite global : <b>{taux_reussite}%</b>\n"
            f"🔄 Progression de l'échantillon : <b>{total_joues}/50</b> matchs validés\n"
            f"🎯 Coupons restants pour clore le test : <b>{restant}</b>\n\n"
            f"📆 Aujourd'hui : {memo['coupons_jour']}/5 générés"
        )
        tg(recap)
    
    elif txt == "/win":
        memo['gagnants'] += 1
        save(memo)
        tg(f"✅ COUPON VALIDÉ EN GAIN !\n📊 Tapez /stats pour voir l'évolution de votre taux de réussite.")
    
    elif txt == "/lose":
        memo['perdants'] += 1
        save(memo)
        tg(f"❌ COUPON ENREGISTRÉ COMME PERDANT.\n📊 Suivi sur l'échantillon de 50 tests actif. Vérifiez avec /stats.")

    elif txt == "/bilan":
        tg("⏳ Analyse de l'évolution financière et calcul de la rentabilité réelle...")
        prompt_bilan = f"Données comptables : {memo['gagnants']} validations positives et {memo['perdants']} validations négatives. Agis comme un actuaire financier de haut niveau. Rédige une note analytique rapide en français pour évaluer la croissance du modèle, estimer si le taux de réussite actuel protège le capital et donner un conseil de sécurité pour la journée."
        c = client.chat.completions.create(messages=[{"role":"user","content":prompt_bilan}], model="llama3-70b-8192", temperature=0.3, max_tokens=800)
        tg(f"📋 <b>RECAPITULATIF TECHNIQUE DE LA VEILLE :</b>\n\n{c.choices.message.content}")

    elif txt in ["/analyse", "/coupon"]:
        if memo['coupons_jour'] >= 5:
            tg(f"⏳ Limite de 5 coupons atteinte pour aujourd'hui. Sécurisons notre capital.")
        else:
            tg("🔍 Scan intelligent en cours (Libre Arbitre & Sélection Optimale)...")
            home_t, away_t, cotes = get_live_odds()
            stats_terrain = get_team_stats(home_t, away_t)
            
            prompt = (
                f"Sujet d'étude : {home_t} vs {away_t}.\n"
                f"Matrice des marchés et cotes (H2H, BTTS, TOTALS) : {cotes}.\n"
                f"Données historiques et de terrain : {stats_terrain}\n\n"
                f"Agis comme un système expert doté d'un LIBRE ARBITRE TECHNIQUE TOTAL. Ton objectif principal est de maximiser la fiabilité et de rejeter les valeurs sans avantage réel (cotes en dessous de 1.30).\n\n"
                f"INSTRUCTIONS DE STRUCTURE DU COUPON :\n"
                f"- Tu n'as AUCUNE obligation de faire un combiné systématique. Propose en priorité un pari UNIQUE (Simple) s'il est statistiquement optimal, hautement fiable et rentable.\n"
                f"- Tu es autorisé à construire un COMBINÉ intelligent uniquement si tu estimes, d'après ton analyse, que c'est strictement nécessaire, plus sécurisé et nettement plus avantageux financièrement pour la feuille de match qu'un pari unique.\n\n"
                f"Présente clairement l'option retenue (Simple ou Combiné selon ton choix) avec sa cote finale, suivi d'une justification mathématique très courte. Réponse obligatoire en français sous l'intitulé : COUPON SNIPER MULTI-OPTIONS."
            )
            c = client.chat.completions.create(messages=[{"role":"user","content":prompt}], model="llama3-70b-8192", temperature=0.2, max_tokens=1200)
            memo['coupons_jour'] += 1
            save(memo)
            tg(f"<b>🎯 COUPON {memo['coupons_jour']}/5</b>\n\n{c.choices.message.content}\n\n👉 Enregistrez le résultat avec /win ou /lose")

    else:
        prompt_discussion = (
            f"Tu es un conseiller expert en statistiques de données footballistiques. "
            f"L'utilisateur te sollicite pour une analyse ou une discussion : '{message_obj.get('text')}'\n"
            f"Réponds de manière professionnelle, utile, précise et en français. Évalue sa demande avec ton libre arbitre technique et propose-lui des pistes sécurisées."
        )
        c = client.chat.completions.create(messages=[{"role":"user","content":prompt_discussion}], model="llama3-70b-8192", temperature=0.6, max_tokens=1000)
        tg(c.choices.message.content)

    return "ok"

if __name__ == "__main__":
    try: requests.get(f"https://telegram.org{TELEGRAM_TOKEN}/setWebhook?url=https://onrender.com{TELEGRAM_TOKEN}")
    except: pass
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
