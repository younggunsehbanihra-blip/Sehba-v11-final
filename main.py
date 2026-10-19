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

# --- CONFIG TON MODE COMBINÉ ---
MODE_COMBINE_ACTIF = True
LIGUES_D2_D3_VALUE = ["Championship", "League One", "League Two", "LaLiga2", "Serie B", "2. Bundesliga", "Ligue 2", "Eredivisie", "Challenger", "Primera B", "J2 League"]

def tg(txt):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": txt, "parse_mode": "HTML"}, timeout=12)
    except: pass

def poisson_pmf(k, lam):
    try: return (math.exp(-lam) * (lam ** k)) / math.factorial(k)
    except: return 0

def calcul_poisson_xg(xG_dom=1.4, xG_ext=1.1, Att_H=1.2, Def_A=0.9, Att_A=1.0, Def_H=1.0):
    lam = Att_H * Def_A * xG_dom
    mu = Att_A * Def_H * xG_ext
    p1=pn=p2=0
    for i in range(6):
        for j in range(6):
            p = poisson_pmf(i, lam) * poisson_pmf(j, mu)
            if i>j: p1+=p
            elif i==j: pn+=p
            else: p2+=p
    tot=p1+pn+p2
    return (p1/tot, pn/tot, p2/tot, lam, mu) if tot>0 else (0.45,0.25,0.30,lam,mu)

def get_historique_pondere(team_id, headers):
    try:
        url = f"https://api-football-v1.p.rapidapi.com/v3/fixtures?team={team_id}&last=10"
        r = requests.get(url, headers=headers, timeout=12).json()
        if not r.get("response"): return 1.2, 1.0
        s_marques=s_encaisses=s_poids=0
        for fix in r["response"]:
            try:
                date_str=fix.get("fixture",{}).get("date","")[:10]
                date_match=datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                t_i=(datetime.date.today()-date_match).days
                W_i=math.exp(-0.05 * max(0,t_i))
                gh=fix.get("goals",{}).get("home",0) or 0
                ga=fix.get("goals",{}).get("away",0) or 0
                is_home=fix.get("teams",{}).get("home",{}).get("id")==team_id
                marques=gh if is_home else ga
                encaisses=ga if is_home else gh
                s_marques+=marques*W_i
                s_encaisses+=encaisses*W_i
                s_poids+=W_i
            except: continue
        if s_poids>0:
            Att=max(0.6,min(1.8,s_marques/s_poids/1.4+0.6))
            Def=max(0.6,min(1.8,s_encaisses/s_poids/1.1))
            return Att,Def
        return 1.2,1.0
    except: return 1.2,1.0

