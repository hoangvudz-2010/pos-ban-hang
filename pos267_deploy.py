#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║     POS BÁN HÀNG - Web Version (Deploy Edition)              ║
║  Lưu dữ liệu lên Google Drive — chạy được trên Render.com   ║
╚══════════════════════════════════════════════════════════════╝
"""
import json, os, datetime, base64, smtplib, ssl, threading
import concurrent.futures
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from flask import Flask, request, jsonify, session, render_template_string

# ── CẤU HÌNH ─────────────────────────────────────────────────
SECRET_KEY = os.environ.get("SECRET_KEY", "POS_WEB_2024_SECRET")

# ── GOOGLE DRIVE CONFIG ───────────────────────────────────────
GDRIVE_CREDENTIALS = os.environ.get("GDRIVE_CREDENTIALS", "")
GDRIVE_FOLDER_ID   = os.environ.get("GDRIVE_FOLDER_ID", "")
DRIVE_DATA_FILE     = "pos_data.json"
DRIVE_USERS_FILE    = "users.json"
DRIVE_SETTINGS_FILE = "settings.json"

_cache = {}
_cache_time = {}
CACHE_TTL = 5

# ── EMAIL ─────────────────────────────────────────────────────
_EK = b'POS2024SecretKey'
def _decode(enc):
    b = base64.b64decode(enc)
    return bytes(b[i] ^ _EK[i % len(_EK)] for i in range(len(b))).decode()
DEFAULT_SENDER   = _decode('OCAyXFdEQX0QDRslEyYEEDxhMF1d')
DEFAULT_PASSWORD = _decode('Myg+VBBURjQHQwgBByZFETo1Jw==')
_email_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)

# ── ARGON2 ────────────────────────────────────────────────────
_ph = None
def get_ph():
    global _ph
    if _ph is None:
        from argon2 import PasswordHasher
        _ph = PasswordHasher(time_cost=2, memory_cost=32768, parallelism=2, hash_len=32)
    return _ph
def hash_pw(pw):    return get_ph().hash(pw)
def verify_pw(h,pw):
    try: get_ph().verify(h, pw); return True
    except: return False

# ── GOOGLE DRIVE HELPERS ──────────────────────────────────────
_drive_service = None

def get_drive():
    global _drive_service
    if _drive_service:
        return _drive_service
    if not GDRIVE_CREDENTIALS:
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds_dict = json.loads(GDRIVE_CREDENTIALS)
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=["https://www.googleapis.com/auth/drive"])
        _drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return _drive_service
    except Exception as e:
        print(f"[Drive] Lỗi kết nối: {e}")
        return None

def drive_find_file(name):
    try:
        svc = get_drive()
        if not svc: return None
        q = f"name='{name}' and '{GDRIVE_FOLDER_ID}' in parents and trashed=false"
        res = svc.files().list(q=q, fields="files(id,name)").execute()
        files = res.get("files", [])
        return files[0]["id"] if files else None
    except Exception as e:
        print(f"[Drive] Lỗi tìm {name}: {e}")
        return None

def drive_read(name):
    import time
    if name in _cache and time.time() - _cache_time.get(name, 0) < CACHE_TTL:
        return _cache[name]
    try:
        svc = get_drive()
        if not svc: return None
        fid = drive_find_file(name)
        if not fid: return None
        from googleapiclient.http import MediaIoBaseDownload
        import io
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=fid))
        done = False
        while not done: _, done = dl.next_chunk()
        data = json.loads(buf.getvalue().decode("utf-8"))
        _cache[name] = data; _cache_time[name] = time.time()
        return data
    except Exception as e:
        print(f"[Drive] Lỗi đọc {name}: {e}")
        return None

def drive_write(name, data):
    import time
    try:
        svc = get_drive()
        if not svc: return False
        from googleapiclient.http import MediaIoBaseUpload
        import io
        content = json.dumps(data, ensure_ascii=False, separators=(',',':')).encode("utf-8")
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype="application/json")
        fid = drive_find_file(name)
        if fid:
            svc.files().update(fileId=fid, media_body=media).execute()
        else:
            svc.files().create(body={"name": name, "parents": [GDRIVE_FOLDER_ID]},
                               media_body=media).execute()
        _cache[name] = data; _cache_time[name] = time.time()
        return True
    except Exception as e:
        print(f"[Drive] Lỗi ghi {name}: {e}")
        return False

def _use_drive():
    return bool(GDRIVE_CREDENTIALS and GDRIVE_FOLDER_ID)

# ══════════════════════════════════════════════════════════════
#  DỮ LIỆU
# ══════════════════════════════════════════════════════════════
def load_users():
    if _use_drive():
        d = drive_read(DRIVE_USERS_FILE)
        if d is not None: return d
    if os.path.exists("users.json"):
        try:
            with open("users.json","r",encoding="utf-8") as f: return json.load(f)
        except: pass
    return {}

def save_users(u):
    if _use_drive():
        ok = drive_write(DRIVE_USERS_FILE, u)
        if not ok:
            raise RuntimeError("Luu Drive that bai")
    else:
        with open("users.json","w",encoding="utf-8") as f: json.dump(u,f,ensure_ascii=False,indent=2)

def load_data():
    if _use_drive():
        d = drive_read(DRIVE_DATA_FILE)
        if d is not None: return d
    if os.path.exists("pos_data.json"):
        try:
            with open("pos_data.json","r",encoding="utf-8") as f: return json.load(f)
        except: pass
    return {"tables":["A1","A2","B1","B2"],
            "menu_items":[
                {"name":"Cà Phê Đen", "price":20000,"cost_price":10000,"stock":None,"category":"Cà phê"},
                {"name":"Cà Phê Sữa","price":25000,"cost_price":12000,"stock":50,  "category":"Cà phê"},
                {"name":"Trà Đào",    "price":30000,"cost_price":15000,"stock":30,  "category":"Trà & Trà sữa"},
                {"name":"Sinh Tố Bơ","price":45000,"cost_price":20000,"stock":None,"category":"Nước ép & Sinh tố"},
            ],
            "orders":{},"sales_history":[],
            "table_orders_store":{},"table_tab_list":{},"table_active_order":{}}

def save_data(d):
    if _use_drive():
        drive_write(DRIVE_DATA_FILE, d)
    else:
        tmp = "pos_data.json.tmp"
        with open(tmp,"wb") as f:
            f.write(json.dumps(d,ensure_ascii=False,separators=(',',':')).encode("utf-8"))
        os.replace(tmp, "pos_data.json")

def load_settings():
    if _use_drive():
        d = drive_read(DRIVE_SETTINGS_FILE)
        if d is not None: return d
    if os.path.exists("settings.json"):
        try:
            with open("settings.json","r",encoding="utf-8") as f: return json.load(f)
        except: pass
    return {"use_qr_payment":True,"bank_id":"MB","account_no":"","account_name":"",
            "email_enabled":False,"email_recipient":"",
            "email_smtp_server":"smtp.gmail.com","email_smtp_port":465,
            "email_sender":DEFAULT_SENDER,"email_password":DEFAULT_PASSWORD}

def save_settings(s):
    if _use_drive(): drive_write(DRIVE_SETTINGS_FILE, s)
    else:
        with open("settings.json","w",encoding="utf-8") as f: json.dump(s,f,ensure_ascii=False,indent=2)


def gen_tab_id(data):
    now = datetime.datetime.now()
    ds  = now.strftime("%y%m%d")
    mm  = {1:"JA",2:"FE",3:"MR",4:"AP",5:"MY",6:"JN",7:"JL",8:"AU",9:"SE",10:"OC",11:"NO",12:"DE"}
    used = set()
    for tbl in data.get("table_orders_store",{}).values():
        for oid in tbl:
            if ds in oid:
                try: used.add(int(oid.split(ds)[-1]))
                except: pass
    for h in data.get("sales_history",[]):
        oid = h.get("order_id","")
        if ds in oid:
            try: used.add(int(oid.split(ds)[-1]))
            except: pass
    n=1
    while n in used: n+=1
    return f"{mm.get(now.month,'UN')}-{ds}{n:02d}"

def send_email_bg(record, settings):
    if not settings.get("email_enabled"): return
    recs = [r.strip() for r in settings.get("email_recipient","").split(",") if r.strip()]
    if not recs: return
    sender=settings.get("email_sender",DEFAULT_SENDER)
    pw=settings.get("email_password",DEFAULT_PASSWORD)
    srv=settings.get("email_smtp_server","smtp.gmail.com")
    port=int(settings.get("email_smtp_port",465))
    rows="".join(f"<tr><td style='padding:5px 10px'>{i['name']}</td>"
                 f"<td style='text-align:center'>{i.get('quantity',1)}</td>"
                 f"<td style='text-align:right;padding:5px 10px'>{i['price']*i.get('quantity',1):,.0f}đ</td></tr>"
                 for i in record.get("items",[]))
    html=(f"<html><body style='font-family:Segoe UI,Arial;background:#f5f5f5;padding:20px'>"
          f"<div style='max-width:500px;margin:auto;background:#fff;border-radius:10px;overflow:hidden'>"
          f"<div style='background:#007bff;color:#fff;padding:20px 24px'>"
          f"<h2 style='margin:0'>🧾 Hoá Đơn #{record.get('order_id','')}</h2>"
          f"<p style='margin:4px 0 0;opacity:.8'>Bàn {record.get('table','')} · {record.get('date','')}</p></div>"
          f"<div style='padding:16px 24px'>"
          f"<table style='width:100%;border-collapse:collapse;font-size:13px'>"
          f"<thead><tr style='background:#f8f9fa'><th style='text-align:left;padding:8px 10px'>Món</th>"
          f"<th style='text-align:center'>SL</th><th style='text-align:right;padding:8px 10px'>Tiền</th></tr></thead>"
          f"<tbody>{rows}</tbody></table>"
          f"<div style='margin:12px 0;padding:12px 16px;background:#f8f9fa;border-radius:8px;"
          f"display:flex;justify-content:space-between'>"
          f"<b>TỔNG CỘNG</b><b style='color:#28a745'>{record.get('total',0):,.0f}đ</b></div>"
          f"</div></div></body></html>")
    msg=MIMEMultipart("alternative")
    msg["Subject"]=f"[Hoá đơn] #{record.get('order_id','')} — {record.get('total',0):,.0f}đ"
    msg["From"]=sender; msg["To"]=", ".join(recs)
    msg.attach(MIMEText(html,"html","utf-8"))
    def _go():
        try:
            with smtplib.SMTP_SSL(srv,port,context=ssl.create_default_context(),timeout=10) as s:
                s.login(sender,pw); s.sendmail(sender,recs,msg.as_bytes())
        except Exception as e: print(f"[Email] Lỗi: {e}")
    _email_pool.submit(_go)

# ══════════════════════════════════════════════════════════════
#  GIAO DIỆN — toàn bộ viết bằng Python string
#  Cấu trúc y hệt bản tkinter gốc:
#  TOPBAR (xanh) | MAIN = [Notebook 2 tab] + [Panel Bill phải]
# ══════════════════════════════════════════════════════════════

CSS = r"""
/* ═══════════════════════════════════════════════════════════
   POS BÁN HÀNG — Liquid Glass · Light Edition
   Frosted · Bright · Airy · Apple-Vision-inspired
   ═══════════════════════════════════════════════════════════ */
@import url('https://fonts.googleapis.com/css2?family=Mona+Sans:ital,wdth,wght@0,75..125,200..900;1,75..125,200..900&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@300;400;500&display=swap');

:root {
  /* ── Glass surfaces (light mode) ── */
  --g-white:    rgba(255,255,255,.62);
  --g-white-hi: rgba(255,255,255,.80);
  --g-white-lo: rgba(255,255,255,.40);
  --g-blue:     rgba(200,220,255,.55);
  --g-green:    rgba(190,245,220,.50);
  --g-rose:     rgba(255,210,220,.50);

  /* ── Specular ── */
  --spec:     rgba(255,255,255,.90);
  --spec-lo:  rgba(255,255,255,.55);
  --edge:     rgba(255,255,255,.75);
  --edge-lo:  rgba(180,200,255,.40);
  --edge-out: rgba(100,140,255,.22);

  /* ── Accent colors ── */
  --p:  #2f7aff;  --pd: #1a5fe0;
  --pl: rgba(47,122,255,.14);  --p2: rgba(47,122,255,.07);
  --a:  #00b074;  --ad: #009060;
  --al: rgba(0,176,116,.14);
  --d:  #f03060;  --dd: #cc1a45;
  --dl: rgba(240,48,96,.13);
  --w:  #f07020;

  /* ── Text on light glass ── */
  --txt:  #0c1a3a;
  --txt2: #1e3565;
  --mu:   #5a72a8;
  --mu2:  #8da0cc;

  /* ── Geometry ── */
  --r: 16px;  --r-sm: 10px;  --r-xs: 7px;
  --r-lg: 22px;  --r-xl: 28px;  --r-pill: 10px;

  /* ── Shadows ── */
  --sh0: 0 2px 8px rgba(30,60,160,.10), 0 1px 3px rgba(30,60,160,.07);
  --sh1: 0 6px 24px rgba(30,60,160,.13), 0 2px 8px rgba(30,60,160,.08);
  --sh2: 0 12px 40px rgba(30,60,160,.16), 0 4px 12px rgba(30,60,160,.10);
  --sh3: 0 24px 72px rgba(30,60,160,.20), 0 6px 20px rgba(30,60,160,.12);
  --glow-p: 0 0 28px rgba(47,122,255,.30), 0 4px 16px rgba(47,122,255,.18);
  --glow-a: 0 0 28px rgba(0,176,116,.28), 0 4px 16px rgba(0,176,116,.16);
  --glow-d: 0 0 24px rgba(240,48,96,.26);
  --inner-hi: inset 0 1.5px 0 var(--spec);
  --inner-lo: inset 0 1px 0 var(--spec-lo);

  /* ── Motion ── */
  --spring: cubic-bezier(.34,1.56,.64,1);
  --smooth: cubic-bezier(.4,0,.2,1);

  /* ── iOS 26 Liquid Glass system ── */
  /* These are overridden at runtime by applyGlassOpacity() */
  --lg-fill:    0.03;    /* very low fill — nearly invisible glass */
  --lg-blur:    18px;    /* light blur — background clearly visible */
  --lg-sat:     180%;    /* moderate saturate */
  --lg-bri:     1.08;
  --lg-spec:    rgba(255,255,255,0.90);
  --lg-spec-lo: rgba(255,255,255,0.50);
  --lg-edge:    rgba(255,255,255,0.55);
  --lg-refract: rgba(200,225,255,0.08);
  --lg-shadow:  0 2px 24px rgba(0,0,0,0.06), 0 1px 4px rgba(0,0,0,0.03);
  /* Legacy compat */
  --go-surface: 0.12;
  --go-element: 0.14;
  --go-modal:   0.18;
  --go-overlay: 0.06;
  --go-blur:    18px;
}

/* ── RESET ── */
*{box-sizing:border-box;margin:0;padding:0}
*::selection{background:rgba(47,122,255,.22);color:var(--txt)}

/* ══════════════════════════════════════
   LIVE BACKGROUND — bright pastel aurora
   ══════════════════════════════════════ */
body {
  font-family: 'Mona Sans','Segoe UI',sans-serif;
  font-stretch: 95%;
  height: 100vh; overflow: hidden;
  display: flex; flex-direction: column;
  font-size: 13px; line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  color: var(--txt);
  background: #fff;
  position: relative;
}

/* Canvas animated background */
#bg-canvas { display: none; }

/* Portal container — pointer-events none trên chính nó, children vẫn nhận event */
#hpop-portal {
  position: fixed; inset: 0; z-index: 9999;
  pointer-events: none;
}
#hpop-portal > * {
  pointer-events: auto;
}

/* Remove old CSS blobs */
body::before { display: none; }
body::after  { display: none; }

/* Subtle dot grid */
body .grain {
  position: fixed; inset: 0; z-index: 1; pointer-events: none; opacity: .025;
  background-image: radial-gradient(circle, #1040a0 1px, transparent 1px);
  background-size: 24px 24px;
  mask-image: radial-gradient(ellipse 90% 90% at 50% 50%, black 40%, transparent 100%);
  -webkit-mask-image: radial-gradient(ellipse 90% 90% at 50% 50%, black 40%, transparent 100%);
}

#wf-overlay-el { display: none; }

#main,#topbar,#login-page,.hist-bar,.hist-body,
#bill-panel,.nb-tabs,.nb-content,#pos-page,#hist-page {
  position: relative; z-index: 2;
}

button { cursor: pointer; font-family: inherit; font-stretch: inherit; transition: all .22s var(--smooth) }
input, select, textarea { font-family: inherit; font-stretch: inherit }
::-webkit-scrollbar { width: 3px; height: 3px }
::-webkit-scrollbar-track { background: transparent }
::-webkit-scrollbar-thumb { background: rgba(47,122,255,.25); border-radius: 99px }
::-webkit-scrollbar-thumb:hover { background: rgba(47,122,255,.45) }

/* ══════════════════════════════════════
   TOPBAR
   ══════════════════════════════════════ */
#topbar {
  height: 56px;
  background: rgba(255,255,255,.20);
  backdrop-filter: blur(18px) saturate(160%) brightness(1.05);
  -webkit-backdrop-filter: blur(18px) saturate(160%) brightness(1.05);
  border-bottom: 1px solid var(--edge-lo);
  box-shadow: 0 1px 0 var(--edge), 0 4px 24px rgba(30,80,200,.06), var(--inner-lo);
  display: flex; align-items: center; padding: 0 20px; gap: 14px; flex-shrink: 0;
  z-index: 100; position: relative;
}
/* Prismatic rainbow top edge */
#topbar::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg,
    transparent 0%,
    #60a5fa 15%, #818cf8 30%,
    #a78bfa 45%, #34d399 60%,
    #60a5fa 75%, transparent 100%);
  background-size: 200% 100%;
  animation: prism 5s linear infinite;
}
@keyframes prism { 0% { background-position:0% } 100% { background-position:200% } }

#topbar .brand {
  font-family: 'Mona Sans', sans-serif;
  font-size: 15px; font-weight: 800; font-stretch: 115%;
  flex: 1; letter-spacing: -.5px; color: var(--txt);
}
#topbar .brand em {
  font-style: normal;
  background: linear-gradient(90deg, var(--p), #818cf8, var(--a));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text; background-size: 200%;
  animation: prism 4s linear infinite reverse;
}
#topbar .ttime {
  font-family: 'Fira Code', monospace;
  font-size: 11px; font-weight: 400; cursor: pointer; letter-spacing: .6px;
  padding: 7px 15px; border-radius: var(--r-pill);
  background: rgba(255,255,255,.55); color: var(--mu);
  border: 1px solid var(--edge-lo);
  box-shadow: var(--inner-lo), var(--sh0);
  transition: all .25s var(--spring);
}
#topbar .ttime:hover {
  background: rgba(255,255,255,.85);
  border-color: rgba(47,122,255,.35); color: var(--p);
  box-shadow: var(--glow-p), var(--inner-hi);
  transform: translateY(-2px);
}
.menu-dd { position: relative }
.menu-btn {
  background: rgba(255,255,255,.55); color: var(--txt2);
  border: 1px solid var(--edge-lo);
  box-shadow: var(--inner-lo), var(--sh0);
  padding: 8px 18px; border-radius: var(--r-pill);
  font-size: 12px; font-weight: 700; letter-spacing: .4px;
  transition: all .25s var(--spring);
}
.menu-btn:hover {
  background: rgba(255,255,255,.85);
  border-color: rgba(47,122,255,.35); color: var(--p);
  box-shadow: var(--glow-p), var(--inner-hi);
  transform: translateY(-2px);
}
.ddlist {
  position: absolute; top: calc(100% + 14px); right: 0;
  background: linear-gradient(145deg,
    rgba(240,250,255,.82) 0%,
    rgba(225,242,255,.78) 50%,
    rgba(235,248,255,.82) 100%);
  backdrop-filter: blur(56px) saturate(220%) brightness(1.10);
  -webkit-backdrop-filter: blur(56px) saturate(220%) brightness(1.10);
  border-radius: 18px;
  box-shadow: var(--sh3), var(--inner-hi),
    0 0 0 .5px rgba(255,255,255,.55),
    inset 0 0 40px rgba(180,215,255,.12);
  min-width: 258px; padding: 10px; z-index: 999; display: none;
  border: 1px solid rgba(200,225,255,.60);
  overflow: hidden;
}
/* Caustic water light inside dropdown */
.ddlist::before {
  content: '';
  position: absolute; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(ellipse 70% 50% at 80% 20%, rgba(160,205,255,.22) 0%, transparent 60%),
    radial-gradient(ellipse 50% 40% at 15% 75%, rgba(130,230,190,.15) 0%, transparent 55%);
  animation: causticsShift 10s ease-in-out infinite;
  filter: blur(20px);
}
/* Prismatic top edge */
.ddlist::after {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg, transparent, #a5c8ff 20%, #c4b5fd 45%, #67e8f9 65%, #a5c8ff 85%, transparent);
  background-size: 200% 100%;
  animation: prism 4s linear infinite;
  border-radius: 18px 18px 0 0;
}
.ddlist.open { display: block; animation: dropletIn .26s var(--spring) }
@keyframes dropletIn {
  0%   { opacity:0; transform: translateY(-16px) scale(.91) scaleX(1.05) scaleY(.95) }
  40%  { opacity:1; transform: translateY(4px) scale(1.01) scaleX(.97) scaleY(1.03) }
  62%  { transform: translateY(-2px) scale(.999) }
  80%  { transform: translateY(1px) }
  100% { transform: translateY(0) scale(1) }
}
.ddi {
  display: flex; align-items: center; gap: 11px;
  padding: 12px 14px; border: none;
  background: none; width: 100%; text-align: left;
  font-size: 13px; font-weight: 600; color: var(--mu);
  border-radius: var(--r-sm); cursor: pointer;
  transition: all .22s var(--spring);
  border: 1px solid transparent;
  position: relative; z-index: 1; overflow: hidden;
}
/* Left water-streak accent */
.ddi::before {
  content: '';
  position: absolute; left: 0; top: 50%; bottom: 50%;
  width: 3px; background: linear-gradient(180deg, var(--p), #818cf8);
  border-radius: 2px; transition: all .24s var(--spring);
}
/* Ripple sweep on hover */
.ddi::after {
  content: '';
  position: absolute; inset: 0; pointer-events: none;
  background: linear-gradient(90deg, rgba(47,122,255,.08) 0%, rgba(47,122,255,.14) 50%, transparent 100%);
  transform: translateX(-100%); transition: transform .3s ease;
  border-radius: inherit;
}
.ddi:hover {
  background: rgba(47,122,255,.10); border-color: rgba(47,122,255,.20);
  color: var(--p); transform: translateX(5px);
}
.ddi:hover::before { top: 10%; bottom: 10% }
.ddi:hover::after { transform: translateX(0) }
.ddsep { height: 1px; background: var(--edge-lo); margin: 6px 0; position: relative; z-index:1 }

/* ══════════════════════════════════════
   MAIN LAYOUT
   ══════════════════════════════════════ */
#main { display: flex; flex: 1; overflow: hidden; padding: 10px; gap: 10px }

/* ══════════════════════════════════════
   NOTEBOOK (LEFT PANEL)
   ══════════════════════════════════════ */
#nb { display: flex; flex-direction: column; flex: 1; overflow: hidden; min-width: 0 }
.nb-tabs {
  display: flex; flex-shrink: 0; overflow: hidden;
  background: rgba(255,255,255,.18);
  backdrop-filter: blur(18px) saturate(160%);
  -webkit-backdrop-filter: blur(18px) saturate(160%);
  border-radius: var(--r) var(--r) 0 0;
  border: 1px solid var(--edge-lo); border-bottom: none;
  box-shadow: var(--inner-lo), var(--sh0);
}
.nb-tab {
  flex: 1; padding: 14px 16px;
  font-size: 11px; font-weight: 700;
  border: none; background: transparent; color: var(--mu);
  cursor: pointer; transition: all .25s var(--smooth);
  white-space: nowrap; letter-spacing: 1px; text-transform: uppercase;
  position: relative;
}
.nb-tab::after {
  content: '';
  position: absolute; bottom: 0; left: 50%; right: 50%; height: 2.5px;
  background: linear-gradient(90deg, var(--p), #818cf8);
  border-radius: 3px; transition: all .32s var(--spring); opacity: 0;
}
.nb-tab:hover { color: var(--p); background: rgba(47,122,255,.07) }
.nb-tab.on { color: var(--p); background: rgba(47,122,255,.10) }
.nb-tab.on::after { left: 10%; right: 10%; opacity: 1 }
.nb-content {
  flex: 1; overflow: hidden; display: flex; flex-direction: column;
  background: rgba(255,255,255,.15);
  backdrop-filter: blur(18px) saturate(160%);
  -webkit-backdrop-filter: blur(18px) saturate(160%);
  border: 1px solid var(--edge-lo); border-top: none;
  border-radius: 0 0 var(--r) var(--r);
  box-shadow: var(--sh1), var(--inner-lo);
}

/* ══════════════════════════════════════
   TABLE TAB
   ══════════════════════════════════════ */
#tab-tables { display: flex; flex-direction: column; flex: 1; overflow: hidden }
#tab-tables.hide { display: none }
.table-area { flex: 1; overflow-y: auto; padding: 14px }
.table-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 12px }

/* Staggered entrance */
.tcard { animation: cardRise .4s var(--spring) both }
.tcard:nth-child(1){animation-delay:.03s} .tcard:nth-child(2){animation-delay:.07s}
.tcard:nth-child(3){animation-delay:.11s} .tcard:nth-child(4){animation-delay:.15s}
.tcard:nth-child(5){animation-delay:.19s} .tcard:nth-child(6){animation-delay:.23s}
.tcard:nth-child(n+7){animation-delay:.27s}
@keyframes cardRise {
  0%   { opacity:0; transform: translateY(20px) scale(.91) scaleX(1.07) scaleY(.94) }
  38%  { opacity:1; transform: translateY(-3px) scale(1.02) scaleX(.98) scaleY(1.03) }
  58%  { transform: translateY(2px) scale(.998) }
  76%  { transform: translateY(-1px) scale(1.001) }
  100% { transform: translateY(0) scale(1) }
}

.tcard {
  background: rgba(255,255,255,.22);
  backdrop-filter: blur(16px) saturate(150%);
  -webkit-backdrop-filter: blur(16px) saturate(150%);
  border: 1px solid var(--edge);
  border-radius: var(--r);
  padding: 24px 14px 20px;
  text-align: center; cursor: pointer;
  transition: all .28s var(--spring);
  position: relative; overflow: hidden;
  box-shadow: var(--sh0), var(--inner-lo);
}
/* Top specular streak */
.tcard::before {
  content: '';
  position: absolute; top: 0; left: 8%; right: 8%; height: 1px;
  background: linear-gradient(90deg, transparent, var(--spec), transparent);
  transition: .28s ease;
}
/* Refraction shimmer on hover */
.tcard::after {
  content: '';
  position: absolute; top: -50%; left: -75%; width: 50%; height: 200%;
  background: linear-gradient(105deg, transparent, rgba(255,255,255,.35), transparent);
  transform: skewX(-15deg);
  transition: left .5s ease; pointer-events: none;
}
.tcard:hover {
  background: rgba(255,255,255,.82);
  border-color: rgba(47,122,255,.35);
  transform: translateY(-5px) scale(1.02);
  box-shadow: var(--sh2), var(--inner-hi);
}
.tcard:hover::before { left: 0; right: 0 }
.tcard:hover::after  { left: 140% }
.tcard.sel {
  background: rgba(200,220,255,.70);
  border-color: rgba(47,122,255,.55);
  box-shadow: var(--glow-p), var(--inner-hi);
  transform: translateY(-4px) scale(1.02);
}
.tcard.sel::before {
  left: 0; right: 0;
  background: linear-gradient(90deg, transparent, rgba(100,170,255,.8), rgba(200,230,255,.9), rgba(100,170,255,.8), transparent);
}
.tcard.busy {
  background: rgba(255,235,215,.65);
  border-color: rgba(240,112,32,.35);
}
.tcard.busy:hover {
  background: rgba(255,235,215,.85); border-color: rgba(240,112,32,.55);
  box-shadow: 0 0 28px rgba(240,112,32,.2), var(--sh2), var(--inner-hi);
}
.tcard.busy.sel { background: rgba(200,220,255,.70); border-color: rgba(47,122,255,.55) }
.tcard .tname {
  font-family: 'Mona Sans', sans-serif; font-stretch: 110%;
  font-size: 24px; font-weight: 800; color: var(--txt);
  letter-spacing: -.7px; margin-bottom: 9px;
  text-shadow: 0 1px 6px rgba(30,60,160,.12);
}
.tcard .tstat {
  font-size: 11px; font-weight: 600; color: var(--mu);
  letter-spacing: .4px; display: flex; align-items: center;
  justify-content: center; gap: 7px;
}
.tcard .tstat::before {
  content: ''; width: 6px; height: 6px; border-radius: 50%;
  background: var(--mu2); flex-shrink: 0; transition: .22s ease;
}
.tcard.sel .tstat::before { background: var(--p); box-shadow: 0 0 8px rgba(47,122,255,.5) }
.tcard.busy .tstat { color: var(--w) }
.tcard.busy .tstat::before { background: var(--w); animation: glowPulse 1.8s ease infinite }
@keyframes glowPulse {
  0%,100% { box-shadow:0 0 0 0 rgba(240,112,32,.6); transform:scale(1) }
  50%     { box-shadow:0 0 0 6px rgba(240,112,32,0);transform:scale(1.25) }
}
.tcard .ttot {
  font-family: 'Fira Code', monospace;
  font-size: 13px; color: #e03060;
  margin-top: 14px; padding-top: 13px;
  border-top: 1px solid rgba(220,60,90,.18); letter-spacing: .2px;
}

/* ══════════════════════════════════════
   MENU TAB
   ══════════════════════════════════════ */
#tab-menu { display: none; flex-direction: column; flex: 1; overflow: hidden }
#tab-menu.show { display: flex }
.menu-header {
  font-size: 10px; font-weight: 700; text-align: center; letter-spacing: 2px;
  padding: 10px 12px; color: var(--mu2);
  background: rgba(255,255,255,.40); border-bottom: 1px solid var(--edge-lo);
  flex-shrink: 0; text-transform: uppercase;
}
.cat-bar {
  display: flex; gap: 6px; padding: 10px 12px; overflow-x: auto; flex-shrink: 0;
  border-bottom: 1px solid var(--edge-lo); background: rgba(255,255,255,.35);
}
.cat-bar::-webkit-scrollbar { height: 0 }
.cbtn {
  padding: 6px 16px; font-size: 11px; font-weight: 700;
  border: 1px solid var(--edge-lo); border-radius: var(--r-pill);
  background: rgba(255,255,255,.55); color: var(--mu); cursor: pointer;
  white-space: nowrap; text-transform: uppercase; letter-spacing: .5px; flex-shrink: 0;
  box-shadow: var(--inner-lo), var(--sh0);
  transition: all .25s var(--spring);
}
.cbtn:hover {
  background: rgba(255,255,255,.90); border-color: rgba(47,122,255,.35);
  color: var(--p); transform: translateY(-2px); box-shadow: var(--glow-p), var(--inner-hi);
}
.cbtn.on {
  background: linear-gradient(135deg, rgba(47,122,255,.25), rgba(129,140,248,.20));
  border-color: rgba(47,122,255,.45); color: var(--p);
  box-shadow: var(--glow-p), var(--inner-hi); transform: translateY(-2px);
}
.menu-items { flex: 1; overflow-y: auto; padding: 6px 0 }
.mrow {
  display: flex; align-items: center;
  margin: 3px 8px; padding: 12px 14px;
  background: rgba(255,255,255,.50); cursor: pointer;
  transition: all .22s var(--spring);
  border-radius: var(--r-sm); border: 1px solid transparent;
  box-shadow: var(--inner-lo), var(--sh0);
  position: relative; overflow: hidden;
}
/* Left accent bar */
.mrow::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: linear-gradient(180deg, var(--p), #818cf8);
  transform: scaleY(0); transition: transform .25s var(--spring);
  border-radius: 0 2px 2px 0;
}
/* Shimmer sweep */
.mrow::after {
  content: ''; position: absolute; top: -50%; left: -80%; width: 45%; height: 200%;
  background: linear-gradient(105deg, transparent, rgba(255,255,255,.5), transparent);
  transform: skewX(-15deg); transition: left .5s ease; pointer-events: none;
}
.mrow:hover {
  background: rgba(255,255,255,.88); border-color: rgba(47,122,255,.22);
  transform: translateX(4px); box-shadow: var(--sh1), var(--inner-hi);
}
.mrow:hover::before { transform: scaleY(1) }
.mrow:hover::after  { left: 140% }
.mrow.oos { opacity: .45; cursor: not-allowed; background: rgba(240,48,96,.08) }
.mrow.oos:hover { transform: none; box-shadow: var(--sh0) }
.mrow.oos:hover::before { transform: scaleY(0) }
.mrow.oos:hover::after  { left: -80% }
.mrow.low { background: rgba(240,112,32,.10); border-color: rgba(240,112,32,.22) }
.mrow .mname  { font-size: 13px; font-weight: 600; flex: 1; color: var(--txt); position:relative;z-index:1 }
.mrow .mprice {
  font-family: 'Fira Code', monospace;
  font-size: 12px; color: var(--p); width: 90px; text-align: right;position:relative;z-index:1
}
.mrow .mstock { font-size: 11px; color: var(--mu); width: 78px; text-align: right;position:relative;z-index:1 }

/* ══════════════════════════════════════
   BILL PANEL (RIGHT)
   ══════════════════════════════════════ */
#bill-panel {
  flex: 0 0 37%; min-width: 0;
  background: rgba(255,255,255,.18);
  backdrop-filter: blur(18px) saturate(160%);
  -webkit-backdrop-filter: blur(18px) saturate(160%);
  border: 1px solid var(--edge);
  border-radius: var(--r);
  box-shadow: var(--sh2), var(--inner-lo);
  display: flex; flex-direction: column; overflow: hidden;
}
.bill-title {
  font-family: 'Mona Sans', sans-serif; font-stretch: 110%;
  font-size: 11px; font-weight: 800; color: var(--txt2);
  text-align: center; padding: 13px 12px 12px;
  letter-spacing: 2.5px; text-transform: uppercase;
  background: linear-gradient(135deg, rgba(190,245,220,.75), rgba(120,210,180,.55));
  border-bottom: 1px solid rgba(0,176,116,.25); flex-shrink: 0;
  position: relative; overflow: hidden;
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,.80);
}
.bill-title::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 1.5px;
  background: linear-gradient(90deg,transparent,rgba(0,176,116,.8),rgba(255,255,255,.7),rgba(0,176,116,.8),transparent);
  background-size: 200%; animation: prism 3s linear infinite;
}
/* Scan shimmer */
.bill-title::after {
  content: ''; position: absolute; top: 0; left: -60%; width: 40%; height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.35), transparent);
  animation: scanLine 4s ease-in-out infinite;
}
@keyframes scanLine {
  0%   { left:-40%; opacity:.4 }
  30%  { opacity:.85 }
  80%  { opacity:.7 }
  100% { left:150%; opacity:.3 }
}

