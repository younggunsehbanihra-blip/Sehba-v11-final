import os, requests, datetime, time, unicodedata, json, re, math
from flask import Flask, request, jsonify
from datetime import timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials
try:
    from tavily import TavilyClient
except: TavilyClient = None

app = Flask(__name__)
APISPORTS_KEY = os.getenv("APISPORTS_KEY") or os.getenv("SPORTS_API_KEY")
GROQ_KEY = os.getenv("GROQ_API_KEY")
TELE_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELE_CHAT = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDS_RAW = os.getenv("GOOGLE_CREDENTIALS_JSON")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
BASE = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": APISPORTS_KEY} if APISPORTS_KEY else {}
MAROUA_TZ = timezone(timedelta(hours=1))
TRACKED_FILE = "/tmp/tracked.json"
TRACKED_MATCHES = []
LAST_RUN_FILE = "/tmp/last_run.txt"
TAVILY_CACHE_FILE = "/tmp/tavily_cache.json"

def get_today_maroua(): return datetime.datetime.now(MAROUA_TZ).date().isoformat()
def ecrire_dans_sheet(d, m, l, p, c, n):
    if not GOOGLE_CREDS_RAW or not GOOGLE_SHEET_ID: return
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_RAW), scopes=scopes)
        gspread.authorize(creds).open_by_key(GOOGLE_SHEET_ID).sheet1.append_row([d,m,l,p,c,n,"EN COURS"], value_input_option="USER_ENTERED")
    except Exception as e: print(f"Sheet {e}")
def strip_accents(s): return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
def send_telegram(text, chat_id=None):
    cid = chat_id or TELE_CHAT
    if not TELE_TOKEN or not cid: print(text); return
    try:
        url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
        for chunk in [text[i:i+3900] for i in range(0, len(text), 3900)]:
            requests.post(url, json={"chat_id": cid, "text": chunk, "parse_mode":"Markdown"}, timeout=15)
    except Exception as e: print(f"TG {e}")
def save_tracked():
    try:
        with open(TRACKED_FILE, 'w') as f: json.dump(TRACKED_MATCHES, f)
    except: pass
def load_tracked():
    global TRACKED_MATCHES
    try:
        with open(TRACKED_FILE, 'r') as f: TRACKED_MATCHES = json.load(f)
    except: TRACKED_MATCHES = []
def call_api(endpoint):
    if not APISPORTS_KEY: return []
    try:
        res = requests.get(f"{BASE}/{endpoint}", headers=HEADERS, timeout=20).json()
        if res.get('errors'): print(f"API Error {endpoint}: {res.get('errors')}"); return []
        return res.get('response', [])
    except Exception as e: print(f"call_api {e}"); return []
def get_all_pinnacle_odds(fid):
    odds = call_api(f"odds?fixture={fid}")
    if not odds: return {}, "Marche en cours"
    try:
        for fx in odds:
            for book in fx.get('bookmakers', []):
                if 'Pinnacle' in book['name']:
                    txt=""; od={}
                    for bet in book['bets']:
                        txt+=f"{bet['name']}: "
                        for v in bet['values']:
                            txt+=f"{v['value']}@{v['odd']} | "
                            if bet['name']=='Match Winner': od[v['value']]=float(v['odd'])
                        txt+=" || "
                    return od, txt[:3500]
    except: pass
    return {}, "Pinnacle non dispo"
def get_tavily_cache():
    try:
        with open(TAVILY_CACHE_FILE,'r') as f: return json.load(f)
    except: return {}
def save_tavily_cache(c):
    try:
        with open(TAVILY_CACHE_FILE,'w') as f: json.dump(c,f)
    except: pass
def recherche_web_contextuelle(home, away):
    query = f"{home} {away} injuries lineup prediction today"
    today = get_today_maroua()
    cache = get_tavily_cache()
    key = f"{today}_{home}_{away}"
    if key in cache: return cache[key]
    if TAVILY_API_KEY and TavilyClient:
        try:
            client = TavilyClient(api_key=TAVILY_API_KEY)
            res = client.search(query=query, search_depth="advanced", max_results=5, include_answer=True, time_range="day")
            txt = (res.get("answer","") + " " + " ".join([r["content"][:600] for r in res.get("results",[])[:3]]))[:3500]
            if len(txt)>50:
                cache[key]=txt; save_tavily_cache(cache); return txt
        except Exception as e: print(f"Tavily {e}")
    return f"Contexte {home} vs {away} stable, pas d'absence majeure signalee"
def calculer_value(prob_modele, cote):
    try:
        if not cote or cote <= 1.01: return 0
        prob_marche = 100 / cote
        return round(prob_modele - prob_marche, 1)
    except: return 0
