import os, json, requests, datetime, threading, time, math
from flask import Flask, request
from groq import Groq

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")

FICHIER_STATS = "/tmp/stats.json"
DERNIER_APPEL_ODDS_TS = 0
MEMOIRE_MATCHS_ODDS = []
CACHE_RAPIDAPI_STATS = {}
app = Flask(__name__)

MODE_COMBINE_ACTIF = True
LIGUES_D2_D3_VALUE = ["Championship", "League One", "LaLiga2", "Serie B", "2. Bundesliga", "Ligue 2", "Eredivisie"]

def tg(txt):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": txt, "parse_mode": "HTML"}, timeout=12)
    except Exception as e:
        print(f"TG ERR {e}")

def poisson_pmf(k, lam):
    if lam <= 0 or k < 0: return 0
    try: return (math.exp(-lam) * (lam ** k)) / math.factorial(int(k))
    except: return 0

def generer_probabilites_matrice(lam, mu):
    matrice = {}; p1=pn=p2=0; p_btts=0
    for i in range(6):
        for j in range(6):
            p = poisson_pmf(i, lam) * poisson_pmf(j, mu)
            matrice[f"{i}-{j}"] = p
            if i>j: p1+=p
            elif i==j: pn+=p
            else: p2+=p
            if i>0 and j>0: p_btts+=p
    tot=sum(matrice.values())
    if tot>0:
        for k in matrice: matrice[k]/=tot
        p1/=tot; pn/=tot; p2/=tot; p_btts/=tot
    else:
        p1,pn,p2,p_btts=0.45,0.25,0.30,0.50
    return matrice,p1,pn,p2,p_btts

def extraire_over_under(matrice, seuil):
    p_over=0
    for score_str,p in matrice.items():
        i,j=map(int,score_str.split("-"))
        if (i+j)>seuil: p_over+=p
    return p_over,1.0-p_over

def get_historique_pondere(team_id, headers):
    try:
        url=f"https://api-football-v1.p.rapidapi.com/v3/fixtures?team={team_id}&last=10"
        r=requests.get(url,headers=headers,timeout=12).json()
        if not r or not r.get("response"): return 1.2,1.0
        s_marques=s_encaisses=s_poids=0
        for fix in r["response"]:
            try:
                date_str=fix.get("fixture",{}).get("date","")[:10]
                date_match=datetime.datetime.strptime(date_str,"%Y-%m-%d").date()
                t_i=(datetime.date.today()-date_match).days
                W_i=math.exp(-0.05*max(0,t_i))
                gh=fix.get("goals",{}).get("home",0) or 0
                ga=fix.get("goals",{}).get("away",0) or 0
                is_home=fix.get("teams",{}).get("home",{}).get("id")==team_id
                marques=gh if is_home else ga
                encaisses=ga if is_home else gh
                s_marques+=marques*W_i; s_encaisses+=encaisses*W_i; s_poids+=W_i
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
    headers={"X-RapidAPI-Key":RAPIDAPI_KEY,"X-RapidAPI-Host":"api-football-v1.p.rapidapi.com"}
    try:
        team_id_h=team_id_a=None
        try:
            rh=requests.get(f"https://api-football-v1.p.rapidapi.com/v3/teams?search={home_team}",headers=headers,timeout=10).json()
            if rh.get("response"): team_id_h=rh["response"][0]["team"]["id"]
            ra=requests.get(f"https://api-football-v1.p.rapidapi.com/v3/teams?search={away_team}",headers=headers,timeout=10).json()
            if ra.get("response"): team_id_a=ra["response"][0]["team"]["id"]
        except: pass
        Att_H,Def_H=get_historique_pondere(team_id_h,headers) if team_id_h else (1.2,1.0)
        Att_A,Def_A=get_historique_pondere(team_id_a,headers) if team_id_a else (1.0,0.9)
        res=(None,1.4,1.1,Att_H,Def_A,Att_A,Def_H)
        CACHE_RAPIDAPI_STATS[cle]=res
        return res
    except:
        return (None,1.4,1.1,1.15,0.9,0.95,1.0)