.bill-tablename {
  font-family: 'Fira Code', monospace;
  font-size: 12px; color: var(--p); text-align: center;
  padding: 9px 12px 8px;
  background: rgba(200,220,255,.45);
  border-bottom: 1px solid rgba(47,122,255,.20); flex-shrink: 0;
  letter-spacing: .5px;
}
/* Tab bar */
.tab-bar-wrap { background: rgba(255,255,255,.30); flex-shrink: 0 }
.tab-bar {
  display: flex; align-items: flex-end;
  background: rgba(255,255,255,.30);
  padding: 7px 5px 0; gap: 3px; overflow-x: auto; min-height: 44px;
  border-bottom: 1px solid var(--edge-lo);
  position: relative;
}
.tab-bar::-webkit-scrollbar { height: 0 }
.otab {
  display: flex; align-items: center; gap: 3px; padding: 6px 8px;
  border: 1px solid var(--edge-lo); border-bottom: none;
  font-size: 10px; font-weight: 700;
  background: rgba(255,255,255,.42); color: var(--mu);
  cursor: pointer; border-radius: 8px 8px 0 0; white-space: nowrap;
  /* Cố định: 5 tab + ••• (~40px) + + (~34px) + padding(10px) + 6 gap(18px) = ~102px overhead */
  /* Mỗi tab = (100% - 102px) / 5 */
  flex: 0 0 calc((100% - 102px) / 5); min-width: 0;
  transition: all .2s var(--spring); letter-spacing: .3px;
  box-shadow: var(--inner-lo); position: relative;
  justify-content: center;
}
.otab > span {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block;
}
.otab:hover { background: rgba(255,255,255,.75); color: var(--p); border-color: rgba(47,122,255,.25) }
.otab.on {
  background: rgba(220,238,255,.90); color: var(--p);
  border-color: rgba(47,122,255,.25);
  border-bottom-color: rgba(220,238,255,.90);
  margin-bottom: -1px;
  padding-bottom: 7px;
  z-index: 2;
}
.otab.on::before {
  content: ''; position: absolute; top:-1px; left:-1px; right:-1px; height:2.5px;
  background: linear-gradient(90deg, var(--p), #818cf8);
  border-radius: 8px 8px 0 0;
}
.otab .cx {
  background: none; border: none; color: inherit; font-size: 14px;
  padding: 0 0 0 2px; opacity: .35; line-height: 1;
  transition: all .22s var(--spring);
}
.otab .cx:hover { opacity: 1; color: var(--d); transform: rotate(90deg) scale(1.3) }
.tab-plus {
  background: none; border: none; color: var(--mu2); font-size: 20px;
  font-weight: 300; padding: 2px 0; cursor: pointer; flex-shrink: 0; line-height: 1;
  transition: all .25s var(--spring); width: 34px; text-align: center;
}
.tab-plus:hover { color: var(--p); transform: rotate(90deg) scale(1.3) }
.tab-sep { display: none; }

/* Bill content */
.bill-body { flex: 1; overflow-y: auto; background: transparent; padding: 4px 0 }
.bitem {
  display: flex; align-items: center; padding: 12px 14px;
  border-bottom: 1px solid rgba(47,122,255,.08); transition: all .2s ease;
  animation: slideIn .25s var(--spring) both;
}
@keyframes slideIn {
  0%   { opacity:0; transform: translateX(16px) scaleX(.94) scaleY(1.04) }
  40%  { opacity:1; transform: translateX(-2.5px) scaleX(1.02) scaleY(.99) }
  65%  { transform: translateX(1px) scaleX(.999) }
  82%  { transform: translateX(-.3px) }
  100% { transform: translateX(0) scale(1) }
}
.bitem:last-child { border: none }
.bitem:hover { background: rgba(255,255,255,.55) }
.binfo { flex: 1; cursor: pointer; min-width: 0 }
.bname {
  font-size: 13px; font-weight: 600; color: var(--txt);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: .18s;
}
.binfo:hover .bname { color: var(--p) }
.bdetail {
  font-family: 'Fira Code', monospace;
  font-size: 11px; color: var(--mu); margin-top: 3px; letter-spacing: .2px;
}
.bbtns { display: flex; align-items: center; gap: 5px; flex-shrink: 0; margin-left: 8px }
.qbtn {
  width: 30px; height: 30px; border-radius: var(--r-sm);
  font-size: 15px; display: flex; align-items: center; justify-content: center;
  border: 1px solid var(--edge-lo);
  box-shadow: var(--inner-lo), var(--sh0);
  transition: all .25s var(--spring);
}
.qbtn:hover { transform: scale(1.18); box-shadow: var(--sh1), var(--inner-hi) }
.qbtn:active { transform: scale(.88) }
.qp { background: linear-gradient(135deg, rgba(0,176,116,.35), rgba(52,211,153,.28)); color: var(--ad) }
.qm { background: linear-gradient(135deg, rgba(240,48,96,.30), rgba(249,115,22,.25)); color: var(--dd) }

.bill-empty {
  display: flex; flex-direction: column; align-items: center;
  justify-content: center; height: 100%; padding: 40px 20px; text-align: center;
}
.bill-empty .eico {
  font-size: 48px; display: block; margin-bottom: 16px;
  animation: floatSway 5s ease-in-out infinite;
  filter: drop-shadow(0 4px 14px rgba(47,122,255,.20));
}
@keyframes floatSway {
  0%   { transform: translateY(0px) scaleX(1) scaleY(1) rotate(0deg) }
  22%  { transform: translateY(-7px) scaleX(.98) scaleY(1.02) rotate(-2deg) }
  45%  { transform: translateY(-3px) scaleX(1.01) scaleY(.99) rotate(1.5deg) }
  68%  { transform: translateY(-9px) scaleX(.99) scaleY(1.01) rotate(-1deg) }
  100% { transform: translateY(0px) scaleX(1) scaleY(1) rotate(0deg) }
}
.bill-empty p { font-size: 13px; font-weight: 500; color: var(--mu2) }

/* Bill footer */
.bill-total {
  font-family: 'Fira Code', monospace;
  font-size: 16px; color: var(--a); text-align: center;
  padding: 13px 12px 12px;
  background: linear-gradient(135deg, rgba(190,245,220,.55), rgba(150,230,200,.40));
  border-top: 1px solid rgba(0,176,116,.22); letter-spacing: .5px; flex-shrink: 0;
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,.70);
}
.bill-btns { display: flex; gap: 8px; padding: 11px 12px 13px; flex-shrink: 0 }
.btn-split {
  flex: 0 0 auto; padding: 11px 14px;
  background: rgba(255,255,255,.55); color: var(--mu);
  border: 1px solid var(--edge-lo); border-radius: var(--r-sm);
  font-size: 11px; font-weight: 700; white-space: nowrap; letter-spacing: .4px;
  box-shadow: var(--inner-lo), var(--sh0); transition: all .25s var(--spring);
}
.btn-split:hover {
  background: rgba(255,255,255,.90); border-color: rgba(47,122,255,.30); color: var(--p);
  transform: translateY(-2px); box-shadow: var(--glow-p), var(--inner-hi);
}
.btn-checkout {
  flex: 1; padding: 14px; border: none; border-radius: 12px;
  font-family: 'Mona Sans', sans-serif; font-stretch: 110%;
  font-size: 12px; font-weight: 800; letter-spacing: 1px; text-transform: uppercase;
  color: rgba(0,90,55,1);
  background: linear-gradient(145deg,
    rgba(100,240,180,.55) 0%, rgba(0,176,116,.45) 40%,
    rgba(52,211,153,.38) 80%, rgba(100,240,180,.50) 100%);
  border: 1px solid rgba(0,200,130,.50);
  box-shadow: var(--glow-a),
    inset 0 1.5px 0 rgba(150,255,200,.60),
    inset 0 -1px 0 rgba(0,130,85,.20);
  transition: border-radius .5s var(--spring), transform .28s var(--spring),
              box-shadow .28s var(--spring), background .28s ease;
  position: relative; overflow: hidden;
  -webkit-tap-highlight-color: transparent;
}
/* Liquid surface layer */
.btn-checkout::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 45%;
  background: linear-gradient(180deg, rgba(200,255,220,.50) 0%, transparent 100%);
  border-radius: inherit; pointer-events: none; transition: opacity .28s;
}
/* Shimmer sweep */
.btn-checkout::after {
  content: ''; position: absolute; top: 0; left: -100%; width: 60%; height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.45), transparent);
  transition: left .65s ease;
}
.btn-checkout:hover {
  background: linear-gradient(145deg,
    rgba(130,250,200,.65) 0%, rgba(0,200,130,.58) 40%,
    rgba(80,225,165,.50) 100%);
  transform: translateY(-3px) scale(1.01);
  border-radius: 14px 10px 13px 11px / 11px 13px 10px 14px;
  box-shadow: 0 14px 44px rgba(0,176,116,.48),
    inset 0 1.5px 0 rgba(200,255,225,.75);
}
.btn-checkout:hover::after { left: 160% }
.btn-checkout:active {
  transform: scale(.97) !important;
  filter: brightness(.92) !important;
  border-radius: 10px 14px 11px 13px / 13px 10px 14px 11px !important;
}

/* ══════════════════════════════════════
   LIQUID GLASS MODAL — Caustic Water
   ══════════════════════════════════════ */
@keyframes causticsShift {
  0%   { background-position: 0% 0%, 100% 100%, 0% 100% }
  33%  { background-position: 40% 20%, 60% 80%, 20% 40%  }
  66%  { background-position: 80% 50%, 20% 30%, 60% 80%  }
  100% { background-position: 0% 0%, 100% 100%, 0% 100%  }
}
@keyframes liquidBorder {
  0%   { border-radius: 22px 22px 22px 22px }
  20%  { border-radius: 24px 20px 23px 21px }
  40%  { border-radius: 20px 24px 21px 23px }
  60%  { border-radius: 23px 21px 24px 20px }
  80%  { border-radius: 21px 23px 20px 24px }
  100% { border-radius: 22px 22px 22px 22px }
}
@keyframes glassRise {
  0%   { background-position: 0% 0%, 100% 0% }
  100% { background-position: 100% 100%, 0% 100% }
}

.mbg {
  position: fixed; inset: 0;
  background: rgba(200,215,245,.04);
  backdrop-filter: blur(8px) saturate(130%) brightness(1.01);
  -webkit-backdrop-filter: blur(8px) saturate(130%) brightness(1.01);
  z-index: 1000; display: flex; align-items: center; justify-content: center;
  animation: bgFade .22s ease;
}
/* Water caustic light on overlay */
.mbg::before {
  content: '';
  position: absolute; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(ellipse 30% 20% at 25% 35%, rgba(120,180,255,.14) 0%, transparent 70%),
    radial-gradient(ellipse 25% 18% at 72% 68%, rgba(100,220,170,.10) 0%, transparent 65%),
    radial-gradient(ellipse 20% 15% at 55% 18%, rgba(180,140,255,.12) 0%, transparent 60%);
  animation: causticsShift 8s ease-in-out infinite;
  filter: blur(30px);
}
@keyframes bgFade {
  0%   { opacity:0; transform: scale(.99) }
  60%  { opacity:.85 }
  100% { opacity:1; transform: scale(1) }
}

.mcard {
  background: linear-gradient(145deg,
    rgba(250,253,255,.92) 0%,
    rgba(235,246,255,.88) 40%,
    rgba(245,250,255,.90) 100%);
  backdrop-filter: blur(60px) saturate(240%) brightness(1.08);
  -webkit-backdrop-filter: blur(60px) saturate(240%) brightness(1.08);
  border-radius: 22px; width: 92%; max-width: 500px; max-height: 92vh;
  overflow-y: auto; border: 1px solid var(--edge);
  box-shadow: var(--sh3), var(--inner-hi),
    0 0 0 .5px rgba(255,255,255,.60),
    inset 0 0 80px rgba(200,225,255,.10);
  animation: modalRise .32s var(--spring);
  position: relative; z-index: 1;
}
/* Caustic shimmer inside modal */
.mcard::before {
  content: '';
  position: absolute; inset: 0; pointer-events: none; z-index: 0;
  border-radius: inherit;
  background:
    radial-gradient(ellipse 60% 40% at 80% 10%, rgba(180,210,255,.20) 0%, transparent 60%),
    radial-gradient(ellipse 40% 30% at 10% 85%, rgba(150,240,200,.15) 0%, transparent 55%);
  animation: causticsShift 12s ease-in-out infinite reverse;
}
/* Liquid prismatic top edge */
.mcard::after {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg,
    transparent, #a5c8ff 10%, #c4b5fd 28%,
    #a78bfa 42%, #67e8f9 56%, #6ee7b7 72%,
    #a5c8ff 86%, transparent);
  background-size: 300% 100%;
  border-radius: 22px 22px 0 0;
  animation: prism 4s linear infinite;
  z-index: 2;
}
@keyframes modalRise {
  0%   { opacity:0; transform: translateY(-28px) scale(.93) scaleX(1.04) scaleY(.96) }
  35%  { opacity:1; transform: translateY(5px) scale(1.01) scaleX(.97) scaleY(1.03) }
  58%  { transform: translateY(-2px) scale(.999) scaleX(1.005) }
  75%  { transform: translateY(1.5px) scale(1.001) }
  88%  { transform: translateY(-.5px) }
  100% { transform: translateY(0) scale(1) }
}
.mhdr {
  padding: 20px 22px 18px; display: flex; align-items: center;
  justify-content: space-between; border-bottom: 1px solid var(--edge-lo);
  background: linear-gradient(135deg,
    rgba(190,215,255,.65) 0%,
    rgba(220,240,255,.55) 50%,
    rgba(200,230,255,.60) 100%);
  border-radius: var(--r-lg) var(--r-lg) 0 0;
  position: sticky; top: 0; z-index: 3;
  box-shadow: inset 0 1.5px 0 var(--spec),
    inset 0 -1px 0 rgba(180,210,255,.25);
  backdrop-filter: blur(20px);
}
.mttl {
  font-family: 'Mona Sans', sans-serif; font-stretch: 110%;
  font-size: 15px; font-weight: 800; color: var(--txt); letter-spacing: -.3px;
}
.mclose {
  background: rgba(255,255,255,.65); border: 1px solid var(--edge-lo);
  font-size: 16px; color: var(--mu);
  width: 32px; height: 32px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  transition: all .28s var(--spring);
  box-shadow: var(--inner-lo), 0 2px 8px rgba(30,60,160,.08);
}
.mclose:hover {
  background: rgba(240,48,96,.20); border-color: rgba(240,48,96,.40);
  color: var(--d); transform: rotate(90deg) scale(1.2);
  box-shadow: var(--glow-d), var(--inner-hi);
  border-radius: 40% 60% 55% 45% / 45% 40% 60% 55%;
}
.mbody  { padding: 22px; position: relative; z-index: 1 }
.mftr {
  padding: 16px 22px 20px; display: flex; gap: 10px; justify-content: flex-end;
  border-top: 1px solid var(--edge-lo);
  background: linear-gradient(90deg,
    rgba(230,242,255,.60) 0%, rgba(210,232,255,.72) 100%);
  border-radius: 0 0 var(--r-lg) var(--r-lg);
  position: sticky; bottom: 0; z-index: 3;
  backdrop-filter: blur(20px);
  box-shadow: inset 0 1px 0 rgba(200,220,255,.35);
}
@keyframes fi  { from{opacity:0} to{opacity:1} }
@keyframes su  { from{opacity:0;transform:translateY(-16px) scale(.96)} to{opacity:1;transform:none} }

/* ══════════════════════════════════════
   LIQUID GLASS BUTTONS — Water Droplet
   ══════════════════════════════════════ */
@keyframes dropletMorph {
  0%,100% { border-radius: 10px }
  50%     { border-radius: 14px }
}
@keyframes dropletHover {
  0%,100% { border-radius: 10px; transform: translateY(0) }
  30%     { border-radius: 12px; transform: translateY(-3px) }
  60%     { border-radius: 10px; transform: translateY(-2px) }
}
@keyframes liquidRipple {
  0%   { transform: scale(0) scaleX(1.08); opacity: .55 }
  22%  { transform: scale(1.1) scaleX(.98); opacity: .38 }
  52%  { transform: scale(2.3) scaleX(1.01); opacity: .18 }
  80%  { transform: scale(3.5) scaleX(1.00); opacity: .05 }
  100% { transform: scale(4.2); opacity: 0 }
}
@keyframes surfaceTension {
  0%   { transform: scaleX(1)    scaleY(1)   }
  18%  { transform: scaleX(1.07) scaleY(.93) }
  38%  { transform: scaleX(.95) scaleY(1.07) }
  58%  { transform: scaleX(1.03) scaleY(.97) }
  78%  { transform: scaleX(.98) scaleY(1.02) }
  100% { transform: scaleX(1)    scaleY(1)   }
}

.btn {
  padding: 11px 20px;
  border-radius: 10px;
  font-size: 12px; font-weight: 700; cursor: pointer;
  letter-spacing: .4px;
  position: relative; overflow: hidden;
  transition: border-radius .4s var(--spring), transform .25s var(--spring),
              box-shadow .25s var(--spring), background .25s ease,
              filter .25s ease;
  -webkit-tap-highlight-color: transparent;
}
/* Liquid surface highlight streak */
.btn::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 40%;
  background: linear-gradient(180deg, rgba(255,255,255,.40) 0%, transparent 100%);
  border-radius: inherit; pointer-events: none;
  transition: opacity .25s ease;
}
/* Shimmer sweep */
.btn::after {
  content: ''; position: absolute; top: -50%; left: -70%;
  width: 45%; height: 200%;
  background: linear-gradient(105deg, transparent, rgba(255,255,255,.50), transparent);
  transform: skewX(-15deg); transition: left .55s ease; pointer-events: none;
}
.btn:hover { transform: translateY(-2px) }
.btn:hover::after { left: 160% }
.btn:hover::before { opacity: .7 }
.btn:active {
  animation: surfaceTension .35s var(--spring) forwards !important;
  filter: brightness(.90) !important;
  box-shadow: none !important;
}

/* Ripple container (injected by JS) */
.btn-ripple {
  position: absolute; border-radius: 50%; pointer-events: none;
  transform: scale(0); background: rgba(255,255,255,.55);
  animation: liquidRipple .6s ease-out forwards;
}

.btn-p {
  background: linear-gradient(145deg,
    rgba(120,170,255,.35) 0%, rgba(47,122,255,.28) 40%,
    rgba(129,140,248,.22) 100%);
  color: var(--p); border: 1px solid rgba(100,150,255,.45);
  box-shadow: var(--glow-p), var(--inner-hi),
    inset 0 -1px 0 rgba(47,122,255,.20);
}
.btn-p:hover {
  background: linear-gradient(145deg,
    rgba(160,200,255,.50) 0%, rgba(47,122,255,.42) 40%,
    rgba(129,140,248,.35) 100%);
  transform: translateY(-3px) scale(1.02);
  box-shadow: 0 14px 40px rgba(47,122,255,.45), var(--inner-hi),
    inset 0 -1px 0 rgba(47,122,255,.30);
}

.btn-a {
  background: linear-gradient(145deg,
    rgba(100,230,170,.38) 0%, rgba(0,176,116,.28) 40%,
    rgba(52,211,153,.22) 100%);
  color: var(--a); border: 1px solid rgba(0,200,130,.42);
  box-shadow: var(--glow-a), var(--inner-hi),
    inset 0 -1px 0 rgba(0,176,116,.20);
}
.btn-a:hover {
  background: linear-gradient(145deg,
    rgba(130,245,190,.52) 0%, rgba(0,176,116,.44) 40%,
    rgba(52,211,153,.36) 100%);
  transform: translateY(-3px) scale(1.02);
  box-shadow: 0 14px 40px rgba(0,176,116,.45), var(--inner-hi),
    inset 0 -1px 0 rgba(0,176,116,.30);
}

.btn-d {
  background: linear-gradient(145deg,
    rgba(255,120,140,.35) 0%, rgba(240,48,96,.28) 40%,
    rgba(249,115,22,.20) 100%);
  color: var(--d); border: 1px solid rgba(240,80,110,.42);
  box-shadow: var(--glow-d), var(--inner-hi),
    inset 0 -1px 0 rgba(240,48,96,.20);
}
.btn-d:hover {
  background: linear-gradient(145deg,
    rgba(255,150,165,.50) 0%, rgba(240,48,96,.44) 40%,
    rgba(249,115,22,.32) 100%);
  transform: translateY(-3px) scale(1.02);
  box-shadow: 0 14px 40px rgba(240,48,96,.45), var(--inner-hi),
    inset 0 -1px 0 rgba(240,48,96,.30);
}

.btn-g {
  background: linear-gradient(145deg,
    rgba(255,255,255,.70) 0%, rgba(240,248,255,.55) 50%,
    rgba(220,235,255,.45) 100%);
  color: var(--mu); border: 1px solid var(--edge-lo);
  box-shadow: var(--inner-lo), var(--sh0);
}
.btn-g:hover {
  background: linear-gradient(145deg,
    rgba(255,255,255,.92) 0%, rgba(230,242,255,.80) 50%,
    rgba(210,228,255,.65) 100%);
  color: var(--p); border-color: rgba(47,122,255,.30);
  transform: translateY(-3px) scale(1.02);
  box-shadow: var(--sh1), var(--inner-hi);
}
.btn-sm { padding: 9px 16px; font-size: 11px }

/* ── LIQUID GLASS FORM ELEMENTS ── */
.fg { margin-bottom: 18px }
.fl {
  display: block; font-size: 10px; font-weight: 800; color: var(--mu);
  text-transform: uppercase; letter-spacing: 1.2px; margin-bottom: 8px;
  transition: .18s ease;
}
.fg:focus-within .fl { color: var(--p) }
.fi {
  width: 100%; padding: 12px 14px;
  border: 1px solid rgba(200,220,255,.55); border-radius: var(--r-sm);
  font-size: 13px; outline: none;
  background: linear-gradient(145deg,
    rgba(250,253,255,.75) 0%, rgba(235,246,255,.65) 100%);
  backdrop-filter: blur(20px) saturate(180%);
  color: var(--txt);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.80),
    inset 0 -1px 0 rgba(180,210,255,.15),
    0 2px 8px rgba(30,60,160,.06);
  transition: all .24s ease;
}
.fi:focus {
  border-color: rgba(47,122,255,.55);
  box-shadow: 0 0 0 4px rgba(47,122,255,.14),
    inset 0 1px 0 rgba(255,255,255,.90),
    inset 0 -1px 0 rgba(47,122,255,.20);
  background: linear-gradient(145deg, rgba(255,255,255,.92) 0%, rgba(240,248,255,.85) 100%);
  border-radius: 11px 9px 10px 10px / 10px 11px 9px 10px;
}
.fi:hover:not(:focus) {
  background: linear-gradient(145deg, rgba(255,255,255,.88) 0%, rgba(240,248,255,.78) 100%);
  border-color: rgba(180,210,255,.70);
}
.fi option { background: #f0f5ff; color: var(--txt) }
.ferr { font-size: 12px; color: var(--d); margin-top: 5px; font-weight: 600 }

/* ══════════════════════════════════════
   LOGIN PAGE
   ══════════════════════════════════════ */
#login-page {
  position: fixed; inset: 0;
  display: flex; align-items: center; justify-content: center;
  z-index: 9999; overflow: hidden;
  background: linear-gradient(150deg, #c8dcff 0%, #d8f0e8 35%, #e8d8ff 65%, #ffd8ec 100%);
}
/* Bright animated aurora */
#login-page::before {
  content: ''; position: absolute; inset: 0;
  background:
    radial-gradient(ellipse 65% 55% at 25% 35%, rgba(120,170,255,.55) 0%, transparent 60%),
    radial-gradient(ellipse 55% 45% at 75% 65%, rgba(100,220,170,.50) 0%, transparent 55%),
    radial-gradient(ellipse 45% 38% at 55% 20%, rgba(190,150,255,.45) 0%, transparent 52%),
    radial-gradient(ellipse 40% 35% at 20% 80%, rgba(255,170,210,.42) 0%, transparent 50%);
  filter: blur(45px);
  animation: loginAurora 14s ease-in-out infinite alternate;
}
@keyframes loginAurora {
  0%   { transform: scale(1.00) scaleX(1)    rotate(0deg)   }
  25%  { transform: scale(1.02) scaleX(.98)  rotate(.7deg)  }
  50%  { transform: scale(1.05) scaleX(1.01) rotate(1.2deg) }
  75%  { transform: scale(1.02) scaleX(.99)  rotate(.4deg)  }
  100% { transform: scale(.97)  scaleX(1.00) rotate(-.8deg) }
}
/* Floating orb */
#login-page::after {
  content: ''; position: absolute;
  width: 560px; height: 560px; border-radius: 50%;
  background: radial-gradient(circle at 40% 40%,
    rgba(47,122,255,.18), rgba(129,140,248,.12), transparent 70%);
  top: -180px; right: -180px;
  animation: orbFloat 12s ease-in-out infinite alternate;
}
@keyframes orbFloat {
  0%   { transform: translate(0px, 0px)    scale(1.00) scaleX(1)    scaleY(1)    }
  30%  { transform: translate(-10px,18px)  scale(1.04) scaleX(.97)  scaleY(1.03) }
  65%  { transform: translate(-25px,36px)  scale(1.07) scaleX(1.02) scaleY(.98)  }
  100% { transform: translate(-35px,45px)  scale(1.08) scaleX(.99)  scaleY(1.01) }
}
.login-card {
  background: rgba(255,255,255,.72);
  backdrop-filter: blur(56px) saturate(220%) brightness(1.06);
  -webkit-backdrop-filter: blur(56px) saturate(220%) brightness(1.06);
  border-radius: var(--r-xl); padding: 48px 38px; width: 430px;
  border: 1px solid var(--edge);
  box-shadow: var(--sh3), var(--inner-hi), 0 0 80px rgba(47,122,255,.16);
  animation: loginCardIn .6s var(--spring);
  position: relative; z-index: 2; overflow: hidden;
}
@keyframes loginCardIn {
  0%   { opacity:0; transform: scale(.86) scaleX(1.12) scaleY(.90) translateY(28px) }
  28%  { opacity:1; transform: scale(1.03) scaleX(.96) scaleY(1.05) translateY(-4px) }
  50%  { transform: scale(.997) scaleX(1.01) scaleY(1.01) translateY(2.5px) }
  68%  { transform: scale(1.002) scaleY(.999) translateY(-1px) }
  84%  { transform: scale(.9995) translateY(.5px) }
  100% { transform: scale(1) translateY(0) }
}
.login-card::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg,
    transparent, #60a5fa 15%, #818cf8 35%,
    #a78bfa 50%, #34d399 68%, #60a5fa 85%, transparent);
  background-size: 200%; animation: prism 4s linear infinite;
}
/* Gloss shine */
.login-card::after {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 42%;
  background: linear-gradient(180deg, rgba(255,255,255,.25), transparent);
  pointer-events: none;
}
.login-logo { text-align: center; margin-bottom: 32px; position: relative; z-index: 1 }
.login-logo span {
  font-size: 52px; display: block;
  filter: drop-shadow(0 0 22px rgba(47,122,255,.35)) drop-shadow(0 4px 10px rgba(30,60,160,.18));
  animation: floatSway 5s ease-in-out infinite;
}
.login-logo h1 {
  font-family: 'Mona Sans', sans-serif; font-stretch: 115%;
  font-size: 24px; font-weight: 900; margin-top: 14px;
  letter-spacing: -.7px; color: var(--txt);
}
.login-logo p { font-size: 12px; color: var(--mu); margin-top: 6px; letter-spacing: .5px }
.ltabs {
  display: flex;
  background: rgba(47,122,255,.07); border-radius: var(--r-sm);
  padding: 4px; margin-bottom: 24px;
  border: 1px solid var(--edge-lo); gap: 4px;
  box-shadow: var(--inner-lo);
  position: relative; z-index: 1;
}
.ltab {
  flex: 1; padding: 10px; border: none; background: none; border-radius: var(--r-xs);
  font-size: 12px; font-weight: 700; color: var(--mu); cursor: pointer;
  transition: all .28s var(--spring); letter-spacing: .4px;
}
.ltab.on {
  background: linear-gradient(135deg, rgba(47,122,255,.25), rgba(129,140,248,.18));
  color: var(--p);
  box-shadow: var(--glow-p), var(--inner-hi);
  transform: scale(1.03);
  border: 1px solid rgba(47,122,255,.35);
}

/* ══════════════════════════════════════
   HISTORY PAGE
   ══════════════════════════════════════ */
#hist-page  { display:none; flex:1; flex-direction:column; overflow:hidden }
#hist-page.show { display:flex }
#pos-page   { display:flex; flex:1; overflow:hidden }
#pos-page.hide  { display:none }

.hist-bar {
  padding: 10px 14px; border-bottom: 1px solid var(--edge-lo);
  display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap;
  background: rgba(255,255,255,.55);
  backdrop-filter: blur(28px) saturate(180%);
  -webkit-backdrop-filter: blur(28px) saturate(180%);
  flex-shrink: 0;
  box-shadow: 0 2px 16px rgba(30,80,200,.08), var(--inner-lo);
}
.hfilt-group { display:flex; flex-direction:column; gap:4px }
.hfilt-label {
  font-size: 9px; font-weight: 800; color: var(--mu2);
  text-transform: uppercase; letter-spacing: 1.2px; padding-left: 4px;
}

/* ── Nhóm tabs (Ngày/Tuần/Tháng) ── */
.hfilt-tabs {
  display: flex; border-radius: var(--r-sm); overflow: hidden;
  border: 1px solid rgba(255,255,255,0.65);
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,0.85), 0 2px 8px rgba(30,80,200,.08);
  backdrop-filter: blur(var(--water-blur,18px));
  -webkit-backdrop-filter: blur(var(--water-blur,18px));
  background: rgba(200,225,255, calc(var(--water-fill,0.06)*1.2));
}
.hftab {
  padding: 7px 14px; font-size: 11px; font-weight: 700; border: none;
  background: transparent; color: var(--mu); cursor: pointer;
  transition: all .2s var(--spring); letter-spacing: .4px; white-space: nowrap;
  border-right: 1px solid rgba(255,255,255,0.45); position: relative;
}
.hftab:last-child { border-right: none }
.hftab:hover:not(.on) {
  background: rgba(255,255,255,0.35); color: var(--p);
}
.hftab.on {
  background: linear-gradient(135deg, rgba(47,122,255,.25), rgba(129,140,248,.20));
  color: var(--p);
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,0.90), inset 0 0 20px rgba(47,122,255,.08);
}

