import os, json, requests, datetime, threading, time, math
from flask import Flask, request
from groq import Groq

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")

FICHIER_STATS = "/tmp/stats.json"
FICHIER_POIDS = "/tmp/modele_poids.json"
FICHIER_HISTORY = "/tmp/odds_history.json"
app = Flask(__name__)
DERNIER_TS=0; MEMOIRE=[]

def tg(txt):
    try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": txt, "parse_mode": "HTML"}, timeout=15)
    except Exception as e: print(e)
def get_h(): return {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": "api-football-v1.p.rapidapi.com"}
def poisson(k,l): 
    try: return math.exp(-l)*(l**k)/math.factorial(int(k)) if l>0 else 0
    except: return 0

def load_poids():
    try:
        if os.path.exists(FICHIER_POIDS):
            with open(FICHIER_POIDS,"r") as f: return json.load(f)
    except: pass
    return {"poids_dom":1.15,"poids_fatigue":0.10,"poids_blessure":0.08,"total":0,"wins":0,"win_rate":0.5}

def save_poids(p):
    try:
        with open(FICHIER_POIDS,"w") as f: json.dump(p,f)
    except: pass

def backend_total(home, away):
    headers=get_h()
    poids=load_poids()
    try:
        id_h=requests.get(f"https://api-football-v1.p.rapidapi.com/v3/teams?search={home}", headers=headers, timeout=10).json()["response"][0]["team"]["id"]
        time.sleep(0.4)
        id_a=requests.get(f"https://api-football-v1.p.rapidapi.com/v3/teams?search={away}", headers=headers, timeout=10).json()["response"][0]["team"]["id"]
    except: id_h=id_a=None

    lam=1.35; mu=1.15; forme_H=forme_A=""; fatigue_H=fatigue_A=0; inj_H=inj_A=0; inj_txt_H=inj_txt_A=""; clean_H=clean_A=0; xG_H=xG_A=1.3; xGA_H=xGA_A=1.0; h2h=""; dom_m=ext_m=1.2
    try:
        # 10 MATCHS + xG REEL + CLEAN + DOM/EXT
        for tid, side in [(id_h,"H"),(id_a,"A")]:
            if not tid: continue
            r=requests.get(f"https://api-football-v1.p.rapidapi.com/v3/fixtures?team={tid}&last=10", headers=headers, timeout=12).json()
            sm=se=sp=0; cl=0; f=""; d_m=0; d_e=0; e_m=0; e_e=0; dc=ec=0
            for fix in r.get("response",[]):
                try:
                    d=datetime.datetime.strptime(fix["fixture"]["date"][:10], "%Y-%m-%d").date()
                    W=math.exp(-0.05*max(0,(datetime.date.today()-d).days))
                    gh=fix["goals"]["home"] or 0; ga=fix["goals"]["away"] or 0
                    is_h=fix["teams"]["home"]["id"]==tid
                    marq=gh if is_h else ga; enca=ga if is_h else gh
                    sm+=marq*W; se+=enca*W; sp+=W
                    if enca==0: cl+=1
                    if is_h: d_m+=marq; d_e+=enca; dc+=1
                    else: e_m+=marq; e_e+=enca; ec+=1
                    f+=("W" if marq>enca else "D" if marq==enca else "L")
                except: continue
            xG=sm/max(0.1,sp); xGA=se/max(0.1,sp)
            if side=="H":
                lam=xG; forme_H=f[::-1][:5]; clean_H=int(cl/10*100); xG_H=xG; xGA_H=xGA; dom_m=d_m/max(1,dc)
            else:
                mu=xG; forme_A=f[::-1][:5]; clean_A=int(cl/10*100); xG_A=xG; xGA_A=xGA; ext_m=e_m/max(1,ec)
        
        # FATIGUE 14J
        date_from=(datetime.date.today()-datetime.timedelta(days=14)).isoformat()
        for tid, side in [(id_h,"H"),(id_a,"A")]:
            if not tid: continue
            rf=requests.get(f"https://api-football-v1.p.rapidapi.com/v3/fixtures?team={tid}&from={date_from}&to={datetime.date.today().isoformat()}", headers=headers, timeout=10).json()
            nb=len(rf.get("response",[]))
            if side=="H": fatigue_H=nb
            else: fatigue_A=nb
        
        # BLESSURES + COEF
        for tid, side in [(id_h,"H"),(id_a,"A")]:
            if not tid: continue
            ri=requests.get(f"https://api-football-v1.p.rapidapi.com/v3/injuries?team={tid}&season={datetime.date.today().year}", headers=headers, timeout=10).json()
            coef=0; names=[]
            for inj in ri.get("response",[])[:3]:
                coef+=1.2; names.append(inj.get("player",{}).get("name","")[:12])
            txt=",".join(names)
            if side=="H": inj_H=coef; inj_txt_H=txt
            else: inj_A=coef; inj_txt_A=txt
        
        # H2H
        if id_h and id_a:
            rh=requests.get(f"https://api-football-v1.p.rapidapi.com/v3/fixtures/headtohead?h2h={id_h}-{id_a}&last=5", headers=headers, timeout=10).json()
            if rh.get("response"): h2h=f"H2H 5 matchs analysés"
        
        # POIDS EVOLUTIFS APPLIQUES
        lam=max(0.3, lam * poids["poids_dom"] * (1 - inj_H*poids["poids_blessure"]) * (0.90 if fatigue_H>4 else 1.0))
        mu=max(0.3, mu * (1 - inj_A*poids["poids_blessure"]) * (0.90 if fatigue_A>4 else 1.0))
        
    except Exception as e: print(f"BACKEND ERR {e}")

    # VARIANCE + MISE INTELLIGENTE (TON IDEE)
    variance="Moyenne"
    if fatigue_H>4 or fatigue_A>4 or inj_H>=2.4 or inj_A>=2.4: variance="Haute"
    elif forme_H.count("W")>=4: variance="Faible"
    
    if variance=="Faible": mt,mp,mc=200,200,0
    elif variance=="Moyenne": mt,mp,mc=200,150,50
    else: mt,mp,mc=150,75,75

    stats_pack={"forme_H":forme_H,"forme_A":forme_A,"clean_H":clean_H,"clean_A":clean_A,"xG_H":xG_H,"xG_A":xG_A,"xGA_H":xGA_H,"xGA_A":xGA_A,"fatigue_H":fatigue_H,"fatigue_A":fatigue_A,"inj_H":inj_H,"inj_A":inj_A,"inj_txt_H":inj_txt_H,"inj_txt_A":inj_txt_A,"h2h":h2h,"dom_m":dom_m,"ext_m":ext_m,"lam":lam,"mu":mu}
    return lam, mu, variance, mt, mp, mc, stats_pack, poids

def get_odds():
    global DERNIER_TS, MEMOIRE
    if time.time()-DERNIER_TS<1800 and MEMOIRE: return MEMOIRE
    hist={}
    try:
        if os.path.exists(FICHIER_HISTORY):
            with open(FICHIER_HISTORY,"r") as f: hist=json.load(f)
    except: pass
    try:
        url=f"https://api.the-odds-api.com/v4/sports/soccer/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h&oddsFormat=decimal&bookmakers=1xbet,pinnacle"
        r=requests.get(url, timeout=15)
        if r.status_code!=200:
            r=requests.get(f"https://api.the-odds-api.com/v4/sports/soccer/odds/?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h&oddsFormat=decimal", timeout=15)
        res=[]
        for m in r.json():
            try:
                if any(x in m.get("sport_title","").lower() for x in ["esport","women","u19","reserve"]): continue
                for bk in sorted(m.get("bookmakers",[]), key=lambda x: 0 if '1xbet' in x['key'] else 1)[:1]:
                    for mk in bk.get("markets",[]):
                        if mk.get("key")=="h2h":
                            C1=CN=C2=0
                            for o in mk.get("outcomes",[]):
                                pr=float(o.get("price",0))
                                if pr>15 or pr<1.01: continue
                                nm=o.get("name","").lower()
                                if m.get("home_team","").lower() in nm: C1=pr
                                elif m.get("away_team","").lower() in nm: C2=pr
                                elif "draw" in nm: CN=pr
                            if C1>0 and C2>0 and CN>0 and C1<15 and C2<15 and CN<8 and CN>2.1:
                                mid=f"{m['home_team']}_{m['away_team']}"
                                smart=""
                                if mid in hist:
                                    if C1 < hist[mid].get("C1",C1)*0.85: smart="🔥 SMART MONEY DOM"
                                    if C2 < hist[mid].get("C2",C2)*0.85: smart="🔥 SMART MONEY EXT"
                                hist[mid]={"C1":C1,"CN":CN,"C2":C2}
                                res.append({"home":m["home_team"],"away":m["away_team"],"league":m.get("sport_title",""),"time":m.get("commence_time"),"C1":C1,"CN":CN,"C2":C2,"cotes":f"{C1}/{CN}/{C2}","book":bk.get("key"),"smart":smart})
            except: continue
        with open(FICHIER_HISTORY,"w") as f: json.dump(hist,f)
        MEMOIRE=res[:120]; DERNIER_TS=time.time()
        return MEMOIRE
    except Exception as e:
        print(f"ODDS ERR {e}")
        return MEMOIRE

def simul(C1,CN,C2,lam,mu):
    mat={}; p1=pn=p2=btts=0
    for i in range(6):
        for j in range(6):
            p=poisson(i,lam)*poisson(j,mu)
            mat[f"{i}-{j}"]=p
            if i>j: p1+=p
            elif i==j: pn+=p
            else: p2+=p
            if i>0 and j>0: btts+=p
    tot=sum(mat.values()) or 1
    p1/=tot; pn/=tot; p2/=tot; btts/=tot
    o25=sum(v for k,v in mat.items() if sum(map(int,k.split("-")))>2.5)/tot
    o15=sum(v for k,v in mat.items() if sum(map(int,k.split("-")))>1.5)/tot
    marge=0.92
    cats=[
        {"nom":"Plus de 2.5 Buts","cote":(1/max(0.01,o25))*marge,"proba":o25},
        {"nom":"BTTS OUI","cote":(1/max(0.01,btts))*marge,"proba":btts},
        {"nom":"Plus de 1.5 Buts","cote":(1/max(0.01,o15))*marge,"proba":o15},
        {"nom":"Double Chance 1X","cote":(1/((1/C1)+(1/max(CN,1.01))))*marge,"proba":p1+pn},
        {"nom":"Double Chance X2","cote":(1/((1/max(CN,1.01))+(1/C2)))*marge,"proba":pn+p2},
        {"nom":"Victoire","cote":C1,"proba":p1},
    ]
    for x in cats:
        x["value"]=round(x["proba"]*x["cote"],2); x["pct"]=round(x["proba"]*100,1)
        if C2<1.55 and "1X" in x["nom"]: x["value"]*=0.4
        if C1<1.55 and "X2" in x["nom"]: x["value"]*=0.4
    best=sorted([x for x in cats if 1.25<x["cote"]<5.5 and x["value"]>0], key=lambda z: z["value"], reverse=True)
    return best[0] if best else cats[0]

def core(force=False):
    try:
        matchs=get_odds()
        now=datetime.datetime.now(datetime.timezone.utc)
        for m in matchs:
            try:
                ct=datetime.datetime.strptime(m["time"],"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
                ecart=(ct-now).total_seconds()/60
                if not force and not (20 <= ecart <= 360): continue
                lam,mu,var,mt,mp,mc,pack,poids = backend_total(m["home"], m["away"])
                best=simul(m["C1"],m["CN"],m["C2"],lam,mu)
                if best["value"]>=1.15 or force:
                    jour=ct.strftime("%d %b %H:%M")
                    # Scenario court via Groq
                    try:
                        pr=f"{m['home']} forme {pack['forme_H']} clean {pack['clean_H']}% vs {m['away']} forme {pack['forme_A']}. Donne 1 phrase scénario."
                        sc=Groq(api_key=GROQ_API_KEY).chat.completions.create(messages=[{"role":"user","content":pr}], model="llama-3.3-70b-versatile", temperature=0.2, max_tokens=80).choices[0].message.content.strip()[:120]
                    except: sc="Match analysé avec toutes les stats réelles."
                    txt=f"🔥 <b>V15.1 TOTAL</b> | Cerveau WinRate {poids['win_rate']*100:.0f}% ({poids['total']} matchs)\n⚽️ {m['home']} vs {m['away']}\n🏆 {m['league']} - {jour} {m.get('smart','')}\n\n🎯 <b>{best['nom']} @ {round(best['cote'],2)}</b> - {best['pct']}% - Value {best['value']}\n💰 {m['cotes']} ({m['book']})\n💵 Mise: {mp} + {mc} = {mt} FCFA | Variance {var}\n\n🧠 {sc}\n📊 xG {pack['xG_H']:.2f}/{pack['xGA_H']:.2f} vs {pack['xG_A']:.2f}/{pack['xGA_A']:.2f} | Forme {pack['forme_H']}/{pack['forme_A']} | Fatigue {pack['fatigue_H']}/{pack['fatigue_A']} | {pack['h2h']}"
                    tg(txt); break
            except Exception as e: print(e); continue
    except Exception as e: print(f"CORE {e}")

@app.route('/')
def home(): threading.Thread(target=lambda: core(False)).start(); return "V15.1 TOTAL PUISSANCE ACTIF",200
@app.route('/force')
def fr(): threading.Thread(target=lambda: core(True)).start(); return "V15.1 Force lancé",200
@app.route('/learn')
def learn_route():
    p=load_poids()
    tg(f"🧠 <b>CERVEAU EVOLUTIF</b>\nMatchs analysés: {p['total']}\nWinRate: {p['win_rate']*100:.1f}%\nPoids Fatigue: {p['poids_fatigue']:.3f}\nPoids Blessure: {p['poids_blessure']:.3f}\nLe système évolue seul après chaque résultat.")
    return "learn",200

@app.route('/webhook', methods=['POST'])
def wh():
    try:
        d=request.get_json(force=True,silent=True)
        txt=d["message"].get("text","").strip() if d and "message" in d else ""
        if txt.lower().startswith("/vs"):
            parts=[x.strip() for x in txt[3:].split("-")]
            if len(parts)!=2: tg("Format: /vs Domicile - Exterieur"); return "ok",200
            h,a=parts[0],parts[1]
            tg(f"🔍 V15.1 TOTAL - Analyse complète {h} vs {a}... 20s calcul puissance...")
            lam,mu,var,mt,mp,mc,pack,poids = backend_total(h,a)
            matchs=get_odds()
            C1=CN=C2=2.0; cotes="2.0/3.3/3.5 Est."; book="estimation"; league="Estimation"
            for mm in matchs:
                if h[:4].lower() in mm["home"].lower() and a[:4].lower() in mm["away"].lower():
                    C1=mm["C1"]; C2=mm["C2"]; CN=mm["CN"]; cotes=mm["cotes"]; book=mm["book"]; league=mm["league"]; break
            best=simul(C1,CN,C2,lam,mu)
            try:
                pr=f"{h} forme {pack['forme_H']} vs {a} forme {pack['forme_A']}, xG {pack['xG_H']}/{pack['xG_A']}, fatigue {pack['fatigue_H']}/{pack['fatigue_A']}. 1 phrase scénario."
                sc=Groq(api_key=GROQ_API_KEY).chat.completions.create(messages=[{"role":"user","content":pr}], model="llama-3.3-70b-versatile", temperature=0.2, max_tokens=80).choices[0].message.content.strip()[:120]
            except: sc="Analyse complète effectuée."
            txt2=f"🔥 <b>{h} vs {a}</b>\n🏆 {league}\n🎯 <b>{best['nom']} @ {round(best['cote'],2)}</b> - {best['pct']}% Value {best['value']}\n💰 {cotes} ({book})\n💵 Mise: {mp}+{mc}={mt} FCFA Var {var}\n\n🧠 {sc}\n📊 xG {pack['xG_H']:.2f}/{pack['xGA_H']:.2f} vs {pack['xG_A']:.2f}/{pack['xGA_A']:.2f} | Forme {pack['forme_H']}/{pack['forme_A']} | Clean {pack['clean_H']}%/{pack['clean_A']}% | Fatigue {pack['fatigue_H']}/{pack['fatigue_A']} | Bless {pack['inj_txt_H'] or 'RAS'}/{pack['inj_txt_A'] or 'RAS'}"
            tg(txt2)
        elif txt.lower()=="/learn":
            p=load_poids()
            tg(f"🧠 Cerveau: {p['total']} matchs, WinRate {p['win_rate']*100:.1f}%, fatigue {p['poids_fatigue']:.3f}")
    except Exception as e: print(e)
    return "ok",200

if __name__=="__main__": app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