def arbitrage_ia(home, away, prono_natif, league, markets, prob=50, odds_dict=None):
    if not GROQ_KEY: return prono_natif, f"Base {prono_natif} conservee - contexte stable", 0, prob
    web = recherche_web_contextuelle(home, away)
    cote_ref = 0
    if odds_dict:
        if 'home' in prono_natif.lower() or home.lower() in prono_natif.lower(): cote_ref = odds_dict.get('Home',0)
        elif 'away' in prono_natif.lower() or away.lower() in prono_natif.lower(): cote_ref = odds_dict.get('Away',0)
        elif 'draw' in prono_natif.lower(): cote_ref = odds_dict.get('Draw',0)
    value = calculer_value(prob, cote_ref) if cote_ref else 0
    prompt = f"""Tu es analyste performance football {league}. Match: {home} vs {away} Base: {prono_natif} confiance {prob}% Ecart marche: {value}% Contexte: {web} Stats: {markets} Regles: >=70% => Victoire seche possible, 55-69% => Double Chance ou Handicap 0, <55% => Over 1.5 ou BTTS Oui Format: ORIENTATION: [choix securise] JUSTIF: [15 mots max FR avec info blessure/compo]"""
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type":"application/json"}, json={"model":"openai/gpt-oss-120b","messages":[{"role":"user","content":prompt}],"temperature":0.2,"max_tokens":320}, timeout=30).json()
        ia = r['choices'][0]['message']['content'].strip()
        ori = re.search(r"ORIENTATION:\s*(.*)", ia, re.IGNORECASE)
        final = ori.group(1).split("\n")[0].strip() if ori else prono_natif
        if "sorry" in final.lower() or "can't help" in final.lower() or "unable" in final.lower(): final = prono_natif
        return final, ia, value, prob
    except Exception as e:
        print(f"IA err {e}"); return prono_natif, f"Base conservee - {web[:100]}", value, prob

def analyser_match_pour_coupon(match_obj):
    fx=match_obj['fixture']; pred=match_obj['pred']
    home=fx['teams']['home']['name']; away=fx['teams']['away']['name']
    prono_natif=pred['predictions'].get('advice','Domicile')
    pct=pred.get('predictions',{}).get('percent',{})
    try:
        h=int(str(pct.get('home','0')).replace('%','') or 0); d=int(str(pct.get('draw','0')).replace('%','') or 0); a=int(str(pct.get('away','0')).replace('%','') or 0)
        prob=max(h,d,a)
        if prob < 40: prob = 60
    except: prob=60
    odds_dict, markets_text = get_all_pinnacle_odds(match_obj['fid'])
    prono_final, note_ia, value, prob = arbitrage_ia(home, away, prono_natif, fx['league']['name'], markets_text, prob, odds_dict)
    cote_retenue = 1.40
    if odds_dict:
        low = prono_final.lower()
        if 'away' in low or 'exterieur' in low or away.lower() in low: cote_retenue = odds_dict.get('Away', list(odds_dict.values())[0])
        elif 'domicile' in low or 'home' in low or home.lower() in low: cote_retenue = odds_dict.get('Home', list(odds_dict.values())[0])
        elif 'draw' in low or 'nul' in low: cote_retenue = odds_dict.get('Draw', 3.2)
        else: cote_retenue = list(odds_dict.values())[0] if odds_dict else 1.40
    try:
        dt=datetime.datetime.fromisoformat(fx['fixture']['date'].replace('Z','+00:00')).astimezone(MAROUA_TZ)
        heure=dt.strftime("%H:%M"); date_s=dt.strftime("%d/%m")
    except: heure="12:30"; date_s=get_today_maroua(); dt=datetime.datetime.now(MAROUA_TZ)
    return {"fid":match_obj['fid'],"home":home,"away":away,"league":fx['league']['name'],"heure":heure,"date":date_s,"dt":dt,"prono_natif":prono_natif,"final":prono_final,"prob":prob,"value":value,"cote":cote_retenue,"note":note_ia,"odds_str":", ".join([f"{k}:{v}" for k,v in odds_dict.items()]) if odds_dict else markets_text[:150]}

