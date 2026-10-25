import os, requests, datetime, time, unicodedata, json, re, math, random
from flask import Flask, request, jsonify
from datetime import timezone, timedelta
try:
    from tavily import TavilyClient
except:
    TavilyClient = None
try:
    import gspread
    from google.oauth2.service_account import Credentials
except:
    gspread = None

app = Flask(__name__)

APISPORTS_KEY = os.getenv("APISPORTS_KEY") or os.getenv("SPORTS_API_KEY")
GROQ_KEY = os.getenv("GROQ_API_KEY")
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELE_CHAT = os.getenv("TELEGRAM_CHAT_ID")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": APISPORTS_KEY} if APISPORTS_KEY else {}
MAROUA_TZ = timezone(timedelta(hours=1))

TRACKED_FILE = "/tmp/tracked_v25plus_final.json"
QUOTA_FILE = "/tmp/quota_v25plus_final.json"
TAVILY_CACHE_FILE = "/tmp/tavily_v25plus_final.json"
FIXTURE_CACHE_FILE = "/tmp/fixture_v25plus_final.json"
LAST_CALL_FILE = "/tmp/last_call_v25plus_final.json"
TRACKED_MATCHES = []

PRIORITY_LEAGUES = ["Premier League","La Liga","Serie A","Bundesliga","Ligue 1","Eredivisie","Primeira Liga","Championship","Champions League","Europa League"]

def get_today_maroua():
    return datetime.datetime.now(MAROUA_TZ).date().isoformat()

def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')

def send_telegram(text, chat_id=None):
    cid = chat_id or TELE_CHAT
    if not TELE_TOKEN or not cid:
        print(text)
        return
    try:
        url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
        for chunk in [text[i:i+3900] for i in range(0, len(text), 3900)]:
            requests.post(url, json={"chat_id": cid, "text": chunk, "parse_mode": "Markdown"}, timeout=15)
    except:
        pass

def load_json(p,d):
    try:
        with open(p,'r') as f: return json.load(f)
    except: return d
def save_json(p,d):
    try:
        with open(p,'w') as f: json.dump(d,f)
    except: pass

def get_quota():
    q=load_json(QUOTA_FILE,{"used":0,"remaining":100,"date":get_today_maroua()})
    if q["date"]!=get_today_maroua():
        q={"used":0,"remaining":100,"date":get_today_maroua()}
        save_json(QUOTA_FILE,q)
    return q

def call_api_throttled(endpoint, min_interval=7, max_retry=3):
    q=get_quota()
    if q["remaining"]<=12:
        print(f"QUOTA STOP {q}"); return []
    last=load_json(LAST_CALL_FILE,{"t":0})
    diff=time.time()-last.get("t",0)
    if diff<min_interval: time.sleep(min_interval-diff)
    if not APISPORTS_KEY: return []
    for attempt in range(max_retry):
        try:
            res=requests.get(f"{BASE}/{endpoint}", headers=HEADERS, timeout=25)
            save_json(LAST_CALL_FILE,{"t":time.time()})
            try:
                rem=res.headers.get('x-ratelimit-requests-remaining')
                lim=res.headers.get('x-ratelimit-requests-limit')
                if rem is not None:
                    q["remaining"]=int(rem)
                    q["used"]=(int(lim) if lim else 100)-int(rem)
                    q["date"]=get_today_maroua()
                    save_json(QUOTA_FILE,q)
            except: pass
            if res.status_code==429:
                wait=15*(2**attempt)+random.uniform(0,3)
                print(f"429 sur {endpoint} - backoff {wait}s tentative {attempt+1}/{max_retry}")
                send_telegram(f"⚠️ 429 API {endpoint} - pause {int(wait)}s (tentative {attempt+1})",None)
                time.sleep(wait)
                continue
            j=res.json()
            if j.get('errors'):
                print(f"API ERR {endpoint} {j.get('errors')}")
                return []
            return j.get('response',[])
        except Exception as e:
            print(f"call_api {e} attempt {attempt}")
            time.sleep(2+attempt*2)
    return []

