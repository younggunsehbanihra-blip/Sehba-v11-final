import os, json, requests, datetime, threading, time, math
from flask import Flask, request
from groq import Groq

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")
FICHIER_STATS = "/tmp/stats.json"
FICHIER_HISTORY = "/tmp/odds_history.json"

DERNIER_APPEL_ODDS_TS = 0
MEMOIRE_MATCHS_ODDS = []
CACHE_RAPIDAPI_STATS = {}
app = Flask(__name__)

def tg(txt):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": txt, "parse_mode": "HTML"}, timeout=12)
    except: pass

def poisson_pmf(k, lam):
    try: return (math.exp(-lam) * (lam ** k)) / math.factorial(k)
    except: return 0

def calcul_poisson_xg(xG_moy_dom=1.4, xG_moy_ext=1.1, Att_H=1.2, Def_A=0.9, Att_A=1.0, Def_H=1.0):
    lam = Att_H * Def_A * xG_moy_dom
    mu = Att_A * Def_H * xG_moy_ext
    p1=pn=p2=0
    for i in range(6):
        for j in range(6):
            p = poisson_pmf(i, lam) * poisson_pmf(j, mu)
            if i>j: p1+=p
            elif i==j: pn+=p
            else: p2+=p
    tot = p1+pn+p2
    if tot>0: return p1/tot, pn/tot, p2/tot, lam, mu
    return 0.45,0.25,0.30,lam,mu

# ---------- AJUSTEMENT 1: DECROISSANCE EXPONENTIELLE VRAIE ----------
def get_historique_pondere(team_id, headers):
    """
    Interroge /fixtures?team=ID&last=10 et applique W_i = e^{-0.05 * t_i}
    t_i = jours depuis le match
    """
    try:
        url = f"https://api-football-v1.p.rapidapi.com/v3/fixtures?team={team_id}&last=10"
        r = requests.get(url, headers=headers, timeout=12).json()
        if not r.get("response"): return 1.2, 1.0 # Att, Def par défaut
        
        somme_pond_xG_marques = 0
        somme_pond_xG_encaisses = 0
        somme_poids = 0
        
        for fix in r["response"]:
            try:
                # t_i = jours depuis match
                date_str = fix.get("fixture",{}).get("date","")[:10]
                date_match = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                t_i = (datetime.date.today() - date_match).days
                if t_i < 0: t_i = 0
                
                # Formule que tu as donnée: W_i = e^{-0.05 * t_i}
                W_i = math.exp(-0.05 * t_i)
                
                # xG ou buts comme proxy si xG non dispo
                # On essaie de prendre xG, sinon buts
                goals_home = fix.get("goals",{}).get("home",0) or 0
                goals_away = fix.get("goals",{}).get("away",0) or 0
                is_home = fix.get("teams",{}).get("home",{}).get("id") == team_id
                
                xG_marques = goals_home if is_home else goals_away
                xG_encaisses = goals_away if is_home else goals_home
                
                somme_pond_xG_marques += xG_marques * W_i
                somme_pond_xG_encaisses += xG_encaisses * W_i
                somme_poids += W_i
            except: continue
        
        if somme_poids > 0:
            xG_moy_pondere_marques = somme_pond_xG_marques / somme_poids
            xG_moy_pondere_encaisses = somme_pond_xG_encaisses / somme_poids
            # Conversion en force Att/Def par rapport à moyenne ligue 1.4
            Att = max(0.6, min(1.8, xG_moy_pondere_marques / 1.4 + 0.6))
            Def = max(0.6, min(1.8, xG_moy_pondere_encaisses / 1.1))
            return Att, Def
        return 1.2, 1.0
    except Exception as e:
        print(f"HISTOIRE PONDERE ERR {e}")
        return 1.2, 1.0