def analyser_et_envoyer(match_obj, index, chat_id=None):
    data = analyser_match_pour_coupon(match_obj)
    abbr_h = ''.join([w[0] for w in data['home'].split()][:3]).upper()[:3]
    abbr_a = ''.join([w[0] for w in data['away'].split()][:3]).upper()[:3]
    code = data['fid'] % 10000
    value_txt = f"+{data['value']}%" if data['value']>0 else f"{data['value']}%"
    # CERVEAU FIX
    note = data['note']
    if 'JUSTIF:' in note: justif = note.split('JUSTIF:')[-1].strip()
    else:
        # prend apres ORIENTATION
        parts = note.split('\n')
        justif = parts[-1] if len(parts)>1 else note
    if len(justif) < 15: justif = f"{data['home']} stable, {data['away']} avec {data['prono_natif']}"
    msg = f"📋 *COUPON V23 {index}*\n⚽️ {abbr_h}-{abbr_a} | {data['date']} {data['heure']} | Code:{code}\n🏆 {data['league']} - {data['home']} vs {data['away']}\n\n📊 BASE: {data['prono_natif']} ({data['prob']}%)\n🎯 *ORIENTATION:* {data['final']}\n💡 VALEUR: {value_txt} | {data['odds_str']}\n🧠 {justif[:220]}"
    send_telegram(msg, chat_id)
    ecrire_dans_sheet(f"{data['date']} {data['heure']}", f"{data['home']} - {data['away']}", data['league'], data['final'], data['odds_str'], f"V{data['value']}% {justif[:80]}")
    return data

def construire_combinés_auto(liste_data, chat_id=None):
    haute_fiab = [d for d in liste_data if d['prob'] >= 62 and d['cote'] >= 1.15 and d['cote'] <= 2.2]
    haute_fiab = sorted(haute_fiab, key=lambda x: x['prob'], reverse=True)
    if len(haute_fiab) < 2: return
    comb2 = haute_fiab[:2]; cote2 = math.prod([d['cote'] for d in comb2]); prob2 = math.prod([d['prob']/100 for d in comb2])*100
    msg2 = f"🔗 *COMBINE FACULTATIF 2 - HAUTE FIABILITE*\n"
    for d in comb2: msg2 += f"• {d['home'][:14]} - {d['away'][:10]} -> {d['final']} @ {round(d['cote'],2)} ({d['prob']}%)\n"
    msg2 += f"\n📊 Cote totale: {round(cote2,2)} | Fiabilite: {round(prob2,1)}% | Ideal si pas de grosse value"
    send_telegram(msg2, chat_id)
    if len(haute_fiab) >= 3:
        comb3 = haute_fiab[:3]; cote3 = math.prod([d['cote'] for d in comb3]); prob3 = math.prod([d['prob']/100 for d in comb3])*100
        msg3 = f"🔗 *COMBINE FACULTATIF 3 - EQUILIBRE*\n"
        for d in comb3: msg3 += f"• {d['home'][:14]} - {d['away'][:10]} -> {d['final']} @ {round(d['cote'],2)} ({d['prob']}%)\n"
        msg3 += f"\n📊 Cote totale: {round(cote3,2)} | Fiabilite: {round(prob3,1)}% | x{round(cote3,2)} pour 3 bases solides"
        send_telegram(msg3, chat_id)

def choisir_7_obligatoires():
    global TRACKED_MATCHES
    today = get_today_maroua()
    fixtures = call_api(f"fixtures?date={today}")
    if not fixtures: return []
    filtered = [fx for fx in fixtures if fx['fixture']['status']['short']=='NS']
    scored=[]
    for fx in filtered:
        fid=fx['fixture']['id']
        pred_res=call_api(f"predictions?fixture={fid}")
        if not pred_res: continue
        pred=pred_res[0]
        try:
            pct=pred.get('predictions',{}).get('percent',{}); conf=max(int(str(pct.get('home','0')).replace('%','') or 0), int(str(pct.get('draw','0')).replace('%','') or 0), int(str(pct.get('away','0')).replace('%','') or 0))
            scored.append({"fid":fid,"fixture":fx,"pred":pred,"conf":conf})
        except: continue
        if len(scored)>=12: break
        time.sleep(0.5)
    result=sorted(scored, key=lambda x: x['conf'], reverse=True)[:7]
    TRACKED_MATCHES=[]
    for m in result: TRACKED_MATCHES.append({"fid":m['fid'],"home":m['fixture']['teams']['home']['name'],"away":m['fixture']['teams']['away']['name'],"heure":m['fixture']['fixture']['date'],"statut":"ATTENTE","league":m['fixture']['league']['name']})
    save_tracked()
    return result