def get_fixtures_cached():
    today=get_today_maroua()
    cache=load_json(FIXTURE_CACHE_FILE,{})
    if cache.get("date")==today and cache.get("data"): return cache["data"]
    data=call_api_throttled(f"fixtures?date={today}",7)
    if data: save_json(FIXTURE_CACHE_FILE,{"date":today,"data":data})
    return data

def get_all_pinnacle_odds_full(fid):
    odds=call_api_throttled(f"odds?fixture={fid}",7)
    if not odds: return {}, "Pinnacle scan"
    try:
        for fx in odds:
            for book in fx.get('bookmakers',[]):
                if 'Pinnacle' in book['name']:
                    od={}; full_txt=""
                    for bet in book.get('bets',[]):
                        name=bet.get('name',''); vals=[]
                        for v in bet.get('values',[]):
                            try:
                                odd_val=float(v['odd'])
                                od[f"{name}:{v['value']}"]=odd_val
                                od[v['value']]=odd_val
                                od[f"{name} {v['value']}"]=odd_val
                                if name=='Match Winner':
                                    if 'Home' in v['value']: od['Home']=odd_val
                                    if 'Draw' in v['value']: od['Draw']=odd_val
                                    if 'Away' in v['value']: od['Away']=odd_val
                                vals.append(f"{v['value']}@{v['odd']}")
                            except: pass
                        if vals: full_txt+=f"{name}: {' | '.join(vals)} || "
                    return od, full_txt[:7000]
    except: pass
    return {}, "Pinnacle non dispo"

def get_lineups_compo(fid):
    lin=call_api_throttled(f"fixtures/lineups?fixture={fid}",7)
    if not lin: return ""
    try:
        txt="COMPOS OFFICIELLES: "
        for team in lin:
            tname=team['team']['name']
            starters=[p['player']['name'] for p in team.get('start XI',[])[:5]]
            txt+=f"{tname} {','.join(starters)} | "
        return txt[:800]
    except:
        return ""
        def recherche_web_complete(home,away,fid=None):
    today=get_today_maroua()
    cache=load_json(TAVILY_CACHE_FILE,{})
    key=f"{today}_{home}_{away}"
    if key in cache: return cache[key]
    compo_txt=""
    if fid:
        compo_txt=get_lineups_compo(fid)
    web_txt=""
    if TAVILY_API_KEY and TavilyClient:
        try:
            client=TavilyClient(api_key=TAVILY_API_KEY)
            q=f"{home} vs {away} forme 5 derniers matchs classement enjeu motivation blessés suspendus état mental confiance vestiaire sentiment supporters fatigue météo composition probable lineup"
            res=client.search(query=q, search_depth="advanced", max_results=5, include_answer=True, time_range="day")
            web_txt=(res.get("answer","")+" "+" ".join([r["content"][:800] for r in res.get("results",[])[:3]]))[:4500]
        except Exception as e:
            print(f"Tavily {e}")
    final=(compo_txt+" "+web_txt).strip()[:5000]
    if len(final)>60:
        cache[key]=final; save_json(TAVILY_CACHE_FILE,cache)
        return final
    return f"Contexte {home} vs {away} - {compo_txt} forme stable"

def calculer_value(prob,cote):
    try:
        if not cote or cote<=1.01: return 0
        return round(prob-(100/cote),1)
    except: return 0