def get_complete_team_stats(home_team, away_team):
    global CACHE_RAPIDAPI_STATS
    cle = f"{home_team}_{away_team}"
    if cle in CACHE_RAPIDAPI_STATS: return CACHE_RAPIDAPI_STATS[cle]
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    try:
        today = datetime.date.today().isoformat()
        # 1. Trouver les IDs des équipes
        url_search_h = f"https://api-football-v1.p.rapidapi.com/v3/teams?search={home_team}"
        r_h = requests.get(url_search_h, headers=headers, timeout=10).json()
        team_id_h = r_h["response"][0]["team"]["id"] if r_h.get("response") else None
        
        url_search_a = f"https://api-football-v1.p.rapidapi.com/v3/teams?search={away_team}"
        r_a = requests.get(url_search_a, headers=headers, timeout=10).json()
        team_id_a = r_a["response"][0]["team"]["id"] if r_a.get("response") else None
        
        fixture_id = None
        xG_dom, xG_ext = 1.4, 1.1
        Att_H, Def_A, Att_A, Def_H = 1.2, 0.9, 1.0, 1.0
        
        if team_id_h:
            Att_H, Def_H = get_historique_pondere(team_id_h, headers)
        if team_id_a:
            Att_A, Def_A = get_historique_pondere(team_id_a, headers)
        
        # Récup fixture du jour pour settlement
        url_fix = f"https://api-football-v1.p.rapidapi.com/v3/fixtures?date={today}"
        res_raw = requests.get(url_fix, headers=headers, timeout=12)
        if res_raw.status_code not in [401,402,403,429]:
            res_fix = res_raw.json()
            if "response" in res_fix:
                for fix in res_fix["response"]:
                    h = fix.get("teams",{}).get("home",{}).get("name","")
                    a = fix.get("teams",{}).get("away",{}).get("name","")
                    if home_team.lower()[:4] in h.lower() or away_team.lower()[:4] in a.lower():
                        fixture_id = fix.get("fixture",{}).get("id"); break
        
        resultat = (fixture_id, f"Histo pondéré W=e^-0.05t | {home_team} Att {Att_H:.2f} Def {Def_H:.2f} vs {away_team} Att {Att_A:.2f} Def {Def_A:.2f}", xG_dom, xG_ext, Att_H, Def_A, Att_A, Def_H)
        CACHE_RAPIDAPI_STATS[cle] = resultat
        return resultat
    except Exception as e:
        print(f"STATS V8 ERR {e}")
        return (None, "Mode degrade V8", 1.4, 1.1, 1.15, 0.9, 0.95, 1.0)

def load_history():
    try:
        if os.path.exists(FICHIER_HISTORY):
            with open(FICHIER_HISTORY, "r") as f: return json.load(f)
    except: pass
    return {}
def save_history(h):
    try:
        with open(FICHIER_HISTORY, "w") as f: json.dump(h, f)
    except: pass

# ---------- AJUSTEMENT 2: SMART MONEY SUR LA BONNE EQUIPE ----------
def detect_smart_money_v8(match_id, C1_now, CN_now, C2_now):
    """
    Retourne boost_P1, boost_P2 selon quelle cote a chuté
    Si C1 2.10 -> 1.85 => boost sur P1
    Si C2 2.10 -> 1.85 => boost sur P2
    """
    try:
        hist = load_history()
        if match_id not in hist:
            hist[match_id] = {"C1_open": C1_now, "CN_open": CN_now, "C2_open": C2_now, "C1_last": C1_now, "C2_last": C2_now}
            save_history(hist)
            return 0, 0
        
        C1_open = hist[match_id].get("C1_open", C1_now)
        C2_open = hist[match_id].get("C2_open", C2_now)
        
        chute_C1 = (hist[match_id].get("C1_last", C1_open) - C1_now) / hist[match_id].get("C1_last", C1_open) if hist[match_id].get("C1_last") else 0
        chute_C2 = (hist[match_id].get("C2_last", C2_open) - C2_now) / hist[match_id].get("C2_last", C2_open) if hist[match_id].get("C2_last") else 0
        
        hist[match_id]["C1_last"] = C1_now
        hist[match_id]["C2_last"] = C2_now
        save_history(hist)
        
        boost_P1 = 0.05 if chute_C1 >= 0.07 else 0
        boost_P2 = 0.05 if chute_C2 >= 0.07 else 0
        
        if boost_P1>0: print(f"SMART MONEY V8: Domicile {match_id} chute {chute_C1*100:.1f}% -> boost P1")
        if boost_P2>0: print(f"SMART MONEY V8: Extérieur {match_id} chute {chute_C2*100:.1f}% -> boost P2")
        
        return boost_P1, boost_P2
    except Exception as e:
        print(f"SMART V8 ERR {e}")
        return 0,0

def filtre_sentimental(q):
    try:
        url = f"https://duckduckgo.com/html/?q={requests.utils.quote(q)}"
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=8)
        txt = r.text.lower() if r.status_code==200 else ""
        if any(m in txt for m in ["blessure","suspension","forfait","gardien titulaire"]): return -0.15
    except: pass
    return 0