/* ── Quick buttons ── */
.hfilt-quick {
  display: flex; border-radius: var(--r-sm); overflow: hidden;
  border: 1px solid rgba(255,255,255,0.65);
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,0.85), 0 2px 8px rgba(30,80,200,.08);
  backdrop-filter: blur(var(--water-blur,18px));
  -webkit-backdrop-filter: blur(var(--water-blur,18px));
  background: rgba(200,225,255, calc(var(--water-fill,0.06)*1.2));
}
.hqbtn {
  padding: 7px 11px; font-size: 11px; font-weight: 700; border: none;
  background: transparent; color: var(--mu); cursor: pointer;
  transition: all .2s var(--spring); letter-spacing: .3px; white-space: nowrap;
  border-right: 1px solid rgba(255,255,255,0.45);
}
.hqbtn:last-child { border-right: none }
.hqbtn:hover { background: rgba(255,255,255,0.35); color: var(--p) }
.hqbtn-all { color: var(--a); font-weight: 800 }
.hqbtn-all:hover { background: rgba(0,176,116,.15) !important; color: var(--a) !important }

/* ── Search + Date + Select inputs ── */
.hfilt-search,
.hist-bar select,
.hist-bar input[type=date] {
  padding: 7px 12px; height: 34px; align-self: flex-end;
  border: 1px solid rgba(255,255,255,0.65);
  border-radius: var(--r-sm);
  font-size: 12px; font-weight: 500; outline: none;
  background: rgba(200,225,255, calc(var(--water-fill,0.06)*1.4));
  backdrop-filter: blur(var(--water-blur,18px));
  -webkit-backdrop-filter: blur(var(--water-blur,18px));
  color: var(--txt);
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,0.85), 0 2px 8px rgba(30,80,200,.06);
  transition: all .22s ease;
}
.hfilt-search { min-width: 190px; }
.hfilt-search:focus,
.hist-bar select:focus,
.hist-bar input[type=date]:focus {
  border-color: rgba(47,122,255,.45);
  background: rgba(220,238,255, calc(var(--water-fill,0.06)*1.8));
  box-shadow: 0 0 0 3px rgba(47,122,255,.12),
              inset 0 1.5px 0 rgba(255,255,255,0.90);
}
.hfilt-search::placeholder { color: var(--mu2) }
.hist-bar select option { background: #e8f2ff }

/* ── Revenue badge ── */
.hrev-badge { display:flex; align-items:center; gap:8px; flex-shrink:0 }
.hrev-num { font-family:'Fira Code',monospace; font-size:14px; color:var(--a); font-weight:600 }
.hrev-ct {
  font-size: 11px; font-weight: 600; color: var(--mu);
  background: rgba(200,225,255, calc(var(--water-fill,0.06)*1.5));
  backdrop-filter: blur(var(--water-blur,18px));
  -webkit-backdrop-filter: blur(var(--water-blur,18px));
  padding: 3px 10px; border-radius: var(--r-sm);
  border: 1px solid rgba(255,255,255,0.65);
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,0.85);
}
.hrev-none { font-size:12px; color:var(--mu2); font-style:italic }

/* ── Custom liquid glass select ── */
.hsel-wrap { position: relative; align-self: flex-end; }
.hsel-btn {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 12px; height: 34px; min-width: 110px;
  border: 1px solid rgba(255,255,255,0.65);
  border-radius: var(--r-sm);
  font-size: 12px; font-weight: 600; cursor: pointer;
  background: rgba(200,225,255, calc(var(--water-fill,0.06)*1.4));
  backdrop-filter: blur(var(--water-blur,18px));
  -webkit-backdrop-filter: blur(var(--water-blur,18px));
  color: var(--txt);
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,0.85), 0 2px 8px rgba(30,80,200,.06);
  transition: all .2s var(--spring);
  white-space: nowrap;
}
.hsel-btn:hover {
  background: rgba(215,235,255, calc(var(--water-fill,0.06)*2));
  border-color: rgba(47,122,255,.35);
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,0.90), 0 0 0 3px rgba(47,122,255,.08);
}
.hsel-btn span:first-child { flex: 1; text-align: left; }
.hsel-arrow {
  font-size: 10px; color: var(--mu2);
  transition: transform .2s var(--spring);
}
.hsel-wrap.open .hsel-arrow { transform: rotate(180deg); }
.hsel-drop {
  position: absolute; z-index: 9999;
  background: rgba(200,225,255, calc(var(--water-fill, 0.06) * 0.6));
  backdrop-filter: blur(var(--water-blur, 18px)) saturate(180%);
  -webkit-backdrop-filter: blur(var(--water-blur, 18px)) saturate(180%);
  border: 1px solid rgba(255,255,255,.75);
  border-radius: var(--r-sm);
  box-shadow: 0 12px 40px rgba(20,60,180,.18), inset 0 1.5px 0 rgba(255,255,255,.85);
  overflow: hidden; min-width: 130px;
  animation: hselIn .18s var(--spring);
  display: none;
}
.hsel-drop.pop-open { display: block; }
@keyframes hselIn {
  from { opacity:0; }
  to   { opacity:1; }
}
.hsel-opt {
  padding: 9px 14px; font-size: 12px; font-weight: 600;
  color: var(--mu); cursor: pointer; white-space: nowrap;
  transition: all .15s ease;
  border-bottom: 1px solid rgba(200,225,255,.30);
}
.hsel-opt:last-child { border-bottom: none; }
.hsel-opt:hover {
  background: rgba(47,122,255,.12); color: var(--p);
}
.hsel-opt.on {
  background: linear-gradient(90deg, rgba(47,122,255,.15), rgba(129,140,248,.10));
  color: var(--p); font-weight: 700;
}

/* ── Liquid Glass Date Picker ── */
.hdp-btn { min-width: 130px; justify-content: space-between; }
.hdp-wrap { position: relative; align-self: flex-end; }
.hdp-drop {
  position: absolute; z-index: 9999;
  width: 280px;
  background: rgba(200,225,255, calc(var(--water-fill, 0.06) * 0.6));
  backdrop-filter: blur(var(--water-blur, 18px)) saturate(180%);
  -webkit-backdrop-filter: blur(var(--water-blur, 18px)) saturate(180%);
  border: 1px solid rgba(255,255,255,.80);
  border-radius: 16px;
  box-shadow: 0 16px 50px rgba(20,60,180,.18), inset 0 1.5px 0 rgba(255,255,255,.90);
  padding: 14px;
  overflow: hidden;
  display: none;
  animation: hselIn .18s var(--spring);
}
.hdp-drop.pop-open { display: block; }
.hdp-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 12px;
}
.hdp-month-label {
  font-size: 13px; font-weight: 800; color: var(--txt2); cursor: pointer;
  padding: 4px 8px; border-radius: var(--r-sm);
  transition: background .15s;
}
.hdp-month-label:hover { background: rgba(47,122,255,.10); color: var(--p); }
.hdp-nav {
  display: flex; gap: 4px;
}
.hdp-nav button {
  width: 28px; height: 28px; border: 1px solid rgba(255,255,255,.65);
  border-radius: var(--r-sm); font-size: 14px; cursor: pointer;
  background: rgba(200,225,255,.40);
  backdrop-filter: blur(10px);
  color: var(--mu); transition: all .15s;
  display: flex; align-items: center; justify-content: center;
}
.hdp-nav button:hover { background: rgba(47,122,255,.15); color: var(--p); }
.hdp-dow {
  display: grid; grid-template-columns: repeat(7, 1fr);
  gap: 2px; margin-bottom: 4px;
}
.hdp-dow span {
  text-align: center; font-size: 10px; font-weight: 800;
  color: var(--mu2); letter-spacing: .5px; padding: 3px 0;
}
.hdp-days {
  display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px;
}
.hdp-day {
  aspect-ratio: 1; display: flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 600; border-radius: 8px; cursor: pointer;
  color: var(--txt2); border: 1px solid transparent;
  transition: all .15s var(--spring);
  user-select: none; -webkit-user-select: none;
}
.hdp-day:hover:not(.hdp-empty):not(.hdp-today):not(.hdp-sel):not(.hdp-other) {
  background: rgba(47,122,255,.12); color: var(--p);
  border-color: rgba(47,122,255,.20);
}
.hdp-day.hdp-other { color: var(--mu2); opacity: .5; cursor: default; pointer-events: none; }
.hdp-day.hdp-today {
  background: rgba(47,122,255,.12); color: var(--p);
  border-color: rgba(47,122,255,.35); font-weight: 800;
}
.hdp-day.hdp-sel {
  background: linear-gradient(135deg, rgba(47,122,255,.90), rgba(99,102,241,.85));
  color: #fff; border-color: transparent;
  box-shadow: 0 4px 12px rgba(47,122,255,.35), inset 0 1px 0 rgba(255,255,255,.35);
}
.hdp-foot {
  display: flex; justify-content: space-between; align-items: center;
  margin-top: 10px; padding-top: 10px;
  border-top: 1px solid rgba(200,225,255,.50);
}
.hdp-clear {
  font-size: 11px; font-weight: 700; color: var(--d); cursor: pointer;
  background: none; border: none; padding: 4px 6px;
  border-radius: var(--r-sm); transition: all .15s;
}
.hdp-clear:hover { background: rgba(240,80,110,.10); }
.hdp-today-btn {
  font-size: 11px; font-weight: 700; color: var(--p); cursor: pointer;
  background: none; border: none; padding: 4px 6px;
  border-radius: var(--r-sm); transition: all .15s;
}
.hdp-today-btn:hover { background: rgba(47,122,255,.10); }

.hist-body { display:flex; flex:1; overflow:hidden }
.hist-left { flex:1; overflow-y:auto; padding:14px; min-width:0 }
.hist-right {
  width: 280px; border-left: 1px solid var(--edge-lo); overflow-y: auto;
  padding: 14px; background: rgba(255,255,255,.38);
  backdrop-filter: blur(20px);
}

/* Summary bar */
.ol2-summary { margin-bottom: 10px }
.ol2-sum-txt {
  font-size: 12px; color: var(--mu); padding: 8px 14px;
  background: rgba(200,220,255,.45); border-radius: var(--r-sm);
  border-left: 3px solid rgba(47,122,255,.50); line-height: 1.6;
  border: 1px solid rgba(47,122,255,.20); border-left: 3px solid rgba(47,122,255,.50);
  box-shadow: var(--inner-lo);
}
.ol2-sum-txt b { color: var(--txt2) }
.ol2-sum-empty {
  background: rgba(255,255,255,.40); border-color: var(--edge-lo);
  border-left: 3px solid var(--edge-lo); color: var(--mu2); font-style: italic;
}

/* Group headers */
.holi-grp-hdr {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px 7px; margin-top: 8px;
  border-bottom: 1px solid var(--edge-lo); margin-bottom: 4px;
  position: sticky; top: 0; z-index: 2;
  background: rgba(225,238,255, 0.96);
  backdrop-filter: none;
}
.holi-grp-label {
  font-family: 'Mona Sans', sans-serif; font-stretch: 110%;
  font-size: 12px; font-weight: 800; color: var(--p);
}
.holi-grp-meta {
  font-family: 'Fira Code', monospace;
  font-size: 11px; color: var(--mu);
  background: rgba(255,255,255,.65); padding: 3px 10px; border-radius: 99px;
  border: 1px solid var(--edge-lo); box-shadow: var(--inner-lo);
}

/* KPI Cards */
.scs { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:16px }
.sc {
  background: rgba(255,255,255,.58);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border: 1px solid var(--edge); border-radius: var(--r);
  padding: 18px 16px 15px; position: relative; overflow: hidden;
  box-shadow: var(--sh1), var(--inner-lo);
  transition: all .28s var(--spring);
}
.sc:hover { transform:translateY(-4px) scale(1.02); box-shadow:var(--sh2), var(--inner-hi) }
.sc::before { content:''; position:absolute; top:0; left:0; right:0; height:2.5px }
.sc::after {
  content:''; position:absolute; right:-30px; bottom:-30px;
  width:100px; height:100px; border-radius:50%; opacity:.12; filter:blur(18px);
}
.sc.sc-rev::before { background:linear-gradient(90deg,var(--p),#818cf8) }
.sc.sc-rev::after  { background:radial-gradient(circle,var(--p),transparent) }
.sc.sc-ord::before { background:linear-gradient(90deg,var(--a),#34d399) }
.sc.sc-ord::after  { background:radial-gradient(circle,var(--a),transparent) }
.sc.sc-avg::before { background:linear-gradient(90deg,var(--w),#fbbf24) }
.sc.sc-avg::after  { background:radial-gradient(circle,var(--w),transparent) }
.sc-ico  { font-size:22px; margin-bottom:8px; display:block }
.scv     { font-family:'Fira Code',monospace; font-size:15px; color:var(--txt); letter-spacing:-.2px }
.scl     { font-size:10px; font-weight:700; color:var(--mu); text-transform:uppercase; letter-spacing:1px; margin-top:5px }
.sc-trend {
  display:inline-flex; align-items:center; gap:3px; margin-top:8px;
  font-size:10px; font-weight:700; padding:3px 9px; border-radius:99px;
  letter-spacing:.2px; border:1px solid transparent;
}
.sc-trend.up  { background:rgba(0,176,116,.14); color:var(--a);  border-color:rgba(0,176,116,.28) }
.sc-trend.dn  { background:rgba(240,48,96,.12);  color:var(--d);  border-color:rgba(240,48,96,.25) }
.sc-trend.neu { background:rgba(255,255,255,.55); color:var(--mu); border-color:var(--edge-lo) }

/* ══════════════════════════════════════
   CHART SYSTEM
   ══════════════════════════════════════ */
.chart-area {
  background: rgba(255,255,255,.55);
  backdrop-filter: blur(24px) saturate(180%);
  -webkit-backdrop-filter: blur(24px) saturate(180%);
  border: 1px solid var(--edge); border-radius: var(--r);
  padding: 0; margin-bottom: 16px;
  box-shadow: var(--sh1), var(--inner-lo); overflow: hidden;
}
.chart-hdr {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 18px 0;
}
.chart-hdr .ct {
  font-family: 'Mona Sans', sans-serif; font-stretch: 110%;
  font-size: 13px; font-weight: 800; color: var(--txt);
}
.chart-hdr .ct span { font-size:11px; font-weight:400; color:var(--mu); margin-left:7px; font-stretch:100% }
.chart-type-tabs { display:flex; gap:5px }
.cttab {
  padding: 6px 14px; border: 1px solid var(--edge-lo); border-radius: var(--r-pill);
  font-size: 11px; font-weight: 700; color: var(--mu);
  background: rgba(255,255,255,.55); cursor: pointer;
  transition: all .25s var(--spring); letter-spacing: .3px;
  box-shadow: var(--inner-lo);
}
.cttab.on {
  background: linear-gradient(135deg, rgba(47,122,255,.22), rgba(129,140,248,.18));
  color: var(--p); border-color: rgba(47,122,255,.40);
  box-shadow: var(--glow-p), var(--inner-hi); transform: scale(1.04);
}
.cttab:hover:not(.on) { border-color:rgba(47,122,255,.30); color:var(--p); transform:translateY(-1px) }
.chart-svg-wrap { padding:12px 14px 10px; position:relative }
.chart-svg-wrap svg { width:100%; display:block; overflow:visible }
.ch-tip {
  position: absolute;
  background: rgba(240,246,255,.92);
  backdrop-filter: blur(24px);
  color: var(--txt); padding: 10px 14px; border-radius: var(--r-sm);
  font-size: 12px; font-weight: 600; pointer-events: none;
  white-space: nowrap; z-index: 99;
  border: 1px solid var(--edge); box-shadow: var(--sh2), var(--inner-hi);
  transition: opacity .12s ease; opacity: 0;
  transform: translateX(-50%); font-family: 'Fira Code', monospace;
}
.ch-tip::after {
  content:''; position:absolute; top:100%; left:50%; transform:translateX(-50%);
  border:5px solid transparent; border-top-color:var(--edge);
}
.ch-tip.show  { opacity:1 }
.ch-grid line { stroke:rgba(47,122,255,.08); stroke-width:1 }
.ch-axis text { font-size:10px; fill:var(--mu2); font-family:'Fira Code',monospace }
.ch-line-path { fill:none; stroke:url(#lineGrad); stroke-width:2.5; stroke-linecap:round; stroke-linejoin:round }
.ch-area-path { fill:url(#areaGrad); opacity:.14 }
.ch-dot { fill:rgba(255,255,255,.9); stroke:var(--p); stroke-width:2.5; cursor:pointer; transition:all .25s var(--spring) }
.ch-dot:hover { stroke-width:3; filter:drop-shadow(0 0 10px rgba(47,122,255,.55)) }
.ch-bar { cursor:pointer; transition:all .22s var(--spring) }
.ch-bar:hover { filter:brightness(1.15) }
.donut-wrap   { display:flex; align-items:center; gap:20px; padding:16px 18px 18px }
.donut-svg    { flex-shrink:0 }
.donut-legend { flex:1; display:flex; flex-direction:column; gap:9px }
.d-leg-row    { display:flex; align-items:center; gap:9px; font-size:12px; transition:.2s ease }
.d-leg-row:hover { transform:translateX(3px) }
.d-leg-dot  { width:10px; height:10px; border-radius:3px; flex-shrink:0 }
.d-leg-name { flex:1; color:var(--txt2); font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
.d-leg-val  { color:var(--mu); font-weight:700; font-size:11px; font-family:'Fira Code',monospace }
.donut-center { pointer-events:none }

/* History order list */
.holi {
  background: rgba(255,255,255,.58);
  border: 1px solid var(--edge); border-radius: var(--r);
  padding: 13px 14px; margin-bottom: 9px; cursor: pointer;
  transition: all .25s var(--spring);
  box-shadow: var(--sh0), var(--inner-lo);
}
.holi:hover {
  background: rgba(255,255,255,.88); border-color: rgba(47,122,255,.35);
  transform: translateX(4px); box-shadow: var(--sh1), var(--inner-hi);
}
.holi.on {
  background: rgba(200,220,255,.70); border-color: rgba(47,122,255,.50);
  box-shadow: 0 0 0 3px rgba(47,122,255,.12), var(--sh1), var(--inner-hi);
}
.hold    { display:flex; align-items:center; justify-content:space-between }
.hoid    { font-family:'Fira Code',monospace; font-size:12px; color:var(--p); letter-spacing:.4px }
.hotot   { font-family:'Fira Code',monospace; font-size:13px; color:var(--a); font-weight:600 }
.hometa  { font-size:11px; color:var(--mu); margin-top:5px }

/* Order detail */
.od-wr .oh3 {
  font-family:'Mona Sans',sans-serif; font-stretch:110%;
  font-size:14px; font-weight:800; margin-bottom:12px; color:var(--txt);
}
.odrow {
  display:flex; justify-content:space-between; padding:10px 0;
  border-bottom:1px solid rgba(47,122,255,.08); font-size:13px; transition:.18s ease;
}
.odrow:hover { background:rgba(47,122,255,.05); padding-left:4px; border-radius:4px }
.odrow span:last-child { font-family:'Fira Code',monospace; color:var(--mu); font-size:12px }
.odtot {
  display:flex; justify-content:space-between;
  font-family:'Fira Code',monospace; font-size:14px; color:var(--a); font-weight:600;
  padding-top:13px; border-top:1.5px solid rgba(0,176,116,.22); margin-top:5px;
}
.od2-empty { color:var(--mu2); text-align:center; padding:48px 20px }
.od2-empty p { font-size:13px; font-weight:500; color:var(--mu2); line-height:1.7 }
.od-acts { display:flex; gap:8px; margin-top:14px }

/* Manage items */
.man-row {
  display:flex; align-items:center; gap:8px; padding:13px 0;
  border-bottom:1px solid rgba(47,122,255,.08); transition:.22s ease;
}
.man-row:hover { padding-left:4px }
.man-row:last-child { border:none }
.man-info { flex:1 }
.man-name { font-weight:700; font-size:13px; color:var(--txt) }
.man-sub  { font-family:'Fira Code',monospace; color:var(--mu); font-size:11px; margin-top:4px; letter-spacing:.2px }

/* Chips */
.chip {
  display:inline-flex; align-items:center; gap:5px; padding:6px 14px;
  background:rgba(47,122,255,.14); border:1px solid rgba(47,122,255,.30);
  border-radius:99px; font-size:12px; font-weight:700; color:var(--p); margin:3px;
  box-shadow:var(--inner-lo); transition:all .22s var(--spring);
}
.chip:hover { background:rgba(47,122,255,.22); transform:scale(1.05); box-shadow:var(--glow-p) }
.chip button {
  background:none; border:none; color:inherit; font-size:15px; font-weight:700;
  cursor:pointer; padding:0; opacity:.45; line-height:1; transition:all .22s var(--spring);
}
.chip button:hover { opacity:1; color:var(--d); transform:rotate(90deg) scale(1.3) }

/* Toast */
#ts { position:fixed; top:14px; right:14px; z-index:9998; display:flex; flex-direction:column; gap:8px; pointer-events:none }
.tk {
  padding:13px 20px; border-radius:var(--r-sm); font-weight:600; font-size:13px;
  animation:toastSlide .32s var(--spring);
  pointer-events:auto; min-width:200px;
  backdrop-filter:blur(32px) saturate(200%);
  border:1px solid var(--edge);
  box-shadow:var(--sh2), var(--inner-hi);
  position:relative; overflow:hidden;
}
.tk::after { content:''; position:absolute; bottom:0; left:0; height:2px; width:100%; animation:tkBar 3s linear forwards }
@keyframes toastSlide {
  0%   { opacity:0; transform: translateX(50px) scaleX(.88) scaleY(1.10) }
  35%  { opacity:1; transform: translateX(-4px) scaleX(1.04) scaleY(.96) }
  58%  { transform: translateX(2px) scaleX(.999) scaleY(1.002) }
  76%  { transform: translateX(-1px) }
  100% { transform: translateX(0) scale(1) }
}
@keyframes tkBar { from{width:100%} to{width:0} }
.tok { background:rgba(190,245,220,.80); color:var(--a); border-color:rgba(0,176,116,.30) }
.tok::after { background:linear-gradient(90deg,var(--a),#34d399) }
.ter { background:rgba(255,220,228,.80); color:var(--d); border-color:rgba(240,48,96,.28) }
.ter::after { background:linear-gradient(90deg,var(--d),var(--w)) }
.tif { background:rgba(210,228,255,.80); color:var(--p); border-color:rgba(47,122,255,.28) }
.tif::after { background:linear-gradient(90deg,var(--p),#818cf8) }

/* QR */
.qr-wrap { text-align:center; padding:8px 0 }
.qr-wrap img {
  border-radius:var(--r); border:1px solid var(--edge); max-width:210px;
  box-shadow:var(--sh2), var(--inner-lo); transition:.25s ease;
}
.qr-wrap img:hover { transform:scale(1.04); box-shadow:var(--glow-p), var(--inner-hi) }
.qr-amt  { font-family:'Fira Code',monospace; font-size:24px; color:var(--a); margin:10px 0 4px; letter-spacing:.5px }
.qr-info { font-size:13px; color:var(--mu) }

/* ══════════════════════════════════════
   LIQUID GLASS EXTRAS & UPGRADES
   ══════════════════════════════════════ */

/* ── Liquid menu-btn ── */
.menu-btn {
  background: linear-gradient(145deg,
    rgba(255,255,255,.68) 0%, rgba(235,245,255,.58) 100%);
  color: var(--txt2);
  border: 1px solid rgba(200,220,255,.55);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.80),
    0 2px 8px rgba(30,60,160,.08);
  padding: 9px 18px; border-radius: var(--r-pill);
  font-size: 12px; font-weight: 700; letter-spacing: .4px;
  transition: border-radius .5s var(--spring), transform .25s var(--spring),
              box-shadow .25s var(--spring), background .25s ease;
  position: relative; overflow: hidden;
}
.menu-btn::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 50%;
  background: linear-gradient(180deg, rgba(255,255,255,.42) 0%, transparent 100%);
  pointer-events: none;
}
.menu-btn:hover {
  background: linear-gradient(145deg,
    rgba(255,255,255,.88) 0%, rgba(220,238,255,.78) 100%);
  border-color: rgba(47,122,255,.40); color: var(--p);
  box-shadow: var(--glow-p), inset 0 1px 0 rgba(255,255,255,.90);
  transform: translateY(-2px);
  border-radius: 20px 16px 18px 17px / 17px 20px 16px 18px;
}

/* ── Enhanced qbtn (+ / - in bill) ── */
.qbtn {
  width: 32px; height: 32px; border-radius: 10px;
  font-size: 16px; display: flex; align-items: center; justify-content: center;
  border: 1px solid var(--edge-lo);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.80), var(--sh0);
  transition: border-radius .45s var(--spring), transform .22s var(--spring),
              box-shadow .22s var(--spring), background .22s ease;
  position: relative; overflow: hidden;
}
.qbtn::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 50%;
  background: linear-gradient(180deg, rgba(255,255,255,.50) 0%, transparent 100%);
  pointer-events: none; border-radius: inherit;
}
.qbtn:hover {
  transform: scale(1.20);
  box-shadow: var(--sh1), inset 0 1px 0 rgba(255,255,255,.90);
  border-radius: 12px 8px 11px 9px / 9px 12px 8px 11px;
}
.qbtn:active {
  transform: scale(.88) !important;
  border-radius: 8px 12px 9px 11px / 11px 8px 12px 9px !important;
}
.qp {
  background: linear-gradient(145deg, rgba(100,230,170,.42) 0%, rgba(0,176,116,.28) 100%);
  color: var(--ad);
}
.qm {
  background: linear-gradient(145deg, rgba(255,140,155,.40) 0%, rgba(240,48,96,.25) 100%);
  color: var(--dd);
}

/* ── Toast upgrade ── */
.tk {
  padding: 13px 20px; border-radius: 14px 10px 13px 11px / 11px 14px 10px 13px;
  font-weight: 600; font-size: 13px;
  animation: toastSlide .34s var(--spring);
  pointer-events: auto; min-width: 200px;
  backdrop-filter: blur(40px) saturate(210%);
  border: 1px solid var(--edge);
  box-shadow: var(--sh2), var(--inner-hi),
    inset 0 1px 0 rgba(255,255,255,.65);
  position: relative; overflow: hidden;
}
/* Liquid surface inside toast */
.tk::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 40%;
  background: linear-gradient(180deg, rgba(255,255,255,.35) 0%, transparent 100%);
  pointer-events: none; border-radius: inherit;
}
.tk::after {
  content: ''; position: absolute; bottom: 0; left: 0; height: 2.5px; width: 100%;
  animation: tkBar 3s linear forwards;
  border-radius: 0 0 2px 2px;
}
.tok { background: rgba(185,248,215,.82); color: var(--a); border-color: rgba(0,176,116,.32) }
.tok::after { background: linear-gradient(90deg, var(--a), #34d399) }
.ter { background: rgba(255,215,225,.82); color: var(--d); border-color: rgba(240,48,96,.30) }
.ter::after { background: linear-gradient(90deg, var(--d), var(--w)) }
.tif { background: rgba(205,228,255,.82); color: var(--p); border-color: rgba(47,122,255,.30) }
.tif::after { background: linear-gradient(90deg, var(--p), #818cf8) }

/* ── Liquid btn-split ── */
.btn-split {
  flex: 0 0 auto; padding: 11px 14px;
  background: linear-gradient(145deg, rgba(255,255,255,.68) 0%, rgba(235,245,255,.55) 100%);
  color: var(--mu);
  border: 1px solid rgba(200,220,255,.55); border-radius: var(--r-sm);
  font-size: 11px; font-weight: 700; white-space: nowrap; letter-spacing: .4px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.75), var(--sh0);
  transition: border-radius .45s var(--spring), transform .25s var(--spring),
              box-shadow .25s var(--spring), background .25s ease;
  position: relative; overflow: hidden;
}
.btn-split::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 45%;
  background: linear-gradient(180deg, rgba(255,255,255,.40) 0%, transparent 100%);
  pointer-events: none;
}
.btn-split:hover {
  background: linear-gradient(145deg, rgba(255,255,255,.90) 0%, rgba(220,238,255,.80) 100%);
  border-color: rgba(47,122,255,.32); color: var(--p);
  transform: translateY(-2px);
  box-shadow: var(--glow-p), inset 0 1px 0 rgba(255,255,255,.88);
  border-radius: 11px 8px 10px 9px / 9px 11px 8px 10px;
}

/* ── Enhanced login card ── */
.login-card {
  background: linear-gradient(150deg,
    rgba(255,255,255,.78) 0%,
    rgba(240,250,255,.72) 40%,
    rgba(245,252,255,.76) 100%);
  backdrop-filter: blur(60px) saturate(230%) brightness(1.08);
  -webkit-backdrop-filter: blur(60px) saturate(230%) brightness(1.08);
  border-radius: 28px; padding: 48px 38px; width: 430px;
  border: 1px solid rgba(210,235,255,.70);
  box-shadow: var(--sh3), var(--inner-hi),
    0 0 100px rgba(47,122,255,.14),
    inset 0 0 60px rgba(200,230,255,.10);
  animation: loginCardIn .65s var(--spring);
  position: relative; z-index: 2; overflow: hidden;
}
/* Caustic light inside login card */
.login-card::after {
  content: '';
  position: absolute; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(ellipse 65% 45% at 75% 15%, rgba(160,205,255,.22) 0%, transparent 60%),
    radial-gradient(ellipse 45% 35% at 20% 80%, rgba(140,230,195,.15) 0%, transparent 55%);
  animation: causticsShift 14s ease-in-out infinite;
  filter: blur(25px);
}

/* ── Glass card sections inside settings modal ── */
.glass-section {
  background: linear-gradient(145deg,
    rgba(240,248,255,.70) 0%, rgba(225,240,255,.60) 100%);
  backdrop-filter: blur(20px);
  border-radius: var(--r); padding: 16px; margin-bottom: 14px;
  border: 1px solid rgba(200,225,255,.50);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.75), var(--sh0);
  position: relative; overflow: hidden;
}
.glass-section::before {
  content: '';
  position: absolute; top: 0; left: 0; right: 0; height: 1.5px;
  background: linear-gradient(90deg, transparent, rgba(100,170,255,.60), transparent);
  animation: prism 5s linear infinite;
}
.glass-section-title {
  font-size: 11px; font-weight: 800; color: var(--mu);
  margin-bottom: 12px; text-transform: uppercase; letter-spacing: .6px;
}

/* ── Liquid glass toggle checkbox ── */
.glass-toggle {
  display: flex; align-items: center; gap: 10px;
  cursor: pointer; font-size: 13px; font-weight: 600;
  margin-bottom: 12px; padding: 10px 12px;
  background: linear-gradient(145deg, rgba(255,255,255,.72) 0%, rgba(240,248,255,.60) 100%);
  border-radius: var(--r-sm);
  border: 1px solid rgba(200,225,255,.50);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.75);
  transition: all .24s var(--spring);
}
.glass-toggle:hover {
  background: linear-gradient(145deg, rgba(255,255,255,.88) 0%, rgba(220,238,255,.78) 100%);
  border-color: rgba(47,122,255,.30);
  border-radius: 12px 8px 11px 9px / 9px 12px 8px 11px;
}

/* ── Liquid inner highlight on bill panel ── */
#bill-panel {
  flex: 0 0 37%; min-width: 0;
  background: linear-gradient(160deg,
    rgba(255,255,255,.58) 0%,
    rgba(240,250,255,.52) 50%,
    rgba(248,252,255,.55) 100%);
  backdrop-filter: blur(44px) saturate(210%);
  -webkit-backdrop-filter: blur(44px) saturate(210%);
  border: 1px solid rgba(210,235,255,.60);
  border-radius: var(--r);
  box-shadow: var(--sh2), var(--inner-lo),
    inset 0 0 50px rgba(200,225,255,.08);
  display: flex; flex-direction: column; overflow: hidden;
  position: relative;
}
/* Caustic light in bill panel */
#bill-panel::before {
  content: '';
  position: absolute; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(ellipse 60% 45% at 85% 10%, rgba(150,200,255,.14) 0%, transparent 55%),
    radial-gradient(ellipse 40% 30% at 10% 85%, rgba(130,230,190,.10) 0%, transparent 50%);
  animation: causticsShift 16s ease-in-out infinite reverse;
  filter: blur(30px);
}
#bill-panel > * { position: relative; z-index: 1; }

/* ── Liquid bill items ── */
.bitem {
  display: flex; align-items: center; padding: 12px 14px;
  border-bottom: 1px solid rgba(47,122,255,.07);
  transition: all .22s ease;
  animation: dropletAppear .28s var(--spring) both;
}
@keyframes dropletAppear {
  0%   { opacity:0; transform: scale(.88) scaleX(1.10) scaleY(.92) }
  35%  { opacity:1; transform: scale(1.025) scaleX(.97) scaleY(1.04) }
  58%  { transform: scale(.998) scaleX(1.004) }
  78%  { transform: scale(1.002) }
  100% { transform: scale(1) }
}
.bitem:last-child { border: none }
.bitem:hover {
  background: linear-gradient(90deg, rgba(220,236,255,.60) 0%, rgba(240,250,255,.45) 100%);
  border-radius: var(--r-xs);
}

/* ── Water ripple effect on table cards ── */
.tcard {
  background: linear-gradient(150deg,
    rgba(255,255,255,.65) 0%, rgba(240,248,255,.55) 60%,
    rgba(245,252,255,.60) 100%);
  backdrop-filter: blur(28px) saturate(190%);
  -webkit-backdrop-filter: blur(28px) saturate(190%);
  border: 1px solid rgba(200,225,255,.55);
  border-radius: var(--r);
  padding: 24px 14px 20px;
  text-align: center; cursor: pointer;
  transition: border-radius .5s var(--spring), transform .28s var(--spring),
              box-shadow .28s var(--spring), background .28s ease;
  position: relative; overflow: hidden;
  box-shadow: var(--sh0), inset 0 1px 0 rgba(255,255,255,.80);
}
/* Specular streak */
.tcard::before {
  content: '';
  position: absolute; top: 0; left: 8%; right: 8%; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.90), transparent);
  transition: .28s ease;
}
/* Shimmer */
.tcard::after {
  content: '';
  position: absolute; top: -50%; left: -75%; width: 50%; height: 200%;
  background: linear-gradient(105deg, transparent, rgba(255,255,255,.38), transparent);
  transform: skewX(-15deg);
  transition: left .55s ease; pointer-events: none;
}
.tcard:hover {
  background: linear-gradient(150deg,
    rgba(255,255,255,.85) 0%, rgba(225,242,255,.75) 60%,
    rgba(235,248,255,.80) 100%);
  border-color: rgba(47,122,255,.40);
  transform: translateY(-6px) scale(1.025);
  box-shadow: var(--sh2), var(--inner-hi);
  border-radius: 17px 14px 16px 15px / 15px 17px 14px 16px;
}
.tcard:hover::before { left: 0; right: 0 }
.tcard:hover::after  { left: 145% }
.tcard.sel {
  background: linear-gradient(150deg,
    rgba(195,218,255,.78) 0%, rgba(170,210,255,.68) 60%,
    rgba(185,215,255,.72) 100%);
  border-color: rgba(47,122,255,.60);
  box-shadow: var(--glow-p), var(--inner-hi);
  transform: translateY(-5px) scale(1.025);
  border-radius: 17px 14px 16px 15px / 15px 17px 14px 16px;
}

/* ── nb-content glass upgrade ── */
.nb-content {
  flex: 1; overflow: hidden; display: flex; flex-direction: column;
  background: linear-gradient(160deg,
    rgba(255,255,255,.52) 0%, rgba(240,250,255,.45) 50%,
    rgba(248,252,255,.48) 100%);
  backdrop-filter: blur(36px) saturate(190%);
  -webkit-backdrop-filter: blur(36px) saturate(190%);
  border: 1px solid rgba(200,225,255,.50); border-top: none;
  border-radius: 0 0 var(--r) var(--r);
  box-shadow: var(--sh1), var(--inner-lo);
}

/* ── Receipt card items glass style ── */
.receipt-info-card {
  flex: 1; padding: 10px 13px;
  background: linear-gradient(145deg, rgba(220,240,255,.65) 0%, rgba(200,228,255,.55) 100%);
  backdrop-filter: blur(20px);
  border-radius: var(--r-sm);
  border: 1px solid rgba(170,210,255,.50);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.70);
}
.receipt-total-bar {
  margin-top: 12px; padding: 13px 15px;
  background: linear-gradient(120deg,
    rgba(175,248,220,.65) 0%, rgba(145,240,200,.55) 100%);
  backdrop-filter: blur(20px);
  border-radius: var(--r-sm);
  border: 1px solid rgba(130,230,185,.50);
  display: flex; justify-content: space-between; align-items: center;
  box-shadow: inset 0 1px 0 rgba(200,255,230,.70);
}
.qr-warning {
  margin-top: 10px; padding: 9px 14px;
  background: linear-gradient(145deg, rgba(255,250,220,.75) 0%, rgba(255,245,195,.65) 100%);
  backdrop-filter: blur(15px);
  border-radius: var(--r-sm);
  border: 1px solid rgba(240,220,100,.40);
  font-size: 12px; color: #7a5500; font-weight: 600;
  box-shadow: inset 0 1px 0 rgba(255,255,220,.70);
}

/* ── Chip liquid upgrade ── */
.chip {
  display: inline-flex; align-items: center; gap: 5px; padding: 6px 14px;
  background: linear-gradient(145deg, rgba(180,210,255,.28) 0%, rgba(47,122,255,.18) 100%);
  border: 1px solid rgba(47,122,255,.32);
  border-radius: 99px; font-size: 12px; font-weight: 700; color: var(--p); margin: 3px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.65);
  transition: border-radius .4s var(--spring), transform .22s var(--spring),
              box-shadow .22s var(--spring), background .22s ease;
}
.chip:hover {
  background: linear-gradient(145deg, rgba(200,225,255,.42) 0%, rgba(47,122,255,.30) 100%);
  transform: scale(1.06);
  box-shadow: var(--glow-p), inset 0 1px 0 rgba(255,255,255,.75);
  border-radius: 55% 45% 50% 50% / 50% 55% 45% 50%;
}

/* ── Enhanced nb-tabs ── */
.nb-tabs {
  display: flex; flex-shrink: 0; overflow: hidden;
  background: linear-gradient(145deg,
    rgba(255,255,255,.62) 0%, rgba(235,248,255,.55) 100%);
  backdrop-filter: blur(36px) saturate(190%);
  -webkit-backdrop-filter: blur(36px) saturate(190%);
  border-radius: var(--r) var(--r) 0 0;
  border: 1px solid rgba(200,225,255,.52); border-bottom: none;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.80), var(--sh0);
}

/* ── Liquid topbar time badge ── */
#topbar .ttime {
  font-family: 'Fira Code', monospace;
  font-size: 11px; font-weight: 400; cursor: pointer; letter-spacing: .6px;
  padding: 7px 15px; border-radius: var(--r-pill);
  background: linear-gradient(145deg, rgba(255,255,255,.60) 0%, rgba(235,248,255,.50) 100%);
  color: var(--mu);
  border: 1px solid rgba(200,225,255,.50);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.75), var(--sh0);
  transition: border-radius .45s var(--spring), transform .25s var(--spring),
              box-shadow .25s var(--spring), background .25s ease;
}
#topbar .ttime:hover {
  background: linear-gradient(145deg, rgba(255,255,255,.88) 0%, rgba(220,238,255,.78) 100%);
  border-color: rgba(47,122,255,.38); color: var(--p);
  box-shadow: var(--glow-p), inset 0 1px 0 rgba(255,255,255,.90);
  transform: translateY(-2px);
  border-radius: 18px 14px 16px 15px / 15px 18px 14px 16px;
}
"""
# The loader CSS was moved above; add it back as an extra block
CSS_LOADER = r"""
/* Loader */
#ldr { position:fixed; top:0; left:0; right:0; height:2.5px; z-index:9999; display:none; background:transparent }
#ldr.on { display:block }
#ldrb {
  height:100%; width:40%;
  background:linear-gradient(90deg,transparent,var(--p),#818cf8,#34d399,var(--p),transparent);
  animation:ld .9s ease-in-out infinite;
  box-shadow:0 0 10px rgba(47,122,255,.5);
}
@keyframes ld { 0%{margin-left:-40%} 100%{margin-left:140%} }
/* ═══════════════════════════════════════════════════════════════
   iOS 26 LIQUID GLASS  — Surface Overrides
   Formula:
     background = near-zero rgba fill
     backdrop-filter = heavy blur + saturate + brightness
     border = thin specular white line
     box-shadow = inset top highlight + subtle outer drop
   ═══════════════════════════════════════════════════════════════ */

