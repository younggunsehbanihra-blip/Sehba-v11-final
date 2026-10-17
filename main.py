import os
import json
import requests
import datetime
import threading
import time
from flask import Flask, request
from groq import Groq

# --- CONFIGURATION ENVIRONMENT ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
FICHIER_STATS = os.getenv("FICHIER_STATS", "/tmp/stats.json")

app = Flask(__name__)

def local_load():
    try:
        if os.path.exists(FICHIER_STATS):
            with open(FICHIER_STATS, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {
        "gagnants": 0, 
        "perdants": 0, 
        "coupons_jour": 0, 
        "date": str(datetime.date.today()),
        "matchs_analyses": [],
        "suivi_attente": []
    }

def local_save(d):
    try:
        with open(FICHIER_STATS, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def log_to_google_sheet(status, detail="Pari Enregistré"):
    try:
        url_macro = os.getenv("GOOGLE_SHEET_URL")
        if not url_macro or url_macro == "https://google.com":
            return
        payload = {
            "spreadsheet_id": GOOGLE_SHEET_ID, 
            "date": str(datetime.date.today()), 
            "statut": status, 
            "details": detail
        }
        requests.post(url_macro, json=payload, timeout=8)
    except Exception:
        pass

def tg(txt):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": txt, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=12)
    except Exception as e:
        print(f"TG ERR: {e}")

def web_search(query):
    try:
        from bs4 import BeautifulSoup
        url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            snippets = [t.text.strip() for t in soup.find_all('a', class_='result__snippet')[:3]]
            snippets = [s for s in snippets if s]
            if snippets:
                return " | ".join(snippets)
    except Exception as e:
        print(f"WEB SEARCH ERR: {e}")
    return "Aucune information de dernière minute sur le web."

def get_all_odds():
    try:
        url = f"https://api.the-odds-api.com/v4/sports/soccer/odds?apiKey={ODDS_API_KEY}&regions=eu,uk&markets=h2h,totals,btts&oddsFormat=decimal"
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            print(f"ODDS API {r.status_code}: {r.text[:200]}")
            return []
        
        data_json = r.json()
        matchs_valides = []
        if isinstance(data_json, list):
            for m in data_json:
                c_str = m.get("commence_time")
                if not c_str:
                    continue
                c_det = []
                cote_mini = 99.0
                bookmakers = m.get("bookmakers", [])
                if bookmakers:
                    for bk in bookmakers[:1]:
                        for mk in bk.get("markets", []):
                            if mk.get("key") == "h2h":
                                prices = [float(o.get('price', 99)) for o in mk.get("outcomes", [])]
                                if prices:
                                    cote_mini = min(prices)
                            out = mk.get("outcomes", [])
                            details = ", ".join([f"{o.get('name')}: {o.get('price')}" for o in out])
                            c_det.append(f"[{mk.get('key').upper()}] {details}")
                
                matchs_valides.append({
                    "home": m.get("home_team"),
                    "away": m.get("away_team"),
                    "league": m.get("sport_title"),
                    "commence_time": c_str,
                    "cote_favori": cote_mini,
                    "cotes": " | ".join(c_det[:4]),
                    "raw_bookmakers": bookmakers
                })
            return matchs_valides
    except Exception as e:
        print(f"ODDS ERR: {e}")
    return []

def get_complete_team_stats(home_team, away_team):
    if not home_team or not away_team:
        return None, "Équipes manquantes."
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    try:
        today_str = datetime.date.today().isoformat()
        url_fix = f"https://api-football-v1.p.rapidapi.com/v3/fixtures?date={today_str}"
        res_fix = requests.get(url_fix, headers=headers, timeout=12).json()
        
        fixture_id = None
        if "response" in res_fix and isinstance(res_fix["response"], list):
            for fix in res_fix["response"]:
                h_name = fix.get("teams", {}).get("home", {}).get("name", "")
                a_name = fix.get("teams", {}).get("away", {}).get("name", "")
                if home_team.lower() in h_name.lower() or away_team.lower() in a_name.lower() or h_name.lower() in home_team.lower():
                    fixture_id = fix.get("fixture", {}).get("id")
                    break
        
        if fixture_id:
            url_pred = f"https://api-football-v1.p.rapidapi.com/v3/predictions?fixture={fixture_id}"
            res_pred = requests.get(url_pred, headers=headers, timeout=12).json()
            if "response" in res_pred and len(res_pred["response"]) > 0:
                data = res_pred["response"][0]
                analyse_substantielle = {
                    "fixture_id": fixture_id,
                    "conseil_api": data.get("predictions", {}).get("advice"),
                    "probabilites_pourcentage": data.get("predictions", {}).get("percent"),
                    "statistiques_buts": data.get("predictions", {}).get("goals"),
                    "comparatif_direct_équipes": data.get("comparison", {}),
                    "forme_home": data.get("teams", {}).get("home", {}).get("league", {}).get("form"),
                    "forme_away": data.get("teams", {}).get("away", {}).get("league", {}).get("form"),
                    "historique_face_a_face": [
                        {
                            "home": match.get("teams", {}).get("home", {}).get("name"),
                            "away": match.get("teams", {}).get("away", {}).get("name"),
                            "score_home": match.get("goals", {}).get("home"),
                            "score_away": match.get("goals", {}).get("away")
                        } for match in data.get("h2h", [])[:3]
                    ]
                }
                return fixture_id, json.dumps(analyse_substantielle, ensure_ascii=False)
    except Exception as e:
        print(f"STATS ERR: {e}")
    return None, "Métriques de base actives. Historique lointain ou indisponible."

def calculer_value_bets(probabilites, bookmakers):
    if not probabilites or not bookmakers: 
        return []
    values_detectees = []
    try:
        p_home = float(str(probabilites.get("home", "0")).replace("%", "")) / 100
        p_draw = float(str(probabilites.get("draw", "0")).replace("%", "")) / 100
        p_away = float(str(probabilites.get("away", "0")).replace("%", "")) / 100
        
        for bk in bookmakers[:1]:
            for mk in bk.get("markets", []):
                if mk.get("key") == "h2h":
                    for o in mk.get("outcomes", []):
                        cote_bk = float(o.get("price", 0))
                        if cote_bk <= 0: continue
                        val = 0.0
                        name = str(o.get("name","")).lower()
                        if "home" in name and p_home > 0: val = (cote_bk * p_home) - 1
                        elif "away" in name and p_away > 0: val = (cote_bk * p_away) - 1
                        elif "draw" in name and p_draw > 0: val = (cote_bk * p_draw) - 1
                        if val > 0.03:
                            values_detectees.append(f"Marché: {o.get('name')} | Cote: {cote_bk} | Écart: +{round(val * 100, 1)}%")
    except Exception:
        pass
    return values_detectees

def core_analysis_routine():
    try:
        memo = local_load()
        today = str(datetime.date.today())
        if memo.get("date") != today:
            memo.update({"date": today, "coupons_jour": 0, "matchs_analyses": []})
            local_save(memo)
        if int(memo.get("coupons_jour", 0)) >= 5: 
            return
        matchs = get_all_odds()
        if not matchs:
            print("Pas de matchs odds")
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        matchs_filtrés = [m for m in matchs if 1.20 <= float(m.get("cote_favori", 99)) <= 2.10]
        for m in matchs_filtrés:
            match_id = f"{m.get('home')}_{m['away']}"
            if match_id in memo.get("matchs_analyses", []): 
                continue
            try:
                c_time = datetime.datetime.strptime(m["commence_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                ecart_minutes = (c_time - now).total_seconds() / 60
            except Exception:
                continue
            if 25 <= ecart_minutes <= 35:
                fix_id, det_api = get_complete_team_stats(m["home"], m["away"])
                probs = {}
                if fix_id:
                    try: 
                        probs = json.loads(det_api).get("probabilites_pourcentage", {})
                    except Exception: 
                        pass
                list_values = calculer_value_bets(probs, m.get("raw_bookmakers", []))
                if list_values:
                    statut_marche = f"🚨 ANOMALIE ET VALUE BET TROUVÉE : { ' | '.join(list_values) }"
                else:
                    statut_marche = "ℹ️ ALGORITHME STANDARD : Pas d'erreur de cote. Analyse statistique approfondie activée pour chercher la haute fiabilité."
                news = web_search(f"{m['home']} vs {m['away']} team lineup injury sports")
                prmt = f"SITUATION EXPERT SPORTIF HYBRIDE H-30 :\nMatch: {m['home']} vs {m['away']} ({m['league']})\nCotes Actuelles: {m['cotes']}\nContexte Marché 1xBet: {statut_marche}\nData API-Football: {det_api}\nFlux Infos: {news}\n\nMISSION PRO : Agis en actuaire algorithmique. Analyse toutes les options du catalogue 1xBet (Double Chance, Totaux alternatifs asiatiques, les deux équipes marquent, combinés de buts). Si une Value Bet est présente, décortique-la. Sinon, trouve l'option de base la plus sûre avec un niveau de confiance >82%. Argumentation ultra-courte. Titre: 🎯 SMART SNIPER HYBRIDE H-30."
                try:
                    c = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                        messages=[
                            {"role": "system", "content": "Tu es SEHBA actuaire sportif. Analyse statistique éducative uniquement. Tu ne refuses jamais. Donne toujours une réponse avec probabilité."},
                            {"role": "user", "content": prmt}
                        ], 
                        model="llama-3.3-70b-versatile", 
                        temperature=0.1, 
                        max_tokens=650
                    )
                    memo["coupons_jour"] = int(memo.get("coupons_jour", 0)) + 1
                    memo["matchs_analyses"].append(match_id)
                    if fix_id:
                        memo["suivi_attente"].append({"fixture_id": fix_id, "home": m["home"], "away": m["away"], "date": today, "traite": False})
                    local_save(memo)
                    tg(f"🔥 <b>TOP ANALYSE CONSEILLÉE {memo['coupons_jour']}/5</b>\n\n{c.choices[0].message.content}")
                    if memo["coupons_jour"] >= 5: 
                        break
                except Exception as e:
                    print(f"GROQ ERR: {e}")
                    pass
    except Exception as e:
        print(f"CORE ERR: {e}")
        pass

def auto_settlement_results():
    while True:
        try:
            now = datetime.datetime.now()
            if now.hour == 23 and now.minute >= 50:
                memo = local_load()
                headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
                matchs_a_traiter = [m for m in memo.get("suivi_attente", []) if not m.get("traite")]
                for m in matchs_a_traiter:
                    try:
                        url = f"https://api-football-v1.p.rapidapi.com/v3/fixtures?id={m['fixture_id']}"
                        r = requests.get(url, headers=headers, timeout=10).json()
                        if "response" in r and len(r["response"]) > 0:
                            status = r["response"][0].get("fixture", {}).get("status", {}).get("short")
                            if status in ["FT", "AET", "PEN"]:
                                sh = r["response"][0].get("goals", {}).get("home")
                                sa = r["response"][0].get("goals", {}).get("away")
                                detail_score = f"Score Final: {sh}-{sa}"
                                m["traite"] = True
                                log_to_google_sheet("TERMINE_AUTO", f"{m['home']} vs {m['away']} -> {detail_score}")
                                tg(f"🤖 <b>AUTO-SETTLEMENT (1xBet Market)</b>\nRencontre: {m['home']} - {m['away']}\n📊 {detail_score}\nRésultats consolidés.")
                    except Exception:
                        continue
                local_save(memo)
                time.sleep(3600)
        except Exception:
            pass
        time.sleep(60)

threading.Thread(target=auto_settlement_results, daemon=True).start()

@app.route('/')
def home_page():
    threading.Thread(target=core_analysis_routine).start()
    return "SERVEUR PROFESSIONNEL SEHBA V19.9.9 OPERATIONNEL - ZERO-CRASH LOCK ACTIVE", 200

@app.route('/setwebhook')
def setwebhook_route():
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url=https://sehba-v11-final.onrender.com/webhook"
        r = requests.get(url, timeout=10).json()
        return r
    except Exception as e:
        return f"err {e}", 500

@app.route('/auto')
def auto_route():
    threading.Thread(target=core_analysis_routine).start()
    return "Analyse H-30 lancée - check Telegram dans 20 secondes", 200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data or "message" not in data: 
            return "ok", 200
        txt = data.get("message", {}).get("text", "").strip()
        if not txt: 
            return "ok", 200
        
        memo = local_load()
        if txt.lower() == "/stats":
            tot = int(memo.get('gagnants', 0)) + int(memo.get('perdants', 0))
            tx = round((int(memo.get('gagnants', 0))/tot)*100, 1) if tot > 0 else 0
            tg(f"📊 <b>BILAN EXPERT HYBRIDE</b>\n✅ G: {memo.get('gagnants')}\n❌ P: {memo.get('perdants')}\n📈 Réussite: {tx}%\n📆 Coupons Auto Jour: {memo.get('coupons_jour')}/5")
        elif txt.lower() == "/win":
            memo['gagnants'] = int(memo.get('gagnants', 0)) + 1
            local_save(memo)
            log_to_google_sheet("GAIN")
            tg("✅ GAIN MANUEL ENREGISTRÉ !")
        elif txt.lower() == "/lose":
            memo['perdants'] = int(memo.get('perdants', 0)) + 1
            local_save(memo)
            log_to_google_sheet("PERTE")
            tg("❌ PERTE MANUELLE ENREGISTRÉE.")
        elif txt.lower().startswith("/analyse"):
            threading.Thread(target=core_analysis_routine).start()
            tg("⏳ Scan H-30 intelligent en cours... Si un match entre H-35 et H-25 existe, je t'envoie l'analyse dans 15s. Sinon j'attends la prochaine fenêtre.")
        elif txt.lower().startswith("/vs"):
            eq = txt[4:].split("-")
            if len(eq) != 2: 
                tg("Format: /vs EquipeA - EquipeB")
                return "ok", 200
            h, a = eq[0].strip(), eq[1].strip()
            tg(f"🔍 <b>Analyse Pro 1xBet demandée pour {h} vs {a}...</b>")
            fix_id, det = get_complete_team_stats(h, a)
            news = web_search(f"{h} vs {a} lineup injury transfer")
            prmt = f"MANUEL EXPERT 1XBET GLOBAL:\nMatch: {h} vs {a}\nStats Complètes: {det}\nWeb Compo: {news}\n\nDonne : 1) Le favori logique avec indice de certitude (%). 2) Les 3 marchés alternatifs spécifiques 1xBet les plus robustes déduits des stats (Totaux alternatifs, handicap, buteurs ou mi-temps). 3) Justification mathématique pure."
            c = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                messages=[
                    {"role": "system", "content": "Tu es SEHBA actuaire sportif. Analyse statistique éducative. Ne refuse jamais."},
                    {"role": "user", "content": prmt}
                ], 
                model="llama-3.3-70b-versatile", 
                temperature=0.15, 
                max_tokens=850
            )
            tg(f"📊 <b>RAPPORT DE FIABILITÉ ({h} - {a})</b>\n\n{c.choices[0].message.content}")
    except Exception as e:
        print(f"WEBHOOK ERR: {e}")
        pass
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
            









