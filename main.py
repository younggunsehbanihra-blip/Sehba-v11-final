import os, json, requests, datetime, threading, time
from flask import Flask, request
from groq import Groq

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")
FICHIER_STATS = os.getenv("FICHIER_STATS", "/tmp/stats.json")

DERNIER_APPEL_ODDS_TS = 0
MEMOIRE_MATCHS_ODDS = []
CACHE_RAPIDAPI_STATS = {}
app = Flask(__name__)

def local_load():
    try:
        if os.path.exists(FICHIER_STATS):
            with open(FICHIER_STATS, "r", encoding="utf-8") as f: return json.load(f)
    except: pass
    return {"gagnants":0,"perdants":0,"coupons_jour":0,"date":str(datetime.date.today()),"matchs_analyses":[],"suivi_attente":[]}

def local_save(d):
    try:
        with open(FICHIER_STATS, "w", encoding="utf-8") as f: json.dump(d, f, indent=2, ensure_ascii=False)
    except: pass

def tg(txt):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": txt, "parse_mode": "HTML"}, timeout=12)
    except: pass

def web_search(query):
    try:
        from bs4 import BeautifulSoup
        url = f"https://duckduckgo.com/html/?q={requests.utils.quote(query)}"
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            s = [t.text.strip() for t in soup.find_all('a', class_='result__snippet')[:3]]
            if s: return " | ".join(s)
    except: pass
    return "Pas d'info web dispo"

def get_all_odds():
    global DERNIER_APPEL_ODDS_TS, MEMOIRE_MATCHS_ODDS
    if time.time() - DERNIER_APPEL_ODDS_TS < 7200 and MEMOIRE_MATCHS_ODDS:
        print("CACHE ODDS 2h actif - economie")
        return MEMOIRE_MATCHS_ODDS
    try:
        url = f"https://api.the-odds-api.com/v4/sports/upcoming/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h&oddsFormat=decimal"
        r = requests.get(url, timeout=15)
        print(f"ODDS CODE: {r.status_code}")
        if r.status_code in [401,402,422,429]:
            print(f"ODDS ERR DETAIL: {r.text[:500]}")
            return MEMOIRE_MATCHS_ODDS
        if r.status_code != 200: return MEMOIRE_MATCHS_ODDS
        DERNIER_APPEL_ODDS_TS = time.time()
        data = r.json()
        res = []
        for m in data:
            try:
                cote_mini = 99.0
                bks = m.get("bookmakers", [])
                c_det = []
                for bk in bks[:1]:
                    for mk in bk.get("markets", []):
                        if mk.get("key") == "h2h":
                            prices = [float(o.get('price',99)) for o in mk.get("outcomes",[])]
                            if prices: cote_mini = min(prices)
                            c_det.append(", ".join([f"{o.get('name')}:{o.get('price')}" for o in mk.get("outcomes",[])]))
                res.append({"home":m.get("home_team"),"away":m.get("away_team"),"league":m.get("sport_title"),"commence_time":m.get("commence_time"),"cote_favori":cote_mini,"cotes":" | ".join(c_det),"raw_bookmakers":bks})
            except: continue
        MEMOIRE_MATCHS_ODDS = res
        print(f"ODDS OK: {len(res)} matchs trouves")
        return res
    except Exception as e:
        print(f"ODDS ERR: {e}")
        return MEMOIRE_MATCHS_ODDS