def get_all_odds():
    global DERNIER_APPEL_ODDS_TS,MEMOIRE_MATCHS_ODDS
    if time.time()-DERNIER_APPEL_ODDS_TS<3600 and MEMOIRE_MATCHS_ODDS: return MEMOIRE_MATCHS_ODDS
    try:
        url=f"https://api.the-odds-api.com/v4/sports/soccer/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h&oddsFormat=decimal&bookmakers=1xbet,pinnacle"
        r=requests.get(url,timeout=15)
        if r.status_code!=200:
            url=f"https://api.the-odds-api.com/v4/sports/soccer/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h&oddsFormat=decimal"
            r=requests.get(url,timeout=15)
            if r.status_code!=200: return MEMOIRE_MATCHS_ODDS
        data=r.json()
        res=[]
        for m in data:
            try:
                league=m.get("sport_title","")
                if any(x in league.lower() for x in ["esports","virtual","women","u19","u21","reserve","youth"]): continue
                bks=sorted(m.get("bookmakers",[]), key=lambda x: 0 if '1xbet' in x['key'] else 1)
                for bk in bks[:1]:
                    for mk in bk.get("markets",[]):
                        if mk.get("key")=="h2h":
                            C1=CN=C2=0
                            for o in mk.get("outcomes",[]):
                                name=o.get("name","").lower()
                                price=float(o.get("price",0))
                                if price>15 or price<1.01: continue
                                if m.get("home_team","").lower() in name: C1=price
                                elif m.get("away_team","").lower() in name: C2=price
                                elif "draw" in name: CN=price
                            if C1>0 and CN>0 and C2>0 and C1<15 and C2<15 and CN<8 and CN>2.2:
                                res.append({"home":m.get("home_team"),"away":m.get("away_team"),"league":league,"commence_time":m.get("commence_time"),"C1":C1,"CN":CN,"C2":C2,"cotes":f"{C1} / {CN} / {C2}","bookmaker":bk.get("key")})
                            break
                    break
            except: continue
        MEMOIRE_MATCHS_ODDS=res[:100]
        DERNIER_APPEL_ODDS_TS=time.time()
        print(f"V11 CLEAN: {len(res)} matchs reels")
        return MEMOIRE_MATCHS_ODDS
    except Exception as e:
        print(f"ODDS ERR {e}")
        return MEMOIRE_MATCHS_ODDS

def simuler_catalogue_marches(C1,CN,C2,lam,mu,seuil_value,mise_totale=200):
    if C1>15 or C1<1.1 or C2>15 or C2<1.1 or CN>8 or CN<2.2: return None
    matrice_match,p1,pn,p2,btts=generer_probabilites_matrice(lam,mu)
    matrice_m1,p1_m1,pn_m1,p2_m1,_=generer_probabilites_matrice(lam*0.45,mu*0.45)
    CN_safe=max(CN,1.01)
    marge=0.92
    o15,_=extraire_over_under(matrice_match,1.5)
    o25,u25=extraire_over_under(matrice_match,2.5)
    o35,u35=extraire_over_under(matrice_match,3.5)

    catalog=[
        {"nom":"DNB - Victoire Domicile (Remboursé si Nul)","cote":(C1*(1.0-(1.0/CN_safe))),"proba":p1/(p1+p2) if (p1+p2)>0 else 0.5,"cat":"DNB"},
        {"nom":"Double Chance - 1X","cote":(1/((1/C1)+(1/CN_safe)))*marge,"proba":p1+pn,"cat":"1X"},
        {"nom":"Les Deux Équipes Marquent (BTTS)","cote":(1/max(0.01,btts))*marge,"proba":btts,"cat":"BTTS"},
        {"nom":"Plus de 1.5 Buts","cote":(1/max(0.01,o15))*marge,"proba":o15,"cat":"O15"},
        {"nom":"Plus de 2.5 Buts","cote":(1/max(0.01,o25))*marge,"proba":o25,"cat":"O25"},
        {"nom":"Moins de 2.5 Buts","cote":(1/max(0.01,u25))*marge,"proba":u25,"cat":"U25"},
        {"nom":"Moins de 3.5 Buts","cote":(1/max(0.01,u35))*marge,"proba":u35,"cat":"U35"},
        {"nom":"1ère Mi-temps - Victoire Domicile","cote":(1/max(0.01,p1_m1))*marge,"proba":p1_m1,"cat":"M1"},
    ]
    for item in catalog:
        item["value"]=round(item["proba"]*item["cote"],3)
        item["cote"]=round(item["cote"],2)
        item["proba"]=round(item["proba"]*100,1)
        if item["cote"]>5.0 or item["cote"]<1.1: item["value"]=0

    catalog_trie=sorted([x for x in catalog if x["cote"]>1.25 and x["value"]>0], key=lambda x: x["value"], reverse=True)
    if not catalog_trie: return None
    meilleur=catalog_trie[0]
    m_principale=mise_totale if meilleur["cat"] not in ["DNB","1X"] else (mise_totale*0.75)
    m_couverture=0 if m_principale==mise_totale else (mise_totale-m_principale)
    return {"marche_choisi":meilleur["nom"],"cote":meilleur["cote"],"proba":meilleur["proba"],"value":meilleur["value"],"valide":meilleur["value"]>=seuil_value,"mise_principale":round(m_principale,1),"mise_couverture":round(m_couverture,1),"mise_totale":mise_totale}

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
        if not force and len(memo.get("suivi_attente",[]))>0: return
        today=str(datetime.date.today())
        if memo.get("date")!=today:
            memo.update({"date":today,"coupons_jour":0,"matchs_analyses":[]})
            local_save(memo)
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
                fid,xG_dom,xG_ext,Att_H,Def_A,Att_A,Def_H=get_complete_team_stats(m["home"],m["away"])
                lam=max(0.1,Att_H*Def_A*xG_dom)
                mu=max(0.1,Att_A*Def_H*xG_ext)
                seuil_value=1.15
                if MODE_COMBINE_ACTIF and any(x.lower() in m["league"].lower() for x in LIGUES_D2_D3_VALUE): seuil_value=1.12
                bankroll=memo.get("bankroll",1000)
                mise_tot=200 if bankroll<5000 else int(bankroll*0.05)
                if bankroll<300: return
                mar=simuler_catalogue_marches(m["C1"],m["CN"],m["C2"],lam,mu,seuil_value,mise_tot)
                if mar is None: continue
                if mar["valide"]:
                    bons_coupons.append((m,mar,fid))
                    if not force: break
        if not bons_coupons: return
        m,mar,fid=bons_coupons[0]
        try:
            c=Groq(api_key=GROQ_API_KEY).chat.completions.create(messages=[{"role":"system","content":"Tu es analyste foot pro. Réponse en 1 phrase courte en français."},{"role":"user","content":f"Match {m['home']} - {m['away']}. Pourquoi {mar['marche_choisi']} à {mar['cote']} est bon?"}],model="openai/gpt-oss-120b",temperature=0.2,max_tokens=100)
            justification=c.choices[0].message.content.strip()[:120]
        except:
            justification="Modélisation matricielle optimale."
        texte=f"🔥 <b>COUPON V11 CLEAN</b>\n⚽️ {m['home']} vs {m['away']}\n🏆 {m['league']}\n\n🎯 <b>{mar['marche_choisi']}</b>\n\n💰 COTE {m['bookmaker']}: {mar['cote']}\n📊 COTES: {m['cotes']}\n📈 {mar['proba']}% - Value {mar['value']}\n\n💵 {mar['mise_principale']} + {mar['mise_couverture']} = {mar['mise_totale']} FCFA\n\n🧠 {justification}"
        memo["coupons_jour"]+=1
        memo["matchs_analyses"].append(f"{m['home']}_{m['away']}")
        memo["suivi_attente"].append({"fixture_id":fid,"home":m["home"],"away":m["away"],"marche":mar["marche_choisi"],"cote":mar["cote"],"date":today})
        local_save(memo)
        tg(texte)
    except Exception as e:
        print(f"CORE ERR {e}")