def get_all_odds():
    global DERNIER_APPEL_ODDS_TS, MEMOIRE_MATCHS_ODDS
    if time.time() - DERNIER_APPEL_ODDS_TS < 7200 and MEMOIRE_MATCHS_ODDS: return MEMOIRE_MATCHS_ODDS
    try:
        url = f"https://api.the-odds-api.com/v4/sports/upcoming/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h&oddsFormat=decimal"
        r = requests.get(url, timeout=15)
        if r.status_code != 200: return MEMOIRE_MATCHS_ODDS
        DERNIER_APPEL_ODDS_TS = time.time()
        data = r.json()
        res=[]
        for m in data:
            try:
                bks=m.get("bookmakers",[])
                for bk in bks[:2]:
                    for mk in bk.get("markets",[]):
                        if mk.get("key")=="h2h":
                            outs=mk.get("outcomes",[])
                            C1=CN=C2=0
                            for o in outs:
                                name=o.get("name","").lower()
                                price=float(o.get("price",0))
                                if m.get("home_team","").lower() in name: C1=price
                                elif m.get("away_team","").lower() in name: C2=price
                                elif "draw" in name: CN=price
                            if C1>0 and CN>0 and C2>0:
                                res.append({"home":m.get("home_team"),"away":m.get("away_team"),"league":m.get("sport_title"),"commence_time":m.get("commence_time"),"C1":C1,"CN":CN,"C2":C2,"cote_favori":min(C1,CN,C2),"cotes":f"{m.get('home_team')}:{C1} N:{CN} {m.get('away_team')}:{C2}"})
                            break
                    break
            except: continue
        MEMOIRE_MATCHS_ODDS=res[:200]
        return MEMOIRE_MATCHS_ODDS
    except: return MEMOIRE_MATCHS_ODDS

def simuler_tous_marches(C1,CN,C2,P1,PN,P2,lam,mu,mise_totale=200):
    try:
        CN_safe=max(CN,1.01)
        Cote_DNB=C1*(1.0-(1.0/CN_safe))
        Proba_DNB=P1/(P1+P2) if (P1+P2)>0 else 0.5
        Value_DNB=Proba_DNB*Cote_DNB
        est_valide=Value_DNB>=1.15
        Mise_Nul=mise_totale/CN_safe
        Mise_Vic=mise_totale-Mise_Nul
        Proba_Over15=1-(poisson_pmf(0,lam)*poisson_pmf(0,mu)+poisson_pmf(1,lam)*poisson_pmf(0,mu)+poisson_pmf(0,lam)*poisson_pmf(1,mu))
        Proba_BTTS=(1-poisson_pmf(0,lam))*(1-poisson_pmf(0,mu))
        return {"DNB_cote":round(Cote_DNB,2),"DNB_proba":round(Proba_DNB*100,1),"DNB_value":round(Value_DNB,3),"DC_1X_proba":round((1.0-P2)*100,1),"Over15_proba":round(Proba_Over15*100,1),"BTTS_proba":round(Proba_BTTS*100,1),"valide":est_valide,"mise_victoire":round(Mise_Vic,1),"mise_nul":round(Mise_Nul,1),"mise_totale":mise_totale}
    except:
        return {"valide":False,"DNB_cote":1.3,"DNB_proba":70,"DNB_value":0.9,"DC_1X_proba":75,"Over15_proba":80,"BTTS_proba":55,"mise_victoire":0,"mise_nul":0,"mise_totale":mise_totale}

def local_load():
    try:
        if os.path.exists(FICHIER_STATS):
            with open(FICHIER_STATS,"r",encoding="utf-8") as f: return json.load(f)
    except: pass
    return {"gagnants":0,"perdants":0,"coupons_jour":0,"date":str(datetime.date.today()),"matchs_analyses":[],"suivi_attente":[],"bankroll":1000}
def local_save(d):
    try:
        with open(FICHIER_STATS,"w",encoding="utf-8") as f: json.dump(d,f,indent=2,ensure_ascii=False)
    except: pass