def get_complete_team_stats(home_team, away_team):
    global CACHE_RAPIDAPI_STATS
    cle=f"{home_team}_{away_team}"
    if cle in CACHE_RAPIDAPI_STATS: return CACHE_RAPIDAPI_STATS[cle]
    headers={"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
    try:
        today=datetime.date.today().isoformat()
        # IDs
        try:
            rh=requests.get(f"https://api-football-v1.p.rapidapi.com/v3/teams?search={home_team}", headers=headers, timeout=10).json()
            team_id_h=rh["response"][0]["team"]["id"] if rh.get("response") else None
            ra=requests.get(f"https://api-football-v1.p.rapidapi.com/v3/teams?search={away_team}", headers=headers, timeout=10).json()
            team_id_a=ra["response"][0]["team"]["id"] if ra.get("response") else None
        except: team_id_h=team_id_a=None
        
        Att_H,Def_H=get_historique_pondere(team_id_h, headers) if team_id_h else (1.2,1.0)
        Att_A,Def_A=get_historique_pondere(team_id_a, headers) if team_id_a else (1.0,0.9)
        
        # fixture id du jour
        fixture_id=None
        try:
            res_raw=requests.get(f"https://api-football-v1.p.rapidapi.com/v3/fixtures?date={today}", headers=headers, timeout=12)
            if res_raw.status_code not in [401,402,403,429]:
                for fix in res_raw.json().get("response",[]):
                    h=fix.get("teams",{}).get("home",{}).get("name","")
                    a=fix.get("teams",{}).get("away",{}).get("name","")
                    if home_team.lower()[:4] in h.lower() or away_team.lower()[:4] in a.lower():
                        fixture_id=fix.get("fixture",{}).get("id"); break
        except: pass
        
        res=(fixture_id, f"Decay W=e^-0.05t OK | {home_team} Att{Att_H:.2f} Def{Def_H:.2f} vs {away_team} Att{Att_A:.2f} Def{Def_A:.2f}", 1.4, 1.1, Att_H, Def_A, Att_A, Def_H)
        CACHE_RAPIDAPI_STATS[cle]=res
        return res
    except Exception as e:
        print(f"STATS ERR {e}")
        return (None,"Mode degrade",1.4,1.1,1.15,0.9,0.95,1.0)

def load_history():
    try:
        if os.path.exists(FICHIER_HISTORY):
            with open(FICHIER_HISTORY,"r") as f: return json.load(f)
    except: pass
    return {}
def save_history(h):
    try:
        with open(FICHIER_HISTORY,"w") as f: json.dump(h,f)
    except: pass

def detect_smart_money_v8(mid, C1_now, CN_now, C2_now):
    try:
        hist=load_history()
        if mid not in hist:
            hist[mid]={"C1_open":C1_now,"C2_open":C2_now,"C1_last":C1_now,"C2_last":C2_now}
            save_history(hist)
            return 0,0
        C1_open=hist[mid].get("C1_open",C1_now)
        C2_open=hist[mid].get("C2_open",C2_now)
        chute_C1=(hist[mid].get("C1_last",C1_open)-C1_now)/hist[mid].get("C1_last",C1_open) if hist[mid].get("C1_last") else 0
        chute_C2=(hist[mid].get("C2_last",C2_open)-C2_now)/hist[mid].get("C2_last",C2_open) if hist[mid].get("C2_last") else 0
        hist[mid]["C1_last"]=C1_now
        hist[mid]["C2_last"]=C2_now
        save_history(hist)
        return (0.05 if chute_C1>=0.07 else 0, 0.05 if chute_C2>=0.07 else 0)
    except: return 0,0

# ---------- CORRECTION 1 : QUE DU FOOT ----------
def get_all_odds():
    global DERNIER_APPEL_ODDS_TS, MEMOIRE_MATCHS_ODDS
    if time.time()-DERNIER_APPEL_ODDS_TS<7200 and MEMOIRE_MATCHS_ODDS: return MEMOIRE_MATCHS_ODDS
    try:
        # CORRECTION: soccer uniquement, pas upcoming (qui donne hockey)
        url=f"https://api.the-odds-api.com/v4/sports/soccer/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h&oddsFormat=decimal"
        r=requests.get(url,timeout=15)
        if r.status_code!=200:
            print(f"ODDS ERR {r.status_code} {r.text[:100]}")
            return MEMOIRE_MATCHS_ODDS
        DERNIER_APPEL_ODDS_TS=time.time()
        data=r.json()
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
                                res.append({"home":m.get("home_team"),"away":m.get("away_team"),"league":m.get("sport_title","Foot"),"commence_time":m.get("commence_time"),"C1":C1,"CN":CN,"C2":C2,"cotes":f"{C1} / {CN} / {C2}"})
                            break
                    break
            except: continue
        MEMOIRE_MATCHS_ODDS=res[:200]
        print(f"ODDS V9 FOOT ONLY: {len(MEMOIRE_MATCHS_ODDS)} matchs")
        return MEMOIRE_MATCHS_ODDS
    except Exception as e:
        print(f"ODDS V9 ERR {e}")
        return MEMOIRE_MATCHS_ODDS

def simuler_tous_marches(C1,CN,C2,P1,PN,P2,lam,mu,mise_totale=200):
    CN_safe=max(CN,1.01)
    Cote_DNB=C1*(1.0-(1.0/CN_safe))
    Proba_DNB=P1/(P1+P2) if (P1+P2)>0 else 0.5
    Value_DNB=Proba_DNB*Cote_DNB
    Mise_Nul=mise_totale/CN_safe
    Mise_Vic=mise_totale-Mise_Nul
    return {"DNB_cote":round(Cote_DNB,2),"DNB_proba":round(Proba_DNB*100,1),"DNB_value":round(Value_DNB,3),"valide":Value_DNB>=1.15,"mise_victoire":round(Mise_Vic,1),"mise_nul":round(Mise_Nul,1),"mise_totale":mise_totale,"P1":P1,"lam":lam,"mu":mu}

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

def mise_amorcage(bankroll):
    if bankroll<300: return 0
    if bankroll<5000: return 200
    if bankroll<15000: return 500
    return int(bankroll*0.05)

def core_analysis_routine(force=False):
    try:
        memo=local_load()
        if not force and len(memo.get("suivi_attente",[]))>0:
            print("VERROU V9 ACTIF"); return
        today=str(datetime.date.today())
        if memo.get("date")!=today:
            memo.update({"date":today,"coupons_jour":0,"matchs_analyses":[]}); local_save(memo)
        if not force and memo.get("coupons_jour",0)>=5: return
        
        matchs=get_all_odds()
        if not matchs: return
        
        bons_coupons=[]
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
                
                # CORRECTION 2: BOOST D2/D3
                bonus_D2D3=0
                seuil_value=1.15
                is_D2D3=any(x.lower() in m["league"].lower() for x in LIGUES_D2_D3_VALUE)
                if MODE_COMBINE_ACTIF:
                    if is_D2D3:
                        bonus_D2D3=0.04 # +4% proba car plus de value
                        seuil_value=1.12 # plus tolérant
                        print(f"BONUS D2/D3 APPLIQUÉ {m['league']}")
                    else:
                        seuil_value=1.20 # plus strict sur gros championnats
                
                P1_aj=max(0.05,min(0.85,P1_p+boost_P1+bonus_D2D3))
                P2_aj=max(0.05,min(0.85,P2_p+boost_P2))
                tot=P1_aj+PN_p+P2_aj
                P1_aj,PN_aj,P2_aj=P1_aj/tot,PN_p/tot,P2_aj/tot
                
                mise_tot=mise_amorcage(memo.get("bankroll",1000))
                if mise_tot==0: tg("🛑 STOP BANKROLL <300"); return
                
                mar=simuler_tous_marches(m["C1"],m["CN"],m["C2"],P1_aj,PN_aj,P2_aj,lam,mu,mise_tot)
                
                # Application seuil adapté
                mar["valide"]=mar["DNB_value"]>=seuil_value
                
                # LOG EN ARRIERE PLAN, PAS DANS TELEGRAM
                print(f"V9 CALC BG: {m['home']} vs {m['away']} | {m['league']} | P1 {P1_aj*100:.1f}% lam {lam:.2f} mu {mu:.2f} | DNB {mar['DNB_cote']} Value {mar['DNB_value']} is_D2D3={is_D2D3} | {det}")
                
                if mar["valide"]:
                    bons_coupons.append((m,mar,fid))
                    if not force and len(bons_coupons)>=1: break
        
        if not bons_coupons: 
            print("V9: Aucun coupon value")
            return
        
        # CORRECTION 4 & 5: MESSAGE CLEAN EN FRANCAIS - STYLE COUPON 1XBET
        m,mar,fid=bons_coupons[0]
        
        # On demande à Groq UNIQUEMENT une justification courte en FRANCAIS
        try:
            prompt_fr = f"Match foot {m['home']} vs {m['away']} {m['league']}. Donne en 1 phrase courte en FRANÇAIS pourquoi le domicile a l'avantage. Pas de stats."
            c=Groq(api_key=GROQ_API_KEY).chat.completions.create(
                messages=[{"role":"system","content":"Tu es analyste foot français. Réponse en 1 phrase courte en français, pas de tableau."},{"role":"user","content":prompt_fr}],
                model="openai/gpt-oss-20b",temperature=0.3,max_tokens=150
            )
            justification_fr=c.choices[0].message.content.strip()[:120]
        except:
            justification_fr="Avantage forme récente à domicile."
        
        # COUPON FINAL LISIBLE
        texte_coupon = f"""🔥 COUPON V9 - {m['league']}
⚽️ {m['home']} vs {m['away']}

🎯 MARCHÉ À MISER : 
👉 DNB - Victoire {m['home']} (Remboursé si Nul)

💰 COTE : {mar['DNB_cote']}
📈 RÉUSSITE : {mar['DNB_proba']}%
✅ VALUE : {mar['DNB_value']} (Seuil 1.15)
📊 COTES 1xBet : {m['cotes']}

💵 MISE : 
• {mar['mise_victoire']} FCFA sur Victoire {m['home']}
• {mar['mise_nul']} FCFA sur Nul (couverture)
= {mar['mise_totale']} FCFA Total

🧠 Analyse : {justification_fr}
"""
        memo["coupons_jour"]+=1
        memo["matchs_analyses"].append(f"{m['home']}_{m['away']}")
        memo["suivi_attente"].append({"fixture_id":fid,"home":m["home"],"away":m["away"],"marche":"DNB","cote":mar["DNB_cote"],"date":today})
        local_save(memo)
        tg(texte_coupon)

    except Exception as e:
        print(f"CORE V9 ERR {e}")

@app.route('/')
def home_page():
    try: threading.Thread(target=core_analysis_routine).start()
    except: pass
    return "V9 CLEAN - FOOT ONLY + COMBINE + FR + COUPON LISIBLE",200

@app.route('/force')
def force_route():
    try: threading.Thread(target=lambda: core_analysis_routine(force=True)).start()
    except: pass
    return "Force V9 lancee",200

@app.route('/webhook',methods=['POST'])
def telegram_webhook():
    try:
        data=request.get_json(force=True,silent=True)
        if not data or "message" not in data: return "ok",200
        txt=data["message"].get("text","").strip()
        memo=local_load()
        if txt.lower()=="/stats":
            tot=memo.get('gagnants',0)+memo.get('perdants',0)
            tg(f"📊 V9 STATS\nCoupons jour: {memo.get('coupons_jour')}/5\nVerrou: {len(memo.get('suivi_attente',[]))} en attente\nMode: Foot Only + D2/D3 boosté")
        elif txt.lower().startswith("/vs"):
            eq=txt[4:].split("-")
            if len(eq)!=2: return "ok",200
            h,a=eq[0].strip(),eq[1].strip()
            tg(f"🔍 Analyse V9 en cours {h} vs {a}...")
            try:
                fid,det,xG_dom,xG_ext,Att_H,Def_A,Att_A,Def_H=get_complete_team_stats(h,a)
                P1,PN,P2,lam,mu=calcul_poisson_xg(xG_dom,xG_ext,Att_H,Def_A,Att_A,Def_H)
                mar=simuler_tous_marches(1.85,3.4,4.0,P1,PN,P2,lam,mu,200)
                # Meme format clean
                texte=f"""🔥 ANALYSE MANUELLE V9
⚽️ {h} vs {a}

🎯 MARCHÉ : DNB Victoire {h}
💰 COTE : {mar['DNB_cote']}
📈 RÉUSSITE : {mar['DNB_proba']}%
✅ VALUE : {mar['DNB_value']}

💵 MISE : {mar['mise_victoire']} + {mar['mise_nul']} Nul = {mar['mise_totale']} FCFA

Calculs faits en arrière-plan (decay W=e^-0.05t)
"""
                tg(texte)
            except Exception as e:
                tg(f"⚠️ Erreur /vs V9 {e}")
    except Exception as e:
        print(f"WEBHOOK V9 ERR {e}")
    return "ok",200

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)))