/* ══════════════════════════════════════════════════════════════
   WATER DROPLET SURFACE — không blur, trong suốt hoàn toàn,
   chỉ méo hình ảnh đằng sau qua SVG displacement (feDisplacementMap)
   Giọt nước = trong suốt + khúc xạ, KHÔNG phải kính mờ = blur + opaque
   ══════════════════════════════════════════════════════════════ */

/* ── Shared water surface mixin ── */
.lg-surface,
#topbar,
.nb-tabs,
.nb-content,
#bill-panel,
.mcard,
.ddlist,
.hist-bar,
.login-card,
.sc,
.chart-area,
.glass-section,
.hist-right {
  /* Nước = gần như trong suốt, ánh xanh nhẹ */
  background: rgba(180,220,255, var(--water-fill, 0.06)) !important;
  /* Blur NHẸ (4-8px): đủ để làm mịn, không đủ để làm mờ hoàn toàn như kính */
  /* Nước thật có khả năng tán xạ ánh sáng nhẹ ở bề mặt */
  backdrop-filter: blur(var(--water-blur, 18px)) saturate(130%) !important;
  -webkit-backdrop-filter: blur(var(--water-blur, 18px)) saturate(130%) !important;
  /* Viền mặt nước: sáng, mỏng */
  border-color: rgba(255,255,255, 0.75) !important;
  /* Highlight bề mặt nước */
  box-shadow:
    inset 0 2px 0 rgba(255,255,255, 0.95),
    inset 0 -1px 0 rgba(100,180,255, 0.15),
    inset 0 0 40px rgba(180,220,255, var(--water-inner, 0.08)),
    0 4px 32px rgba(0,80,200, 0.08) !important;
}

/* ── Topbar ── */
#topbar {
  border-bottom: 0.5px solid rgba(255,255,255,0.70) !important;
  background: rgba(180,220,255, var(--water-fill, 0.06)) !important;
  backdrop-filter: blur(var(--water-blur, 18px)) saturate(130%) !important;
  -webkit-backdrop-filter: blur(var(--water-blur, 18px)) saturate(130%) !important;
}
/* Keep topbar prismatic top edge */
#topbar::before { content: ''; position: absolute; top: 0; left: 0; right: 0; height: 1.5px;
  background: linear-gradient(90deg, transparent, rgba(100,170,255,.7) 20%, rgba(190,140,255,.8) 45%, rgba(80,230,200,.7) 65%, rgba(100,170,255,.7) 85%, transparent);
  background-size: 200%; animation: prism 5s linear infinite; }

/* ── Notebook panels ── */
.nb-tabs  { border-radius: var(--r) var(--r) 0 0 !important; }
.nb-content { border-top: 0 !important; border-radius: 0 0 var(--r) var(--r) !important; }

/* ── Bill panel header ── */
.bill-title {
  background: rgba(80,220,160, 0.10) !important;
  backdrop-filter: blur(20px) saturate(250%) !important;
  -webkit-backdrop-filter: blur(20px) saturate(250%) !important;
  border-bottom: 0.5px solid rgba(0,200,130, 0.30) !important;
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,0.85), inset 0 0 20px rgba(80,220,160,0.08) !important;
}
.bill-tablename {
  background: rgba(47,122,255, 0.06) !important;
  backdrop-filter: blur(20px) saturate(200%) !important;
  -webkit-backdrop-filter: blur(20px) saturate(200%) !important;
}

/* ── Table cards (bàn) ── */
.tcard {
  background: rgba(255,255,255, calc(var(--lg-fill,0.06) * 1.5)) !important;
  backdrop-filter: blur(calc(var(--lg-blur,72px) * 0.6)) saturate(var(--lg-sat,300%)) brightness(1.08) !important;
  -webkit-backdrop-filter: blur(calc(var(--lg-blur,72px) * 0.6)) saturate(var(--lg-sat,300%)) brightness(1.08) !important;
  border: 0.5px solid rgba(255,255,255,0.55) !important;
  box-shadow:
    inset 0 1.5px 0 rgba(255,255,255,0.92),
    inset 0 0 20px rgba(180,220,255,0.07),
    0 2px 20px rgba(0,0,0,0.06) !important;
}
.tcard:hover {
  background: rgba(255,255,255, calc(var(--lg-fill,0.06) * 2.8)) !important;
  border-color: rgba(100,180,255,0.45) !important;
  box-shadow:
    inset 0 2px 0 rgba(255,255,255,0.98),
    inset 0 0 30px rgba(100,180,255,0.12),
    0 8px 40px rgba(47,122,255,0.14) !important;
}
.tcard.sel {
  background: rgba(80,140,255, calc(var(--lg-fill,0.06) * 2)) !important;
  border-color: rgba(100,160,255,0.55) !important;
  box-shadow:
    inset 0 2px 0 rgba(255,255,255,0.96),
    inset 0 0 40px rgba(80,140,255,0.14),
    0 0 0 1px rgba(100,160,255,0.25), var(--glow-p) !important;
}
.tcard.busy {
  background: rgba(240,120,40, calc(var(--lg-fill,0.06) * 2)) !important;
  border-color: rgba(240,130,60,0.40) !important;
}

/* ── BUTTONS — Water Droplet ── */

/* Base button: nước trong suốt, không blur, chỉ bóng bề mặt */
.btn {
  background: rgba(200,230,255, calc(var(--water-fill, 0.06) * 1.2)) !important;
  backdrop-filter: none !important;
  -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(255,255,255,0.80) !important;
  box-shadow:
    inset 0 2px 0 rgba(255,255,255,0.95),
    inset 0 -0.5px 0 rgba(100,160,255,0.08),
    inset 0 0 20px rgba(200,225,255,0.10),
    0 2px 14px rgba(0,60,180,0.06) !important;
  color: var(--txt) !important;
}
.btn:hover {
  background: rgba(200,230,255, calc(var(--water-fill, 0.06) * 2.8)) !important;
  border-color: rgba(255,255,255,0.92) !important;
  box-shadow:
    inset 0 2.5px 0 rgba(255,255,255,0.98),
    inset 0 0 30px rgba(180,220,255,0.18),
    0 8px 36px rgba(0,80,200,0.12) !important;
}

/* Blue tinted water */
.btn-p {
  background: rgba(100,160,255, calc(var(--water-fill, 0.06) * 2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(180,210,255,0.70) !important;
  color: var(--p) !important;
  box-shadow:
    inset 0 2px 0 rgba(255,255,255,0.95),
    inset 0 0 24px rgba(100,160,255,0.15),
    0 3px 18px rgba(47,122,255,0.12) !important;
}
.btn-p:hover {
  background: rgba(100,160,255, calc(var(--water-fill, 0.06) * 4.5)) !important;
  border-color: rgba(200,225,255,0.85) !important;
  box-shadow:
    inset 0 2.5px 0 rgba(255,255,255,0.98),
    inset 0 0 36px rgba(100,160,255,0.25),
    0 10px 44px rgba(47,122,255,0.22) !important;
}

/* Green tinted water */
.btn-a {
  background: rgba(80,210,150, calc(var(--water-fill, 0.06) * 2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(150,240,200,0.65) !important;
  color: var(--a) !important;
  box-shadow:
    inset 0 2px 0 rgba(255,255,255,0.95),
    inset 0 0 24px rgba(80,210,150,0.15),
    0 3px 18px rgba(0,176,116,0.12) !important;
}
.btn-a:hover {
  background: rgba(80,210,150, calc(var(--water-fill, 0.06) * 4.5)) !important;
  border-color: rgba(180,255,225,0.82) !important;
  box-shadow:
    inset 0 2.5px 0 rgba(255,255,255,0.98),
    inset 0 0 36px rgba(80,210,150,0.25),
    0 10px 44px rgba(0,176,116,0.22) !important;
}

/* Red tinted water */
.btn-d {
  background: rgba(240,80,110, calc(var(--water-fill, 0.06) * 2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(255,150,170,0.65) !important;
  color: var(--d) !important;
  box-shadow:
    inset 0 2px 0 rgba(255,255,255,0.95),
    inset 0 0 24px rgba(240,80,110,0.15),
    0 3px 18px rgba(240,48,96,0.12) !important;
}
.btn-d:hover {
  background: rgba(240,80,110, calc(var(--water-fill, 0.06) * 4.5)) !important;
  border-color: rgba(255,180,200,0.82) !important;
  box-shadow:
    inset 0 2.5px 0 rgba(255,255,255,0.98),
    inset 0 0 36px rgba(240,80,110,0.25),
    0 10px 44px rgba(240,48,96,0.22) !important;
}

/* Clear/ghost water */
.btn-g {
  background: rgba(200,225,255, calc(var(--water-fill, 0.06) * 1.5)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(255,255,255,0.75) !important;
  color: var(--mu) !important;
}
.btn-g:hover {
  background: rgba(200,225,255, calc(var(--water-fill, 0.06) * 3)) !important;
  color: var(--p) !important;
  border-color: rgba(180,220,255,0.70) !important;
}

/* Checkout green water */
.btn-checkout {
  background: rgba(0,200,130, calc(var(--water-fill, 0.06) * 2.5)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(100,240,180,0.72) !important;
  color: rgba(0,90,55,0.95) !important;
  box-shadow:
    inset 0 2.5px 0 rgba(220,255,240,0.92),
    inset 0 -1px 0 rgba(0,150,90,0.10),
    inset 0 0 36px rgba(0,220,150,0.18),
    0 5px 32px rgba(0,176,116,0.16) !important;
}
.btn-checkout:hover {
  background: rgba(0,200,130, calc(var(--water-fill, 0.06) * 5)) !important;
  border-color: rgba(150,255,210,0.88) !important;
  box-shadow:
    inset 0 3px 0 rgba(230,255,245,0.98),
    inset 0 0 48px rgba(0,220,150,0.28),
    0 12px 56px rgba(0,176,116,0.30) !important;
}

/* ── Menu buttons / topbar buttons — water ── */
.menu-btn {
  background: rgba(200,225,255, calc(var(--water-fill, 0.06) * 1.2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(255,255,255,0.80) !important;
  box-shadow: inset 0 2px 0 rgba(255,255,255,0.92), 0 2px 10px rgba(0,60,200,0.06) !important;
}
.menu-btn:hover {
  background: rgba(180,210,255, calc(var(--water-fill, 0.06) * 2.5)) !important;
  border-color: rgba(200,230,255,0.88) !important;
  box-shadow: inset 0 2.5px 0 rgba(255,255,255,0.98), 0 6px 28px rgba(47,122,255,0.14) !important;
  color: var(--p) !important;
}
.btn-split {
  background: rgba(200,225,255, calc(var(--water-fill, 0.06) * 1.2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(255,255,255,0.75) !important;
  box-shadow: inset 0 2px 0 rgba(255,255,255,0.92), 0 1px 8px rgba(0,60,200,0.05) !important;
}
.btn-split:hover {
  background: rgba(180,210,255, calc(var(--water-fill, 0.06) * 2.5)) !important;
  color: var(--p) !important;
}

/* ── Quick buttons (.qbtn) — water ── */
.qbtn {
  background: rgba(200,225,255, calc(var(--water-fill, 0.06) * 1.3)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(255,255,255,0.75) !important;
  box-shadow: inset 0 2px 0 rgba(255,255,255,0.92), 0 1px 8px rgba(0,60,200,0.05) !important;
}
.qbtn:hover {
  background: rgba(180,210,255, calc(var(--water-fill, 0.06) * 3)) !important;
  border-color: rgba(200,230,255,0.75) !important;
}

/* ── Tabs ── */
.nb-tab { background: transparent !important; backdrop-filter: none !important; -webkit-backdrop-filter: none !important; }
.nb-tab:hover { background: rgba(180,210,255, calc(var(--water-fill,0.06)*1.8)) !important; }
.nb-tab.on  { background: rgba(180,210,255, calc(var(--water-fill,0.06)*3)) !important; }
.otab {
  background: rgba(200,225,255, calc(var(--water-fill,0.06)*1.3)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(255,255,255,0.72) !important;
  box-shadow: inset 0 2px 0 rgba(255,255,255,0.90) !important;
}
.otab.on {
  background: rgba(180,215,255, calc(var(--water-fill,0.06)*2.8)) !important;
  border-color: rgba(200,235,255,0.80) !important;
}

/* ── Form inputs — water ── */
.fi {
  background: rgba(220,240,255, calc(var(--water-fill,0.06) * 2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(180,220,255, 0.60) !important;
  box-shadow:
    inset 0 1.5px 0 rgba(255,255,255,0.85),
    inset 0 -0.5px 0 rgba(100,160,255,0.06),
    0 1px 6px rgba(0,60,200,0.05) !important;
}
.fi:focus {
  background: rgba(210,235,255, calc(var(--water-fill,0.06) * 3.5)) !important;
  border-color: rgba(100,180,255,0.65) !important;
  box-shadow:
    0 0 0 3.5px rgba(47,122,255,0.14),
    inset 0 2px 0 rgba(255,255,255,0.92),
    inset 0 0 24px rgba(100,180,255,0.12) !important;
}
select.fi option { background: rgba(235,248,255,0.98); color: var(--txt); }

/* ── Dropdown — water ── */
.ddlist {
  border: 0.5px solid rgba(255,255,255,0.80) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  background: rgba(200,230,255, calc(var(--water-fill,0.06) * 1.2)) !important;
  box-shadow: inset 0 2px 0 rgba(255,255,255,0.92), 0 8px 40px rgba(0,80,200,0.14) !important;
}

/* ── Chips — water ── */
.chip {
  background: rgba(100,160,255, calc(var(--water-fill,0.06) * 2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(180,215,255,0.55) !important;
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,0.85) !important;
}

/* ── Modal card — water ── */
.mcard {
  border: 0.5px solid rgba(255,255,255,0.80) !important;
  backdrop-filter: blur(calc(var(--water-blur, 18px) * 0.6)) !important;
  -webkit-backdrop-filter: blur(calc(var(--water-blur, 18px) * 0.6)) !important;
  background: rgba(190,225,255, calc(var(--water-fill,0.06) * 0.8)) !important;
  box-shadow:
    inset 0 2.5px 0 rgba(255,255,255,0.96),
    inset 0 0 60px rgba(180,220,255,0.12),
    0 20px 80px rgba(0,80,200,0.18) !important;
}
.mhdr {
  background: rgba(160,210,255, calc(var(--water-fill,0.06) * 1.2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border-bottom: 0.5px solid rgba(180,220,255,0.50) !important;
  box-shadow: inset 0 2px 0 rgba(255,255,255,0.92) !important;
}
.mftr {
  background: rgba(175,215,255, calc(var(--water-fill,0.06) * 2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border-top: 0.5px solid rgba(180,220,255,0.40) !important;
  box-shadow: inset 0 -2px 0 rgba(255,255,255,0.80) !important;
}
.mbg {
  background: rgba(30,60,140, 0.08) !important;
  backdrop-filter: none !important;
  -webkit-backdrop-filter: none !important;
}

/* ── KPI / Stats cards — water ── */
.sc {
  background: rgba(200,225,255, calc(var(--water-fill,0.06) * 1.4)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
}
.chart-area {
  background: rgba(200,225,255, calc(var(--water-fill,0.06) * 1.2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
}

/* ── History — water ── */
.holi {
  background: rgba(200,225,255, calc(var(--water-fill,0.06) * 1.2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(255,255,255,0.72) !important;
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,0.80), 0 1px 6px rgba(0,60,200,0.05) !important;
}
.holi:hover {
  background: rgba(180,215,255, calc(var(--water-fill,0.06) * 2)) !important;
  border-color: rgba(47,122,255,.25) !important;
  transform: translateX(4px);
}
.holi.on {
  background: rgba(47,122,255, calc(var(--water-fill,0.06) * 4)) !important;
  border-color: rgba(47,122,255,.35) !important;
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,0.80), 0 0 0 2px rgba(47,122,255,.10) !important;
}
.holi-grp-hdr {
  background: rgba(225,238,255, 0.96) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
}
.hist-right {
  background: rgba(200,225,255, calc(var(--water-fill,0.06) * 1.5)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
}

/* ── Menu rows — water ── */
.mrow {
  background: rgba(200,225,255, calc(var(--water-fill,0.06) * 1.2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(220,238,255,0.45) !important;
}
.mrow:hover {
  background: rgba(180,215,255, calc(var(--water-fill,0.06) * 1.2)) !important;
  border-color: rgba(200,230,255,0.60) !important;
}

/* ── Login card — water ── */
.login-card {
  border: 0.5px solid rgba(255,255,255,0.82) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  background: rgba(190,225,255, calc(var(--water-fill,0.06) * 1.2)) !important;
  box-shadow:
    inset 0 2.5px 0 rgba(255,255,255,0.97),
    inset 0 0 80px rgba(180,220,255,0.14),
    0 0 100px rgba(47,122,255,0.16) !important;
}

/* ── Toast — water ── */
.tk {
  background: rgba(200,230,255, calc(var(--water-fill,0.06) * 3.5)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(255,255,255,0.82) !important;
  box-shadow:
    inset 0 2px 0 rgba(255,255,255,0.95),
    0 5px 36px rgba(0,60,200,0.14) !important;
}

/* ── Glass-section (settings) — water ── */
.glass-section {
  background: rgba(190,220,255, calc(var(--water-fill,0.06) * 2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(200,230,255,0.55) !important;
  box-shadow: inset 0 2px 0 rgba(255,255,255,0.90) !important;
}

/* ── Ttime clock chip — water ── */
#topbar .ttime {
  background: rgba(200,228,255, calc(var(--water-fill, 0.06) * 1.5)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(255,255,255,0.75) !important;
  box-shadow: inset 0 2px 0 rgba(255,255,255,0.92), 0 1px 8px rgba(0,60,200,0.05) !important;
}
#topbar .ttime:hover {
  background: rgba(180,215,255, calc(var(--water-fill, 0.06) * 3)) !important;
  border-color: rgba(200,235,255,0.88) !important;
}

/* ── Text contrast boost — needed since backgrounds are now very transparent ── */
/* Text must remain readable against colorful aurora background */
#topbar .brand,
.nb-tab,
.tcard .tname,
.tcard .tamnt,
.bill-title,
.bitem-name,
.bitem-qty,
.bitem-price {
  text-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

/* ── Liquid glass specular animation ── */
/* Every glass surface gets a subtle animated inner light */
@keyframes lgRefract {
  0%, 100% { opacity: 0.8 }
  50%       { opacity: 1; filter: brightness(1.04) }
}

/* ── Tab bar wrap — water ── */
.tab-bar-wrap { background: rgba(200,228,255, calc(var(--water-fill,0.06)*0.8)) !important; }
.tab-bar { background: transparent !important; }
.bill-items { background: transparent !important; }
.qc { background: rgba(200,228,255, calc(var(--water-fill,0.06)*1.8)) !important; border-color: rgba(220,240,255,0.60) !important; }
.bill-total { background: rgba(0,180,120, calc(var(--water-fill,0.06)*1.8)) !important; border-top: 0.5px solid rgba(0,200,130,0.35) !important; box-shadow: inset 0 2px 0 rgba(200,255,230,0.80) !important; }
#msearch, .srch { background: rgba(220,240,255, calc(var(--water-fill,0.06)*2)) !important; border-color: rgba(180,220,255,0.50) !important; backdrop-filter: blur(calc(var(--water-blur,18px)*0.5)) !important; -webkit-backdrop-filter: blur(calc(var(--water-blur,18px)*0.5)) !important; }
.bill-tablename { backdrop-filter: blur(calc(var(--water-blur,18px)*0.6)) !important; -webkit-backdrop-filter: blur(calc(var(--water-blur,18px)*0.6)) !important; }

/* ── Go preset buttons — water ── */
.go-preset-btn {
  background: rgba(200,228,255, calc(var(--water-fill,0.06)*1.3)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(200,230,255,0.55) !important;
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,0.85) !important;
}
.go-preset-btn.active {
  background: rgba(100,160,255, calc(var(--water-fill,0.06)*3)) !important;
  border-color: rgba(180,220,255,0.70) !important;
  box-shadow: inset 0 2px 0 rgba(255,255,255,0.92), var(--glow-p) !important;
}

/* ── KPI donut bg ── */
.dnut { background: transparent !important; }

/* ── Category pills — water ── */
.ccat {
  background: rgba(200,228,255, calc(var(--water-fill,0.06)*1.5)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(220,240,255,0.60) !important;
  box-shadow: inset 0 1.5px 0 rgba(255,255,255,0.85) !important;
}
.ccat.on, .ccat:hover {
  background: rgba(100,160,255, calc(var(--water-fill,0.06)*3)) !important;
  border-color: rgba(180,220,255,0.68) !important;
}

/* ── Receipt / order detail backgrounds in modal — water ── */
.receipt-info-card {
  background: rgba(190,225,255, calc(var(--water-fill,0.06)*2)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(180,215,255,0.50) !important;
  box-shadow: inset 0 2px 0 rgba(255,255,255,0.90) !important;
}
.receipt-total-bar {
  background: rgba(0,180,120, calc(var(--water-fill,0.06)*2.5)) !important;
  backdrop-filter: none !important; -webkit-backdrop-filter: none !important;
  border: 0.5px solid rgba(0,210,140,0.50) !important;
  box-shadow: inset 0 2px 0 rgba(200,255,230,0.88) !important;
}

/* WATER REFRACTION OVERRIDE — blur nhẹ + trong suốt + khúc xạ */
#topbar{background:rgba(180,220,255,var(--water-fill));backdrop-filter:blur(var(--water-blur,18px)) saturate(130%);-webkit-backdrop-filter:blur(var(--water-blur,18px)) saturate(130%)}
.ddlist{background:rgba(190,225,255,calc(var(--water-fill)*2));backdrop-filter:blur(var(--water-blur,18px)) saturate(140%);-webkit-backdrop-filter:blur(var(--water-blur,18px)) saturate(140%)}
.mcard{background:rgba(185,222,255,calc(var(--water-fill)*0.8));backdrop-filter:blur(calc(var(--water-blur,18px)*0.6));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.6))}
.mbg{background:rgba(30,60,140,0.08);backdrop-filter:none;-webkit-backdrop-filter:none}
#bill-panel{background:rgba(180,220,255,var(--water-fill));backdrop-filter:blur(var(--water-blur,18px)) saturate(130%);-webkit-backdrop-filter:blur(var(--water-blur,18px)) saturate(130%)}
.nb-tabs{background:rgba(180,220,255,var(--water-fill));backdrop-filter:blur(var(--water-blur,18px)) saturate(130%);-webkit-backdrop-filter:blur(var(--water-blur,18px)) saturate(130%)}
.nb-content{background:rgba(180,220,255,var(--water-fill));backdrop-filter:blur(var(--water-blur,18px)) saturate(130%);-webkit-backdrop-filter:blur(var(--water-blur,18px)) saturate(130%)}
.tcard{background:rgba(200,228,255,calc(var(--water-fill)*1.4));backdrop-filter:blur(calc(var(--water-blur,18px)*0.7)) saturate(120%);-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.7)) saturate(120%)}
.mrow{background:rgba(200,228,255,calc(var(--water-fill)*1.2));backdrop-filter:blur(calc(var(--water-blur,18px)*0.5));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.5))}
.fi{background:rgba(220,240,255,calc(var(--water-fill)*2));backdrop-filter:blur(calc(var(--water-blur,18px)*0.6));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.6))}
.btn-g{background:rgba(200,225,255,calc(var(--water-fill)*1.5));backdrop-filter:blur(calc(var(--water-blur,18px)*0.5));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.5))}
.btn-p{background:rgba(100,160,255,calc(var(--water-fill)*2));backdrop-filter:blur(calc(var(--water-blur,18px)*0.5));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.5))}
.btn-a{background:rgba(80,210,150,calc(var(--water-fill)*2));backdrop-filter:blur(calc(var(--water-blur,18px)*0.5));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.5))}
.btn-d{background:rgba(240,80,110,calc(var(--water-fill)*2));backdrop-filter:blur(calc(var(--water-blur,18px)*0.5));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.5))}
.btn-checkout{background:rgba(0,200,130,calc(var(--water-fill)*1.2));backdrop-filter:blur(calc(var(--water-blur,18px)*0.5));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.5))}
.menu-btn{background:rgba(200,225,255,calc(var(--water-fill)*1.2));backdrop-filter:blur(calc(var(--water-blur,18px)*0.5));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.5))}
.hist-bar{background:rgba(180,220,255,var(--water-fill));backdrop-filter:blur(var(--water-blur,18px)) saturate(130%);-webkit-backdrop-filter:blur(var(--water-blur,18px)) saturate(130%)}
.holi{background:rgba(200,228,255,calc(var(--water-fill)*1.2));backdrop-filter:none;-webkit-backdrop-filter:none}
.sc{background:rgba(200,228,255,calc(var(--water-fill)*1.4));backdrop-filter:blur(calc(var(--water-blur,18px)*0.6));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.6))}
.chart-area{background:rgba(200,228,255,calc(var(--water-fill)*1.2));backdrop-filter:blur(calc(var(--water-blur,18px)*0.6));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.6))}
.glass-section{background:rgba(190,220,255,calc(var(--water-fill)*2));backdrop-filter:blur(calc(var(--water-blur,18px)*0.6));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.6))}
.glass-toggle{background:rgba(200,228,255,calc(var(--water-fill)*1.5));backdrop-filter:blur(calc(var(--water-blur,18px)*0.4));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.4))}
.login-card{background:rgba(185,222,255,calc(var(--water-fill)*1.2));backdrop-filter:blur(calc(var(--water-blur,18px)*1.8)) saturate(140%);-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*1.8)) saturate(140%)}
.tk{background:rgba(200,230,255,calc(var(--water-fill)*3));backdrop-filter:blur(calc(var(--water-blur,18px)*1.4));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*1.4))}
.otab{background:rgba(200,228,255,calc(var(--water-fill)*1.3));backdrop-filter:blur(calc(var(--water-blur,18px)*0.4));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.4))}
.otab.on{background:rgba(180,215,255,calc(var(--water-fill)*2.8));backdrop-filter:blur(calc(var(--water-blur,18px)*0.5));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.5))}
.hist-right{background:rgba(200,228,255,calc(var(--water-fill)*1.5));backdrop-filter:blur(calc(var(--water-blur,18px)*0.7));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.7))}
.holi-grp-hdr{background:rgba(225,238,255,0.96) !important;backdrop-filter:none !important;-webkit-backdrop-filter:none !important}
.mhdr{background:rgba(160,210,255,calc(var(--water-fill)*3));backdrop-filter:blur(calc(var(--water-blur,18px)*0.8));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.8))}
.mftr{background:rgba(175,215,255,calc(var(--water-fill)*1.2));backdrop-filter:blur(calc(var(--water-blur,18px)*0.7));-webkit-backdrop-filter:blur(calc(var(--water-blur,18px)*0.7))}