def core_analysis_routine(force=False):
    try:
        memo=local_load()
        if not force and len(memo.get("suivi_attente",[]))>0:
            print("VERROU ACTIF V8"); return
        today=str(datetime.date.today())
        if memo.get("date")!=today:
            memo.update({"date":today,"coupons_jour":0,"matchs_analyses":[]}); local_save(memo)
        if not force and memo.get("coupons_jour",0)>=5: return
        matchs=get_all_odds()
        if not matchs: return
        bons=[]
        now=datetime.datetime.now(datetime.timezone.utc)
        for m in matchs:
            mid=f"{m['home']}_{m['away']}"
            if not force and mid in memo.get("matchs_analyses",[]): continue
            try:
                c_time=datetime.datetime.strptime(m["commence_time"],"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                ecart=(c_time-now).total_seconds()/60
            except: continue
            if force or (20<=ecart<=300):
                fid,det,xG_dom,xG_ext,Att_H,Def_A,Att_A,Def_H=get_complete_team_stats(m["home"],m["away"])
                P1_p,PN_p,P2_p,lam,mu=calcul_poisson_xg(xG_dom,xG_ext,Att_H,Def_A,Att_A,Def_H)
                boost_P1,boost_P2=detect_smart_money_v8(mid,m["C1"],m["CN"],m["C2"])
                malus=filtre_sentimental(f"{m['home']} {m['away']}")
                # Application correcte sur bonne équipe
                P1_aj=max(0.05,min(0.85,P1_p+boost_P1+malus if boost_P1>0 else P1_p+malus))
                P2_aj=max(0.05,min(0.85,P2_p+boost_P2 if boost_P2>0 else P2_p))
                # Redistribution Reste = 1 - P1_aj - P2_aj
                Reste=1.0-P1_aj-P2_aj
                PN_aj=max(0.05,PN_p+Reste) if Reste>0 else PN_p
                # Normalisation
                tot=P1_aj+PN_aj+P2_aj
                P1_aj,PN_aj,P2_aj=P1_aj/tot,PN_aj/tot,P2_aj/tot
                mar=simuler_tous_marches(m["C1"],m["CN"],m["C2"],P1_aj,PN_aj,P2_aj,lam,mu,200)
                if mar["valide"]:
                    bons.append((m,mar,fid,det,P1_aj))
                    if not force: break
        if not bons: return
        m,mar,fid,det,P1_aj=bons[0]
        prmt=f"Match V8 Redoutable: {m['home']} vs {m['away']} | Poisson decay W=e^-0.05t | P1={P1_aj:.2f} | DNB {mar['DNB_cote']} Value {mar['DNB_value']} | {det}"
        try:
            c=Groq(api_key=GROQ_API_KEY).chat.completions.create(
                messages=[{"role":"system","content":"Actuaire V8 redoutable. Ticket DNB."},{"role":"user","content":prmt}],
                model="openai/gpt-oss-20b",temperature=0.1,max_tokens=800
            )
            contenu=c.choices[0].message.content
            memo["coupons_jour"]+=1; memo["matchs_analyses"].append(f"{m['home']}_{m['away']}")
            memo["suivi_attente"].append({"fixture_id":fid,"home":m["home"],"away":m["away"],"prono":contenu[:200],"date":today})
            local_save(memo)
            tg(f"🔥 V8 REDOUTABLE - POISSON DECAY + SMART CORRIGÉ\n{contenu}\n📊 DNB {mar['DNB_cote']} ({mar['DNB_proba']}%) Value {mar['DNB_value']}\n💰 V {mar['mise_victoire']} + N {mar['mise_nul']} = {mar['mise_totale']} | P1 decay {P1_aj*100:.1f}%\nCotes: {m['cotes']}")
        except Exception as e: print(f"GROQ V8 ERR {e}")
    except Exception as e: print(f"CORE V8 ERR {e}")

@app.route('/')
def home_page():
    try: threading.Thread(target=core_analysis_routine).start()
    except: pass
    return "SERVEUR V8 REDOUTABLE - DECAY + SMART CORRIGE",200
@app.route('/force')
def force_route():
    try: threading.Thread(target=lambda: core_analysis_routine(force=True)).start()
    except: pass
    return "Force V8 lancee",200
@app.route('/webhook',methods=['POST'])
def telegram_webhook():
    try:
        data=request.get_json(force=True,silent=True)
        if not data or "message" not in data: return "ok",200
        txt=data["message"].get("text","").strip()
        memo=local_load()
        if txt.lower()=="/stats":
            tg(f"📊 V8 REDOUTABLE | {memo.get('coupons_jour')}/5 | Verrou {len(memo.get('suivi_attente',[]))}")
        elif txt.lower().startswith("/vs"):
            eq=txt[4:].split("-")
            if len(eq)!=2: return "ok",200
            h,a=eq[0].strip(),eq[1].strip()
            tg(f"🔍 V8 {h} vs {a} [decay e^-0.05t + smart corrigé]...")
            try:
                fid,det,xG_dom,xG_ext,Att_H,Def_A,Att_A,Def_H=get_complete_team_stats(h,a)
                P1,PN,P2,lam,mu=calcul_poisson_xg(xG_dom,xG_ext,Att_H,Def_A,Att_A,Def_H)
                mar=simuler_tous_marches(1.9,3.3,3.8,P1,PN,P2,lam,mu,200)
                c=Groq(api_key=GROQ_API_KEY).chat.completions.create(
                    messages=[{"role":"system","content":"Actuaire V8"},{"role":"user","content":f"{h} vs {a} {det} Poisson decay P1 {P1:.2f} DNB {mar}"}],
                    model="openai/gpt-oss-120b",temperature=0.15,max_tokens=900
                )
                tg(f"📊 V8 REDOUTABLE\n{c.choices[0].message.content}\n\n🔮 DNB {mar['DNB_cote']} Value {mar['DNB_value']}")
            except Exception as e: tg(f"⚠️ /vs V8 {e}")
    except: pass
    return "ok",200

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)))