def arbitrage_ia_v25plus(home,away,prono_natif,league,markets_full,prob,odds_dict,smart_money_context="",is_auto=True):
    if not GROQ_KEY: return prono_natif, f"Base {prono_natif}",0,prob
    web=recherche_web_complete(home,away)
    cote_ref=list(odds_dict.values())[0] if odds_dict else 0
    value=calculer_value(prob,cote_ref) if cote_ref else 0
    mode_txt="AUTO AVEC SMART MONEY INTERNE" if is_auto else "MANUEL PUR - SENTIMENT COMPLET"
    prompt=f"""Tu es ELITE PRO {league} - {mode_txt} - V25 PLUS FINAL 100%.
Match: {home} vs {away}
Base native: {prono_natif} {prob}% | Value: {value}%
WEB + COMPO: {web}
SMART MONEY: {smart_money_context}
MARCHES PINNACLE: {markets_full[:6500]}
MISSION: Choisis MARCHE LE PLUS SAFE avec VALUE.
Autorisés: Handicap Asiatique 2 (0.25) / Handicap Asiatique 1 (-1.5) / Total Plus de 0.5/1.5/2.5/3.5 / Double Chance 1X/X2 / BTTS Oui/Non / DNB / Victoire si prob>=63%
Format STRICT:
ORIENTATION: [ex: Handicap Asiatique 2 (0.25)]
FORME: [15 mots]
ENJEU: [15 mots]
MENTAL: [15 mots]
DEROULEMENT: [30 mots]
JUSTIF: [40 mots max]
"""
    try:
        r=requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}","Content-Type":"application/json"},
            json={"model":"openai/gpt-oss-120b","messages":[{"role":"user","content":prompt}],"temperature":0.25,"max_tokens":650},
            timeout=35).json()
        ia=r['choices'][0]['message']['content'].strip()
        ori=re.search(r"ORIENTATION:\s*(.*)",ia,re.IGNORECASE)
        final=ori.group(1).split("\n")[0].strip() if ori else prono_natif
        return final, ia, value, prob
    except Exception as e:
        print(f"IA V25+ {e}")
        return prono_natif, f"Base {prono_natif} - {web[:250]}", value, prob

def analyser_match_pour_coupon(match_obj,smart_context="",is_auto=True):
    fx=match_obj['fixture']; pred=match_obj['pred']
    home=fx['teams']['home']['name']; away=fx['teams']['away']['name']
    prono_natif=pred['predictions'].get('advice','Domicile')
    pct=pred.get('predictions',{}).get('percent',{})
    try:
        h=int(str(pct.get('home','0')).replace('%','') or 0)
        d=int(str(pct.get('draw','0')).replace('%','') or 0)
        a=int(str(pct.get('away','0')).replace('%','') or 0)
        adv=prono_natif.lower()
        if 'double' in adv or 'or draw' in adv or '1x' in adv or 'x2' in adv:
            prob=h+d if ('home' in adv or home.lower()[:4] in adv or '1x' in adv) else d+a
        elif 'draw' in adv and 'or' not in adv: prob=d
        else: prob=max(h,d,a)
        if prob<40: prob=64
        if prob>85: prob=84
    except: prob=65
    odds_dict,markets_text=get_all_pinnacle_odds_full(match_obj['fid'])
    prono_final,note_ia,value,prob=arbitrage_ia_v25plus(home,away,prono_natif,fx['league']['name'],markets_text,prob,odds_dict,smart_context,is_auto)
    cote_retenue=1.40
    if odds_dict:
        low=prono_final.lower()
        for k,v in odds_dict.items():
            if v>1.1 and (low in k.lower() or k.split(':')[-1].lower() in low):
                cote_retenue=v; break
        if cote_retenue==1.40: cote_retenue=list(odds_dict.values())[0] if odds_dict else 1.40
    try:
        dt=datetime.datetime.fromisoformat(fx['fixture']['date'].replace('Z','+00:00')).astimezone(MAROUA_TZ)
        heure=dt.strftime("%H:%M"); date_s=dt.strftime("%d.%m.%Y")
    except:
        heure="12:30"; date_s=get_today_maroua(); dt=datetime.datetime.now(MAROUA_TZ)
    return {"fid":match_obj['fid'],"home":home,"away":away,"league":fx['league']['name'],"heure":heure,"date":date_s,"dt":dt,"prono_natif":prono_natif,"final":prono_final,"prob":prob,"value":value,"cote":cote_retenue,"note":note_ia}