/* Water wobble on hover */
@keyframes waterWobble {
  0%, 100% { transform: translateY(0) scaleX(1) scaleY(1) }
  20% { transform: translateY(-2px) scaleX(.993) scaleY(1.007) }
  40% { transform: translateY(1.5px) scaleX(1.005) scaleY(.994) }
  60% { transform: translateY(-1px) scaleX(.998) scaleY(1.002) }
  80% { transform: translateY(.8px) scaleX(1.002) scaleY(.998) }
}
@keyframes waterShimmer {
  0%{left:-70%;opacity:0}15%{opacity:.55}85%{opacity:.45}100%{left:130%;opacity:0}
}
.tcard:hover{animation:waterWobble 2.6s ease-in-out infinite !important}
.nb-tab:hover{animation:waterWobble 2s ease-in-out infinite}
.menu-btn:hover{transform:translateY(-2px)}
.btn:active{animation:surfaceTension .42s var(--spring) !important}

/* ══════════════════════════════════════════════════════════════
   WATER REFRACTION — áp dụng ĐÚNG cách:
   - filter url() chỉ áp lên #bg-canvas (nền bị méo)
   - Buttons/elements trong suốt → nhìn qua thấy nền đã bị khúc xạ
   - Bản thân button KHÔNG bị méo: text, border, icon vẫn sắc nét
   ══════════════════════════════════════════════════════════════ */

/* Nền bị méo bởi nước — áp trong JS để có thể animate */
/* #bg-canvas { filter: url(#wf-bg); } — được set bởi JS */

/* Slider */
.go-labels{display:flex;justify-content:space-between;font-size:10px;color:var(--mu2);font-weight:600;padding:0 4px;margin-bottom:5px}
.go-value-badge{display:inline-flex;align-items:center;justify-content:center;min-width:44px;padding:3px 10px;background:rgba(47,122,255,.15);border:1px solid rgba(47,122,255,.28);border-radius:99px;font-size:11px;font-weight:800;color:var(--p);font-family:"Fira Code",monospace;box-shadow:inset 0 1px 0 rgba(255,255,255,.70);transition:all .22s var(--spring)}
input[type=range].go-slider{-webkit-appearance:none;appearance:none;width:100%;height:6px;border-radius:99px;outline:none;background:linear-gradient(90deg,rgba(47,122,255,.40) 0%,rgba(47,122,255,.40) var(--slider-pct,55%),rgba(180,200,255,.28) var(--slider-pct,55%),rgba(180,200,255,.28) 100%);border:1px solid rgba(180,210,255,.38);box-shadow:inset 0 1px 3px rgba(30,60,160,.10),0 1px 0 rgba(255,255,255,.80);cursor:pointer}
input[type=range].go-slider::-webkit-slider-thumb{-webkit-appearance:none;width:22px;height:22px;border-radius:50%;background:linear-gradient(145deg,rgba(255,255,255,.96),rgba(215,235,255,.92));border:1.5px solid rgba(47,122,255,.50);box-shadow:0 3px 12px rgba(47,122,255,.28),inset 0 1.5px 0 rgba(255,255,255,.90);cursor:pointer;transition:transform .2s var(--spring),box-shadow .2s var(--spring)}
input[type=range].go-slider::-webkit-slider-thumb:hover{transform:scale(1.15);box-shadow:0 0 0 6px rgba(47,122,255,.14),0 4px 16px rgba(47,122,255,.38),inset 0 1.5px 0 rgba(255,255,255,.90)}
input[type=range].go-slider::-webkit-slider-thumb:active{transform:scale(.92)}
input[type=range].go-slider::-moz-range-thumb{width:22px;height:22px;border-radius:50%;background:linear-gradient(145deg,rgba(255,255,255,.96),rgba(215,235,255,.92));border:1.5px solid rgba(47,122,255,.50);cursor:pointer}
.go-presets{display:flex;gap:6px;margin-top:10px}
.go-preset-btn{flex:1;padding:6px 4px;font-size:10px;font-weight:700;cursor:pointer;border:1px solid rgba(47,122,255,.22);border-radius:var(--r-sm);background:rgba(255,255,255,var(--go-element));color:var(--mu);letter-spacing:.3px;transition:all .22s var(--spring);box-shadow:inset 0 1px 0 rgba(255,255,255,.65)}
.go-preset-btn:hover{background:rgba(47,122,255,.14);color:var(--p);border-color:rgba(47,122,255,.35);transform:translateY(-2px)}
.go-preset-btn.active{background:rgba(47,122,255,.20);color:var(--p);border-color:rgba(47,122,255,.45);box-shadow:var(--glow-p),inset 0 1px 0 rgba(255,255,255,.70)}

/* ================================================================
   MOBILE RESPONSIVE (max-width: 768px)
   Desktop layout khong thay doi gi ca
   ================================================================ */
#mb-nav { display: none; }

@media (max-width: 768px) {

  /* Topbar */
  #topbar { height: 50px; padding: 0 12px; gap: 8px; }
  .brand { font-size: 13px !important; }
  .ttime { font-size: 10px !important; letter-spacing: 0 !important; }

  /* Main: 2 panel ngang, slide bang transform */
  #main { padding: 0; gap: 0; overflow: hidden; position: relative; }

  #pos-page {
    display: flex !important;
    flex-direction: row;
    width: 200%;
    height: 100%;
    transition: transform .30s cubic-bezier(.4,0,.2,1);
    will-change: transform;
  }
  #pos-page.mb-show-bill { transform: translateX(-50%); }

  #nb {
    flex: 0 0 50%;
    min-width: 0;
    height: 100%;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  #bill-panel {
    flex: 0 0 50%;
    min-width: 0;
    height: 100%;
    border-radius: 0;
    border-top: none;
    border-left: none;
    border-right: none;
    border-bottom: none;
  }

  /* Bottom nav bar */
  #mb-nav {
    display: flex;
    position: fixed;
    bottom: 0; left: 0; right: 0;
    height: 58px;
    z-index: 500;
    background: rgba(200,225,255,calc(var(--water-fill,0.06)*2));
    backdrop-filter: blur(var(--water-blur,18px)) saturate(160%);
    -webkit-backdrop-filter: blur(var(--water-blur,18px)) saturate(160%);
    border-top: 1px solid rgba(255,255,255,.65);
    box-shadow: 0 -4px 20px rgba(30,80,200,.10), inset 0 1px 0 rgba(255,255,255,.80);
  }
  .mb-btn {
    flex: 1;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    gap: 2px;
    background: none; border: none; cursor: pointer;
    font-size: 10px; font-weight: 700;
    color: rgba(60,100,180,.55);
    letter-spacing: .4px; text-transform: uppercase;
    position: relative;
    transition: color .2s;
  }
  .mb-btn .mb-ico { font-size: 19px; line-height: 1; transition: transform .2s; }
  .mb-btn.active { color: var(--p); }
  .mb-btn.active .mb-ico { transform: translateY(-2px); }
  .mb-btn::before {
    content: '';
    position: absolute; top: 0; left: 25%; right: 25%;
    height: 2.5px; border-radius: 0 0 3px 3px;
    background: var(--p); opacity: 0;
    transition: opacity .2s;
  }
  .mb-btn.active::before { opacity: 1; }

  /* Badge so mon tren nut bill */
  .mb-badge {
    display: none;
    position: absolute; top: 6px; right: calc(50% - 20px);
    background: var(--d); color: #fff;
    font-size: 9px; font-weight: 800;
    min-width: 15px; height: 15px;
    border-radius: 99px; padding: 0 3px;
    align-items: center; justify-content: center;
    border: 1.5px solid rgba(255,255,255,.9);
    box-shadow: 0 1px 4px rgba(220,50,50,.35);
  }
  .mb-badge.show { display: flex; }

  /* Padding bottom tranh bi nav che */
  .nb-content { overflow: hidden; }
  #tab-tables .table-area { padding-bottom: 72px; }
  #tab-menu .menu-items { padding-bottom: 72px; }
  .bill-body { padding-bottom: 4px; }
  .bill-btns { margin-bottom: 62px; }

  /* Tab nho hon */
  .nb-tab { padding: 11px 8px; font-size: 10px; }

  /* Lich su: stack doc */
  .hist-body { flex-direction: column; }
  .hist-left { border-right: none; border-bottom: 1px solid var(--edge-lo); max-height: 52vh; overflow-y: auto; }
  .hist-right { flex: 1; min-height: 180px; }

  /* Stat cards: 2 cot */
  .scs { grid-template-columns: repeat(2,1fr) !important; }

  /* Filter bar cuon ngang */
  .hist-bar {
    flex-wrap: nowrap !important;
    overflow-x: auto;
    gap: 7px !important;
    padding: 8px 10px !important;
    -webkit-overflow-scrolling: touch;
  }
  .hist-bar::-webkit-scrollbar { display: none; }
  .hrev-badge { display: none; }
}

"""
# Combine all CSS
CSS = CSS + CSS_LOADER
JS = r"""
// ── STATE ─────────────────────────────────────────────────────
const S={
  user:'',tables:[],menu:[],store:{},tabList:{},activeTab:{},
  curTable:null,history:[],selHist:-1,curCat:'Tất cả'
};
const fmt=n=>Number(n).toLocaleString('vi-VN')+'đ';
const fmtn=n=>Number(n).toLocaleString('vi-VN');
const $=id=>document.getElementById(id);
const qs=s=>document.querySelector(s);
const qsa=s=>document.querySelectorAll(s);

// ── TOAST ─────────────────────────────────────────────────────
function toast(m,t='info'){
  const d=document.createElement('div');
  d.className=`tk t${t==='success'?'ok':t==='error'?'er':'if'}`;
  d.textContent=m;$('ts').appendChild(d);setTimeout(()=>d.remove(),3000);}

// ── LOADER ────────────────────────────────────────────────────
function ld(on){$('ldr').classList.toggle('on',on);}

// ── API ───────────────────────────────────────────────────────
async function api(method,url,body){
  ld(true);
  try{
    const o={method,headers:{'Content-Type':'application/json'}};
    if(body) o.body=JSON.stringify(body);
    const r=await fetch(url,o),d=await r.json();
    if(!r.ok) throw new Error(d.error||'Lỗi server');
    return d;
  }finally{ld(false);}
}

// ── MODAL ─────────────────────────────────────────────────────
function modal(html){$('mc').innerHTML=`<div class="mbg" id="_am" onclick="if(event.target.id==='_am')cModal()">${html}</div>`;}
function cModal(){$('mc').innerHTML='';}

// ── LOGIN ─────────────────────────────────────────────────────
function swLTab(t){
  $('lf').style.display=t==='l'?'block':'none';
  $('rf').style.display=t==='r'?'block':'none';
  qsa('.ltab').forEach(b=>b.classList.toggle('on',b.dataset.t===t));}

async function doLogin(){
  const u=$('lu').value.trim(),p=$('lp2').value,e=$('le');e.style.display='none';
  if(!u||!p){e.textContent='Vui lòng nhập đầy đủ!';e.style.display='block';return;}
  try{const d=await api('POST','/api/login',{username:u,password:p});S.user=d.username;startApp();}
  catch(x){e.textContent=x.message;e.style.display='block';}}

async function doReg(){
  const u=$('ru').value.trim(),p=$('rp1').value,c=$('rp2').value,e=$('re');e.style.display='none';
  try{await api('POST','/api/register',{username:u,password:p,confirm:c});
    toast('Tạo tài khoản thành công!','success');$('lu').value=u;swLTab('l');}
  catch(x){e.textContent=x.message;e.style.display='block';}}

async function doLogout(){
  showConfirm('Bạn có chắc chắn muốn đăng xuất?', async () => {
    await api('POST','/api/logout');
    Object.assign(S,{user:'',tables:[],menu:[],store:{},tabList:{},activeTab:{},
      curTable:null,history:[],selHist:-1,curCat:'Tất cả'});
    $('login-page').style.display='flex';cModal();cDd();
  }, null, {okLabel:'Đăng xuất', okClass:'btn btn-d', icon:'🚪'});
}

// ── STARTUP ───────────────────────────────────────────────────
async function startApp(){
  $('login-page').style.display='none';
  $('brand').textContent=`🚀 POS Bán hàng | ${S.user}`;
  tick(); setInterval(tick,1000);
  await loadData();
  rTables(); rMenu(); showView('pos');
  S.history=await api('GET','/api/history');}

async function loadData(){
  const d=await api('GET','/api/data');
  S.tables=d.tables; S.menu=d.menu_items;
  S.store=d.table_orders_store||{};
  S.tabList=d.table_tab_list||{};
  S.activeTab=d.table_active_order||{};}

function tick(){
  const n=new Date();
  $('ttime').textContent=`⏰ ${n.toLocaleTimeString('vi-VN',{hour12:false})} | 🗓 ${n.toLocaleDateString('vi-VN')}`;}

// ── VIEW / NOTEBOOK ───────────────────────────────────────────
function showView(v){
  cDd();
  $('pos-page').classList.toggle('hide',v!=='pos');
  $('hist-page').classList.toggle('show',v==='hist');
  if(v==='hist') rHist();}

function showNbTab(t){
  $('tab-tables').classList.toggle('hide',t!=='tables');
  $('tab-menu').classList.toggle('show',t==='menu');
  qsa('.nb-tab').forEach(b=>b.classList.toggle('on',b.dataset.t===t));}

// ── DROPDOWN ──────────────────────────────────────────────────
function tDd(){qs('.ddlist').classList.toggle('open');}
function cDd(){qs('.ddlist').classList.remove('open');}
document.addEventListener('click',e=>{if(!e.target.closest('.menu-dd'))cDd();});

// ── TABLES ────────────────────────────────────────────────────
function rTables(){
  const g=$('tgrid');g.innerHTML='';
  S.tables.forEach(t=>{
    const st=S.store[t]||{};
    const busy=Object.values(st).some(a=>a.length>0);
    const tot=Object.values(st).reduce((s,a)=>s+a.reduce((x,i)=>x+i.price*(i.quantity||1),0),0);
    const sel=S.curTable===t;
    const d=document.createElement('div');
    d.className=`tcard${busy?' busy':''}${sel?' sel':''}`;
    d.innerHTML=`<div class="tname">${t}</div>
      <div class="tstat">${busy?'● Đang có khách':'○ Bàn trống'}</div>
      ${busy?`<div class="ttot">${fmt(tot)}</div>`:''}`;
    d.onclick=()=>selTable(t);g.appendChild(d);});}

async function selTable(n){
  if(S.curTable!==n){
    S.curTable=n;
    const tabs=(S.tabList[n]||[]).filter(id=>(S.store[n]||{})[id]!==undefined);
    if(!tabs.length){
      const d=await api('POST','/api/orders/new_tab',{table:n});
      S.store[n]=d.store;S.tabList[n]=d.tab_list;S.activeTab[n]=d.active;}
    rTables();}
  rBill();
  // Tự động chuyển sang tab Menu (y hệt bản gốc)
  showNbTab('menu');}

// ── MENU ──────────────────────────────────────────────────────
function rMenu(q=''){
  const g=$('mitems');g.innerHTML='';
  // Category filter
  const cats=['Tất cả',...new Set(S.menu.map(i=>i.category||'Khác').sort())];
  const cb=$('catbar');cb.innerHTML='';
  cats.forEach(c=>{
    const b=document.createElement('button');b.className='cbtn'+(c===S.curCat?' on':'');
    b.textContent=c.toUpperCase();b.onclick=()=>{S.curCat=c;rMenu(q);};cb.appendChild(b);});
  const lq=(q||'').toLowerCase();
  S.menu.forEach(item=>{
    const cat=item.category||'Khác';
    if(S.curCat!=='Tất cả'&&cat!==S.curCat) return;
    if(lq&&!item.name.toLowerCase().includes(lq)) return;
    const oos=item.stock!==null&&item.stock!==undefined&&item.stock<=0;
    const low=item.stock!==null&&item.stock!==undefined&&item.stock>0&&item.stock<5;
    const cat_icon=cat.includes('Cà phê')?'☕':cat.includes('Trà')?'🍵':cat.includes('Nước')?'🍹':'🍱';
    const d=document.createElement('div');
    d.className='mrow'+(oos?' oos':low?' low':'');
    d.innerHTML=`<span class="mname">${cat_icon} ${item.name}</span>
      <span class="mprice">${fmt(item.price)}</span>
      <span class="mstock">${item.stock===null||item.stock===undefined?'Vô hạn':
        oos?'⚠️ Hết hàng':low?`⚠️ Còn: ${item.stock}`:`Tồn: ${item.stock}`}</span>`;
    if(!oos) d.onclick=()=>addItem(item.name);
    g.appendChild(d);});}

// ── BILL ──────────────────────────────────────────────────────
function rBill(){
  // Table name label
  $('btablename').textContent=S.curTable?`📍 ĐƠN HÀNG: ${S.curTable.toUpperCase()}`:'CHƯA CHỌN BÀN';
  // Tab bar
  const tbr=$('tbar'),bw=$('billbody');
  if(!S.curTable){
    tbr.innerHTML='';bw.innerHTML='<div class="bill-empty"><span class="eico">🛒</span><p>Chưa có món nào được chọn</p></div>';
    $('btot').textContent='Tổng tiền: 0đ';return;}
  const tabs=(S.tabList[S.curTable]||[]).filter(id=>(S.store[S.curTable]||{})[id]!==undefined);
  const act=S.activeTab[S.curTable];
  tbr.innerHTML='';
  const MAX_VIS=5;
  const visible=tabs.slice(0,MAX_VIS);
  const hidden=tabs.slice(MAX_VIS);
  visible.forEach(tid=>{
    const d=document.createElement('div');d.className='otab'+(tid===act?' on':'');
    d.innerHTML=`<span>${tid}</span><button class="cx" onclick="cTab('${S.curTable}','${tid}',event)">×</button>`;
    (function(t,oid){d.onclick=e=>{if(!e.target.classList.contains('cx'))swTabUI(t,oid);};})(S.curTable,tid);
    tbr.appendChild(d);});
  if(hidden.length){
    const dd=document.createElement('button');dd.style.cssText='background:#64748b;color:#fff;border:none;border-radius:5px;font-size:11px;font-weight:700;padding:4px 0;cursor:pointer;flex-shrink:0;letter-spacing:.5px;width:40px;text-align:center';
    dd.textContent='•••';
    dd.onclick=e=>{
      e.stopPropagation();
      const ul=document.createElement('div');
      ul.style.cssText='position:fixed;background:linear-gradient(145deg,rgba(240,250,255,.90) 0%,rgba(225,242,255,.85) 100%);backdrop-filter:blur(40px) saturate(200%);border:1px solid rgba(200,225,255,.55);border-radius:14px;padding:8px;z-index:999;box-shadow:0 20px 60px rgba(20,50,160,.18),inset 0 1px 0 rgba(255,255,255,.75)';
      ul.style.top=(e.clientY+8)+'px';ul.style.left=(e.clientX-90)+'px';
      hidden.forEach(tid=>{const li=document.createElement('div');
        li.style.cssText='padding:9px 15px;font-size:12px;font-weight:700;cursor:pointer;border-radius:8px;color:var(--txt2);transition:all .2s ease';
        li.textContent=tid;li.onmouseenter=()=>{li.style.background='rgba(47,122,255,.12)';li.style.color='var(--p)';li.style.transform='translateX(3px)'};
        li.onmouseleave=()=>{li.style.background='';li.style.color='var(--txt2)';li.style.transform=''};
        li.onclick=()=>{
          ul.remove();
          // Hoán đổi: tab được chọn lên vị trí cuối của visible, tab cuối visible xuống đầu hidden
          const list=S.tabList[S.curTable];
          const fromIdx=list.indexOf(tid);
          const toIdx=MAX_VIS-1; // vị trí cuối của visible (index 4)
          if(fromIdx>toIdx){
            // swap trong mảng
            [list[fromIdx],list[toIdx]]=[list[toIdx],list[fromIdx]];
          }
          swTabUI(S.curTable,tid);
        };ul.appendChild(li);});
      document.body.appendChild(ul);
      setTimeout(()=>document.addEventListener('click',function rm(){ul.remove();document.removeEventListener('click',rm);},{once:true}),0);};
    tbr.appendChild(dd);}
  const pb=document.createElement('button');pb.className='tab-plus';pb.textContent='+';
  pb.title='Thêm đơn mới';pb.onclick=()=>newTab(S.curTable);tbr.appendChild(pb);
  // Bill items
  const items=(S.store[S.curTable]||{})[act]||[];
  if(!items.length){
    bw.innerHTML='<div class="bill-empty"><span class="eico">🛒</span><p>Chưa có món nào được chọn</p></div>';
    $('btot').textContent='Tổng tiền: 0đ';return;}
  bw.innerHTML='';
  // Header
  const hdr=document.createElement('div');
  hdr.style.cssText='padding:7px 12px 4px;font-size:10px;font-weight:800;color:#94a3b8;text-transform:uppercase;letter-spacing:.7px';
  hdr.textContent='Danh sách món';bw.appendChild(hdr);
  let total=0;
  items.forEach((item,idx)=>{
    const q=item.quantity||1,sub=item.price*q;total+=sub;
    const r=document.createElement('div');r.className='bitem';
    r.innerHTML=`<div class="binfo" onclick="editP('${item.name}')">
      <div class="bname">${item.name}</div>
      <div class="bdetail">${q} × ${fmtn(item.price)}đ = ${fmt(sub)}  ✎ Click để sửa giá</div>
    </div>
    <div class="bbtns">
      <button class="qbtn qp" onclick="addItem('${item.name}')">+</button>
      <button class="qbtn qm" onclick="remItem('${item.name}')">-</button>
    </div>`;
    bw.appendChild(r);});
  $('btot').textContent=`TỔNG CỘNG: ${fmt(total)}`;}

async function swTabUI(table,tid){
  await api('POST','/api/orders/switch_tab',{table,tab_id:tid});
  S.activeTab[table]=tid;rBill();}

async function newTab(table){
  const d=await api('POST','/api/orders/new_tab',{table});
  S.store[table]=d.store;S.tabList[table]=d.tab_list;S.activeTab[table]=d.active;
  rBill();rTables();}

async function cTab(table,tid,e){
  e.stopPropagation();
  const items=(S.store[table]||{})[tid]||[];
  if(items.length){
    showConfirm(
      `Tab "${tid}" đang có ${items.length} món.\n\nBạn muốn làm gì?`,
      async () => {
        // OK = xóa cả đơn
        const d=await api('POST','/api/orders/close_tab',{table,tab_id:tid});
        S.store[table]=d.store;S.tabList[table]=d.tab_list;S.activeTab[table]=d.active;
        rBill();rTables();
      },
      async () => {
        // Hủy = chỉ xóa món, giữ tab
        try{const d=await api('POST','/api/orders/clear_tab',{table,tab_id:tid});
          S.store[table]=d.store;rBill();rTables();}catch(x){toast(x.message,'error');}
      },
      {okLabel:'Xóa cả đơn', cancelLabel:'Chỉ xóa món', okClass:'btn btn-d', icon:'🗑️'}
    );
    return;
  }
  const d=await api('POST','/api/orders/close_tab',{table,tab_id:tid});
  S.store[table]=d.store;S.tabList[table]=d.tab_list;S.activeTab[table]=d.active;
  rBill();rTables();}

async function addItem(name){
  if(!S.curTable){toast('Vui lòng chọn bàn!','error');return;}
  const tid=S.activeTab[S.curTable];if(!tid){toast('Vui lòng tạo tab!','error');return;}
  try{const d=await api('POST','/api/orders/add_item',{table:S.curTable,tab_id:tid,name});
    S.store[S.curTable][tid]=d.items;S.menu=d.menu_items;rBill();rTables();rMenu();}
  catch(x){toast(x.message,'error');}}

async function remItem(name){
  const t=S.curTable,tid=S.activeTab[t];if(!t||!tid) return;
  const items=(S.store[t]||{})[tid]||[];
  const item=items.find(i=>i.name===name);
  const isLast=(items.length===1&&(item?.quantity||1)<=1);
  if(isLast){
    showConfirm(
      `Đây là món cuối trong đơn.\n\nBạn muốn làm gì?`,
      async () => {
        // Xóa cả đơn (tab)
        const d=await api('POST','/api/orders/close_tab',{table:t,tab_id:tid});
        S.store[t]=d.store;S.tabList[t]=d.tab_list;S.activeTab[t]=d.active;
        rBill();rTables();rMenu();
      },
      async () => {
        // Chỉ xóa món, giữ tab trống
        const d=await api('POST','/api/orders/remove_item',{table:t,tab_id:tid,name});
        S.store[t][tid]=d.items;S.menu=d.menu_items;rBill();rTables();rMenu();
      },
      {okLabel:'Xóa cả đơn', cancelLabel:'Chỉ xóa món', okClass:'btn btn-d', icon:'🗑️'}
    );
    return;}
  const d=await api('POST','/api/orders/remove_item',{table:t,tab_id:tid,name});
  S.store[t][tid]=d.items;S.menu=d.menu_items;rBill();rTables();rMenu();}

function editP(name){
  const t=S.curTable,tid=S.activeTab[t];if(!t||!tid) return;
  const item=(S.store[t][tid]||[]).find(i=>i.name===name);if(!item) return;
  modal(`<div class="mcard"><div class="mhdr"><span class="mttl">✎ Sửa giá: ${name}</span>
    <button class="mclose" onclick="cModal()">×</button></div>
    <div class="mbody"><div class="fg"><label class="fl">Giá mới (đ)</label>
      <input class="fi" id="ep" type="number" value="${item.price}" min="0"></div></div>
    <div class="mftr"><button class="btn btn-g" onclick="cModal()">Hủy</button>
      <button class="btn btn-p" onclick="applyP('${name}','${t}','${tid}')">Áp dụng</button></div></div>`);
  setTimeout(()=>{const ei=$('ep');if(ei){ei.focus();ei.select();}},80);}

async function applyP(name,table,tid){
  const p=parseInt($('ep').value);if(isNaN(p)||p<0){toast('Giá không hợp lệ','error');return;}
  const d=await api('POST','/api/orders/edit_price',{table,tab_id:tid,name,price:p});
  S.store[table][tid]=d.items;cModal();rBill();}

// ── TÁCH/GHÉP ─────────────────────────────────────────────────
function showMergeSplit(){
  const t=S.curTable;
  if(!t){toast('Vui lòng chọn bàn trước!','error');return;}
  const act=S.activeTab[t];
  const tabs=(S.tabList[t]||[]).filter(id=>(S.store[t]||{})[id]!==undefined);
  const items=(S.store[t]||{})[act]||[];
  const otherTabs=tabs.filter(id=>id!==act);

  // Items list for split
  let itemsHtml='';
  if(!items.length){
    itemsHtml=`<div style="text-align:center;padding:20px 0;color:var(--mu2);font-size:12px">Tab này chưa có món nào.</div>`;
  }else{
    itemsHtml=items.map(item=>{
      const k=item.name.replace(/[^a-zA-Z0-9]/g,'_');
      return `<div style="display:flex;align-items:center;gap:8px;padding:9px 0;border-bottom:1px solid rgba(47,122,255,.07)">
        <input type="checkbox" id="ms-chk-${k}" data-name="${item.name}" checked
          style="width:15px;height:15px;cursor:pointer;accent-color:var(--p);flex-shrink:0">
        <span style="flex:1;font-size:12px;font-weight:600;color:var(--txt)">${item.name}</span>
        <span style="font-size:10px;color:var(--mu);margin-right:2px">SL tách:</span>
        <input type="number" id="ms-qty-${k}" value="${item.quantity||1}" min="1" max="${item.quantity||1}"
          style="width:52px;padding:5px 7px;border:1px solid rgba(200,220,255,.55);border-radius:7px;
                 font-size:12px;text-align:center;background:rgba(255,255,255,.75);color:var(--txt);font-family:'Fira Code',monospace">
        <span style="font-size:10px;color:var(--mu2)">/ ${item.quantity||1}</span>
      </div>`;
    }).join('');
  }

  // Destination options
  let destOpts=`<option value="__new__">✨ Tạo tab mới</option>`;
  destOpts+=otherTabs.map(id=>{
    const cnt=(S.store[t][id]||[]).length;
    return `<option value="${id}">${id} (${cnt} món)</option>`;
  }).join('');

  // Merge section
  let mergeHtml='';
  if(!otherTabs.length){
    mergeHtml=`<div style="text-align:center;padding:24px 0;color:var(--mu2);font-size:12px">
      Không có tab khác để ghép.<br>Nhấn "+" để tạo tab mới trước.</div>`;
  }else{
    mergeHtml=otherTabs.map(id=>{
      const ti=S.store[t][id]||[];
      const tot=ti.reduce((s,i)=>s+i.price*(i.quantity||1),0);
      const preview=ti.slice(0,3).map(i=>`${i.name}×${i.quantity||1}`).join(', ')+(ti.length>3?'...':'');
      return `<div style="padding:12px 14px;border-radius:10px;border:1px solid var(--edge-lo);
                margin-bottom:8px;background:rgba(255,255,255,.52);transition:all .2s ease">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
          <span style="font-size:12px;font-weight:800;color:var(--p);font-family:'Fira Code',monospace">${id}</span>
          <span style="font-size:12px;font-weight:700;color:var(--a);font-family:'Fira Code',monospace">${fmt(tot)}</span>
        </div>
        <div style="font-size:11px;color:var(--mu);margin-bottom:9px;line-height:1.5">${preview||'Trống'}</div>
        <button class="btn btn-p btn-sm" style="width:100%;font-size:11px" onclick="doMergeTab('${t}','${id}','${act}')">
          🔗 Ghép tất cả vào tab <b>${act}</b>
        </button>
      </div>`;
    }).join('');
  }

  modal(`<div class="mcard" style="max-width:500px">
    <div class="mhdr">
      <span class="mttl">⇋ Tách / Ghép Đơn</span>
      <button class="mclose" onclick="cModal()">×</button>
    </div>
    <div class="mbody" style="padding:16px 18px">
      <div style="display:flex;gap:0;margin-bottom:16px;border:1px solid var(--edge-lo);
                  border-radius:10px;overflow:hidden">
        <button id="ms-btn-split" onclick="showMSTab('split')"
          style="flex:1;padding:10px 8px;border:none;font-size:11px;font-weight:700;cursor:pointer;letter-spacing:.3px;
                 background:linear-gradient(135deg,rgba(47,122,255,.20),rgba(129,140,248,.15));
                 color:var(--p);border-right:1px solid var(--edge-lo);transition:all .22s ease">
          ✂️ Tách Đơn</button>
        <button id="ms-btn-merge" onclick="showMSTab('merge')"
          style="flex:1;padding:10px 8px;border:none;font-size:11px;font-weight:700;cursor:pointer;letter-spacing:.3px;
                 background:rgba(255,255,255,.35);color:var(--mu);transition:all .22s ease">
          🔗 Ghép Đơn</button>
      </div>

      <div id="ms-split">
        <p style="font-size:11px;color:var(--mu);margin-bottom:10px;line-height:1.6;padding:8px 10px;
                  background:rgba(47,122,255,.06);border-radius:8px;border-left:3px solid rgba(47,122,255,.40)">
          Tab hiện tại: <b style="color:var(--p)">${act}</b> — Chọn món và số lượng muốn tách sang tab khác.</p>
        <div style="max-height:200px;overflow-y:auto;padding:0 2px;margin-bottom:12px">${itemsHtml}</div>
        <div style="display:flex;align-items:center;gap:10px;padding-top:10px;border-top:1px solid var(--edge-lo)">
          <label style="font-size:10px;font-weight:800;color:var(--mu2);text-transform:uppercase;
                         letter-spacing:.8px;white-space:nowrap">Đến tab:</label>
          <select id="ms-dest" class="fi" style="flex:1;padding:8px 10px;font-size:12px">
            ${destOpts}
          </select>
        </div>
        <div style="display:flex;gap:8px;margin-top:12px">
          <button class="btn btn-p" style="flex:1" onclick="doSplitItems('${t}','${act}')">✂️ Tách Ngay</button>
          <button class="btn btn-g" onclick="cModal()">Hủy</button>
        </div>
      </div>

      <div id="ms-merge" style="display:none">
        <p style="font-size:11px;color:var(--mu);margin-bottom:10px;line-height:1.6;padding:8px 10px;
                  background:rgba(0,176,116,.06);border-radius:8px;border-left:3px solid rgba(0,176,116,.40)">
          Ghép toàn bộ món của tab khác vào tab: <b style="color:var(--p)">${act}</b>. Tab nguồn sẽ bị đóng.</p>
        <div style="max-height:280px;overflow-y:auto">${mergeHtml}</div>
      </div>
    </div>
    <div class="mftr"><button class="btn btn-g" onclick="cModal()">Đóng</button></div>
  </div>`);}