def get_complete_team_stats(home_team, away_team):
    global CACHE_RAPIDAPI_STATS
    if not home_team or not away_team: return None, "Equipes manquantes"
    cle = f"{home_team}_{away_team}"
    if cle in CACHE_RAPIDAPI_STATS:
        print("CACHE RAPIDAPI actif - economie")
        return CACHE_RAPIDAPI_STATS[cle]
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    try:
        today = datetime.date.today().isoformat()
        url_fix = f"https://api-football-v1.p.rapidapi.com/v3/fixtures?date={today}"
        res_raw = requests.get(url_fix, headers=headers, timeout=12)
        if res_raw.status_code in [401,402,403,429]: return None, "CREDITS RAPIDAPI EPUISÉS"
        res_fix = res_raw.json()
        fixture_id = None
        league_name = ""
        if "response" in res_fix:
            for fix in res_fix["response"]:
                h = fix.get("teams",{}).get("home",{}).get("name","")
                a = fix.get("teams",{}).get("away",{}).get("name","")
                if home_team.lower() in h.lower() or home_team.lower() in a.lower() or away_team.lower() in h.lower() or away_team.lower() in a.lower():
                    fixture_id = fix.get("fixture",{}).get("id")
                    league_name = fix.get("league",{}).get("name","")
                    break
        if fixture_id:
            url_pred = f"https://api-football-v1.p.rapidapi.com/v3/predictions?fixture={fixture_id}"
            rp = requests.get(url_pred, headers=headers, timeout=12).json()
            if rp.get("response"):
                data = rp["response"][0]
                # VALUE BET DATA
                advice = data.get("predictions",{}).get("advice","")
                percent = data.get("predictions",{}).get("percent",{})
                teams_data = data.get("teams",{})
                h2h_data = data.get("h2h",[])
                analyse = {"fixture_id":fixture_id,"league":league_name,"conseil":advice,"percent":percent,"teams":teams_data,"h2h_len":len(h2h_data)}
                resultat = (fixture_id, json.dumps(analyse, ensure_ascii=False))
                CACHE_RAPIDAPI_STATS[cle] = resultat
                return resultat
        # Fallback web search si pas de fixture
        web_info = web_search(f"{home_team} vs {away_team} prediction")
        return None, f"Mode degrade Web: {web_info}"
    except Exception as e: 
        print(f"STATS ERR: {e}")
        return None, f"Erreur stats: {e} | Web: {web_search(f'{home_team} {away_team}')}"

def check_settlement():
    try:
        memo = local_load()
        if not memo.get("suivi_attente"): return
        headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
        restants = []
        for item in memo["suivi_attente"]:
            try:
                fid = item.get("fixture_id")
                if not fid: continue
                url = f"https://api-football-v1.p.rapidapi.com/v3/fixtures?id={fid}"
                r = requests.get(url, headers=headers, timeout=12).json()
                if r.get("response"):
                    status = r["response"][0].get("fixture",{}).get("status",{}).get("short","")
                    if status in ["FT","AET","PEN"]:
                        goals_home = r["response"][0].get("goals",{}).get("home")
                        goals_away = r["response"][0].get("goals",{}).get("away")
                        winner = "NUL"
                        if goals_home > goals_away: winner = item["home"]
                        elif goals_away > goals_home: winner = item["away"]
                        # Simple settlement: si pronostic contient le gagnant
                        if winner.lower() in item.get("prono","").lower() or item.get("prono","").lower() in winner.lower():
                            memo["gagnants"]+=1
                            tg(f"✅ <b>GAGNÉ</b> {item['home']} {goals_home}-{goals_away} {item['away']}\nProno: {item['prono']}")
                        else:
                            memo["perdants"]+=1
                            tg(f"❌ <b>PERDU</b> {item['home']} {goals_home}-{goals_away} {item['away']}\nProno: {item['prono']}")
                    else:
                        restants.append(item)
            except: restants.append(item)
        memo["suivi_attente"]=restants
        local_save(memo)
    except Exception as e: print(f"SETTLEMENT ERR: {e}")

