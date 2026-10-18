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
    return "Pas d'info web"

def get_all_odds():
    global DERNIER_APPEL_ODDS_TS, MEMOIRE_MATCHS_ODDS
    if time.time() - DERNIER_APPEL_ODDS_TS < 7200 and MEMOIRE_MATCHS_ODDS:
        print("CACHE ODDS 2h actif")
        return MEMOIRE_MATCHS_ODDS
    try:
        url = f"https://api.the-odds-api.com/v4/sports/upcoming/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h&oddsFormat=decimal"
        r = requests.get(url, timeout=15)
        print(f"ODDS CODE: {r.status_code}")
        if r.status_code in [401,402,422,429]:
            print(f"ODDS DETAILS: {r.text[:500]}")
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
                res.append({"home":m.get("home_team"),"away":m.get("away_team"),"league":m.get("sport_title"),"commence_time":m.get("commence_time"),"cote_favori":cote_mini,"cotes":" | ".join(c_det)})
            except: continue
        MEMOIRE_MATCHS_ODDS = res
        print(f"ODDS OK: {len(res)} matchs")
        return res
    except Exception as e:
        print(f"ODDS ERR: {e}")
        return MEMOIRE_MATCHS_ODDS

def get_complete_team_stats(home_team, away_team):
    global CACHE_RAPIDAPI_STATS
    cle = f"{home_team}_{away_team}"
    if cle in CACHE_RAPIDAPI_STATS: return CACHE_RAPIDAPI_STATS[cle]
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    try:
        today = datetime.date.today().isoformat()
        url_fix = f"https://api-football-v1.p.rapidapi.com/v3/fixtures?date={today}"
        res_raw = requests.get(url_fix, headers=headers, timeout=12)
        if res_raw.status_code in [401,402,403,429]: return None, "CREDITS RAPIDAPI EPUISÉS"
        res_fix = res_raw.json()
        fixture_id = None
        if "response" in res_fix:
            for fix in res_fix["response"]:
                h = fix.get("teams",{}).get("home",{}).get("name","")
                a = fix.get("teams",{}).get("away",{}).get("name","")
                if home_team.lower() in h.lower() or away_team.lower() in a.lower():
                    fixture_id = fix.get("fixture",{}).get("id"); break
        if fixture_id:
            url_pred = f"https://api-football-v1.p.rapidapi.com/v3/predictions?fixture={fixture_id}"
            rp = requests.get(url_pred, headers=headers, timeout=12).json()
            if rp.get("response"):
                data = rp["response"][0]
                analyse = {"fixture_id":fixture_id,"conseil":data.get("predictions",{}).get("advice"),"percent":data.get("predictions",{}).get("percent")}
                resultat = (fixture_id, json.dumps(analyse, ensure_ascii=False))
                CACHE_RAPIDAPI_STATS[cle] = resultat
                return resultat
    except Exception as e: print(f"STATS ERR: {e}")
    return None, f"Mode degrade Web: {web_search(f'{home_team} vs {away_team}')}"

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
                        gh = r["response"][0].get("goals",{}).get("home")
                        ga = r["response"][0].get("goals",{}).get("away")
                        winner = "NUL"
                        if gh > ga: winner = item["home"]
                        elif ga > gh: winner = item["away"]
                        if winner.lower() in item.get("prono","").lower():
                            memo["gagnants"]+=1
                            tg(f"✅ GAGNÉ {item['home']} {gh}-{ga} {item['away']}")
                        else:
                            memo["perdants"]+=1
                            tg(f"❌ PERDU {item['home']} {gh}-{ga} {item['away']}")
                    else: restants.append(item)
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
        if not force and memo.get("coupons_jour",0) >= 5: return
        matchs = get_all_odds()
        if not matchs: return
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
                prmt = f"Match: {m['home']} vs {m['away']} | Cotes: {m['cotes']} | Data: {det} | Value Bet >5% ?"
                try:
                    # MODELE 1 - LE RAPIDE POUR AUTO - EXISTE EN PROD
                    c = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                        messages=[{"role":"system","content":"Tu es SEHBA actuaire. Si value bet dis prono, sinon REFUS."},{"role":"user","content":prmt}],
                        model="openai/gpt-oss-20b", temperature=0.1, max_tokens=750
                    )
                    contenu = c.choices[0].message.content
                    if "REFUS" not in contenu.upper():
                        memo["coupons_jour"]+=1
                        memo["matchs_analyses"].append(mid)
                        memo["suivi_attente"].append({"fixture_id":fix_id,"home":m["home"],"away":m["away"],"prono":contenu[:200],"date":today})
                        local_save(memo)
                        tg(f"🔥 TOP ANALYSE {memo['coupons_jour']}/5 (20B)\n{contenu}\nCotes: {m['cotes']}")
                        if memo["coupons_jour"]>=5 and not force: break
                    if force: break
                except Exception as e: print(f"GROQ 20B ERR: {e}")
    except Exception as e: print(f"CORE ERR: {e}")

@app.route('/')
def home_page():
    threading.Thread(target=core_analysis_routine).start()
    return "SERVEUR OPERATIONNEL - 20B + 120B + CACHE 2H", 200

@app.route('/force')
def force_route():
    threading.Thread(target=lambda: core_analysis_routine(force=True)).start()
    return "Force 20B lancee - check Telegram 15s", 200

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
            tg(f"📊 Bilan: {tx}% | {memo.get('coupons_jour')}/5 | Attente: {len(memo.get('suivi_attente',[]))}")
        elif txt.lower().startswith("/vs"):
            eq = txt[4:].split("-")
            if len(eq)!=2: return "ok",200
            h,a = eq[0].strip(), eq[1].strip()
            tg(f"🔍 Analyse 120B {h} vs {a}...")
            fid, det = get_complete_team_stats(h,a)
            prmt = f"EXPERT 1XBET: {h} vs {a} | Stats: {det}"
            # MODELE 2 - LE PUISSANT POUR /vs - EXISTE EN PROD
            c = Groq(api_key=GROQ_API_KEY).chat.completions.create(
                messages=[{"role":"system","content":"Actuaire sportif elite value bet."},{"role":"user","content":prmt}],
                model="openai/gpt-oss-120b", temperature=0.15, max_tokens=900
            )
            tg(f"📊 RAPPORT 120B ({h}-{a})\n\n{c.choices[0].message.content}")
    except Exception as e: print(f"WEBHOOK ERR: {e}")
    return "ok",200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