function showMSTab(tab){
  const sp=document.getElementById('ms-split'),mg=document.getElementById('ms-merge');
  const bs=document.getElementById('ms-btn-split'),bm=document.getElementById('ms-btn-merge');
  if(sp) sp.style.display=tab==='split'?'block':'none';
  if(mg) mg.style.display=tab==='merge'?'block':'none';
  if(bs){bs.style.background=tab==='split'?'linear-gradient(135deg,rgba(47,122,255,.20),rgba(129,140,248,.15))':'rgba(255,255,255,.35)';
    bs.style.color=tab==='split'?'var(--p)':'var(--mu)';}
  if(bm){bm.style.background=tab==='merge'?'linear-gradient(135deg,rgba(0,176,116,.18),rgba(52,211,153,.14))':'rgba(255,255,255,.35)';
    bm.style.color=tab==='merge'?'var(--a)':'var(--mu)';}}

async function doSplitItems(table,fromTab){
  const destEl=document.getElementById('ms-dest');
  if(!destEl){toast('Lỗi giao diện','error');return;}
  const dest=destEl.value;
  const items=(S.store[table]||{})[fromTab]||[];
  const moveItems=[];
  for(const item of items){
    const k=item.name.replace(/[^a-zA-Z0-9]/g,'_');
    const chk=document.getElementById(`ms-chk-${k}`);
    const qtyEl=document.getElementById(`ms-qty-${k}`);
    if(chk&&chk.checked){
      const q=Math.min(parseInt(qtyEl?.value)||1,item.quantity||1);
      if(q>0) moveItems.push({name:item.name,quantity:q});}}
  if(!moveItems.length){toast('Vui lòng chọn ít nhất 1 món!','error');return;}
  let toTab=dest;
  if(dest==='__new__'){
    const nd=await api('POST','/api/orders/new_tab',{table});
    S.store[table]=nd.store;S.tabList[table]=nd.tab_list;
    toTab=nd.active;
    // keep fromTab as active
    await api('POST','/api/orders/switch_tab',{table,tab_id:fromTab});
    S.activeTab[table]=fromTab;}
  const d=await api('POST','/api/orders/move_items',{table,from_tab:fromTab,to_tab:toTab,items:moveItems});
  S.store[table]=d.store;cModal();rBill();rTables();
  toast(`Tách ${moveItems.length} món sang tab ${toTab} thành công!`,'success');}

async function doMergeTab(table,fromTab,toTab){
  showConfirm(
    `Ghép toàn bộ món từ tab "${fromTab}" vào tab "${toTab}"?\n\nTab "${fromTab}" sẽ bị đóng sau khi ghép.`,
    async () => {
      const d=await api('POST','/api/orders/merge_tab',{table,from_tab:fromTab,to_tab:toTab});
      S.store[table]=d.store;S.tabList[table]=d.tab_list;S.activeTab[table]=d.active;
      cModal();rBill();rTables();
      toast(`Ghép đơn từ ${fromTab} vào ${toTab} thành công!`,'success');
    }, null, {okLabel:'Ghép đơn', okClass:'btn btn-p', icon:'🔀'}
  );
}

// ── CHECKOUT ──────────────────────────────────────────────────
async function doCheckout(){
  const t=S.curTable,tid=S.activeTab[t];
  if(!t||!tid){toast('Vui lòng chọn bàn!','error');return;}
  const items=(S.store[t]||{})[tid]||[];if(!items.length){toast('Đơn hàng trống!','error');return;}
  // Lấy cài đặt để kiểm tra QR
  try{
    const s=await api('GET','/api/settings');
    if(s.use_qr_payment&&s.account_no){
      // Hiển thị QR trước — chưa hoàn tất đơn hàng
      const total=items.reduce((sum,i)=>sum+i.price*(i.quantity||1),0);
      showQRPreview(t,tid,total,s);
    } else {
      // Không dùng QR → thanh toán ngay
      await doConfirmCheckout(t,tid);
    }
  } catch(x){toast(x.message,'error');}}

function showQRPreview(table,tid,total,s){
  // Tạo mã đơn tạm để hiển thị trên QR (chưa lưu DB)
  const tempId=`${table}-${new Date().toLocaleTimeString('vi-VN',{hour12:false}).replace(/:/g,'')}`;
  const url=`https://img.vietqr.io/image/${s.bank_id}-${s.account_no}-compact2.png?amount=${total}&addInfo=${encodeURIComponent(tempId)}&accountName=${encodeURIComponent(s.account_name||'')}`;
  modal(`<div class="mcard" style="max-width:380px"><div class="mhdr">
    <span class="mttl">💳 Quét mã thanh toán</span>
    <button class="mclose" onclick="cModal()">×</button></div>
    <div class="mbody"><div class="qr-wrap">
      <img src="${url}" onerror="this.outerHTML='<p style=color:var(--d)>⚠️ Không tải được QR</p>'" alt="QR">
      <div class="qr-amt">${fmt(total)}</div>
      <div class="qr-info">Bàn: ${table}</div>
      ${s.account_name?`<div class="qr-info" style="margin-top:3px;font-weight:600">${s.account_name} — ${s.bank_id}</div>`:''}
      <div class="qr-warning">
        ⚠️ Nhấn <b>"Xác nhận"</b> sau khi khách đã chuyển khoản thành công
      </div>
    </div></div>
    <div class="mftr" style="justify-content:stretch;gap:10px">
      <button class="btn btn-g" style="flex:1" onclick="cModal()">✕ Hủy giao dịch</button>
      <button class="btn btn-a" style="flex:1" onclick="confirmQR('${table}','${tid}')">✅ Xác nhận đã nhận tiền</button>
    </div></div>`);
}

async function confirmQR(table,tid){
  cModal();
  await doConfirmCheckout(table,tid);}

async function doConfirmCheckout(table,tid){
  try{const d=await api('POST','/api/checkout',{table,tab_id:tid,discount:0});
    S.store[table]=d.store;S.tabList[table]=d.tab_list;S.activeTab[table]=d.active;S.history.push(d.record);
    await loadData();rTables();rBill();rMenu();
    showReceipt(d.record);}
  catch(x){toast(x.message,'error');}}

function showReceipt(rec){
  const rows=rec.items.map(i=>
    `<tr><td style="padding:8px 6px;font-size:13px;color:#0f172a">${i.name}</td>
     <td style="text-align:center;padding:8px 4px;font-size:13px;font-weight:700;color:#64748b">${i.quantity||1}</td>
     <td style="text-align:right;padding:8px 6px;font-size:13px;font-weight:700;color:#2563eb">${fmt(i.price*(i.quantity||1))}</td></tr>`).join('');
  modal(`<div class="mcard" style="max-width:380px"><div class="mhdr">
    <span class="mttl">🧾 Hoá đơn thanh toán</span>
    <button class="mclose" onclick="cModal()">×</button></div>
    <div class="mbody">
      <div style="display:flex;gap:10px;margin-bottom:14px">
        <div class="receipt-info-card">
          <div style="font-size:10px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.5px">Mã đơn</div>
          <div style="font-size:14px;font-weight:800;color:var(--txt);margin-top:2px">${rec.order_id}</div>
        </div>
        <div class="receipt-info-card" style="background:linear-gradient(145deg,rgba(195,248,225,.65) 0%,rgba(170,240,205,.55) 100%);border-color:rgba(130,220,175,.50)">
          <div style="font-size:10px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.5px">Bàn</div>
          <div style="font-size:14px;font-weight:800;color:var(--txt);margin-top:2px">${rec.table}</div>
        </div>
      </div>
      <div style="font-size:12px;color:var(--mu2);margin-bottom:12px;font-weight:500;padding:6px 10px;background:rgba(255,255,255,.45);border-radius:var(--r-xs);border:1px solid var(--edge-lo)">🕐 ${rec.date||''}</div>
      <table style="width:100%;border-collapse:collapse">
        <thead><tr style="background:rgba(220,235,255,.50);border-radius:6px;backdrop-filter:blur(10px)">
          <th style="text-align:left;padding:8px 6px;font-size:11px;font-weight:700;color:var(--mu);text-transform:uppercase;letter-spacing:.4px;border-bottom:1px solid var(--edge-lo)">Món</th>
          <th style="text-align:center;padding:8px 4px;font-size:11px;font-weight:700;color:var(--mu);text-transform:uppercase;border-bottom:1px solid var(--edge-lo)">SL</th>
          <th style="text-align:right;padding:8px 6px;font-size:11px;font-weight:700;color:var(--mu);text-transform:uppercase;border-bottom:1px solid var(--edge-lo)">Tiền</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="receipt-total-bar">
        <span style="font-size:13px;font-weight:700;color:var(--a)">TỔNG CỘNG</span>
        <span style="font-size:19px;font-weight:900;color:var(--a);letter-spacing:-.4px">${fmt(rec.total)}</span>
      </div>
    </div>
    <div class="mftr"><button class="btn btn-g btn-sm" onclick="cModal()">Đóng</button>
      <button class="btn btn-a" onclick="cModal();toast('✅ Thanh toán thành công!','success')">✅ Hoàn tất</button>
    </div></div>`);}

// ── HISTORY ───────────────────────────────────────────────────
let _chartType='line';
let _chartData={keys:[],vals:[],itemData:[]};
let _histMode='day';

function setHMode(m){
  _histMode=m;
  document.querySelectorAll('.hftab').forEach(b=>b.classList.toggle('on',b.dataset.v===m));
  rHist();}

function swChartType(t){
  _chartType=t;
  document.querySelectorAll('.cttab').forEach(b=>b.classList.toggle('on',b.dataset.ct===t));
  drawChart();}

function clearHFilt(){
  pickDP('hsd',''); pickDP('hed','');
  $('hsq').value='';
  if($('htbl'))$('htbl').value='';
  if($('hsrt'))$('hsrt').value='new';
  const lbl=document.getElementById('htbl-label'); if(lbl) lbl.textContent='Tất cả';
  const lbl2=document.getElementById('hsrt-label'); if(lbl2) lbl2.textContent='Mới nhất';
  rHist();}

function setQuickRange(days){
  if(days===0){
    pickDP('hsd',''); pickDP('hed','');
  } else {
    const end=new Date();
    const start=new Date(); start.setDate(start.getDate()-days+1);
    pickDP('hsd', start.toISOString().slice(0,10));
    pickDP('hed', end.toISOString().slice(0,10));
  }
  rHist();}

function rHistTableOpts(){
  const tbls=[...new Set(S.history.map(o=>o.table).filter(Boolean))].sort();
  const cur=$('htbl').value;
  $('htbl').innerHTML='<option value="">Tất cả</option>'+tbls.map(t=>`<option value="${t}">${t}</option>`).join('');
  $('htbl').value=cur;
  // Sync custom dropdown
  const drop=$('htbl-drop');
  if(drop){
    drop.innerHTML=[{v:'',l:'Tất cả'},...tbls.map(t=>({v:t,l:t}))]
      .map(o=>`<div class="hsel-opt${o.v===cur?' on':''}" data-v="${o.v}" onclick="pickHSel('htbl','${o.v}','${o.l||'Tất cả'}')">${o.l}</div>`)
      .join('');
    const lbl=$('htbl-label');
    if(lbl) lbl.textContent=cur||'Tất cả';
  }
}

function periodKey(dateStr,mode){
  const d=new Date((dateStr||'').slice(0,10));
  if(mode==='day')  return(dateStr||'').slice(0,10);
  if(mode==='week'){
    const jan1=new Date(d.getFullYear(),0,1);
    const w=Math.ceil((((d-jan1)/86400000)+jan1.getDay()+1)/7);
    return `${d.getFullYear()}-W${String(w).padStart(2,'0')}`;}
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;}

function periodLabel(key,mode){
  if(mode==='month'){
    const [y,m]=key.split('-');
    const mn=['','Tháng 1','Tháng 2','Tháng 3','Tháng 4','Tháng 5','Tháng 6','Tháng 7','Tháng 8','Tháng 9','Tháng 10','Tháng 11','Tháng 12'];
    return `${mn[+m]} / ${y}`;}
  if(mode==='week'){const [y,w]=key.split('-W');return `Tuần ${w} / ${y}`;}
  const d=new Date(key);
  if(isNaN(d))return key;
  return d.toLocaleDateString('vi-VN',{weekday:'short',day:'2-digit',month:'2-digit',year:'numeric'});}

function rHist(){
  rHistTableOpts();
  const mode=_histMode;
  const sd=$('hsd').value,ed=$('hed').value;
  const tbl=$('htbl').value,srt=$('hsrt').value;
  const q=($('hsq').value||'').toLowerCase().trim();
  const start=sd?new Date(sd):new Date(0);
  const end=ed?new Date(ed+'T23:59:59'):new Date('2999-12-31');

  let filt=S.history.filter(o=>{
    const d=new Date((o.date||'').slice(0,10));
    if(d<start||d>end)return false;
    if(tbl&&o.table!==tbl)return false;
    if(q){
      const hay=[o.order_id,o.table,...(o.items||[]).map(i=>i.name)].join(' ').toLowerCase();
      if(!hay.includes(q))return false;}
    return true;});

  if(srt==='new')     filt=[...filt].sort((a,b)=>new Date(b.date)-new Date(a.date));
  else if(srt==='old')filt=[...filt].sort((a,b)=>new Date(a.date)-new Date(b.date));
  else if(srt==='hi') filt=[...filt].sort((a,b)=>Number(b.total)-Number(a.total));
  else if(srt==='lo') filt=[...filt].sort((a,b)=>Number(a.total)-Number(b.total));

  const rev=filt.reduce((s,o)=>s+Number(o.total||0),0);
  const avg=filt.length?Math.round(rev/filt.length):0;

  $('hrev').innerHTML=filt.length
    ?`<span class="hrev-num">${fmt(rev)}</span><span class="hrev-ct">${filt.length} đơn</span>`
    :'<span class="hrev-none">Không có dữ liệu</span>';

  const half=filt.length>>1;
  const r1=filt.slice(-half).reduce((s,o)=>s+Number(o.total||0),0);
  const r0=filt.slice(0,half).reduce((s,o)=>s+Number(o.total||0),0);
  const trendPct=r0>0?Math.round((r1-r0)/r0*100):null;
  function trendBadge(pct){
    if(pct===null)return'<span class="sc-trend neu">— Chưa có dữ liệu</span>';
    if(pct>0) return`<span class="sc-trend up">▲ +${pct}% so với trước</span>`;
    if(pct<0) return`<span class="sc-trend dn">▼ ${pct}% so với trước</span>`;
    return'<span class="sc-trend neu">→ Không đổi</span>';}
  $('scs').innerHTML=`
    <div class="sc sc-rev"><span class="sc-ico">💰</span>
      <div class="scv">${fmtn(rev)}đ</div><div class="scl">Doanh thu</div>${trendBadge(trendPct)}</div>
    <div class="sc sc-ord"><span class="sc-ico">🧾</span>
      <div class="scv">${filt.length}</div><div class="scl">Đơn hàng</div>
      <span class="sc-trend neu">${sd||ed?'Trong kỳ lọc':'Tất cả thời gian'}</span></div>
    <div class="sc sc-avg"><span class="sc-ico">📊</span>
      <div class="scv">${fmtn(avg)}đ</div><div class="scl">Trung bình / Đơn</div>
      <span class="sc-trend neu">${tbl?'Bàn '+tbl:'Tất cả bàn'}</span></div>`;

  const grp={};
  filt.forEach(o=>{const k=periodKey(o.date,mode);grp[k]=(grp[k]||0)+Number(o.total||0);});
  const ckeys=Object.keys(grp).sort().slice(-16);
  const vals=ckeys.map(k=>grp[k]);
  const itemMap={};
  filt.forEach(o=>(o.items||[]).forEach(i=>{itemMap[i.name]=(itemMap[i.name]||0)+i.price*(i.quantity||1);}));
  const itemData=Object.entries(itemMap).sort((a,b)=>b[1]-a[1]).slice(0,6);
  _chartData={keys:ckeys,vals,itemData};
  $('ch-subtitle').textContent=ckeys.length?`(${periodLabel(ckeys[0],mode)} → ${periodLabel(ckeys[ckeys.length-1],mode)})`:'';
  drawChart();

  $('ol2-summary').innerHTML=filt.length
    ?`<div class="ol2-sum-txt">Hiển thị <b>${filt.length}</b> đơn · ${q?`Từ khoá "<b>${q}</b>" · `:''}${tbl?`Bàn <b>${tbl}</b> · `:''}${sd?`Từ <b>${sd}</b> `:''}${ed?`đến <b>${ed}</b> · `:''}Tổng <b>${fmt(rev)}</b></div>`
    :'<div class="ol2-sum-txt ol2-sum-empty">Không có đơn hàng phù hợp</div>';

  $('ol2').innerHTML='';
  if(!filt.length)return;

  const listGrp={},listGrpOrder=[];
  filt.forEach(o=>{
    const k=periodKey(o.date,mode);
    if(!listGrp[k]){listGrp[k]=[];listGrpOrder.push(k);}
    listGrp[k].push(o);});

  const orderedGrpKeys=(srt==='new'||srt==='hi')
    ?[...new Set(listGrpOrder)].sort((a,b)=>b.localeCompare(a))
    :[...new Set(listGrpOrder)].sort((a,b)=>a.localeCompare(b));

  orderedGrpKeys.forEach(gk=>{
    const orders=listGrp[gk];
    if(!orders)return;
    const gRev=orders.reduce((s,o)=>s+Number(o.total||0),0);
    const hdr=document.createElement('div');
    hdr.className='holi-grp-hdr';
    hdr.innerHTML=`<span class="holi-grp-label">${periodLabel(gk,mode)}</span><span class="holi-grp-meta">${orders.length} đơn · ${fmt(gRev)}</span>`;
    $('ol2').appendChild(hdr);
    orders.forEach(o=>{
      const i=S.history.indexOf(o);
      const el=document.createElement('div');
      el.className='holi'+(i===S.selHist?' on':'');
      el.innerHTML=`<div class="hold"><span class="hoid">#${o.order_id}</span><span class="hotot">${fmt(o.total)}</span></div>
        <div class="hometa">🪑 ${o.table} · ${o.date||''} · ${(o.items||[]).length} món${o.user?' · 👤 '+o.user:''}</div>`;
      el.onclick=()=>showOD(i);$('ol2').appendChild(el);});});}

// ── DRAW CHART ────────────────────────────────────────────────
function drawChart(){
  const {keys,vals,itemData}=_chartData;
  if(_chartType==='donut'){drawDonut(itemData);return;}
  if(!keys.length){
    $('ch-svg').innerHTML='<text x="50%" y="50%" text-anchor="middle" fill="rgba(90,114,168,.55)" font-size="13" font-family="Fira Code,monospace" dominant-baseline="middle">Không có dữ liệu</text>';
    $('ch-svg').style.height='180px';return;}
  if(_chartType==='line') drawLine(keys,vals);
  else drawBar(keys,vals);}

function _chartDefs(){
  return`<defs>
    <linearGradient id="lineGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#2f7aff"/>
      <stop offset="100%" stop-color="#818cf8"/>
    </linearGradient>
    <linearGradient id="areaGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#2f7aff" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="#818cf8" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="barGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#60b0ff"/>
      <stop offset="100%" stop-color="#2f7aff"/>
    </linearGradient>
  </defs>`;}

function drawLine(keys,vals){
  const W=document.getElementById('ch-wrap').clientWidth-28||520;
  const H=170,padL=52,padR=16,padT=14,padB=28;
  const cW=W-padL-padR,cH=H-padT-padB;
  const mx=Math.max(...vals,1);
  const pts=keys.map((k,i)=>[padL+i*(cW/(keys.length-1||1)),padT+cH-(vals[i]/mx)*cH]);
  // Smooth bezier
  function curve(p){
    if(p.length<2)return`M${p[0][0]},${p[0][1]}`;
    let d=`M${p[0][0]},${p[0][1]}`;
    for(let i=1;i<p.length;i++){
      const cx=(p[i-1][0]+p[i][0])/2;
      d+=` C${cx},${p[i-1][1]} ${cx},${p[i][1]} ${p[i][0]},${p[i][1]}`;}
    return d;}
  const linePath=curve(pts);
  const areaPath=linePath+` L${pts[pts.length-1][0]},${padT+cH} L${pts[0][0]},${padT+cH} Z`;
  // Y grid lines
  let grid='',yLabels='';
  [0,.25,.5,.75,1].forEach(t=>{
    const y=padT+cH-t*cH;
    grid+=`<line x1="${padL}" y1="${y}" x2="${padL+cW}" y2="${y}"/>`;
    yLabels+=`<text x="${padL-6}" y="${y}" text-anchor="end" dominant-baseline="middle">${fmtn(Math.round(mx*t))}</text>`;});
  // X labels
  let xLabels='';
  const step=Math.max(1,Math.floor(keys.length/7));
  keys.forEach((k,i)=>{
    if(i%step!==0&&i!==keys.length-1)return;
    xLabels+=`<text x="${pts[i][0]}" y="${padT+cH+16}" text-anchor="middle">${k.slice(-5)}</text>`;});
  // Dots with data attrs
  const dots=pts.map((p,i)=>`<circle class="ch-dot" cx="${p[0]}" cy="${p[1]}" r="4" data-i="${i}"/>`).join('');

  $('ch-svg').style.height=H+'px';
  $('ch-svg').innerHTML=`${_chartDefs()}
    <g class="ch-grid">${grid}</g>
    <g class="ch-axis">${yLabels}${xLabels}</g>
    <path class="ch-area-path" d="${areaPath}"/>
    <path class="ch-line-path" d="${linePath}"/>
    ${dots}`;

  // Attach tooltip
  $('ch-svg').querySelectorAll('.ch-dot').forEach(dot=>{
    dot.addEventListener('mouseenter',e=>{
      const i=+dot.dataset.i,tip=$('ch-tip');
      tip.innerHTML=`<div style="opacity:.7;font-size:10px;margin-bottom:2px">${keys[i]}</div><div style="font-size:14px">${fmt(vals[i])}</div>`;
      const wrap=$('ch-wrap'),wr=wrap.getBoundingClientRect(),dr=dot.getBoundingClientRect();
      tip.style.left=(dr.left-wr.left+dr.width/2)+'px';
      tip.style.top=(dr.top-wr.top-44)+'px';
      tip.classList.add('show');});
    dot.addEventListener('mouseleave',()=>$('ch-tip').classList.remove('show'));});}

function drawBar(keys,vals){
  const W=document.getElementById('ch-wrap').clientWidth-28||520;
  const H=170,padL=52,padR=16,padT=14,padB=28;
  const cW=W-padL-padR,cH=H-padT-padB;
  const mx=Math.max(...vals,1);
  const bW=Math.max(4,cW/keys.length-4);
  // Grid + Y labels
  let grid='',yLabels='';
  [0,.25,.5,.75,1].forEach(t=>{
    const y=padT+cH-t*cH;
    grid+=`<line x1="${padL}" y1="${y}" x2="${padL+cW}" y2="${y}"/>`;
    yLabels+=`<text x="${padL-6}" y="${y}" text-anchor="end" dominant-baseline="middle">${fmtn(Math.round(mx*t))}</text>`;});
  // Bars
  let bars='',xLabels='';
  const step=Math.max(1,Math.floor(keys.length/7));
  keys.forEach((k,i)=>{
    const bH=Math.max(2,(vals[i]/mx)*cH);
    const x=padL+i*(cW/keys.length)+(cW/keys.length-bW)/2;
    const y=padT+cH-bH;
    bars+=`<rect class="ch-bar" x="${x}" y="${y}" width="${bW}" height="${bH}" rx="4" fill="url(#barGrad)" data-i="${i}"/>`;
    if(i%step===0||i===keys.length-1)
      xLabels+=`<text x="${x+bW/2}" y="${padT+cH+16}" text-anchor="middle">${k.slice(-5)}</text>`;});

  $('ch-svg').style.height=H+'px';
  $('ch-svg').innerHTML=`${_chartDefs()}
    <g class="ch-grid">${grid}</g>
    <g class="ch-axis">${yLabels}${xLabels}</g>
    ${bars}`;

  $('ch-svg').querySelectorAll('.ch-bar').forEach(bar=>{
    bar.addEventListener('mouseenter',e=>{
      const i=+bar.dataset.i,tip=$('ch-tip');
      tip.innerHTML=`<div style="opacity:.7;font-size:10px;margin-bottom:2px">${keys[i]}</div><div style="font-size:14px">${fmt(vals[i])}</div>`;
      const wrap=$('ch-wrap'),wr=wrap.getBoundingClientRect(),br=bar.getBoundingClientRect();
      tip.style.left=(br.left-wr.left+br.width/2)+'px';
      tip.style.top=(br.top-wr.top-44)+'px';
      tip.classList.add('show');});
    bar.addEventListener('mouseleave',()=>$('ch-tip').classList.remove('show'));});}

function drawDonut(itemData){
  const svgWrap=$('ch-svg');
  svgWrap.style.height='0';
  if(!itemData.length){
    $('ch-wrap').innerHTML='<div style="text-align:center;padding:32px;color:#94a3b8;font-size:13px;font-weight:600">Không có dữ liệu</div>';
    return;}
  const COLORS=['#2f7aff','#00b074','#f07020','#f03060','#9b6dff','#00b8d4'];
  const total=itemData.reduce((s,[,v])=>s+v,0);
  const R=68,CX=80,CY=80,thick=26;
  let arcs='',startAngle=-Math.PI/2;
  itemData.forEach(([name,val],i)=>{
    const angle=(val/total)*2*Math.PI;
    const endAngle=startAngle+angle;
    const x1=CX+(R)*Math.cos(startAngle),y1=CY+(R)*Math.sin(startAngle);
    const x2=CX+(R)*Math.cos(endAngle),  y2=CY+(R)*Math.sin(endAngle);
    const xi=CX+(R-thick)*Math.cos(endAngle),  yi=CY+(R-thick)*Math.sin(endAngle);
    const xj=CX+(R-thick)*Math.cos(startAngle),yj=CY+(R-thick)*Math.sin(startAngle);
    const lg=angle>Math.PI?1:0;
    const pct=Math.round(val/total*100);
    arcs+=`<path d="M${x1},${y1} A${R},${R} 0 ${lg},1 ${x2},${y2} L${xi},${yi} A${R-thick},${R-thick} 0 ${lg},0 ${xj},${yj} Z"
      fill="${COLORS[i%COLORS.length]}" opacity=".92"
      style="cursor:pointer;transition:opacity .15s"
      onmouseenter="this.style.opacity=1;showDonutTip(event,'${name.replace(/'/g,"\\'")}','${pct}%','${fmtn(val)}đ')"
      onmouseleave="this.style.opacity=.92;$('ch-tip').classList.remove('show')"/>`;
    startAngle=endAngle;});
  const legend=itemData.map(([name,val],i)=>
    `<div class="d-leg-row">
      <div class="d-leg-dot" style="background:${COLORS[i%COLORS.length]}"></div>
      <span class="d-leg-name">${name}</span>
      <span class="d-leg-val">${Math.round(val/total*100)}%</span>
    </div>`).join('');

  $('ch-wrap').innerHTML=`
    <div id="ch-tip" class="ch-tip"></div>
    <div class="donut-wrap">
      <svg class="donut-svg" width="160" height="160" viewBox="0 0 160 160">
        ${arcs}
        <g class="donut-center">
          <text x="80" y="74" text-anchor="middle" font-size="11" fill="rgba(90,114,168,.60)" font-family="Inter,sans-serif" font-weight="600">Tổng</text>
          <text x="80" y="93" text-anchor="middle" font-size="13" fill="#0c1a3a" font-family="Inter,sans-serif" font-weight="800">${fmtn(total)}đ</text>
        </g>
      </svg>
      <div class="donut-legend">${legend}</div>
    </div>`;}

function showDonutTip(e,name,pct,val){
  const tip=$('ch-tip'),wrap=$('ch-wrap');
  tip.innerHTML=`<div style="font-size:11px;opacity:.7;margin-bottom:3px">${name}</div><div>${val} · <b>${pct}</b></div>`;
  const wr=wrap.getBoundingClientRect();
  tip.style.left=(e.clientX-wr.left)+'px';
  tip.style.top=(e.clientY-wr.top-54)+'px';
  tip.classList.add('show');}
function showOD(idx){
  S.selHist=idx;const o=S.history[idx];const p=$('od2');
  if(!o){p.innerHTML='';return;}
  const rows=(o.items||[]).map(i=>
    `<div class="odrow"><span>${i.name} ×${i.quantity||1}</span><span>${fmt(i.price*(i.quantity||1))}</span></div>`).join('');
  p.innerHTML=`<div class="od-wr"><div class="oh3">📋 Đơn #${o.order_id}</div>
    <div style="margin-bottom:12px;padding:9px 11px;background:linear-gradient(145deg,rgba(220,235,255,.65) 0%,rgba(200,222,255,.55) 100%);border-radius:var(--r-xs);font-size:12px;color:var(--mu);font-weight:500;border:1px solid rgba(180,210,255,.45);box-shadow:inset 0 1px 0 rgba(255,255,255,.70)">
      🪑 <b style="color:var(--txt2)">${o.table}</b> · 📅 ${o.date||''}${o.user?` · 👤 ${o.user}`:''}
    </div>${rows}
    <div class="odtot"><span>TỔNG CỘNG</span><span>${fmt(o.total)}</span></div>
    <div class="od-acts">
      <button class="btn btn-g btn-sm" onclick="editOH(${idx})">✏️ Sửa</button>
      <button class="btn btn-d btn-sm" onclick="delOH(${idx})">🗑 Xóa</button>
    </div></div>`;rHist();}

async function delOH(idx){
  showConfirm('Xóa đơn hàng này? Hành động này không thể hoàn tác.', async () => {
    await api('DELETE',`/api/history/${idx}`);S.history.splice(idx,1);S.selHist=-1;
    $('od2').innerHTML='';rHist();toast('Đã xóa!','success');
  }, null, {okLabel:'Xóa', okClass:'btn btn-d', icon:'🗑️'});}

