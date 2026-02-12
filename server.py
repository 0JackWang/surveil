#!/usr/bin/env python3
"""
HyperDash Monitor — Backend
Fetches top 100 Hyperliquid leaderboard trader positions every 4 hours,
computes per-coin L/S ratios, stores snapshots permanently in a JSON file,
and serves a dashboard.

Deploy: python server.py
Runs on port 8080 (or PORT env var).
"""

import json, os, time, threading, traceback
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

# Config
TOP_N = 100
SNAP_INTERVAL = 4 * 3600  # 4 hours in seconds
MAX_SNAPS = 48            # 48 snapshots = 8 days of 4h data
DATA_FILE = os.environ.get("DATA_FILE", "snapshots.json")
PORT = int(os.environ.get("PORT", 8080))

HL_INFO = "https://api.hyperliquid.xyz/info"
HL_LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"

# ─── Data Layer ──────────────────────────────────────────────────────

def load_snapshots():
    p = Path(DATA_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except:
            pass
    return []

def save_snapshots(snaps):
    Path(DATA_FILE).write_text(json.dumps(snaps, separators=(",", ":")))

# ─── Hyperliquid API ─────────────────────────────────────────────────

def hl_post(payload):
    req = Request(HL_INFO, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def hl_get(url):
    with urlopen(url, timeout=15) as r:
        return json.loads(r.read())

def fetch_leaderboard():
    data = hl_get(HL_LB)
    rows = data["leaderboardRows"]
    rows.sort(key=lambda x: float(x["accountValue"]), reverse=True)
    return rows[:TOP_N]

def fetch_positions(address):
    return hl_post({"type": "clearinghouseState", "user": address})

# ─── Snapshot Logic ──────────────────────────────────────────────────

def take_snapshot():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Taking snapshot...")
    start = time.time()

    traders = fetch_leaderboard()
    print(f"  Loaded {len(traders)} leaderboard traders")

    coin_data = {}
    total_long = 0.0
    total_short = 0.0
    loaded = 0
    errors = 0

    for i, trader in enumerate(traders):
        try:
            pos = fetch_positions(trader["ethAddress"])
            if not pos or "assetPositions" not in pos:
                continue
            for ap in pos["assetPositions"]:
                p = ap["position"]
                sz = float(p["szi"])
                val = float(p["positionValue"])
                if val == 0:
                    continue
                coin = p["coin"]
                if coin not in coin_data:
                    coin_data[coin] = {"lN": 0.0, "sN": 0.0, "lT": 0, "sT": 0}
                if sz > 0:
                    coin_data[coin]["lN"] += val
                    coin_data[coin]["lT"] += 1
                    total_long += val
                else:
                    coin_data[coin]["sN"] += val
                    coin_data[coin]["sT"] += 1
                    total_short += val
            loaded += 1
        except Exception:
            errors += 1
        if (i + 1) % 20 == 0:
            print(f"  Progress: {i+1}/{len(traders)} ({errors} errors)")
            time.sleep(0.1)  # small delay to avoid rate limits

    # Build coin list sorted by total notional
    coins = []
    for c, d in coin_data.items():
        tot = d["lN"] + d["sN"]
        if tot == 0:
            continue
        coins.append({
            "c": c,
            "r": round(d["lN"] / tot, 4),
            "t": round(tot, 2),
            "lN": round(d["lN"], 2),
            "sN": round(d["sN"], 2),
            "lT": d["lT"],
            "sT": d["sT"],
        })
    coins.sort(key=lambda x: x["t"], reverse=True)

    total = total_long + total_short
    snap = {
        "ts": int(time.time() * 1000),
        "gR": round(total_long / total, 4) if total > 0 else 0,
        "tL": round(total_long, 2),
        "tS": round(total_short, 2),
        "traders": loaded,
        "coins": coins[:60],  # top 60 coins to keep file size reasonable
    }

    elapsed = time.time() - start
    print(f"  Done in {elapsed:.1f}s — {loaded} traders, {len(coins)} coins, "
          f"L/S ratio: {snap['gR']*100:.1f}%")

    # Save
    snaps = load_snapshots()
    snaps.append(snap)
    if len(snaps) > MAX_SNAPS:
        snaps = snaps[-MAX_SNAPS:]
    save_snapshots(snaps)
    print(f"  Saved. {len(snaps)} snapshots stored.")
    return snap

# ─── Cron Thread ─────────────────────────────────────────────────────

def cron_loop():
    while True:
        try:
            take_snapshot()
        except Exception as e:
            print(f"[ERROR] Snapshot failed: {e}")
            traceback.print_exc()
        time.sleep(SNAP_INTERVAL)

# ─── HTTP Server ─────────────────────────────────────────────────────

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/snapshots":
            snaps = load_snapshots()
            data = json.dumps(snaps).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        elif self.path == "/api/snapshot/now":
            try:
                snap = take_snapshot()
                data = json.dumps(snap).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())

        elif self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(get_html().encode())

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        if "/api/" in str(args[0]):
            print(f"[HTTP] {args[0]}")


def get_html():
    return DASHBOARD_HTML


# ─── Main ────────────────────────────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚡ HyperDash Monitor</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0c0c14;color:#e2e8f0;font-family:system-ui,-apple-system,sans-serif}
::-webkit-scrollbar{height:6px;width:6px}
::-webkit-scrollbar-track{background:#14141f}
::-webkit-scrollbar-thumb{background:#2a2a3a;border-radius:3px}

.hdr{border-bottom:1px solid #1e1e2e;background:rgba(12,12,20,.96);backdrop-filter:blur(12px);position:sticky;top:0;z-index:50;padding:12px 24px}
.hdr-in{max-width:1500px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.logo{font-size:20px;font-weight:800;background:linear-gradient(135deg,#f97316,#ef4444);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.bdg{font-size:10px;padding:3px 10px;border-radius:999px;font-weight:700}
.bdg-o{background:rgba(249,115,22,.12);color:#f97316}
.bdg-p{background:rgba(139,92,246,.12);color:#a78bfa}
.hdr-r{display:flex;align-items:center;gap:10px}
.dot{width:7px;height:7px;border-radius:50%;background:#4ade80;box-shadow:0 0 6px #4ade80}
.btn{font-size:11px;border:none;color:#fff;padding:5px 12px;border-radius:7px;cursor:pointer;font-weight:600}
.btn:hover{opacity:.85}
.btn-g{background:#1e1e2e;border:1px solid #2a2a3a!important}
.btn-p{background:linear-gradient(135deg,#ea580c,#dc2626)}

.wrap{max-width:1500px;margin:0 auto;padding:16px 24px}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}
@media(max-width:800px){.cards{grid-template-columns:repeat(2,1fr)}}
.crd{background:#14141f;border:1px solid #1e1e2e;border-radius:12px;padding:14px 16px}
.crd-l{font-size:9px;color:#6b7280;text-transform:uppercase;letter-spacing:.08em;font-weight:700}
.crd-v{font-size:22px;font-weight:800;margin-top:4px}
.crd-s{font-size:11px;color:#6b7280;margin-top:3px}

.tb{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.tabs{display:flex;background:#14141f;border-radius:8px;border:1px solid #1e1e2e;overflow:hidden}
.tab{padding:7px 18px;font-size:12px;font-weight:600;border:none;cursor:pointer;color:#6b7280;background:transparent}
.tab.act{color:#fff;background:linear-gradient(135deg,#ea580c,#dc2626);border-radius:7px}
.srch{background:#14141f;border:1px solid #1e1e2e;border-radius:7px;padding:6px 12px;font-size:12px;color:#fff;outline:none;width:160px}
.tb-i{font-size:11px;color:#4b5563;margin-left:auto}

.pnl{background:#14141f;border:1px solid #1e1e2e;border-radius:12px;overflow:hidden}

.tt{width:100%;border-collapse:collapse}
.tt th{text-align:center;padding:14px 14px;font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.04em;font-weight:700;white-space:nowrap;min-width:120px;border-bottom:2px solid #1e1e2e}
.tt th:first-child{text-align:left;padding-left:20px;position:sticky;left:0;background:#14141f;z-index:2;min-width:100px}
.tt td{text-align:center;padding:15px 14px;font-size:15px;font-weight:600;border-bottom:1px solid rgba(30,30,46,.5)}
.tt td:first-child{text-align:left;padding-left:20px;font-weight:700;font-size:14px;position:sticky;left:0;background:#14141f;z-index:2}
.tt .gr{background:rgba(249,115,22,.03)}
.tt .gr td:first-child{font-size:16px;font-weight:800;background:#16161f}
.tt .gr td{font-size:16px;font-weight:700;border-bottom:2px solid #1e1e2e;padding:16px 14px}
.tt tbody tr:hover td{background:rgba(255,255,255,.015)}
.tt tbody tr:hover td:first-child{background:#18182a}

.pt{width:100%;border-collapse:collapse}
.pt th{text-align:right;padding:11px 12px;font-weight:600;font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid #1e1e2e}
.pt td{padding:11px 12px;border-bottom:1px solid rgba(30,30,46,.4);font-size:13px}
.pt tbody tr{cursor:pointer}.pt tbody tr:hover{background:rgba(255,255,255,.015)}
.sb{font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px}
.sb-l{background:rgba(34,197,94,.1);color:#4ade80}.sb-s{background:rgba(239,68,68,.1);color:#f87171}
.bar-b{width:100%;background:#1e1e2e;border-radius:3px;height:5px;overflow:hidden;margin-top:3px}
.bar-f{height:100%;background:#f97316;border-radius:3px}
.ls-w{display:flex;align-items:center;gap:5px}
.ls-b{display:flex;flex:1;background:#1e1e2e;border-radius:3px;height:7px;overflow:hidden}

.dtl{background:#14141f;border:1px solid rgba(249,115,22,.2);border-radius:12px;padding:18px;margin-top:14px}
.dtl table{width:100%;border-collapse:collapse;font-size:12px}
.dtl th{padding:8px;color:#6b7280;font-weight:500;font-size:10px;text-transform:uppercase;border-bottom:1px solid #1e1e2e}
.dtl td{padding:7px 8px;border-bottom:1px solid rgba(30,30,46,.25)}

.g{color:#4ade80}.r{color:#f87171}.y{color:#fbbf24}.m{color:#6b7280}.d{color:#4b5563}
.ft{text-align:center;font-size:10px;color:#2a2a3a;padding:18px 0}
#err{font-size:11px;color:#f87171;display:none}
.ld{min-height:100vh;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:14px}
.ld-t{background:#1e1e2e;border-radius:999px;height:8px;overflow:hidden;width:280px}
.ld-f{height:100%;background:linear-gradient(90deg,#f97316,#ef4444);border-radius:999px;transition:width .5s}
</style>
</head>
<body>

<div id="ld" class="ld">
  <div class="logo" style="font-size:30px">⚡ HyperDash Monitor</div>
  <div id="lp" class="m" style="font-size:13px">Loading snapshots...</div>
  <div><div class="ld-t"><div id="lb" class="ld-f" style="width:0"></div></div></div>
  <div id="lc" style="font-size:12px;color:#6b7280"></div>
</div>

<div id="app" style="display:none">
<div class="hdr"><div class="hdr-in">
  <div style="display:flex;align-items:center;gap:10px">
    <span class="logo">⚡ HyperDash Monitor</span>
    <span class="bdg bdg-o">Top 100</span>
    <span class="bdg bdg-p">4h Snapshots</span>
  </div>
  <div class="hdr-r">
    <span id="err"></span>
    <div style="display:flex;align-items:center;gap:5px"><div class="dot"></div><span id="upd" class="m" style="font-size:11px">...</span></div>
    <button class="btn btn-g" onclick="snap()">📸 Snapshot Now</button>
    <button class="btn btn-p" onclick="load()">↻ Refresh</button>
  </div>
</div></div>

<div class="wrap">
  <div class="cards" id="cards"></div>
  <div class="tb">
    <div class="tabs">
      <button class="tab act" id="t-trend" onclick="sw('trend')">L/S Trend</button>
      <button class="tab" id="t-pos" onclick="sw('pos')">Positions</button>
    </div>
    <input class="srch" id="flt" placeholder="Search tickers..." oninput="render()">
    <span class="tb-i" id="tbi"></span>
  </div>
  <div id="p-trend" class="pnl"></div>
  <div id="p-pos" class="pnl" style="display:none"></div>
  <div id="p-dtl" style="display:none"></div>
  <div class="ft" id="ft"></div>
</div>
</div>

<script>
let snaps=[], tab="trend", sel=null;

function fmt(n,d=2){const v=parseFloat(n);if(isNaN(v))return"0";if(Math.abs(v)>=1e9)return(v/1e9).toFixed(d)+"B";if(Math.abs(v)>=1e6)return(v/1e6).toFixed(d)+"M";if(Math.abs(v)>=1e3)return(v/1e3).toFixed(d)+"K";return v.toFixed(d)}
function ago(t){const m=Math.floor((Date.now()-t)/6e4),h=Math.floor(m/60);return h>0?h+"h "+(m%60)+"m ago":m+"m ago"}
function rC(r){return r>=.5?"#4ade80":r>=.35?"#fbbf24":"#f87171"}

function arw(cur,prev){
  if(prev==null) return '<span class="d" style="margin-left:5px;font-size:13px">—</span>';
  const d=cur-prev;
  if(Math.abs(d)<0.005) return '<span class="d" style="margin-left:5px;font-size:13px">—</span>';
  if(d>0) return '<span class="g" style="margin-left:5px;font-size:14px">↗</span>';
  return '<span class="r" style="margin-left:5px;font-size:14px">↘</span>';
}

function sLbl(ts){
  const d=new Date(ts),n=new Date(),t=d.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",hour12:false});
  if(d.toDateString()===n.toDateString()) return "TODAY "+t;
  const y=new Date(n);y.setDate(y.getDate()-1);
  if(d.toDateString()===y.toDateString()) return "YEST "+t;
  const dd=["SUN","MON","TUE","WED","THU","FRI","SAT"],mm=["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
  return dd[d.getDay()]+" "+mm[d.getMonth()]+" "+d.getDate()+" "+t;
}

async function load(){
  try{
    const r=await fetch("/api/snapshots");
    snaps=await r.json();
    document.getElementById("ld").style.display="none";
    document.getElementById("app").style.display="block";
    render();
  }catch(e){
    document.getElementById("lp").textContent="Error: "+e.message;
    document.getElementById("err").textContent=e.message;
    document.getElementById("err").style.display="inline";
  }
}

async function snap(){
  document.getElementById("err").textContent="Taking snapshot...";
  document.getElementById("err").style.display="inline";
  document.getElementById("err").style.color="#fbbf24";
  try{
    await fetch("/api/snapshot/now");
    document.getElementById("err").style.display="none";
    await load();
  }catch(e){
    document.getElementById("err").textContent=e.message;
    document.getElementById("err").style.color="#f87171";
  }
}

function sw(t){tab=t;document.getElementById("t-trend").className="tab"+(t==="trend"?" act":"");document.getElementById("t-pos").className="tab"+(t==="pos"?" act":"");render()}
function selC(c){sel=sel===c?null:c;render()}

function render(){
  if(!snaps.length){
    document.getElementById("p-trend").innerHTML='<div style="padding:50px;text-align:center;color:#6b7280"><div style="font-size:28px;margin-bottom:8px">⏳</div><div style="font-size:13px">Waiting for first snapshot...<br><br>Click "📸 Snapshot Now" or wait for the server cron.</div></div>';
    return;
  }
  const last=snaps[snaps.length-1];
  const tL=last.tL,tS=last.tS,tT=tL+tS;
  const bias=tL>tS,bc=bias?"#4ade80":"#f87171";

  document.getElementById("upd").textContent=ago(last.ts);
  const hrs=snaps.length>=2?((snaps[snaps.length-1].ts-snaps[0].ts)/3600000).toFixed(1):0;
  document.getElementById("tbi").textContent=snaps.length+" snapshots • spanning "+hrs+"h";

  document.getElementById("cards").innerHTML=[
    {l:"📊 TOTAL NOTIONAL",v:"$"+fmt(tT),s:last.coins.length+" tickers • "+last.traders+" traders",c:"#fff"},
    {l:"📈 LONG POSITIONS",v:"$"+fmt(tL),s:((tL/tT)*100).toFixed(1)+"% of total",c:"#4ade80"},
    {l:"📉 SHORT POSITIONS",v:"$"+fmt(tS),s:((tS/tT)*100).toFixed(1)+"% of total",c:"#f87171"},
    {l:"🎯 GLOBAL BIAS",v:bias?"LONG":"SHORT",s:"L/S: "+((tL/tT)*100).toFixed(0)+"%",c:bc}
  ].map(c=>`<div class="crd"><div class="crd-l">${c.l}</div><div class="crd-v" style="color:${c.c}">${c.v}</div><div class="crd-s">${c.s}</div></div>`).join("");

  document.getElementById("p-trend").style.display=tab==="trend"?"block":"none";
  document.getElementById("p-pos").style.display=tab==="pos"?"block":"none";
  if(tab==="trend") rTrend();
  if(tab==="pos") rPos();
  rDtl();
  document.getElementById("ft").textContent="Server-backed • Top 100 traders • 4h auto-snapshots • "+snaps.length+" stored";
}

function rTrend(){
  const f=document.getElementById("flt").value.toLowerCase();
  // Get all unique coins from latest snap
  const latest=snaps[snaps.length-1];
  const coins=latest.coins.filter(c=>!f||c.c.toLowerCase().includes(f)).slice(0,30);

  // Show last 8 snapshots max, newest on right
  const show=snaps.slice(-8);

  let h='<div style="overflow-x:auto"><table class="tt"><thead><tr><th>ASSET</th>';
  for(const sn of show) h+='<th>'+sLbl(sn.ts)+'</th>';
  h+='</tr></thead><tbody>';

  // Global row
  h+='<tr class="gr"><td>Global</td>';
  for(let i=0;i<show.length;i++){
    const r=show[i].gR,pr=i>0?show[i-1].gR:null;
    h+=`<td><span style="color:${rC(r)}">${(r*100).toFixed(0)}%</span>${arw(r,pr)}</td>`;
  }
  h+='</tr>';

  for(const coin of coins){
    h+=`<tr onclick="selC('${coin.c}')" style="cursor:pointer"><td>${coin.c} <a href="https://app.hyperliquid.xyz/trade/${coin.c}" target="_blank" onclick="event.stopPropagation()" style="color:#4b5563;font-size:10px;text-decoration:none">↗</a></td>`;
    for(let i=0;i<show.length;i++){
      const cs=show[i].coins.find(x=>x.c===coin.c);
      if(!cs){h+='<td style="color:#2a2a3a">—</td>';continue}
      const ps=i>0?show[i-1].coins.find(x=>x.c===coin.c):null;
      h+=`<td><span style="color:${rC(cs.r)}">${(cs.r*100).toFixed(0)}%</span>${arw(cs.r,ps?ps.r:null)}</td>`;
    }
    h+='</tr>';
  }
  h+='</tbody></table></div>';
  document.getElementById("p-trend").innerHTML=h;
}

function rPos(){
  const f=document.getElementById("flt").value.toLowerCase();
  const last=snaps[snaps.length-1];
  const prev=snaps.length>=2?snaps[snaps.length-2]:null;
  const coins=last.coins.filter(c=>!f||c.c.toLowerCase().includes(f)).slice(0,30);
  const mx=coins[0]?.t||1;

  let h='<div style="overflow-x:auto"><table class="pt"><thead><tr><th style="text-align:left">Asset</th><th>Total Notional</th><th style="text-align:center">Majority</th><th style="text-align:center">L/S Ratio</th><th>Long</th><th>Short</th><th style="text-align:center">Traders</th><th style="text-align:center">Δ vs Prev</th></tr></thead><tbody>';
  coins.forEach((c,i)=>{
    const side=c.r>=.5?"LONG":"SHORT";
    const pc=prev?prev.coins.find(x=>x.c===c.c):null;
    const delta=pc?((c.r-pc.r)*100).toFixed(1):null;
    h+=`<tr onclick="selC('${c.c}')">`;
    h+=`<td style="text-align:left"><span class="d" style="font-size:10px;margin-right:6px;width:20px;display:inline-block">${i+1}</span><b style="font-size:14px">${c.c}</b></td>`;
    h+=`<td style="text-align:right"><div style="font-weight:600">$${fmt(c.t)}</div><div class="bar-b"><div class="bar-f" style="width:${c.t/mx*100}%"></div></div></td>`;
    h+=`<td style="text-align:center"><span class="sb ${side==="LONG"?"sb-l":"sb-s"}">${side}</span></td>`;
    h+=`<td style="width:120px"><div class="ls-w"><div class="ls-b"><div style="width:${c.r*100}%;height:100%;background:#4ade80"></div><div style="width:${(1-c.r)*100}%;height:100%;background:#ef4444"></div></div><span style="font-size:11px;color:${rC(c.r)};font-weight:600;width:32px;text-align:right">${(c.r*100).toFixed(0)}%</span></div></td>`;
    h+=`<td style="text-align:right" class="g">$${fmt(c.lN)}</td>`;
    h+=`<td style="text-align:right" class="r">$${fmt(c.sN)}</td>`;
    h+=`<td style="text-align:center"><span class="g">${c.lT}</span><span class="d"> / </span><span class="r">${c.sT}</span></td>`;
    h+=`<td style="text-align:center">${delta!==null?(parseFloat(delta)>0?'<span class="g" style="font-weight:600">+'+delta+'%</span>':parseFloat(delta)<0?'<span class="r" style="font-weight:600">'+delta+'%</span>':'<span class="d">0%</span>'):'<span class="d">—</span>'}</td>`;
    h+='</tr>';
  });
  h+='</tbody></table></div>';
  document.getElementById("p-pos").innerHTML=h;
}

function rDtl(){
  const el=document.getElementById("p-dtl");
  if(!sel||!snaps.length){el.style.display="none";return}
  // Show coin's L/S history across snapshots
  const show=snaps.slice(-8);
  let history=[];
  for(let i=0;i<show.length;i++){
    const cs=show[i].coins.find(x=>x.c===sel);
    if(cs) history.push({ts:show[i].ts,...cs,prev:i>0?show[i-1].coins.find(x=>x.c===sel):null});
  }
  if(!history.length){el.style.display="none";return}

  el.style.display="block";el.className="dtl";
  let h=`<div style="display:flex;justify-content:space-between;margin-bottom:14px"><span style="font-size:16px;font-weight:700">${sel} — Position History</span><button style="background:none;border:none;color:#6b7280;cursor:pointer;font-size:15px" onclick="selC(null)">✕</button></div>`;
  h+='<table><thead><tr><th style="text-align:left">Time</th><th>L/S Ratio</th><th>Delta</th><th>Total Notional</th><th>Long</th><th>Short</th><th>Traders (L/S)</th></tr></thead><tbody>';
  history.forEach(s=>{
    const delta=s.prev?((s.r-s.prev.r)*100).toFixed(1):null;
    h+='<tr>';
    h+=`<td style="text-align:left;font-weight:600">${sLbl(s.ts)}</td>`;
    h+=`<td style="text-align:center"><span style="color:${rC(s.r)};font-weight:700;font-size:15px">${(s.r*100).toFixed(0)}%</span>${arw(s.r,s.prev?s.prev.r:null)}</td>`;
    h+=`<td style="text-align:center">${delta!==null?(parseFloat(delta)>0?'<span class="g" style="font-weight:600">+'+delta+'%</span>':parseFloat(delta)<0?'<span class="r" style="font-weight:600">'+delta+'%</span>':'<span class="d">0</span>'):'<span class="d">—</span>'}</td>`;
    h+=`<td style="text-align:right">$${fmt(s.t)}</td>`;
    h+=`<td style="text-align:right" class="g">$${fmt(s.lN)}</td>`;
    h+=`<td style="text-align:right" class="r">$${fmt(s.sN)}</td>`;
    h+=`<td style="text-align:center"><span class="g">${s.lT}</span><span class="d"> / </span><span class="r">${s.sT}</span></td>`;
    h+='</tr>';
  });
  h+='</tbody></table>';
  el.innerHTML=h;
}

// Init
load();
setInterval(load, 60000); // refresh view every 1min
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print(f"=== HyperDash Monitor ===")
    print(f"Port: {PORT}")
    print(f"Data file: {DATA_FILE}")
    print(f"Snapshot interval: {SNAP_INTERVAL}s ({SNAP_INTERVAL//3600}h)")
    print(f"Top traders: {TOP_N}")
    print()

    # Check if we have existing data
    existing = load_snapshots()
    print(f"Loaded {len(existing)} existing snapshots")

    # Start cron thread
    cron = threading.Thread(target=cron_loop, daemon=True)
    cron.start()
    print("Cron thread started (first snapshot in progress...)")

    # Start HTTP server
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Server running at http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()