def analyser_et_envoyer(match_obj,index,smart_context="",is_auto=True,chat_id=None):
    data=analyser_match_pour_coupon(match_obj,smart_context,is_auto)
    def extract(tag):
        m=re.search(rf"{tag}:\s*(.*)",data['note'],re.IGNORECASE)
        return m.group(1).split("\n")[0].strip()[:180] if m else ""
    forme=extract("FORME"); enjeu=extract("ENJEU"); mental=extract("MENTAL"); deroul=extract("DEROULEMENT"); justif=extract("JUSTIF")
    if not justif: justif=data['note'][:300]
    value_txt=f"+{data['value']}%" if data['value']>0 else f"{data['value']}%"
    msg=f"""⚽️ Football . {data['league']}
{data['date']} ({data['heure']})
{data['home']} VS {data['away']}
{data['final']} @ {round(data['cote'],3)}
📊 BASE: {data['prono_natif']} ({data['prob']}%) | VALEUR: {value_txt}
📈 FORME: {forme}
🏆 ENJEU: {enjeu}
🧠 MENTAL: {mental}
🔮 DEROULEMENT: {deroul}
💡 JUSTIF: {justif}
Code:{data['fid']%10000} | {index}"""
    send_telegram(msg,chat_id)
    try: log_to_sheet(data, smart_context)
    except Exception as e: print(f"Sheet log {e}")
    return data