def core_analysis_routine(force=False):
    try:
        check_settlement()
        memo = local_load()
        today = str(datetime.date.today())
        if memo.get("date") != today:
            memo.update({"date":today,"coupons_jour":0,"matchs_analyses":[]}); local_save(memo)
        if not force and memo.get("coupons_jour",0) >= 5:
            print("Limite 5/j atteinte")
            return
        matchs = get_all_odds()
        if not matchs: 
            print("Pas de matchs odds")
            return
        now = datetime.datetime.now(datetime.timezone.utc)
        candidats = [x for x in matchs if 1.20 <= float(x.get("cote_favori",99)) <= 2.10]
        if force and not candidats: candidats = matchs[:3]
        for m in candidats:
            mid = f"{m['home']}_{m['away']}"
            if not force and mid in memo.get("matchs_analyses",[]): continue
            try:
                c_time = datetime.datetime.strptime(m["commence_time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                ecart = (c_time - now).total_seconds()/60
            except: continue
            if force or (25 <= ecart <= 35):
                fix_id, det = get_complete_team_stats(m["home"], m["away"])
                # VALUE BET LOGIC + WEB SEARCH
                prmt = f"Match: {m['home']} vs {m['away']} | Ligue: {m['league']} | Cotes: {m['cotes']} | Cote favori: {m['cote_favori']} | Data API-Football: {det} | Heure: {m['commence_time']} | Value Bet: calcule proba reelle vs cote bookmaker, si value >5% signale le"
                try:
                    c = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                        messages=[{"role":"system","content":"Tu es SEHBA, actuaire sportif expert 1xBet. Tu analyses value bet, forme, H2H. Si pas value, dis REFUS. Si value, donne prono precis 1X2 ou Double Chance avec % confiance."},{"role":"user","content":prmt}],
                        model="llama-3.1-8b-instant", temperature=0.1, max_tokens=750
                    )
                    contenu = c.choices[0].message.content
                    if "REFUS" not in contenu.upper():
                        memo["coupons_jour"]+=1
                        memo["matchs_analyses"].append(mid)
                        # Suivi pour settlement auto
                        memo["suivi_attente"].append({"fixture_id":fix_id,"home":m["home"],"away":m["away"],"prono":contenu[:200],"date":today})
                        local_save(memo)
                        tg(f"🔥 <b>TOP ANALYSE {memo['coupons_jour']}/5 - VALUE BET</b>\nLigue: {m['league']}\n{contenu}\n\nCotes: {m['cotes']}")
                        if memo["coupons_jour"]>=5 and not force: break
                    else:
                        print(f"REFUS value pour {mid}")
                        if not force:
                            memo["matchs_analyses"].append(mid)
                            local_save(memo)
                except Exception as e: print(f"GROQ AUTO ERR: {e}")
                if force: break
    except Exception as e: print(f"CORE ERR: {e}")

@app.route('/')
def home_page():
    threading.Thread(target=core_analysis_routine).start()
    return "SERVEUR OPERATIONNEL - CACHE 2H + VALUE BET + SETTLEMENT AUTO", 200

@app.route('/force')
def force_route():
    threading.Thread(target=lambda: core_analysis_routine(force=True)).start()
    return "Force lancee - check Telegram dans 15s + Logs ODDS CODE 200", 200

@app.route('/setwebhook')
def setwebhook_route():
    try:
        my_url = "https://sehba-v11-final.onrender.com/webhook"
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={my_url}"
        return requests.get(url, timeout=10).json()
    except Exception as e: return f"err {e}", 500

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data or "message" not in data: return "ok", 200
        txt = data["message"].get("text","").strip()
        memo = local_load()
        if txt.lower() == "/stats":
            tot = memo.get('gagnants',0)+memo.get('perdants',0)
            tx = round(memo.get('gagnants',0)/tot*100,1) if tot>0 else 0
            tg(f"📊 <b>Bilan SEHBA</b>\n✅ Gagnés: {memo.get('gagnants',0)}\n❌ Perdus: {memo.get('perdants',0)}\n📈 Réussite: {tx}%\n🎫 Aujourd'hui: {memo.get('coupons_jour',0)}/5\n⏳ En attente settlement: {len(memo.get('suivi_attente',[]))}\n💾 Cache Odds: {len(MEMOIRE_MATCHS_ODDS)} matchs\n💾 Cache RapidAPI: {len(CACHE_RAPIDAPI_STATS)}")
        elif txt.lower().startswith("/vs"):
            eq = txt[4:].split("-")
            if len(eq)!=2:
                tg("Usage: /vs Equipe1 - Equipe2")
                return "ok",200
            h,a = eq[0].strip(), eq[1].strip()
            tg(f"🔍 Analyse lourde 120B {h} vs {a}...")
            fid, det = get_complete_team_stats(h,a)
            prmt = f"EXPERT 1XBET GLOBAL: {h} vs {a} | Stats Completes: {det} | Mission: Value bet + prono 1X2 precis"
            c = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                messages=[{"role":"system","content":"Tu es SEHBA, actuaire sportif elite. Analyse complete avec value bet, H2H, forme, conseil final."},{"role":"user","content":prmt}],
                model="openai/gpt-oss-120b", temperature=0.15, max_tokens=900
            )
            tg(f"📊 <b>RAPPORT EXPERT 120B ({h}-{a})</b>\n\n{c.choices[0].message.content}")
    except Exception as e: print(f"WEBHOOK ERR: {e}")
    return "ok",200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