async function editOH(idx){
  const o=S.history[idx];
  showConfirm(`Chuyển đơn về Bàn ${o.table} để sửa?\n\nĐơn sẽ được tạo lại và bạn có thể chỉnh sửa.`, async () => {
    await api('DELETE',`/api/history/${idx}`);S.history.splice(idx,1);
    const d=await api('POST','/api/orders/new_tab',{table:o.table});
    S.store[o.table]=d.store;S.tabList[o.table]=d.tab_list;S.activeTab[o.table]=d.active;
    for(const item of o.items)
      for(let q=0;q<(item.quantity||1);q++)
        try{await api('POST','/api/orders/add_item',{table:o.table,tab_id:d.active,name:item.name});}catch(e){}
    await loadData();S.curTable=o.table;rTables();rBill();showView('pos');showNbTab('menu');
    toast(`Đã chuyển đơn về Bàn ${o.table}!`,'success');
  }, null, {okLabel:'Chuyển về sửa', okClass:'btn btn-p', icon:'✏️'});}

// ── MANAGE TABLES ─────────────────────────────────────────────
function mTables(){cDd();rMTables();}
function rMTables(){
  const chips=S.tables.map(t=>`<span class="chip">${t}
    <button onclick="dTable('${t}')">×</button></span>`).join('');
  modal(`<div class="mcard"><div class="mhdr"><span class="mttl">🪑 Quản lý Bàn</span>
    <button class="mclose" onclick="cModal()">×</button></div>
    <div class="mbody">
      <div style="margin-bottom:14px;display:flex;flex-wrap:wrap">${chips||'<span style="color:var(--mu);font-size:11px">Chưa có bàn</span>'}</div>
      <div class="fg"><label class="fl">Thêm bàn mới</label>
        <div style="display:flex;gap:8px">
          <input class="fi" id="ntn" placeholder="Tên bàn (vd: C1)" style="flex:1">
          <button class="btn btn-a btn-sm" onclick="aTable()">+ Thêm</button></div></div></div>
    <div class="mftr"><button class="btn btn-g" onclick="cModal()">Đóng</button></div></div>`);
  setTimeout(()=>{const e=$('ntn');if(e)e.focus();},80);}

async function aTable(){
  const n=$('ntn').value.trim().toUpperCase();if(!n){toast('Nhập tên bàn!','error');return;}
  try{const d=await api('POST','/api/tables',{name:n});S.tables=d.tables;rTables();rMTables();
    toast(`Đã thêm Bàn ${n}`,'success');}catch(x){toast(x.message,'error');}}

async function dTable(n){
  showConfirm(`Xóa Bàn ${n}?\n\nHành động này không thể hoàn tác.`, async () => {
    try{await api('DELETE',`/api/tables/${n}`);await loadData();
      if(S.curTable===n)S.curTable=null;rTables();rBill();rMTables();
      toast(`Đã xóa Bàn ${n}`,'success');}catch(x){toast(x.message,'error');}
  }, null, {okLabel:'Xóa bàn', okClass:'btn btn-d', icon:'🪑'});}

// ── MANAGE MENU ───────────────────────────────────────────────
function mMenu(){cDd();rMMenu();}
function rMMenu(){
  const rows=S.menu.map((item,i)=>`
    <div class="man-row">
      <div class="man-info">
      <div class="man-name">${item.name} <span style="font-size:11px;color:var(--mu);font-weight:500;padding:2px 8px;background:rgba(47,122,255,.12);border-radius:99px;border:1px solid rgba(47,122,255,.22)">${item.category||'Khác'}</span></div>
        <div class="man-sub">${fmt(item.price)} · Vốn: ${fmt(item.cost_price||0)} · Tồn: ${item.stock??'Vô hạn'}</div>
      </div>
      <button class="btn btn-g btn-sm" onclick="eMI(${i})">✏️</button>
      <button class="btn btn-d btn-sm" onclick="dMI(${i})">🗑</button>
    </div>`).join('');
  modal(`<div class="mcard" style="max-width:520px"><div class="mhdr">
    <span class="mttl">🍔 Quản lý Thực Đơn</span>
    <button class="mclose" onclick="cModal()">×</button></div>
    <div class="mbody" style="max-height:50vh;overflow-y:auto">${rows||'<p style="color:var(--mu);font-size:11px">Chưa có món</p>'}</div>
    <div class="mftr"><button class="btn btn-g" onclick="cModal()">Đóng</button>
      <button class="btn btn-a" onclick="aMIF()">+ Thêm món</button></div></div>`);}

function miForm(item){
  return `
    <div class="fg"><label class="fl">Tên món</label>
      <input class="fi" id="mn" value="${item?.name||''}" placeholder="Tên món ăn"></div>
    <div class="fg"><label class="fl">Danh mục (Chuột phải để quản lý)</label>
      <input class="fi" id="mcat" value="${item?.category||'Khác'}" list="catlist">
      <datalist id="catlist">${[...new Set(S.menu.map(m=>m.category||'Khác'))].map(c=>`<option value="${c}">`).join('')}</datalist></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
      <div class="fg"><label class="fl">Giá bán (đ)</label>
        <input class="fi" id="mpr" type="number" value="${item?.price||''}" min="0" placeholder="0"></div>
      <div class="fg"><label class="fl">Giá vốn (đ)</label>
        <input class="fi" id="mcp" type="number" value="${item?.cost_price||''}" min="0" placeholder="0"></div>
    </div>
    <div class="fg"><label class="fl">Tồn kho (để trống = vô hạn)</label>
      <input class="fi" id="mst" type="number" value="${item?.stock??''}" min="0" placeholder="Để trống = không giới hạn"></div>`;}

function aMIF(){
  modal(`<div class="mcard"><div class="mhdr"><span class="mttl">+ Thêm món mới</span>
    <button class="mclose" onclick="mMenu()">×</button></div>
    <div class="mbody">${miForm()}</div>
    <div class="mftr"><button class="btn btn-g" onclick="mMenu()">Hủy</button>
      <button class="btn btn-a" onclick="sNMI()">Lưu</button></div></div>`);}

function eMI(i){
  const item=S.menu[i];
  modal(`<div class="mcard"><div class="mhdr"><span class="mttl">✏️ Sửa: ${item.name}</span>
    <button class="mclose" onclick="mMenu()">×</button></div>
    <div class="mbody">${miForm(item)}</div>
    <div class="mftr"><button class="btn btn-g" onclick="mMenu()">Hủy</button>
      <button class="btn btn-p" onclick="sEMI(${i})">Lưu</button></div></div>`);}

async function sNMI(){
  const st=$('mst').value;
  try{const d=await api('POST','/api/menu',{name:$('mn').value.trim(),price:+$('mpr').value||0,
    cost_price:+$('mcp').value||0,stock:st===''?null:+st,category:$('mcat').value.trim()||'Khác'});
    S.menu=d.menu_items;rMenu();mMenu();toast('Đã thêm món!','success');}
  catch(x){toast(x.message,'error');}}

async function sEMI(i){
  const st=$('mst').value;
  try{const d=await api('PUT',`/api/menu/${i}`,{name:$('mn').value.trim(),price:+$('mpr').value||0,
    cost_price:+$('mcp').value||0,stock:st===''?null:+st,category:$('mcat').value.trim()||'Khác'});
    S.menu=d.menu_items;rMenu();mMenu();toast('Đã cập nhật!','success');}
  catch(x){toast(x.message,'error');}}

async function dMI(i){
  showConfirm(`Xóa món "${S.menu[i].name}"?\n\nHành động này không thể hoàn tác.`, async () => {
    const d=await api('DELETE',`/api/menu/${i}`);S.menu=d.menu_items;rMenu();rMMenu();
    toast('Đã xóa!','success');
  }, null, {okLabel:'Xóa món', okClass:'btn btn-d', icon:'🍽️'});}

// ── SETTINGS ──────────────────────────────────────────────────
async function showSettings(){
  cDd();const s=await api('GET','/api/settings');
  const BANKS=[["MB Bank","MB"],["Vietcombank","VCB"],["Techcombank","TCB"],["ACB","ACB"],
    ["VPBank","VPB"],["TPBank","TPB"],["Sacombank","STB"],["Vietinbank","CTG"],["BIDV","BIDV"],
    ["Agribank","VBA"],["OCB","OCB"],["SHB","SHB"],["HDBank","HDB"],["MSB","MSB"],
    ["SeABank","SEAB"],["VIB","VIB"],["Eximbank","EIB"],["NamABank","NAB"],["NCB","NVB"],["BacABank","BAB"]];
  const bOpts=BANKS.map(([n,id])=>`<option value="${id}"${s.bank_id===id?' selected':''}>${n}</option>`).join('');
  let emailTags=[...((s.email_recipient||'').split(',').filter(e=>e.trim()))];
  const renderChips=()=>{
    const c=$('echips');if(!c)return;
    c.innerHTML=emailTags.map((em,i)=>
      `<span class="chip">${em}<button onclick="emailTags.splice(${i},1);renderChips()">×</button></span>`).join('');};
  modal(`<div class="mcard" style="max-width:500px"><div class="mhdr">
    <span class="mttl">⚙️ Cài đặt hệ thống</span>
    <button class="mclose" onclick="cModal()">×</button></div>
    <div class="mbody" style="max-height:68vh;overflow-y:auto">
      <!-- Glass Opacity Card -->
      <div class="glass-section">
        <div class="glass-section-title">&#127759; &#x1F5BC; Độ trong suốt giao diện</div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
          <span style="font-size:12px;color:var(--mu);font-weight:600">Mức trong suốt</span>
          <span class="go-value-badge" id="go-badge">0%</span>
        </div>
        <div class="go-labels"><span>🌊 Sóng mạnh</span><span>🫧 Phẳng lặng</span></div>
        <input type="range" class="go-slider" id="go-range" min="0" max="100" value="55"
          oninput="applyGlassOpacity(this.value);updateSliderUI(this.value)">
        <div class="go-presets">
          <button class="go-preset-btn" data-v="10" onclick="applyGlassOpacity(10);updateSliderUI(10)">💎 Tĩnh</button><button class="go-preset-btn" data-v="30" onclick="applyGlassOpacity(30);updateSliderUI(30)">🫧 Gợn</button>
          <button class="go-preset-btn" data-v="55" onclick="applyGlassOpacity(55);updateSliderUI(55)">🌊 Sóng</button>
          <button class="go-preset-btn" data-v="55" onclick="applyGlassOpacity(55);updateSliderUI(55)">🌊 Mặc định</button>
          <button class="go-preset-btn" data-v="72" onclick="applyGlassOpacity(72);updateSliderUI(72)">🔷 Solid</button>
          <button class="go-preset-btn" data-v="88" onclick="applyGlassOpacity(88);updateSliderUI(88)">🧱 Đặc</button>
        </div>
        <div style="font-size:11px;font-style:italic;color:var(--mu2);margin-top:10px">* Cài đặt lưu tự động vào trình duyệt.</div>
      </div>
      <!-- QR Card -->
      <div class="glass-section">
        <div class="glass-section-title">⚡ Cấu hình thanh toán</div>
        <label class="glass-toggle">
          <input type="checkbox" id="sqr"${s.use_qr_payment?' checked':''}>
          Sử dụng thanh toán mã QR</label>
        <div style="font-size:12px;font-style:italic;color:var(--mu2);margin-bottom:14px;padding-left:2px">
          * Nếu tắt, nhấn 'Thanh toán' sẽ hoàn tất đơn ngay lập tức.</div>
        <div style="background:linear-gradient(145deg,rgba(215,235,255,.65) 0%,rgba(195,222,255,.55) 100%);backdrop-filter:blur(15px);border-radius:var(--r-sm);padding:14px;border:1px solid rgba(150,200,255,.40);box-shadow:inset 0 1px 0 rgba(255,255,255,.70)">
          <div style="font-size:11px;font-weight:800;color:var(--p);margin-bottom:10px;text-transform:uppercase;letter-spacing:.5px">🏦 Tài khoản nhận tiền</div>
          <div class="fg"><label class="fl">Ngân hàng</label>
            <select class="fi" id="sbk">${bOpts}</select></div>
          <div class="fg"><label class="fl">Số tài khoản (STK)</label>
            <input class="fi" id="sac" value="${s.account_no||''}" placeholder="Số tài khoản"></div>
          <div class="fg"><label class="fl">Tên chủ tài khoản</label>
            <input class="fi" id="san" value="${s.account_name||''}" placeholder="Tên chủ tài khoản"></div>
          <div style="font-size:12px;font-style:italic;color:var(--mu2);margin-top:2px">* Dùng để tạo mã QR VietQR tự động.</div>
        </div>
      </div>
      <!-- Email Card -->
      <div class="glass-section">
        <div class="glass-section-title">📧 Gửi hoá đơn qua email</div>
        <label class="glass-toggle">
          <input type="checkbox" id="sem"${s.email_enabled?' checked':''}>
          Tự động gửi hoá đơn sau mỗi lần thanh toán</label>
        <div style="font-size:12px;font-weight:600;color:var(--txt2);margin-bottom:8px">Email nhận hoá đơn:</div>
        <div id="echips" style="margin-bottom:8px;min-height:30px;padding:6px;background:rgba(255,255,255,.55);border:1px solid var(--edge-lo);border-radius:var(--r-xs);backdrop-filter:blur(10px);box-shadow:inset 0 1px 0 rgba(255,255,255,.75)"></div>
        <div style="display:flex;gap:8px">
          <input class="fi" id="nemail" type="email" placeholder="Thêm email..." style="flex:1">
          <button class="btn btn-p btn-sm" onclick="(function(){const v=$('nemail').value.trim();if(v&&v.includes('@')&&!emailTags.includes(v)){emailTags.push(v);$('nemail').value='';renderChips();}})()">＋ Thêm</button>
        </div>
        <div style="font-size:12px;font-style:italic;color:var(--mu2);margin-top:8px">* Hệ thống sẽ tự động gửi hoá đơn qua email sau mỗi giao dịch.</div>
      </div>
    </div>
    <div style="height:1px;background:var(--edge-lo);margin:0 0 0"></div>
    <div class="mftr"><button class="btn btn-g" onclick="cModal()">Hủy</button>
      <button class="btn btn-p" onclick="saveSets()">Áp dụng</button></div></div>`);
  setTimeout(()=>{
    renderChips();
    // Sync slider to current saved opacity
    try {
      const sv = +(localStorage.getItem('glass_opacity') || 55);
      const sl = document.getElementById('go-range');
      if(sl) { sl.value = sv; updateSliderUI(sv); }
    } catch(e){}
  }, 50);
  window.emailTags=emailTags;window.renderChips=renderChips;}

async function saveSets(){
  try{await api('POST','/api/settings',{use_qr_payment:$('sqr').checked,bank_id:$('sbk').value,
    account_no:$('sac').value.trim(),account_name:$('san').value.trim(),
    email_enabled:$('sem').checked,email_recipient:window.emailTags.join(',')});
    cModal();toast('Đã lưu cài đặt!','success');}catch(x){toast(x.message,'error');}}

// ── LIQUID GLASS RIPPLE ENGINE ─────────────────────────────
(function(){
  // Inject ripple on all buttons
  function addRipple(e){
    const btn=e.currentTarget;
    const rect=btn.getBoundingClientRect();
    const size=Math.max(rect.width,rect.height)*1.6;
    const x=e.clientX-rect.left-size/2;
    const y=e.clientY-rect.top-size/2;
    const rip=document.createElement('span');
    rip.className='btn-ripple';
    rip.style.cssText=`width:${size}px;height:${size}px;left:${x}px;top:${y}px;`;
    btn.appendChild(rip);
    rip.addEventListener('animationend',()=>rip.remove());
  }
  // Observe DOM for new buttons
  const obs=new MutationObserver(muts=>{
    muts.forEach(m=>{
      m.addedNodes.forEach(node=>{
        if(node.nodeType!==1)return;
        const btns=node.matches?.('button')?[node]:[];
        const nested=[...btns,...node.querySelectorAll?.('button')||[]];
        nested.forEach(b=>{
          if(!b._liqRipple){
            b.addEventListener('click',addRipple);
            b._liqRipple=true;
          }
        });
      });
    });
  });
  obs.observe(document.body,{childList:true,subtree:true});
  // Initial buttons
  document.querySelectorAll('button').forEach(b=>{
    if(!b._liqRipple){b.addEventListener('click',addRipple);b._liqRipple=true;}
  });
})();

// ── LIQUID MORPHING ON INTERACTIVE ELEMENTS ─────────────────
// Add subtle continuous morphing to active table card
function addLiquidMorph(el){
  if(!el||el._morphing)return;
  el._morphing=true;
  const keyframes=[
    {borderRadius:'17px 14px 16px 15px / 15px 17px 14px 16px'},
    {borderRadius:'14px 17px 15px 16px / 16px 14px 17px 15px'},
    {borderRadius:'16px 15px 17px 14px / 14px 16px 15px 17px'},
    {borderRadius:'15px 16px 14px 17px / 17px 15px 16px 14px'},
    {borderRadius:'17px 14px 16px 15px / 15px 17px 14px 16px'},
  ];
  el._anim=el.animate(keyframes,{duration:6000,iterations:Infinity,easing:'ease-in-out'});
}
function removeLiquidMorph(el){
  if(!el)return;
  el._morphing=false;
  if(el._anim){el._anim.cancel();delete el._anim;}
}


// ── WATER INTENSITY SYSTEM ─────────────────────────────────────
// Slider điều khiển mức độ khúc xạ (distortion) của nền
// 0% = nước phẳng lặng (ít méo), 100% = sóng nước mạnh (méo nhiều)
const GO_PRESETS = {crystal:10, airy:30, default:55, solid:75, opaque:95};

function applyGlassOpacity(v) {
  v = Math.max(0, Math.min(100, +v));
  const m = v / 100;  // 0 = calm water, 1 = strong waves

  const r = document.documentElement.style;

  // ── Water fill — giọt nước rất trong, chỉ ánh xanh nhẹ ──
  // Ít thay đổi theo slider, nước luôn trong
  const fill = (0.04 + m * 0.08).toFixed(3);

  // ── Không còn blur — nước không làm mờ ──
  const blur = '0px';

  // ── Specular highlight mạnh hơn khi nước đậm hơn ──
  const specAlpha = (0.88 + m * 0.09).toFixed(2);
  const spec = 'rgba(255,255,255,' + specAlpha + ')';

  // ── Edge: viền mặt nước ──
  const edgeAlpha = (0.60 + m * 0.25).toFixed(2);
  const edge = 'rgba(255,255,255,' + edgeAlpha + ')';

  // ── Cập nhật CSS variables ──
  r.setProperty('--water-fill',  fill);
  r.setProperty('--water-inner', (parseFloat(fill) * 1.5).toFixed(3));
  r.setProperty('--lg-fill',     fill);
  r.setProperty('--lg-blur',     blur);
  r.setProperty('--lg-spec',     spec);
  r.setProperty('--lg-edge',     edge);
  r.setProperty('--go-blur',     blur);
  r.setProperty('--go-surface',  fill);
  r.setProperty('--go-element',  fill);
  r.setProperty('--go-modal',    (parseFloat(fill) * 1.8).toFixed(3));
  r.setProperty('--go-overlay',  (parseFloat(fill) * 0.6).toFixed(3));

  // ── Blur — 0%: 12px   100%: 28px
  const waterBlur = (12 + m * 16).toFixed(1) + 'px';
  r.setProperty('--water-blur', waterBlur);

  // ── Cập nhật water intensity cho engine ──
  window._waterIntensity = m;

  try { localStorage.setItem('glass_opacity', v); } catch(e) {}
  const sl = document.getElementById('go-range');
  if (sl && +sl.value !== v) sl.value = v;
  updateSliderUI(v);
}

function updateSliderUI(v) {
  const sl = document.getElementById('go-range');
  const badge = document.getElementById('go-badge');
  if (sl) {
    sl.style.setProperty('--slider-pct', ((v-sl.min)/(sl.max-sl.min)*100).toFixed(1) + '%');
  }
  if (badge) badge.textContent = v + '%';
  // Highlight active preset button
  document.querySelectorAll('.go-preset-btn').forEach(b => {
    b.classList.toggle('active', +b.dataset.v === v);
  });
}

function loadSavedOpacity() {
  try { localStorage.removeItem('glass_opacity'); } catch(e){}
  applyGlassOpacity(55); // default: medium water intensity, clearly visible distortion
}

async function init(){
  const s=await fetch('/api/session').then(r=>r.json());
  if(s.logged_in){S.user=s.username;await startApp();}
  $('lp2').onkeydown=e=>{if(e.key==='Enter')doLogin();};
  $('lu').onkeydown=e=>{if(e.key==='Enter')$('lp2').focus();};
  window.showDonutTip=showDonutTip;
  window.addEventListener('resize',()=>{if($('hist-page').classList.contains('show'))drawChart();});
  loadSavedOpacity();}

// ── PORTAL DROPDOWN SYSTEM ─────────────────────────────────
// Render tất cả popup vào #hpop-portal với position:fixed
// → thoát hoàn toàn khỏi overflow:hidden của các container cha

const DP = {};
const DP_MONTHS = ['Tháng 1','Tháng 2','Tháng 3','Tháng 4','Tháng 5','Tháng 6',
                   'Tháng 7','Tháng 8','Tháng 9','Tháng 10','Tháng 11','Tháng 12'];
const DP_DAYS   = ['H','B','T','N','S','B','C'];

let _popCurrent = null; // id của popup đang mở
let _popCloseHandler = null;

function getPortal() {
  // Không dùng wrapper — append thẳng vào body để backdrop-filter hoạt động
  return document.body;
}

function positionPop(drop, btn) {
  const r = btn.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight;
  // Body overflow:hidden → scrollY luôn = 0
  // Dùng absolute trong body → tọa độ = getBoundingClientRect()
  drop.style.visibility = 'hidden';
  drop.style.display = 'block';
  const dw = drop.offsetWidth, dh = drop.offsetHeight;
  drop.style.visibility = '';
  let left = r.left;
  if (left + dw > vw - 8) left = vw - dw - 8;
  let top = r.bottom + 6;
  if (top + dh > vh - 8) top = r.top - dh - 6;
  drop.style.left = left + 'px';
  drop.style.top  = top  + 'px';
}

function closeAllPops() {
  // Xóa tất cả popup đang mở khỏi body
  document.querySelectorAll('.hsel-drop.pop-open, .hdp-drop.pop-open').forEach(el => el.remove());
  _popCurrent = null;
  if (_popCloseHandler) {
    document.removeEventListener('mousedown', _popCloseHandler, true);
    _popCloseHandler = null;
  }
  document.querySelectorAll('.hsel-arrow').forEach(a => a.style.transform = '');
}

function openPop(id, dropEl) {
  closeAllPops();
  const btn = document.getElementById(id + '-btn');
  if (!btn) return;
  const portal = getPortal();
  portal.appendChild(dropEl);
  dropEl.classList.add('pop-open');
  positionPop(dropEl, btn);
  _popCurrent = id;
  // Mũi tên xoay
  const arrow = btn.querySelector('.hsel-arrow');
  if (arrow) arrow.style.transform = 'rotate(180deg)';
  // Đóng khi click ngoài
  setTimeout(() => {
    _popCloseHandler = (e) => {
      if (!dropEl.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
        closeAllPops();
      }
    };
    document.addEventListener('mousedown', _popCloseHandler, true);
  }, 0);
}

// ── DATE PICKER ────────────────────────────────────────────
function toggleDP(id) {
  if (_popCurrent === id) { closeAllPops(); return; }
  const curVal = document.getElementById(id)?.value;
  const base = curVal ? new Date(curVal) : new Date();
  DP[id] = { year: base.getFullYear(), month: base.getMonth() };
  const drop = buildDPDrop(id);
  openPop(id, drop);
}

function buildDPDrop(id) {
  const drop = document.createElement('div');
  drop.className = 'hdp-drop';
  drop.id = id + '-drop';
  renderDP(id, drop);
  return drop;
}