def log_to_sheet(data, smart_ctx):
    if not SHEET_ID or not CREDS_JSON or not gspread: return
    try:
        creds_info=json.loads(CREDS_JSON) if isinstance(CREDS_JSON,str) else CREDS_JSON
        creds=Credentials.from_service_account_info(creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"])
        client=gspread.authorize(creds)
        sheet=client.open_by_key(SHEET_ID).sheet1
        if not sheet.get_all_values():
            sheet.append_row(["Date","Heure","Match","League","Prono_Natif","Final_1xBet","Cote","Prob","Value","Open","Mid","Close","Delta","Smart_Money","Note"])
        open_c=""; mid_c=""; close_c=""; delta=""
        if "Open" in smart_ctx:
            try:
                m=re.search(r"Open.*?([\d.]+).*?Mid.*?([\d.]+).*?Close.*?([\d.]+)",smart_ctx)
                if m: open_c=m.group(1); mid_c=m.group(2); close_c=m.group(3); delta=round(float(close_c)-float(open_c),3)
            except: pass
        sheet.append_row([data['date'], data['heure'], f"{data['home']} VS {data['away']}", data['league'], data['prono_natif'], data['final'], round(data['cote'],3), data['prob'], data['value'], open_c, mid_c, close_c, delta, smart_ctx[:200], data['note'][:200]])
    except Exception as e:
        print(f"GSheet err {e}")

def construire_combinés_auto(liste_data,chat_id=None):
    haute_fiab=[d for d in liste_data if d['prob']>=62 and 1.20<=d['cote']<=2.5]
    haute_fiab=sorted(haute_fiab, key=lambda x: x['prob'], reverse=True)
    if len(haute_fiab)<2: return
    comb2=haute_fiab[:2]; cote2=math.prod([d['cote'] for d in comb2]); prob2=math.prod([d['prob']/100 for d in comb2])*100
    msg2=f"🔗 *COMBINE V25 PLUS FINAL 2*\n"
    for d in comb2: msg2+=f"• {d['home'][:14]} VS {d['away'][:10]} -> {d['final']} @ {round(d['cote'],2)} ({d['prob']}%)\n"
    msg2+=f"\n📊 Cote totale: {round(cote2,2)} | Fiabilite: {round(prob2,1)}%"
    send_telegram(msg2,chat_id)

def choisir_7_obligatoires_v25plus():
    global TRACKED_MATCHES
    fixtures=get_fixtures_cached()
    if not fixtures:
        fixtures=call_api_throttled(f"fixtures?date={(datetime.datetime.now(MAROUA_TZ).date()+datetime.timedelta(days=1)).isoformat()}",7)
        if fixtures: save_json(FIXTURE_CACHE_FILE,{"date":get_today_maroua(),"data":fixtures})
    if not fixtures:
        send_telegram("⚠️ V25 PLUS FINAL: 0 fixtures",None); return []
    def league_score(fx):
        name=fx['league']['name']
        for i,pl in enumerate(PRIORITY_LEAGUES):
            if pl.lower() in name.lower(): return i
        return 99
    candidates=[fx for fx in fixtures if fx['fixture']['status']['short'] in ['NS','TBD','PST']]
    candidates=sorted(candidates,key=league_score)[:20]
    send_telegram(f"🚀 *V25 PLUS FINAL 100% - SCAN {len(candidates)} - 43/57* | Quota restant: {get_quota()['remaining']}",None)
    all_scored=[]
    for fx in candidates:
        fid=fx['fixture']['id']
        pred_res=call_api_throttled(f"predictions?fixture={fid}",7)
        if not pred_res: continue
        pred=pred_res[0]
        try:
            pct=pred.get('predictions',{}).get('percent',{})
            h=int(str(pct.get('home','0')).replace('%','') or 0)
            d=int(str(pct.get('draw','0')).replace('%','') or 0)
            a=int(str(pct.get('away','0')).replace('%','') or 0)
            conf=max(h,d,a)
            all_scored.append({"fid":fid,"fixture":fx,"pred":pred,"conf":conf})
        except: continue
    if not all_scored:
        send_telegram("⚠️ 0 predictions",None); return []
    result=sorted(all_scored,key=lambda x:x['conf'],reverse=True)[:7]
    TRACKED_MATCHES=[]
    for m in result:
        TRACKED_MATCHES.append({"fid":m['fid'],"home":m['fixture']['teams']['home']['name'],"away":m['fixture']['teams']['away']['name'],"heure":m['fixture']['fixture']['date'],"statut":"ATTENTE","league":m['fixture']['league']['name'],"open":0,"mid":0,"close":0,"cote_init":0})
    save_json(TRACKED_FILE,TRACKED_MATCHES)
    return result

def load_tracked():
    global TRACKED_MATCHES
    TRACKED_MATCHES=load_json(TRACKED_FILE,[])

@app.route('/')
def home_route():
    q=get_quota()
    return f"SEHBA V25 PLUS FINAL 100% - 43/57 + SHEET + COMPO + BACKOFF - ACTIF | Quota {q['used']}/100 Rest {q['remaining']}"

@app.route('/ping')
def ping_route():
    load_tracked()
    return jsonify({"status":"alive","version":"V25 PLUS FINAL 100%","tracked":len(TRACKED_MATCHES),"quota":get_quota(),"date":get_today_maroua()})

@app.route('/run')
def run_daily():
    load_tracked()
    matches=choisir_7_obligatoires_v25plus()
    if not matches: return jsonify({"error":"0 match","quota":get_quota()})
    send_telegram(f"🚀 *V25 PLUS FINAL 100% - 7 MEILLEURS - COUPONS 1XBET*",None)
    datas=[]
    for i,m in enumerate(matches,1):
        d=analyser_et_envoyer(m,f"AUTO-{i}",smart_context="Ouverture matin",is_auto=True)
        datas.append(d)
        for tm in TRACKED_MATCHES:
            if tm['fid']==d['fid']: tm['cote_init']=d['cote']; tm['open']=d['cote']
        save_json(TRACKED_FILE,TRACKED_MATCHES)
        time.sleep(1)
    construire_combinés_auto(datas)
    send_telegram(f"✅ V25 PLUS FINAL 100%: 7 envoyés + Sheet log | Quota {get_quota()['used']}/100",None)
    return jsonify({"status":"OK FINAL 100%","count":len(datas),"quota":get_quota()})

@app.route('/check_and_analyze')
def check_and_analyze():
    load_tracked()
    if not TRACKED_MATCHES: return jsonify({"status":"vide"})
    now=datetime.datetime.now(MAROUA_TZ)
    q=get_quota()
    if q["remaining"]<=10: return jsonify({"status":"quota bas","quota":q})
    fait=0
    for m in TRACKED_MATCHES:
        try:
            mt=datetime.datetime.fromisoformat(m['heure'].replace('Z','+00:00')).astimezone(MAROUA_TZ)
            diff=(mt-now).total_seconds()/60
            if m.get("statut")=="ATTENTE" and 20<=diff<=400:
                odds_dict,_=get_all_pinnacle_odds_full(m['fid'])
                if not odds_dict: continue
                cote_now=list(odds_dict.values())[0] if odds_dict else 0
                if 300<=diff<=400: m['open']=cote_now
                elif 150<=diff<=220: m['mid']=cote_now
                elif 20<=diff<=110:
                    m['close']=cote_now
                    open_c=m.get('open',0) or m.get('cote_init',0)
                    mid_c=m.get('mid',0)
                    smart_ctx=f"Open H-6h {open_c} -> Mid H-3h {mid_c} -> Close H-90min {cote_now}. Delta {round(cote_now-open_c,3) if open_c else 0}. Pinnacle smart money interne."
                    fx=call_api_throttled(f"fixtures?id={m['fid']}",7)
                    pr=call_api_throttled(f"predictions?fixture={m['fid']}",7)
                    if fx and pr:
                        analyser_et_envoyer({"fid":m['fid'],"fixture":fx[0],"pred":pr[0]},f"FINAL J-{int(diff)}min",smart_context=smart_ctx,is_auto=True)
                        m['statut']="ANALYSE"; fait+=1
                save_json(TRACKED_FILE,TRACKED_MATCHES)
                time.sleep(1)
        except Exception as e: print(e)
    save_json(TRACKED_FILE,TRACKED_MATCHES)
    return jsonify({"lancees":fait,"quota":get_quota()})

@app.route('/webhook', methods=['POST'])
def webhook_route():
    data=request.get_json()
    if not data or 'message' not in data: return jsonify({"ok":True})
    raw=data['message'].get('text','').strip()
    chat_id=data['message']['chat']['id']
    if not raw.lower().startswith('/vs'): return jsonify({"ok":True})
    clean_raw=raw[3:].strip(); clean=strip_accents(clean_raw); lower=clean.lower()
    if ' - ' in clean: sep=' - '
    elif ' vs ' in lower:
        idx=lower.find(' vs '); clean=clean[:idx]+' - '+clean[idx+4:]; sep=' - '
    else: sep=None
    if not sep or ' - ' not in clean:
        send_telegram("Format: /vs Rayo - Elche",chat_id); return jsonify({"ok":True})
    h_q,a_q=[x.strip() for x in clean.split(' - ',1)]
    send_telegram(f"🔍 *V25 PLUS FINAL MANUEL: {h_q} vs {a_q} - native + web sentiment complet + compo...*",chat_id)
    target=None
    for days in range(0,3):
        date_check=(datetime.datetime.now(MAROUA_TZ).date()+datetime.timedelta(days=days)).isoformat()
        fixtures=call_api_throttled(f"fixtures?date={date_check}",7)
        for f in fixtures:
            hn=strip_accents(f['teams']['home']['name'].lower()); an=strip_accents(f['teams']['away']['name'].lower())
            if h_q.lower()[:4] in hn or h_q.lower()[:4] in an:
                if a_q.lower()[:4] in hn or a_q.lower()[:4] in an:
                    target=f; break
        if target: break
        if get_quota()["remaining"]<=12: break
    if not target:
        send_telegram(f"❌ {h_q} vs {a_q} introuvable 3j. Quota {get_quota()['remaining']}",chat_id); return jsonify({"ok":True})
    fid=target['fixture']['id']
    pred=call_api_throttled(f"predictions?fixture={fid}",7)
    if not pred:
        send_telegram(f"⚠️ {target['teams']['home']['name']} vs {target['teams']['away']['name']} stats non dispo",chat_id); return jsonify({"ok":True})
    analyser_et_envoyer({"fid":fid,"fixture":target,"pred":pred[0]}, "MANUEL FINAL 100%", smart_context="", is_auto=False, chat_id=chat_id)
    return jsonify({"ok":True})

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