@app.route('/')
def home_route(): return "SEHBA V23 PRO MAX - ACTIF"
@app.route('/ping')
def ping_route(): return jsonify({"status":"alive","version":"V23 PRO MAX","tracked":len(TRACKED_MATCHES),"date":get_today_maroua()})
@app.route('/run')
def run_daily():
    today=get_today_maroua()
    # FORCE RUN MEME SI DEJA LANCE AUJOURDHUI POUR TON CAS INAUGURATION
    load_tracked()
    matches=choisir_7_obligatoires()
    if not matches:
        send_telegram(f"ℹ️ V23 PRO MAX: Aucun match NS pour {today}, je prends les prochains...")
        return jsonify({"error":"0 match","date":today})
    try:
        with open(LAST_RUN_FILE,'w') as f: f.write(today)
    except: pass
    send_telegram(f"🚀 *V23 PRO MAX - LANCEMENT 7 ANALYSES DU JOUR {today}*\nJe choisis les 7 plus solides...", None)
    datas=[]
    for i,m in enumerate(matches,1):
        d=analyser_et_envoyer(m, f"AUTO-{i}")
        datas.append(d)
        time.sleep(2)
    construire_combinés_auto(datas)
    send_telegram(f"✅ 7 analyses envoyees + combinés facultatifs generes. Suivi Smart Money actif jusqu'au coup d'envoi.", None)
    return jsonify({"status":"OK PRO MAX","count":len(datas),"date":today})

@app.route('/check_and_analyze')
def check_and_analyze():
    load_tracked()
    if not TRACKED_MATCHES: return jsonify({"status":"vide - lance /run"})
    now=datetime.datetime.now(MAROUA_TZ); fait=0
    for m in TRACKED_MATCHES:
        try:
            mt=datetime.datetime.fromisoformat(m['heure'].replace('Z','+00:00')).astimezone(MAROUA_TZ)
            diff=(mt-now).total_seconds()/60
            if m.get("statut")=="ATTENTE" and 30 <= diff <= 90:
                fx=call_api(f"fixtures?id={m['fid']}"); pr=call_api(f"predictions?fixture={m['fid']}")
                if fx and pr:
                    # Detection chute cote Smart Money
                    old_odds, _ = get_all_pinnacle_odds(m['fid'])
                    send_telegram(f"⏰ *J-{int(diff)}min SMART MONEY CHECK: {m['home']} vs {m['away']}*\nAnalyse finale avec cote live...", None)
                    analyser_et_envoyer({"fid":m['fid'],"fixture":fx[0],"pred":pr[0]}, f"J-{int(diff)}min FINAL")
                    m['statut']="ANALYSE"; fait+=1; time.sleep(3)
        except Exception as e: print(e)
    save_tracked()
    return jsonify({"lancees":fait})

@app.route('/webhook', methods=['POST'])
def webhook_route():
    data=request.get_json()
    if not data or 'message' not in data: return jsonify({"ok":True})
    raw=data['message'].get('text','').strip()
    chat_id=data['message']['chat']['id']
    if not raw.lower().startswith('/vs'): return jsonify({"ok":True})
    clean_raw=raw[3:].strip()
    clean=strip_accents(clean_raw)
    lower=clean.lower()
    if ' - ' in clean: sep=' - '
    elif ' vs ' in lower:
        idx=lower.find(' vs '); clean=clean[:idx]+' - '+clean[idx+4:]; sep=' - '
    else: sep=None
    if not sep or ' - ' not in clean:
        send_telegram("Format: /vs Real - Leipzig", chat_id); return jsonify({"ok":True})
    h_q,a_q=[x.strip() for x in clean.split(' - ',1)]
    send_telegram(f"🔍 *V23 PRO MAX: {h_q} vs {a_q}...*", chat_id)
    target=None
    for days in [0,1]:
        date_check=(datetime.datetime.now(MAROUA_TZ).date()+datetime.timedelta(days=days)).isoformat()
        fixtures=call_api(f"fixtures?date={date_check}")
        for f in fixtures:
            hn=strip_accents(f['teams']['home']['name'].lower()); an=strip_accents(f['teams']['away']['name'].lower())
            if h_q.lower()[:4] in hn or h_q.lower()[:4] in an:
                if a_q.lower()[:4] in hn or a_q.lower()[:4] in an:
                    target=f; break
        if target: break
    if not target:
        send_telegram(f"❌ {h_q} vs {a_q} introuvable aujourd'hui.", chat_id)
        return jsonify({"ok":True})
    fid=target['fixture']['id']
    pred=call_api(f"predictions?fixture={fid}")
    if not pred:
        send_telegram(f"⚠️ {target['teams']['home']['name']} vs {target['teams']['away']['name']} trouve mais stats non dispo.", chat_id)
        return jsonify({"ok":True})
    analyser_et_envoyer({"fid":fid,"fixture":target,"pred":pred[0]}, "MANUEL PRO MAX", chat_id)
    return jsonify({"ok":True})

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