function renderDP(id, dropEl) {
  const drop = dropEl || document.getElementById(id + '-drop');
  if (!drop) return;
  const { year, month } = DP[id];
  const today = new Date();
  const selVal = document.getElementById(id)?.value;
  const selD = selVal ? new Date(selVal + 'T00:00:00') : null;

  const first = new Date(year, month, 1);
  let startDow = first.getDay();
  startDow = (startDow === 0) ? 6 : startDow - 1;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const daysInPrev  = new Date(year, month, 0).getDate();

  let daysHtml = '';
  for (let i = startDow - 1; i >= 0; i--)
    daysHtml += `<div class="hdp-day hdp-other">${daysInPrev - i}</div>`;
  for (let d = 1; d <= daysInMonth; d++) {
    const iso = `${year}-${String(month+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const isTod = (d===today.getDate() && month===today.getMonth() && year===today.getFullYear());
    const isSel = selD && (d===selD.getDate() && month===selD.getMonth() && year===selD.getFullYear());
    const cls = ['hdp-day', isTod?'hdp-today':'', isSel?'hdp-sel':''].filter(Boolean).join(' ');
    daysHtml += `<div class="${cls}" onclick="pickDP('${id}','${iso}')">${d}</div>`;
  }
  const total = startDow + daysInMonth;
  const rem = total % 7 === 0 ? 0 : 7 - (total % 7);
  for (let d = 1; d <= rem; d++)
    daysHtml += `<div class="hdp-day hdp-other">${d}</div>`;

  const todayIso = today.toISOString().slice(0, 10);
  drop.innerHTML = `
    <div style="position:absolute;top:0;left:0;right:0;height:2px;
      background:linear-gradient(90deg,transparent,#a5c8ff 20%,#c4b5fd 50%,#67e8f9 70%,transparent);
      background-size:200%;animation:prism 4s linear infinite;border-radius:16px 16px 0 0"></div>
    <div class="hdp-head">
      <span class="hdp-month-label">${DP_MONTHS[month]} ${year}</span>
      <div class="hdp-nav">
        <button onclick="navDP('${id}',-1)">‹</button>
        <button onclick="navDP('${id}',1)">›</button>
      </div>
    </div>
    <div class="hdp-dow">${DP_DAYS.map(d=>`<span>${d}</span>`).join('')}</div>
    <div class="hdp-days">${daysHtml}</div>
    <div class="hdp-foot">
      <button class="hdp-clear" onclick="pickDP('${id}','')">Xóa</button>
      <button class="hdp-today-btn" onclick="pickDP('${id}','${todayIso}')">Hôm nay</button>
    </div>`;
}

function navDP(id, delta) {
  if (!DP[id]) return;
  DP[id].month += delta;
  if (DP[id].month > 11) { DP[id].month = 0; DP[id].year++; }
  if (DP[id].month < 0)  { DP[id].month = 11; DP[id].year--; }
  const drop = document.getElementById(id + '-drop');
  if (drop) renderDP(id, drop);
  // Re-position sau khi nội dung thay đổi
  const btn = document.getElementById(id + '-btn');
  if (drop && btn) positionPop(drop, btn);
}

function pickDP(id, iso) {
  const inp = document.getElementById(id);
  const lbl = document.getElementById(id + '-label');
  if (!inp) return;
  inp.value = iso;
  if (lbl) lbl.textContent = iso ? (() => { const [y,m,d]=iso.split('-'); return `${d}/${m}/${y}`; })() : 'dd/mm/yyyy';
  rHist();
  if (iso) closeAllPops();
  else {
    const drop = document.getElementById(id + '-drop');
    if (drop && DP[id]) renderDP(id, drop);
  }
}

// ── CUSTOM SELECT ──────────────────────────────────────────
function toggleHSel(id) {
  if (_popCurrent === id) { closeAllPops(); return; }
  const drop = document.createElement('div');
  drop.className = 'hsel-drop';
  drop.id = id + '-drop';
  // Điền options
  const sel = document.getElementById(id);
  let html = '';
  if (id === 'htbl') {
    // Options được set bởi rHistTableOpts
    const cur = sel ? sel.value : '';
    const opts = sel ? Array.from(sel.options) : [];
    opts.forEach(o => {
      html += `<div class="hsel-opt${o.value===cur?' on':''}" data-v="${o.value}" onclick="pickHSel('${id}','${o.value}','${o.text}')">${o.text}</div>`;
    });
  } else {
    const cur = sel ? sel.value : '';
    const opts = sel ? Array.from(sel.options) : [];
    opts.forEach(o => {
      html += `<div class="hsel-opt${o.value===cur?' on':''}" data-v="${o.value}" onclick="pickHSel('${id}','${o.value}','${o.text}')">${o.text}</div>`;
    });
  }
  drop.innerHTML = html;
  openPop(id, drop);
}

function pickHSel(id, val, label) {
  const lbl = document.getElementById(id + '-label');
  if (lbl) lbl.textContent = label;
  const sel = document.getElementById(id);
  if (sel) { sel.value = val; sel.dispatchEvent(new Event('change')); }
  closeAllPops();
}


//  của trình duyệt bằng modal overlay đẹp
// ══════════════════════════════════════════════════════════
function showConfirm(msg, onOk, onCancel, opts = {}) {
  const okLabel     = opts.okLabel     || 'Xác nhận';
  const cancelLabel = opts.cancelLabel || 'Hủy';
  const okClass     = opts.okClass     || 'btn btn-d';
  const icon        = opts.icon        || '⚠️';
  const id = '_cdlg_' + Date.now();
  const html = `
  <div class="mbg" id="${id}" style="display:flex;align-items:center;justify-content:center;position:fixed;inset:0;z-index:9999">
    <div class="mcard" style="max-width:380px;width:90%;animation:su .25s var(--spring)">
      <div class="mhdr"><span class="mttl">${icon} Xác nhận</span></div>
      <div class="mbody" style="padding:20px 22px;font-size:14px;line-height:1.7;color:var(--txt2);white-space:pre-line">${msg}</div>
      <div class="mftr" style="display:flex;gap:10px;justify-content:flex-end;padding:14px 18px">
        <button class="btn btn-g" id="${id}_cancel">${cancelLabel}</button>
        <button class="${okClass}" id="${id}_ok">${okLabel}</button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
  const el = document.getElementById(id);
  const cleanup = () => el.remove();
  document.getElementById(id + '_ok').onclick = () => { cleanup(); if (onOk) onOk(); };
  document.getElementById(id + '_cancel').onclick = () => { cleanup(); if (onCancel) onCancel(); };
  // ESC để hủy
  const onKey = e => { if (e.key === 'Escape') { cleanup(); if (onCancel) onCancel(); document.removeEventListener('keydown', onKey); } };
  document.addEventListener('keydown', onKey);
  // Focus nút hủy mặc định
  setTimeout(() => document.getElementById(id + '_cancel')?.focus(), 50);
}

function showAlert(msg, opts = {}) {
  const icon  = opts.icon  || 'ℹ️';
  const label = opts.label || 'Đóng';
  const id = '_adlg_' + Date.now();
  const html = `
  <div class="mbg" id="${id}" style="display:flex;align-items:center;justify-content:center;position:fixed;inset:0;z-index:9999">
    <div class="mcard" style="max-width:360px;width:90%;animation:su .25s var(--spring)">
      <div class="mhdr"><span class="mttl">${icon} Thông báo</span></div>
      <div class="mbody" style="padding:20px 22px;font-size:14px;line-height:1.7;color:var(--txt2);white-space:pre-line">${msg}</div>
      <div class="mftr" style="display:flex;justify-content:flex-end;padding:14px 18px">
        <button class="btn btn-p" id="${id}_ok">${label}</button>
      </div>
    </div>
  </div>`;
  document.body.insertAdjacentHTML('beforeend', html);
  const el = document.getElementById(id);
  document.getElementById(id + '_ok').onclick = () => el.remove();
  const onKey = e => { if (e.key === 'Escape' || e.key === 'Enter') { el.remove(); document.removeEventListener('keydown', onKey); } };
  document.addEventListener('keydown', onKey);
  setTimeout(() => document.getElementById(id + '_ok')?.focus(), 50);
}


// ══════════════════════════════════════════════════════════
//  DRAG-TO-SCROLL ENGINE — nhấn giữ để cuộn ngang/dọc
//  Áp dụng cho mọi container có overflow scroll/auto
// ══════════════════════════════════════════════════════════
function initDragScroll(el) {
  if (!el || el._dragScroll) return;
  el._dragScroll = true;

  let isDown = false, startX = 0, scrollLeft = 0;
  let hasDragged = false;

  el.addEventListener('mousedown', e => {
    if (e.target.closest('button,input,select,textarea,a,.cx,.hdp-day,.hdp-nav,.hdp-foot,.hsel-opt,.hsel-btn,.hdp-btn,.hsel-wrap,.hdp-wrap')) return;
    isDown = true; hasDragged = false;
    startX = e.pageX - el.offsetLeft;
    scrollLeft = el.scrollLeft;
    el.style.cursor = 'grabbing';
    el.style.userSelect = 'none';
  });

  const onMove = e => {
    if (!isDown) return;
    const dx = (e.pageX - el.offsetLeft) - startX;
    if (Math.abs(dx) > 4) hasDragged = true;
    el.scrollLeft = scrollLeft - dx;
  };

  const onUp = () => {
    if (!isDown) return;
    isDown = false;
    el.style.cursor = '';
    el.style.userSelect = '';
    if (hasDragged) {
      el.addEventListener('click', e => e.stopPropagation(), { capture: true, once: true });
    }
  };

  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);

  // Touch support (horizontal only)
  let tStartX = 0, tScrollL = 0;
  el.addEventListener('touchstart', e => {
    tStartX = e.touches[0].pageX;
    tScrollL = el.scrollLeft;
  }, { passive: true });
  el.addEventListener('touchmove', e => {
    el.scrollLeft = tScrollL - (e.touches[0].pageX - tStartX);
  }, { passive: true });
}

// Chỉ áp cho các container scroll NGANG
function applyDragScrollAll() {
  const SELECTORS = [
    '.cat-bar',
    '.tab-bar',
  ];
  SELECTORS.forEach(sel => {
    document.querySelectorAll(sel).forEach(initDragScroll);
  });
}

applyDragScrollAll();
const _dsObs = new MutationObserver(() => applyDragScrollAll());
_dsObs.observe(document.body, { childList: true, subtree: true });

// Cursor grab chỉ cho container ngang
(function injectGrabCSS() {
  const style = document.createElement('style');
  style.textContent = `
    .cat-bar, .tab-bar { cursor: grab; }
    .cat-bar:active, .tab-bar:active { cursor: grabbing; }
    /* Các element tương tác luôn dùng pointer */
    .hdp-day:not(.hdp-other), .hdp-nav button, .hdp-clear, .hdp-today-btn,
    .hdp-month-label, .hsel-opt, .hsel-btn, .hdp-btn,
    .otab, .menu-btn, .tcard, .holi, .ddi, .bitem, .qbtn,
    button, a, [onclick] {
      cursor: pointer !important;
    }
  `;
  document.head.appendChild(style);
})();

// ============================================================
//  MOBILE NAVIGATION & SWIPE
// ============================================================
var _mbPanel = 'tables'; // 'tables' | 'menu' | 'bill'

function isMobile() { return window.innerWidth <= 768; }

function mbGoTo(panel, noNbSwitch) {
  if (!isMobile()) return;
  _mbPanel = panel;

  // Highlight active nav button
  ['tables','menu','bill'].forEach(function(p) {
    var b = document.getElementById('mbn-' + p);
    if (b) b.classList.toggle('active', p === panel);
  });

  var pos = document.getElementById('pos-page');
  if (!pos) return;

  if (panel === 'bill') {
    pos.classList.add('mb-show-bill');
  } else {
    pos.classList.remove('mb-show-bill');
    if (!noNbSwitch) showNbTab(panel === 'menu' ? 'menu' : 'tables');
  }
}

// Cap nhat badge so luong mon tren nut bill
function mbUpdateBadge() {
  var badge = document.getElementById('mb-badge');
  if (!badge) return;
  var table = S.curTable;
  var tab = table && S.activeTab[table];
  var items = (tab && S.store[table] && S.store[table][tab]) || [];
  var count = items.reduce(function(s, i) { return s + (i.quantity || 1); }, 0);
  badge.textContent = count > 0 ? count : '';
  badge.classList.toggle('show', count > 0);
}

// Vuot chuyen man hinh
(function() {
  var tx = 0, ty = 0, _swipeLocked = false;

  document.addEventListener('touchstart', function(e) {
    if (!isMobile()) return;
    if (e.target.closest('#mb-nav,#topbar,.mbg,.hdp-drop,.hsel-drop')) return;
    tx = e.touches[0].clientX;
    ty = e.touches[0].clientY;
    _swipeLocked = false;
  }, { passive: true });

  document.addEventListener('touchmove', function(e) {
    if (!isMobile() || _swipeLocked) return;
    var dx = e.touches[0].clientX - tx;
    var dy = e.touches[0].clientY - ty;
    // Neu vuot doc hon ngang thi khoa lai, khong lam gi
    if (Math.abs(dy) > Math.abs(dx) + 10) { _swipeLocked = true; }
  }, { passive: true });

  document.addEventListener('touchend', function(e) {
    if (!isMobile() || _swipeLocked) return;
    if (e.target.closest('#mb-nav,#topbar,.mbg,.hdp-drop,.hsel-drop')) return;
    var dx = e.changedTouches[0].clientX - tx;
    var dy = e.changedTouches[0].clientY - ty;
    if (Math.abs(dx) < 55 || Math.abs(dy) > Math.abs(dx)) return;

    if (_mbPanel === 'tables') {
      if (dx < 0) mbGoTo('menu');
    } else if (_mbPanel === 'menu') {
      if (dx > 0) mbGoTo('tables');
      else mbGoTo('bill');
    } else if (_mbPanel === 'bill') {
      if (dx > 0) mbGoTo('menu');
    }
  }, { passive: true });
})();

// Khi chon ban tren mobile -> tu dong sang menu
document.addEventListener('click', function(e) {
  if (!isMobile()) return;
  if (!e.target.closest('.tcard')) return;
  setTimeout(function() {
    if (S.curTable) mbGoTo('menu', false);
  }, 60);
});

// Patch rBill de cap nhat badge
var _origRBill = rBill;
function rBill() {
  _origRBill.apply(this, arguments);
  mbUpdateBadge();
}

// Reset khi resize ve desktop
window.addEventListener('resize', function() {
  if (!isMobile()) {
    var pos = document.getElementById('pos-page');
    if (pos) pos.classList.remove('mb-show-bill');
  }
});

"""

def build_page():

    login_html = """
<div id="login-page" style="display:flex">
  <div class="login-card">
    <div class="login-logo">
      <span>🛒</span>
      <h1>QUẢN LÝ BÁN HÀNG</h1>
      <p>Hệ thống POS Bán Hàng</p>
    </div>
    <div class="ltabs">
      <button class="ltab on" data-t="l" onclick="swLTab('l')">Đăng nhập</button>
      <button class="ltab" data-t="r" onclick="swLTab('r')">Đăng ký</button>
    </div>
    <!-- Login form -->
    <div id="lf">
      <div class="fg"><label class="fl">Tên đăng nhập</label>
        <input class="fi" id="lu" type="text" placeholder="Tên đăng nhập" autocomplete="username"></div>
      <div class="fg"><label class="fl">Mật khẩu</label>
        <input class="fi" id="lp2" type="password" placeholder="Mật khẩu" autocomplete="current-password"></div>
      <div id="le" class="ferr" style="display:none"></div>
      <button class="btn btn-p" style="width:100%;padding:12px;margin-top:8px" onclick="doLogin()">ĐĂNG NHẬP</button>
    </div>
    <!-- Register form -->
    <div id="rf" style="display:none">
      <div class="fg"><label class="fl">Tên đăng nhập</label>
        <input class="fi" id="ru" type="text" placeholder="Tên đăng nhập"></div>
      <div class="fg"><label class="fl">Mật khẩu</label>
        <input class="fi" id="rp1" type="password" placeholder="Mật khẩu"></div>
      <div class="fg"><label class="fl">Xác nhận mật khẩu</label>
        <input class="fi" id="rp2" type="password" placeholder="Nhập lại mật khẩu"></div>
      <div id="re" class="ferr" style="display:none"></div>
      <button class="btn btn-a" style="width:100%;padding:12px;margin-top:8px" onclick="doReg()">TẠO TÀI KHOẢN</button>
    </div>
  </div>
</div>"""

    topbar_html = """
<div id="topbar">
  <div class="brand" id="brand">🚀 POS Bán hàng</div>
  <div class="ttime" id="ttime" onclick="showView('hist')">⏰ --:--:-- | 🗓 --/--/----</div>
  <div class="menu-dd">
    <button class="menu-btn" onclick="tDd()">☰</button>
    <div class="ddlist">
      <button class="ddi" onclick="showSettings()">  ⚙️  Cài đặt chung</button>
      <button class="ddi" onclick="mTables()">  🪑  Quản lý Bàn</button>
      <button class="ddi" onclick="mMenu()">  🍔  Quản lý Thực Đơn</button>
      <button class="ddi" onclick="showView('hist')">  🧾  Lịch sử Bán hàng</button>
      <div class="ddsep"></div>
      <button class="ddi" style="color:var(--d)" onclick="doLogout()">  🚪  Đăng xuất</button>
    </div>
  </div>
</div>"""

    # POS view: Notebook (left/center) + Bill panel (right)
    pos_html = """
<div id="pos-page">
  <!-- ── NOTEBOOK (LEFT+CENTER) ── -->
  <div id="nb">
    <div class="nb-tabs">
      <button class="nb-tab on" data-t="tables" onclick="showNbTab('tables')">🍽️ Chọn Bàn</button>
      <button class="nb-tab" data-t="menu" onclick="showNbTab('menu')">☕ Thực Đơn/Đặt Món</button>
    </div>
    <div class="nb-content">
      <!-- TAB 1: CHỌN BÀN -->
      <div id="tab-tables">
        <div class="table-area">
          <div class="table-grid" id="tgrid"></div>
        </div>
      </div>
      <!-- TAB 2: THỰC ĐƠN -->
      <div id="tab-menu">
        <div class="menu-header">THỰC ĐƠN (CLICK ĐỂ THÊM MÓN)</div>
        <div class="cat-bar" id="catbar"></div>
        <div class="menu-items" id="mitems"></div>
      </div>
    </div>
  </div>

  <!-- ── BILL PANEL (RIGHT) ── -->
  <div id="bill-panel">
    <div class="bill-title">🛒 CHI TIẾT ĐƠN HÀNG</div>
    <div class="bill-tablename" id="btablename">CHƯA CHỌN BÀN</div>
    <!-- Tab bar -->
    <div class="tab-bar-wrap">
      <div class="tab-bar" id="tbar"></div>
    </div>
    <div class="tab-sep"></div>
    <!-- Bill content -->
    <div class="bill-body" id="billbody">
      <div class="bill-empty">
        <span class="eico">🛒</span>
        <p>Chưa có món nào được chọn</p>
      </div>
    </div>
    <!-- Footer -->
    <div class="bill-total" id="btot">Tổng tiền: 0đ</div>
    <div class="bill-btns">
      <button class="btn-split" onclick="showMergeSplit()">⇋ Tách/Ghép Đơn ⇌</button>
      <button class="btn-checkout" onclick="doCheckout()">✅ THANH TOÁN</button>
    </div>
  </div>
</div>"""

    history_html = """
<div id="hist-page">
  <!-- ── FILTER BAR ── -->
  <div class="hist-bar">
    <button class="btn btn-g btn-sm" onclick="showView('pos')">← POS</button>
    <div class="hfilt-group">
      <label class="hfilt-label">Nhóm theo</label>
      <div class="hfilt-tabs" id="hvm-tabs">
        <button class="hftab on" data-v="day"   onclick="setHMode('day')"  >Ngày</button>
        <button class="hftab"    data-v="week"  onclick="setHMode('week')" >Tuần</button>
        <button class="hftab"    data-v="month" onclick="setHMode('month')">Tháng</button>
      </div>
    </div>
    <div class="hfilt-group">
      <label class="hfilt-label">Từ ngày</label>
      <div class="hdp-wrap" id="hsd-wrap">
        <button class="hsel-btn hdp-btn" id="hsd-btn" onclick="toggleDP('hsd')">
          <span id="hsd-label">dd/mm/yyyy</span><span style="font-size:13px;color:var(--mu2)">📅</span>
        </button>
      </div>
      <input type="hidden" id="hsd">
    </div>
    <div class="hfilt-group">
      <label class="hfilt-label">Đến ngày</label>
      <div class="hdp-wrap" id="hed-wrap">
        <button class="hsel-btn hdp-btn" id="hed-btn" onclick="toggleDP('hed')">
          <span id="hed-label">dd/mm/yyyy</span><span style="font-size:13px;color:var(--mu2)">📅</span>
        </button>
      </div>
      <input type="hidden" id="hed">
    </div>
    <div class="hfilt-group">
      <label class="hfilt-label">Bàn</label>
      <div class="hsel-wrap" id="htbl-wrap">
        <button class="hsel-btn" id="htbl-btn" onclick="toggleHSel('htbl')">
          <span id="htbl-label">Tất cả</span><span class="hsel-arrow">▾</span>
        </button>
      </div>
      <select id="htbl" style="display:none" onchange="rHist()">
        <option value="">Tất cả</option>
      </select>
    </div>
    <div class="hfilt-group">
      <label class="hfilt-label">Sắp xếp</label>
      <div class="hsel-wrap" id="hsrt-wrap">
        <button class="hsel-btn" id="hsrt-btn" onclick="toggleHSel('hsrt')">
          <span id="hsrt-label">Mới nhất</span><span class="hsel-arrow">▾</span>
        </button>
      </div>
      <select id="hsrt" style="display:none" onchange="rHist()">
        <option value="new">Mới nhất</option>
        <option value="old">Cũ nhất</option>
        <option value="hi">Cao nhất</option>
        <option value="lo">Thấp nhất</option>
      </select>
    </div>
    <input class="fi hfilt-search" id="hsq" placeholder="🔍 Tìm mã đơn, bàn, món..." oninput="rHist()">
    <div class="hfilt-group">
      <label class="hfilt-label">Nhanh</label>
      <div class="hfilt-quick">
        <button class="hqbtn" onclick="setQuickRange(7)">7N</button>
        <button class="hqbtn" onclick="setQuickRange(30)">30N</button>
        <button class="hqbtn" onclick="setQuickRange(90)">3T</button>
        <button class="hqbtn hqbtn-all" onclick="setQuickRange(0)">Tất cả</button>
      </div>
    </div>
    <button class="btn btn-g btn-sm" onclick="clearHFilt()" title="Xóa toàn bộ bộ lọc" style="align-self:flex-end">✕ Xóa lọc</button>
    <span style="flex:1"></span>
    <span id="hrev" class="hrev-badge"></span>
  </div>

  <!-- ── BODY ── -->
  <div class="hist-body">
    <div class="hist-left">
      <!-- KPI Cards -->
      <div class="scs" id="scs"></div>
      <!-- Chart Card -->
      <div class="chart-area">
        <div class="chart-hdr">
          <div class="ct">Biểu đồ doanh thu <span id="ch-subtitle"></span></div>
          <div class="chart-type-tabs">
            <button class="cttab on" data-ct="line"  onclick="swChartType('line')">📈 Xu hướng</button>
            <button class="cttab"    data-ct="bar"   onclick="swChartType('bar')">📊 Cột</button>
            <button class="cttab"    data-ct="donut" onclick="swChartType('donut')">🍩 Top món</button>
          </div>
        </div>
        <div class="chart-svg-wrap" id="ch-wrap">
          <div id="ch-tip" class="ch-tip"></div>
          <svg id="ch-svg" height="180"></svg>
        </div>
      </div>
      <!-- Result summary bar -->
      <div id="ol2-summary" class="ol2-summary"></div>
      <!-- Order list (grouped) -->
      <div id="ol2"></div>
    </div>
    <div class="hist-right" id="od2">
      <div class="od2-empty">
        <span style="font-size:52px;opacity:.18;display:block;margin-bottom:14px">📋</span>
        <p>Chọn một đơn hàng<br>để xem chi tiết</p>
      </div>
    </div>
  </div>
</div>"""

    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="vi">',
        "<head>",
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        "<title>POS Bán Hàng</title>",
        f"<style>{CSS}</style>",
        "</head>",
        """<body>""",
        '<div id="ldr"><div id="ldrb"></div></div>',
        '<div id="ts"></div>',
        login_html,
        topbar_html,
        '<div id="main">',
        pos_html,
        history_html,
        '</div>',
        '<div id="mc"></div>',
        '<div id="hpop-portal"></div>',
        """<div id="mb-nav">
  <button class="mb-btn active" id="mbn-tables" onclick="mbGoTo('tables')">
    <span class="mb-ico">&#x1F37D;</span>Chon ban
  </button>
  <button class="mb-btn" id="mbn-menu" onclick="mbGoTo('menu')">
    <span class="mb-ico">&#x2615;</span>Thuc don
  </button>
  <button class="mb-btn" id="mbn-bill" onclick="mbGoTo('bill')">
    <span class="mb-ico">&#x1F6D2;</span>Don hang
    <span class="mb-badge" id="mb-badge"></span>
  </button>
</div>""",
        f"<script>{JS}</script>",
        "</body>",
        "</html>",
    ])

# ══════════════════════════════════════════════════════════════
#  FLASK APP
# ══════════════════════════════════════════════════════════════
flask_app = Flask(__name__)
flask_app.secret_key = SECRET_KEY

def login_required(f):
    @wraps(f)
    def d(*a,**kw):
        if "username" not in session: return jsonify({"error":"Chưa đăng nhập"}),401
        return f(*a,**kw)
    return d

@flask_app.route("/")
def index(): return render_template_string(build_page())

@flask_app.route("/api/session")
def api_session():
    if "username" in session: return jsonify({"logged_in":True,"username":session["username"]})
    return jsonify({"logged_in":False})

@flask_app.route("/api/login",methods=["POST"])
def api_login():
    d=request.json; u=d.get("username","").strip(); p=d.get("password","")
    users=load_users()
    if u in users and verify_pw(users[u]["password"],p):
        session["username"]=u; return jsonify({"ok":True,"username":u})
    return jsonify({"error":"Sai tên đăng nhập hoặc mật khẩu!"}),401

@flask_app.route("/api/register",methods=["POST"])
def api_register():
    d=request.json; u=d.get("username","").strip(); p1=d.get("password",""); p2=d.get("confirm","")
    users=load_users()
    if not u or not p1: return jsonify({"error":"Vui lòng nhập đầy đủ!"}),400
    if p1!=p2: return jsonify({"error":"Mật khẩu xác nhận không khớp!"}),400
    if u in users: return jsonify({"error":"Tên đăng nhập đã tồn tại!"}),400
    users[u]={"password":hash_pw(p1)}
    try:
        save_users(users)
    except RuntimeError as e:
        return jsonify({"error":f"Lỗi lưu dữ liệu: {e}. Kiểm tra cấu hình Google Drive trên Render."}),500
    return jsonify({"ok":True})

@flask_app.route("/api/logout",methods=["POST"])
def api_logout():
    session.clear(); return jsonify({"ok":True})

@flask_app.route("/api/health")
def api_health():
    """Endpoint kiem tra trang thai Drive - dung de debug"""
    status = {
        "drive_configured": _use_drive(),
        "gdrive_credentials_set": bool(GDRIVE_CREDENTIALS),
        "gdrive_folder_id_set": bool(GDRIVE_FOLDER_ID),
    }
    if _use_drive():
        try:
            svc = get_drive()
            status["drive_connected"] = svc is not None
            if svc:
                # Thu doc danh sach file trong folder
                q = f"'{GDRIVE_FOLDER_ID}' in parents and trashed=false"
                res = svc.files().list(q=q, fields="files(name)").execute()
                files = [f["name"] for f in res.get("files", [])]
                status["drive_files"] = files
                status["users_file_exists"] = "users.json" in files
        except Exception as e:
            status["drive_error"] = str(e)
    return jsonify(status)

@flask_app.route("/api/data")
@login_required
def api_data():
    d=load_data()
    return jsonify({"tables":d.get("tables",[]),"menu_items":d.get("menu_items",[]),
                    "table_orders_store":d.get("table_orders_store",{}),
                    "table_tab_list":d.get("table_tab_list",{}),
                    "table_active_order":d.get("table_active_order",{})})

@flask_app.route("/api/tables",methods=["POST"])
@login_required
def api_add_table():
    n=request.json.get("name","").strip().upper()
    if not n: return jsonify({"error":"Tên bàn không hợp lệ"}),400
    d=load_data()
    if n in d["tables"]: return jsonify({"error":"Bàn đã tồn tại"}),400
    d["tables"].append(n); d.setdefault("orders",{})[n]=[]; save_data(d)
    return jsonify({"ok":True,"tables":d["tables"]})

@flask_app.route("/api/tables/<n>",methods=["DELETE"])
@login_required
def api_del_table(n):
    d=load_data()
    if n not in d["tables"]: return jsonify({"error":"Không tìm thấy bàn"}),404
    d["tables"].remove(n)
    for k in ("orders","table_orders_store","table_tab_list","table_active_order"): d.get(k,{}).pop(n,None)
    save_data(d); return jsonify({"ok":True,"tables":d["tables"]})

@flask_app.route("/api/menu",methods=["GET"])
@login_required
def api_get_menu(): return jsonify(load_data().get("menu_items",[]))

@flask_app.route("/api/menu",methods=["POST"])
@login_required
def api_add_menu():
    d=request.json; name=d.get("name","").strip(); price=d.get("price",0)
    if not name or price<=0: return jsonify({"error":"Dữ liệu không hợp lệ"}),400
    data=load_data()
    if any(i["name"].lower()==name.lower() for i in data["menu_items"]): return jsonify({"error":"Món đã tồn tại"}),400
    st=d.get("stock"); data["menu_items"].append({"name":name,"price":int(price),
        "cost_price":int(d.get("cost_price",0)),"stock":int(st) if st not in (None,"") else None,
        "category":d.get("category","Khác")}); save_data(data)
    return jsonify({"ok":True,"menu_items":data["menu_items"]})

@flask_app.route("/api/menu/<int:i>",methods=["PUT"])
@login_required
def api_edit_menu(i):
    d=request.json; data=load_data()
    if i<0 or i>=len(data["menu_items"]): return jsonify({"error":"Không tìm thấy"}),404
    item=data["menu_items"][i]
    item.update({"name":d.get("name",item["name"]).strip(),"price":int(d.get("price",item["price"])),
                 "cost_price":int(d.get("cost_price",item.get("cost_price",0))),
                 "category":d.get("category",item.get("category","Khác"))})
    st=d.get("stock",item.get("stock")); item["stock"]=int(st) if st not in (None,"") else None
    save_data(data); return jsonify({"ok":True,"menu_items":data["menu_items"]})

@flask_app.route("/api/menu/<int:i>",methods=["DELETE"])
@login_required
def api_del_menu(i):
    data=load_data()
    if i<0 or i>=len(data["menu_items"]): return jsonify({"error":"Không tìm thấy"}),404
    data["menu_items"].pop(i); save_data(data)
    return jsonify({"ok":True,"menu_items":data["menu_items"]})

@flask_app.route("/api/orders/move_items",methods=["POST"])
@login_required
def api_move_items():
    d=request.json; table=d.get("table"); from_tab=d.get("from_tab"); to_tab=d.get("to_tab")
    move_items=d.get("items",[])
    data=load_data(); store=data.get("table_orders_store",{})
    if table not in store or from_tab not in store.get(table,{}):
        return jsonify({"error":"Tab nguồn không tồn tại"}),404
    if to_tab not in store.get(table,{}):
        return jsonify({"error":"Tab đích không tồn tại"}),404
    src=store[table][from_tab]; dst=store[table][to_tab]
    for mv in move_items:
        name=mv.get("name"); qty=mv.get("quantity",1)
        src_item=next((i for i in src if i["name"]==name),None)
        if not src_item: continue
        actual_qty=min(qty,src_item.get("quantity",1))
        price=src_item["price"]
        if src_item.get("quantity",1)<=actual_qty: src.remove(src_item)
        else: src_item["quantity"]-=actual_qty
        dst_item=next((i for i in dst if i["name"]==name),None)
        if dst_item: dst_item["quantity"]=dst_item.get("quantity",1)+actual_qty
        else: dst.append({"name":name,"price":price,"quantity":actual_qty})
    store[table][from_tab]=src; store[table][to_tab]=dst
    save_data(data)
    return jsonify({"ok":True,"store":store.get(table,{})})

@flask_app.route("/api/orders/merge_tab",methods=["POST"])
@login_required
def api_merge_tab():
    d=request.json; table=d.get("table"); from_tab=d.get("from_tab"); to_tab=d.get("to_tab")
    data=load_data(); store=data.get("table_orders_store",{})
    if table not in store or from_tab not in store.get(table,{}):
        return jsonify({"error":"Tab nguồn không tồn tại"}),404
    if to_tab not in store.get(table,{}):
        return jsonify({"error":"Tab đích không tồn tại"}),404
    src=list(store[table][from_tab]); dst=store[table][to_tab]
    for item in src:
        dst_item=next((i for i in dst if i["name"]==item["name"]),None)
        if dst_item: dst_item["quantity"]=dst_item.get("quantity",1)+item.get("quantity",1)
        else: dst.append({"name":item["name"],"price":item["price"],"quantity":item.get("quantity",1)})
    del store[table][from_tab]
    tabs=[t for t in data.get("table_tab_list",{}).get(table,[]) if t in store.get(table,{})]
    data["table_tab_list"][table]=tabs
    data["table_active_order"][table]=to_tab
    save_data(data)
    return jsonify({"ok":True,"store":store.get(table,{}),"tab_list":tabs,"active":to_tab})

@flask_app.route("/api/orders/new_tab",methods=["POST"])
@login_required
def api_new_tab():
    table=request.json.get("table"); data=load_data()
    if table not in data["tables"]: return jsonify({"error":"Bàn không tồn tại"}),404
    tid=gen_tab_id(data)
    data.setdefault("table_orders_store",{}).setdefault(table,{})[tid]=[]
    data.setdefault("table_tab_list",{}).setdefault(table,[]).append(tid)
    data.setdefault("table_active_order",{})[table]=tid; save_data(data)
    return jsonify({"ok":True,"tab_id":tid,"store":data["table_orders_store"].get(table,{}),
                    "tab_list":data["table_tab_list"].get(table,[]),"active":data["table_active_order"].get(table)})

@flask_app.route("/api/orders/switch_tab",methods=["POST"])
@login_required
def api_switch_tab():
    d=request.json; table=d.get("table"); tid=d.get("tab_id"); data=load_data()
    store=data.get("table_orders_store",{})
    if table not in store or tid not in store.get(table,{}): return jsonify({"error":"Tab không tồn tại"}),404
    data["table_active_order"][table]=tid; save_data(data)
    return jsonify({"ok":True,"items":store[table][tid]})

@flask_app.route("/api/orders/clear_tab",methods=["POST"])
@login_required
def api_clear_tab():
    """Xóa hết món trong tab nhưng giữ tab (không đóng)"""
    d=request.json; table=d.get("table"); tid=d.get("tab_id"); data=load_data()
    store=data.get("table_orders_store",{})
    if table not in store or tid not in store.get(table,{}): return jsonify({"error":"Tab không tồn tại"}),404
    for it in store[table].get(tid,[]):
        for mi in data["menu_items"]:
            if mi["name"]==it["name"] and mi.get("stock") is not None: mi["stock"]+=it.get("quantity",1)
    store[table][tid]=[]; save_data(data)
    return jsonify({"ok":True,"store":store.get(table,{})})

@flask_app.route("/api/orders/close_tab",methods=["POST"])
@login_required
def api_close_tab():
    d=request.json; table=d.get("table"); tid=d.get("tab_id"); data=load_data()
    store=data.get("table_orders_store",{})
    if table not in store or tid not in store.get(table,{}): return jsonify({"error":"Tab không tồn tại"}),404
    for it in store[table].get(tid,[]):
        for mi in data["menu_items"]:
            if mi["name"]==it["name"] and mi.get("stock") is not None: mi["stock"]+=it.get("quantity",1)
    del store[table][tid]
    tabs=[t for t in data.get("table_tab_list",{}).get(table,[]) if t in store.get(table,{})]
    data["table_tab_list"][table]=tabs
    if tabs: data["table_active_order"][table]=tabs[0]
    else:
        nid=gen_tab_id(data); store.setdefault(table,{})[nid]=[]
        data["table_tab_list"][table]=[nid]; data["table_active_order"][table]=nid
    save_data(data)
    return jsonify({"ok":True,"store":store.get(table,{}),"tab_list":data["table_tab_list"].get(table,[]),
                    "active":data["table_active_order"].get(table)})

@flask_app.route("/api/orders/add_item",methods=["POST"])
@login_required
def api_add_item():
    d=request.json; table=d.get("table"); tid=d.get("tab_id"); name=d.get("name"); data=load_data()
    mi=next((m for m in data["menu_items"] if m["name"]==name),None)
    if not mi: return jsonify({"error":"Món không tồn tại"}),404
    if mi.get("stock") is not None and mi["stock"]<=0: return jsonify({"error":f"Món '{name}' đã hết hàng."}),400
    items=data.get("table_orders_store",{}).get(table,{}).get(tid,[])
    ex=next((i for i in items if i["name"]==name),None)
    if ex: ex["quantity"]=ex.get("quantity",1)+1
    else: items.append({"name":name,"price":mi["price"],"quantity":1})
    data.setdefault("table_orders_store",{}).setdefault(table,{})[tid]=items
    if mi.get("stock") is not None: mi["stock"]-=1
    save_data(data); return jsonify({"ok":True,"items":items,"menu_items":data["menu_items"]})

@flask_app.route("/api/orders/remove_item",methods=["POST"])
@login_required
def api_rem_item():
    d=request.json; table=d.get("table"); tid=d.get("tab_id"); name=d.get("name"); data=load_data()
    items=data.get("table_orders_store",{}).get(table,{}).get(tid,[])
    ex=next((i for i in items if i["name"]==name),None)
    if not ex: return jsonify({"error":"Không tìm thấy"}),404
    for mi in data["menu_items"]:
        if mi["name"]==name and mi.get("stock") is not None: mi["stock"]+=1
    if ex.get("quantity",1)>1: ex["quantity"]-=1
    else: items.remove(ex)
    data.setdefault("table_orders_store",{}).setdefault(table,{})[tid]=items
    save_data(data); return jsonify({"ok":True,"items":items,"menu_items":data["menu_items"]})

@flask_app.route("/api/orders/edit_price",methods=["POST"])
@login_required
def api_edit_price():
    d=request.json; table=d.get("table"); tid=d.get("tab_id"); name=d.get("name"); price=d.get("price")
    data=load_data(); items=data.get("table_orders_store",{}).get(table,{}).get(tid,[])
    item=next((i for i in items if i["name"]==name),None)
    if not item: return jsonify({"error":"Không tìm thấy"}),404
    item["price"]=int(price); data.setdefault("table_orders_store",{}).setdefault(table,{})[tid]=items
    save_data(data); return jsonify({"ok":True,"items":items})

@flask_app.route("/api/checkout",methods=["POST"])
@login_required
def api_checkout():
    d=request.json; table=d.get("table"); tid=d.get("tab_id"); discount=int(d.get("discount",0))
    data=load_data(); store=data.get("table_orders_store",{})
    items=list(store.get(table,{}).get(tid,[]))
    if not items: return jsonify({"error":"Đơn hàng trống!"}),400
    total=sum(i["price"]*i.get("quantity",1) for i in items); final=max(0,total-discount)
    now_str=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec={"order_id":tid,"table":table,"items":items,"total":final,"discount":discount,
         "date":now_str,"user":session.get("username","")}
    data.setdefault("sales_history",[]).append(rec)
    del store[table][tid]
    tabs=[t for t in data.get("table_tab_list",{}).get(table,[]) if t in store.get(table,{})]
    data["table_tab_list"][table]=tabs
    if tabs: data["table_active_order"][table]=tabs[0]
    else:
        nid=gen_tab_id(data); store.setdefault(table,{})[nid]=[]
        data["table_tab_list"][table]=[nid]; data["table_active_order"][table]=nid
    save_data(data); s=load_settings(); send_email_bg(rec,s)
    return jsonify({"ok":True,"record":rec,"use_qr":s.get("use_qr_payment",False),
                    "bank_id":s.get("bank_id","MB"),"account_no":s.get("account_no",""),
                    "account_name":s.get("account_name",""),
                    "store":store.get(table,{}),"tab_list":data["table_tab_list"].get(table,[]),
                    "active":data["table_active_order"].get(table)})

@flask_app.route("/api/history")
@login_required
def api_history(): return jsonify(load_data().get("sales_history",[]))

@flask_app.route("/api/history/<int:i>",methods=["DELETE"])
@login_required
def api_del_history(i):
    data=load_data(); hist=data.get("sales_history",[])
    if i<0 or i>=len(hist): return jsonify({"error":"Không tìm thấy"}),404
    hist.pop(i); save_data(data); return jsonify({"ok":True})

@flask_app.route("/api/settings",methods=["GET"])
@login_required
def api_get_settings(): return jsonify(load_settings())

@flask_app.route("/api/settings",methods=["POST"])
@login_required
def api_save_settings():
    d=request.json; s=load_settings(); s.update({k:d[k] for k in d if k in s}); save_settings(s)
    return jsonify({"ok":True})

# ══════════════════════════════════════════════════════════════
#  KHỞI ĐỘNG
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") == "development"
    if debug:
        import webbrowser
        threading.Timer(1.3, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"  POS BAN HANG dang chay tai http://0.0.0.0:{port}")
    flask_app.run(debug=debug, host="0.0.0.0", port=port)