@app.route('/')
def home_page():
    try: threading.Thread(target=core_analysis_routine).start()
    except: pass
    return "V11 CLEAN ACTIF",200

@app.route('/force')
def force_route():
    try: threading.Thread(target=lambda: core_analysis_routine(force=True)).start()
    except: pass
    return "Force V11 lancée",200

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        data=request.get_json(force=True,silent=True)
        if not data or "message" not in data: return "ok",200
        txt=data["message"].get("text","").strip()
        memo=local_load()
        if txt.lower()=="/stats":
            tg(f"📊 <b>V11 CLEAN</b>\nCoupons: {memo.get('coupons_jour')}/5\nEn attente: {len(memo.get('suivi_attente',[]))}")
        elif txt.lower().startswith("/vs"):
            eq=txt[3:].strip().split("-")
            if len(eq)!=2:
                tg("❌ Format: /vs Arsenal - Chelsea")
                return "ok",200
            h,a=eq[0].strip(),eq[1].strip()
            tg(f"🔍 Analyse V11 CLEAN {h} vs {a}...")
            matchs=get_all_odds()
            found=None
            for m in matchs:
                if h.lower()[:4] in m["home"].lower() and a.lower()[:4] in m["away"].lower():
                    found=m; break
            if found:
                C1,C2,CN=found["C1"],found["C2"],found["CN"]
                league=found["league"]; cotes_str=found["cotes"]; book=found["bookmaker"]
            else:
                C1,C2,CN=2.0,3.5,3.3
                league="Estimation"; cotes_str=f"{C1} / {CN} / {C2} (Estimation)"; book="estimation"
            fid,xG_dom,xG_ext,Att_H,Def_A,Att_A,Def_H=get_complete_team_stats(h,a)
            lam=max(0.1,Att_H*Def_A*xG_dom)
            mu=max(0.1,Att_A*Def_H*xG_ext)
            mar=simuler_catalogue_marches(C1,CN,C2,lam,mu,1.15,200)
            if mar is None:
                tg(f"❌ Cotes invalides {cotes_str} - rejeté")
                return "ok",200
            texte=f"🔥 <b>ANALYSE V11 CLEAN</b>\n⚽️ {h} vs {a}\n🏆 {league}\n\n🎯 {mar['marche_choisi']}\n💰 COTE {book}: {mar['cote']}\n📊 COTES: {cotes_str}\n📈 {mar['proba']}% - Value {mar['value']}\n\n💵 {mar['mise_principale']} + {mar['mise_couverture']} = {mar['mise_totale']} FCFA"
            tg(texte)
    except Exception as e:
        print(f"WEBHOOK ERR {e}")
    return "ok",200

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
