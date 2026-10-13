# -*- coding: utf-8 -*-
"""
DocScan server — ตัวรับไฟล์สแกนจาก iPhone เข้าโฟลเดอร์บนคอมพิวเตอร์ (2026-10-12)

ใช้ยังไง:
  1) ดับเบิลคลิก start_scan_server.bat (หรือ python server.py)
  2) เปิดหน้า http://127.0.0.1:8780/admin บนคอม → เลือกโฟลเดอร์ปลายทาง + มี QR/ลิงก์ให้ iPhone สแกน
  3) iPhone (ต้องอยู่ Wi-Fi เดียวกับคอม) เปิดลิงก์ สแกนเอกสาร กด "ส่งเข้าคอม" → ไฟล์ JPG/PDF ไปอยู่ในโฟลเดอร์ที่เลือก

ความปลอดภัย:
  - รับอัปโหลดเฉพาะเมื่อมีรหัส (key) ถูกต้อง — key สร้างสุ่มครั้งแรกและเก็บใน config.json อยู่ในลิงก์/QR ที่หน้า admin
  - หน้า admin เปลี่ยนโฟลเดอร์ได้เฉพาะจากเครื่องคอมนี้ (127.0.0.1) เท่านั้น
  - รับเฉพาะไฟล์ .jpg/.jpeg/.pdf ขนาดไม่เกิน 60 MB, ไม่เขียนทับไฟล์เดิม (เติมเลขต่อท้าย), ตัดชื่อไฟล์ให้ปลอดภัย
  - ใช้ได้ในเครือข่ายบ้าน/ร้านเท่านั้น ไม่ควรเปิดพอร์ตออกอินเทอร์เน็ต
ใช้เฉพาะไลบรารีมาตรฐานของ Python (ไม่ต้องติดตั้งอะไรเพิ่ม) — ยกเว้นการสร้างภาพ QR ที่ใช้ไลบรารี qrcode (ไม่มีก็ได้ แสดงแค่ลิงก์แทน)

แพ็กเป็น DocScan.exe ด้วย build_exe.py (PyInstaller): index.html ฝังอยู่ใน exe, ค่าตั้งค่า (key + โฟลเดอร์) เก็บที่ %APPDATA%/DocScan/config.json
(ตั้ง DOCSCAN_HOME เพื่อเปลี่ยนที่เก็บค่าตั้งค่า, DOCSCAN_PORT เพื่อเปลี่ยนพอร์ต)
"""
import json, os, re, secrets, socket, subprocess, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

FROZEN = bool(getattr(sys, 'frozen', False))
HERE = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.abspath(__file__))   # ที่อยู่ของ index.html (ใน exe = โฟลเดอร์ที่ PyInstaller แตกไฟล์)
if os.environ.get('DOCSCAN_HOME'):
    DATA = os.environ['DOCSCAN_HOME']
elif FROZEN:
    DATA = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'DocScan')   # exe อาจอยู่ในโฟลเดอร์ที่เขียนไม่ได้ / ย้ายที่ได้ — ค่าตั้งค่าอยู่ที่เดิมเสมอ
else:
    DATA = HERE
try:
    os.makedirs(DATA, exist_ok=True)
except Exception:
    pass
CONFIG = os.path.join(DATA, 'config.json')
PORT = int(os.environ.get('DOCSCAN_PORT', '8780'))
MAX_BYTES = 60 * 1024 * 1024
ALLOWED = ('.jpg', '.jpeg', '.pdf')
_lock = threading.Lock()


def documents_dir():
    """โฟลเดอร์ Documents จริงของผู้ใช้ (ถ้าย้ายไป OneDrive ก็ได้ path ของ OneDrive) — ถามจาก Windows ไม่เดาจาก ~/Documents"""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:   # 5 = CSIDL_PERSONAL (Documents)
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser('~'), 'Documents')


def in_cloud_sync(path):
    """โฟลเดอร์นี้อยู่ใน OneDrive (ซิงก์ขึ้นคลาวด์) หรือเปล่า"""
    p = os.path.abspath(path).lower()
    for v in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial'):
        r = os.environ.get(v)
        if r and p.startswith(os.path.abspath(r).lower()):
            return True
    return (os.sep + 'onedrive') in p


def default_dir():
    """โฟลเดอร์เริ่มต้น: Documents/DocScan — แต่ถ้า Documents ถูกย้ายไป OneDrive จะใช้ <โฟลเดอร์ผู้ใช้>/DocScan แทน
    เพื่อไม่ให้เอกสารสแกน (บัตร/ใบอนุญาต) ซิงก์ขึ้นคลาวด์โดยที่ลูกค้าไม่รู้ตัว — อยากให้ซิงก์ก็เลือกโฟลเดอร์เองได้ในหน้า admin"""
    d = documents_dir()
    if in_cloud_sync(d):
        return os.path.join(os.path.expanduser('~'), 'DocScan')
    return os.path.join(d, 'DocScan')


def load_cfg():
    cfg = {}
    try:
        with open(CONFIG, encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        pass
    changed = False
    if not cfg.get('key'):
        cfg['key'] = secrets.token_urlsafe(9); changed = True
    if not cfg.get('dir'):
        cfg['dir'] = default_dir(); changed = True
    if changed:
        save_cfg(cfg)
    return cfg


def save_cfg(cfg):
    with open(CONFIG, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


CFG = load_cfg()


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def safe_name(name):
    name = os.path.basename(name or '').strip().replace('\\', '_').replace('/', '_')
    name = re.sub(r'[^\w.\- ()฀-๿]', '_', name)[:120] or 'scan'
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED:
        return None
    return name


def unique_path(folder, name):
    base, ext = os.path.splitext(name)
    p = os.path.join(folder, name); n = 1
    while os.path.exists(p):
        p = os.path.join(folder, '%s_%d%s' % (base, n, ext)); n += 1
    return p


def startup_link():
    """ที่อยู่ไฟล์ทางลัดในโฟลเดอร์ Startup ของผู้ใช้ (ไม่ต้องสิทธิ์ admin) — ตั้ง DOCSCAN_STARTUP_DIR เพื่อทดสอบ"""
    d = os.environ.get('DOCSCAN_STARTUP_DIR') or os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'),
                                                              'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')
    return os.path.join(d, 'DocScan.lnk')


def _ps(cmd):
    r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', cmd], capture_output=True, timeout=30,
                       creationflags=0x08000000)
    return r.returncode, (r.stdout or b'').decode('utf-8', 'replace').strip(), (r.stderr or b'').decode('utf-8', 'replace').strip()


def autostart_state():
    """{'available': ทำได้ไหม (ต้องรันจาก DocScan.exe), 'enabled': เปิดอยู่ไหม, 'exe': ที่อยู่โปรแกรม}"""
    lnk = startup_link()
    st = {'available': FROZEN, 'enabled': os.path.exists(lnk), 'exe': sys.executable if FROZEN else ''}
    if st['enabled'] and FROZEN:
        try:   # ทางลัดเก่าที่ชี้ไปที่อื่น (ย้าย/ลบ exe แล้ว) ถือว่าไม่ตรง — ให้หน้า admin เตือน
            rc, out, _ = _ps("(New-Object -ComObject WScript.Shell).CreateShortcut('%s').TargetPath" % lnk.replace("'", "''"))
            st['target_ok'] = (rc == 0 and os.path.normcase(out) == os.path.normcase(sys.executable))
        except Exception:
            st['target_ok'] = True
    return st


def autostart_set(enable):
    lnk = startup_link()
    if not enable:
        try:
            os.remove(lnk)
        except FileNotFoundError:
            pass
        return True, ''
    if not FROZEN:
        return False, 'ใช้ได้เฉพาะเมื่อเปิดจาก DocScan.exe'
    os.makedirs(os.path.dirname(lnk), exist_ok=True)
    exe = sys.executable
    q = lambda t: t.replace("'", "''")
    # WindowStyle 7 = เปิดแบบย่อหน้าต่าง (ไม่เด้งจอดำขึ้นมาตอนเปิดเครื่อง), --startup = ไม่เปิดเบราว์เซอร์
    cmd = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s'); $s.TargetPath='%s'; $s.Arguments='--startup'; "
           "$s.WorkingDirectory='%s'; $s.WindowStyle=7; $s.Description='DocScan'; $s.Save()") % (q(lnk), q(exe), q(os.path.dirname(exe)))
    rc, out, err = _ps(cmd)
    if rc != 0 or not os.path.exists(lnk):
        return False, err or 'สร้างทางลัดไม่สำเร็จ'
    return True, ''


def pick_dialog_main(initial, outfile):
    """รันใน process แยก (--pick): เปิดหน้าต่างเลือกโฟลเดอร์ของ Windows แล้วเขียน path ลงไฟล์ outfile
    (exe แบบไม่มีหน้าต่างคอนโซลไม่มี stdout ให้ใช้ จึงส่งผลทางไฟล์)
    ปัญหาที่เจอ: process ที่ถูกเรียกจากเบื้องหลัง Windows ไม่ยอมให้ขึ้นหน้าสุด หน้าต่างเลยซ่อนอยู่หลังเบราว์เซอร์
    → จำลองการกดปุ่ม Alt (ปลดล็อกสิทธิ์ขึ้นหน้าสุด) + ให้หน้าต่างเจ้าของเป็น topmost ไร้ขอบโปร่งใส แล้วค่อยเปิด dialog"""
    import tkinter as tk
    from tkinter import filedialog
    try:
        import ctypes
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0); ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)   # Alt down/up
    except Exception:
        pass
    r = tk.Tk()
    r.overrideredirect(True); r.geometry('1x1+0+0'); r.attributes('-alpha', 0.0); r.attributes('-topmost', True)
    r.update(); r.lift(); r.focus_force()
    title = 'เลือกโฟลเดอร์ที่จะเก็บไฟล์สแกน'

    def force_front():   # dialog โผล่แล้ว → ดันให้เป็นหน้าต่างที่ active (แนบ input thread ของหน้าต่างที่ active อยู่ชั่วคราว)
        try:
            import ctypes
            u = ctypes.windll.user32
            h = u.FindWindowW(None, title)
            if h:
                fg = u.GetForegroundWindow()
                t_fg = u.GetWindowThreadProcessId(fg, None); t_me = ctypes.windll.kernel32.GetCurrentThreadId()
                if t_fg and t_fg != t_me: u.AttachThreadInput(t_me, t_fg, True)
                u.keybd_event(0x12, 0, 0, 0); u.keybd_event(0x12, 0, 2, 0)
                u.ShowWindow(h, 9); u.BringWindowToTop(h); u.SetForegroundWindow(h)
                if t_fg and t_fg != t_me: u.AttachThreadInput(t_me, t_fg, False)
        except Exception:
            pass
    r.after(250, force_front); r.after(900, force_front)
    d = filedialog.askdirectory(parent=r, initialdir=initial, title=title)
    r.destroy()
    with open(outfile, 'w', encoding='utf-8') as f:
        f.write(d or '')


def pick_folder_dialog(initial):
    """เปิดหน้าต่างเลือกโฟลเดอร์ของ Windows บนคอม (รันเป็น process แยกเพื่อไม่ค้างเซิร์ฟเวอร์)"""
    import tempfile
    fd, outfile = tempfile.mkstemp(prefix='docscan_pick_', suffix='.txt'); os.close(fd)
    cmd = [sys.executable, '--pick', initial, outfile] if FROZEN else [sys.executable, os.path.abspath(__file__), '--pick', initial, outfile]
    try:
        subprocess.run(cmd, timeout=300)
        with open(outfile, encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return ''
    finally:
        try: os.remove(outfile)
        except Exception: pass


def qr_svg(text):
    """ภาพ QR เป็น SVG (ไม่ต้องใช้อินเทอร์เน็ต) — ไม่มีไลบรารี qrcode ก็คืน None"""
    try:
        import qrcode
        q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.add_data(text); q.make(fit=True)
        m = q.get_matrix(); n = len(m)
        cells = ''.join('M%d %dh1v1h-1z' % (x, y) for y, row in enumerate(m) for x, v in enumerate(row) if v)
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="200" height="200" shape-rendering="crispEdges">'
                '<rect width="100%%" height="100%%" fill="#fff"/><path d="%s" fill="#000"/></svg>') % (n, n, cells)
    except Exception:
        return None


ADMIN_HTML = u'''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DocScan — ตั้งค่า</title>
<style>body{font-family:system-ui,"Segoe UI",sans-serif;max-width:720px;margin:24px auto;padding:0 16px;color:#13201d;background:#f4f6f5}
h1{font-size:22px}.card{background:#fff;border:1px solid #dbe3e0;border-radius:14px;padding:16px;margin:14px 0}
input[type=text]{width:100%;padding:10px;font-size:16px;border:1.5px solid #dbe3e0;border-radius:10px;box-sizing:border-box}
button{font:inherit;font-weight:700;border:0;border-radius:10px;padding:10px 14px;background:#0f766e;color:#fff;cursor:pointer;margin-top:8px}
button.sec{background:#fff;color:#13201d;border:1.5px solid #dbe3e0}.mut{color:#5d6f6a;font-size:14px;line-height:1.55}
#url{font-size:18px;font-weight:700;word-break:break-all;color:#0f766e}#msg{min-height:20px;margin-top:6px;font-size:14px}</style></head><body>
<h1>📄 DocScan — รับไฟล์สแกนจาก iPhone</h1>
<div class="card"><b>โฟลเดอร์ที่เก็บไฟล์บนคอมเครื่องนี้</b>
<input type="text" id="dir"><button onclick="setDir()">บันทึกโฟลเดอร์นี้</button> <button class="sec" onclick="pick()">📁 เลือกโฟลเดอร์…</button> <button class="sec" onclick="openDir()">📂 เปิดโฟลเดอร์</button>
<div id="msg" class="mut"></div><div id="cloud" class="mut" style="display:none;color:#b45309">⚠ โฟลเดอร์นี้อยู่ใน OneDrive — ไฟล์สแกนจะถูกซิงก์ขึ้นคลาวด์ด้วย (ถ้าไม่ต้องการ ให้เลือกโฟลเดอร์นอก OneDrive)</div></div>
<div class="card" id="autocard"><label style="display:flex;gap:10px;align-items:flex-start;cursor:pointer"><input type="checkbox" id="auto" onchange="setAuto()" style="width:20px;height:20px;margin-top:2px">
<span><b>เปิด DocScan อัตโนมัติเมื่อเปิดเครื่อง</b><br><span class="mut">จะรันแบบย่อหน้าต่างไว้เบื้องหลัง ไม่ต้องมาดับเบิลคลิกทุกครั้ง (ย้ายไฟล์ DocScan.exe ไปไว้โฟลเดอร์ถาวรก่อนเปิดใช้ เช่น โฟลเดอร์ DocScan ในไดรฟ์ C — อย่าวางไว้ใน Downloads)</span></span></label>
<div id="automsg" class="mut" style="margin-top:6px"></div></div>
<div class="card"><button class="sec" onclick="quitApp()" style="margin-top:0">⏻ ปิดโปรแกรม DocScan</button> <span class="mut">โปรแกรมทำงานอยู่เบื้องหลัง (ไม่มีหน้าต่าง) — ปิดได้จากปุ่มนี้</span></div>
<div class="card"><b>เปิดบน iPhone (Wi-Fi เดียวกับคอม)</b>
<p class="mut">สแกน QR นี้ด้วยกล้อง iPhone หรือพิมพ์ลิงก์ด้านล่างใน Safari แล้วกด “เพิ่มไปยังหน้าจอโฮม” เพื่อเปิดใช้งานได้สะดวก</p>
<div id="qr" style="margin:10px 0"></div><div id="url"></div>
<button onclick="copyUrl()" style="margin-top:8px">📋 คัดลอกลิงก์ (ไปวางใน LINE)</button> <span id="cpmsg" class="mut"></span>
<p class="mut">ถ้าเปิดไม่ได้: ตอนรันครั้งแรก Windows จะถามเรื่อง Firewall ให้เลือก “อนุญาต” (เครือข่ายส่วนตัว) และต้องให้ iPhone กับคอมอยู่ Wi-Fi เดียวกัน</p></div>
<script>
async function load(){ var r=await (await fetch('/admin/state')).json(); document.getElementById('dir').value=r.dir; document.getElementById('cloud').style.display=r.cloud?'':'none';
 document.getElementById('url').textContent=r.url; var q=document.getElementById('qr'); if(r.qr){ q.innerHTML=r.qr; } else { q.textContent='(แสดง QR ไม่ได้ — ใช้ลิงก์ด้านล่างแทน)'; } }
async function post(path,body){ var r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}); return r.json(); }
async function setDir(){ var j=await post('/admin/setdir',{dir:document.getElementById('dir').value}); document.getElementById('msg').textContent=j.ok?('✅ บันทึกแล้ว: '+j.dir):('❌ '+j.error); if(j.ok) load(); }
async function openDir(){ var j=await post('/admin/open'); if(!j.ok) document.getElementById('msg').textContent='❌ เปิดโฟลเดอร์ไม่ได้: '+j.error; }
async function pick(){ document.getElementById('msg').textContent='กำลังเปิดหน้าต่างเลือกโฟลเดอร์บนคอม…'; var j=await post('/admin/pick'); if(j.ok&&j.dir){ document.getElementById('dir').value=j.dir; setDir(); } else document.getElementById('msg').textContent=j.ok?'ยกเลิกการเลือก':('❌ '+j.error); }
async function loadAuto(){ var r=await (await fetch('/admin/autostart')).json(), c=document.getElementById('auto'), m=document.getElementById('automsg');
 c.checked=!!r.enabled; c.disabled=!r.available;
 m.textContent = !r.available ? 'ใช้ได้เมื่อเปิดจาก DocScan.exe เท่านั้น' : (r.enabled && r.target_ok===false ? '⚠ ทางลัดเดิมชี้ไปที่อื่น (ย้าย DocScan.exe?) — ติ๊กออกแล้วติ๊กใหม่เพื่ออัปเดต' : (r.enabled ? '✅ เปิดอยู่ — โปรแกรมนี้จะเริ่มเองเมื่อเปิดเครื่อง: '+r.exe : '')); }
async function setAuto(){ var c=document.getElementById('auto'), m=document.getElementById('automsg'); var r=await post('/admin/autostart',{enable:c.checked});
 if(!r.ok){ m.textContent='❌ '+(r.error||'ทำไม่สำเร็จ'); } await loadAuto(); if(!r.ok) return; m.textContent = (c.checked?'✅ ตั้งให้เปิดอัตโนมัติแล้ว: '+r.exe:'✅ ยกเลิกการเปิดอัตโนมัติแล้ว'); }
async function copyUrl(){ var t=document.getElementById('url').textContent, m=document.getElementById('cpmsg'), ok=false;
 try{ await navigator.clipboard.writeText(t); ok=true; }catch(e){ try{ var a=document.createElement('textarea'); a.value=t; document.body.appendChild(a); a.select(); ok=document.execCommand('copy'); a.remove(); }catch(e2){} }
 m.textContent=ok?'✅ คัดลอกแล้ว — เปิด LINE แล้ววาง (แนะนำให้กดลิงก์ค้างแล้วเลือกเปิดใน Safari)':'❌ คัดลอกไม่ได้ — ลากคลุมลิงก์แล้วกด Ctrl+C'; }
async function quitApp(){ if(!confirm('ปิด DocScan? (iPhone จะส่งไฟล์เข้าคอมไม่ได้จนกว่าจะเปิดโปรแกรมอีกครั้ง)')) return; try{ await post('/admin/quit'); }catch(e){}
 document.body.innerHTML='<h1>DocScan ปิดแล้ว</h1><p class="mut">เปิดใหม่ได้โดยดับเบิลคลิก DocScan.exe</p>'; }
load(); loadAuto();
</script></body></html>'''


class H(BaseHTTPRequestHandler):
    server_version = 'DocScan/1.0'

    def log_message(self, fmt, *args):
        sys.stderr.write('[%s] %s\n' % (self.log_date_time_string(), fmt % args))

    def _local(self):
        return self.client_address[0] in ('127.0.0.1', '::1')

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(b)

    def _html(self, body, code=200):
        b = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ('/', '/index.html'):
            with open(os.path.join(HERE, 'index.html'), 'rb') as f:
                return self._html(f.read())
        if u.path == '/api/info':
            return self._json({'ok': True, 'dir': CFG['dir'], 'name': os.path.basename(CFG['dir'].rstrip('\\/')) or CFG['dir']})
        if u.path == '/admin':
            if not self._local(): return self._html('admin เปิดได้เฉพาะจากคอมเครื่องนี้', 403)
            return self._html(ADMIN_HTML)
        if u.path == '/admin/autostart':
            if not self._local(): return self._json({'ok': False}, 403)
            return self._json(autostart_state())
        if u.path == '/admin/state':
            if not self._local(): return self._json({'ok': False}, 403)
            url = 'http://%s:%d/?k=%s' % (lan_ip(), PORT, CFG['key'])
            return self._json({'dir': CFG['dir'], 'url': url, 'qr': qr_svg(url), 'cloud': in_cloud_sync(CFG['dir'])})
        self._json({'ok': False, 'error': 'not found'}, 404)

    def do_POST(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        n = int(self.headers.get('Content-Length') or 0)
        if u.path == '/api/upload':
            if (q.get('k') or [''])[0] != CFG['key']:
                return self._json({'ok': False, 'error': 'รหัสไม่ถูกต้อง — เปิดลิงก์จากหน้า admin บนคอมอีกครั้ง'}, 403)
            if n <= 0 or n > MAX_BYTES:
                return self._json({'ok': False, 'error': 'ไฟล์ว่างหรือใหญ่เกิน 60 MB'}, 413)
            name = safe_name((q.get('name') or [''])[0])
            if not name:
                return self._json({'ok': False, 'error': 'รับเฉพาะไฟล์ .jpg / .pdf'}, 400)
            body = self.rfile.read(n)
            if name.lower().endswith('.pdf') and not body.startswith(b'%PDF'):
                return self._json({'ok': False, 'error': 'ไฟล์ PDF ไม่ถูกต้อง'}, 400)
            if name.lower().endswith(('.jpg', '.jpeg')) and not body.startswith(b'\xff\xd8'):
                return self._json({'ok': False, 'error': 'ไฟล์ JPG ไม่ถูกต้อง'}, 400)
            with _lock:
                os.makedirs(CFG['dir'], exist_ok=True)
                path = unique_path(CFG['dir'], name)
                with open(path, 'wb') as f:
                    f.write(body)
            print('saved', path, len(body), 'bytes')
            return self._json({'ok': True, 'saved': os.path.basename(path), 'dir': CFG['dir']})
        if u.path in ('/admin/setdir', '/admin/pick', '/admin/open', '/admin/autostart', '/admin/quit'):
            if not self._local(): return self._json({'ok': False, 'error': 'เฉพาะคอมเครื่องนี้'}, 403)
            if u.path == '/admin/quit':
                self._json({'ok': True})
                threading.Timer(0.4, lambda: os._exit(0)).start()   # ตอบก่อนแล้วค่อยปิดตัวเอง
                return
            if u.path == '/admin/autostart':
                try:
                    data = json.loads(self.rfile.read(n) or b'{}')
                    ok, err = autostart_set(bool(data.get('enable')))
                    st = autostart_state(); st['ok'] = ok
                    if err: st['error'] = err
                    return self._json(st)
                except Exception as e:
                    return self._json({'ok': False, 'error': str(e)})
            if u.path == '/admin/open':
                try:
                    os.makedirs(CFG['dir'], exist_ok=True)
                    os.startfile(CFG['dir'])   # เปิดใน File Explorer บนคอมเครื่องนี้
                    return self._json({'ok': True, 'dir': CFG['dir']})
                except Exception as e:
                    return self._json({'ok': False, 'error': str(e)})
            if u.path == '/admin/pick':
                d = pick_folder_dialog(CFG['dir'])
                return self._json({'ok': True, 'dir': d})
            try:
                data = json.loads(self.rfile.read(n) or b'{}')
                d = os.path.abspath(os.path.expanduser((data.get('dir') or '').strip()))
                if not d or d == os.path.abspath(os.sep):
                    return self._json({'ok': False, 'error': 'ใส่ path ของโฟลเดอร์'})
                os.makedirs(d, exist_ok=True)
                if not os.access(d, os.W_OK):
                    return self._json({'ok': False, 'error': 'เขียนไฟล์ลงโฟลเดอร์นี้ไม่ได้'})
                CFG['dir'] = d; save_cfg(CFG)
                return self._json({'ok': True, 'dir': d})
            except Exception as e:
                return self._json({'ok': False, 'error': str(e)})
        self._json({'ok': False, 'error': 'not found'}, 404)


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == '--pick':
        return pick_dialog_main(sys.argv[2] if len(sys.argv) > 2 else '', sys.argv[3] if len(sys.argv) > 3 else os.devnull)
    if sys.stdout is None or sys.stderr is None:   # exe แบบรันเบื้องหลัง (ไม่มีหน้าต่างคอนโซล): เขียน log ลงไฟล์แทน
        try:
            lp = os.path.join(DATA, 'docscan.log')
            if os.path.exists(lp) and os.path.getsize(lp) > 1024 * 1024: os.remove(lp)
            sys.stdout = sys.stderr = open(lp, 'a', encoding='utf-8', buffering=1)
        except Exception:
            sys.stdout = sys.stderr = open(os.devnull, 'w')
    else:
        try:   # คอนโซลของ Windows ปกติไม่ใช่ UTF-8 — ไม่ตั้งไว้ข้อความไทยจะทำให้ print พังได้
            sys.stdout.reconfigure(encoding='utf-8', errors='replace'); sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    os.makedirs(CFG['dir'], exist_ok=True)
    ip = lan_ip()
    try:
        srv = ThreadingHTTPServer(('0.0.0.0', PORT), H)
    except OSError:
        print('พอร์ต %d ถูกใช้อยู่ — น่าจะเปิด DocScan ไว้แล้ว (เปิดหน้าตั้งค่าให้แทน)' % PORT)
        if '--startup' in sys.argv: return   # เปิดอัตโนมัติตอนบูตแล้วมีตัวอื่นรันอยู่ — เงียบๆ แล้วจบ
        webbrowser.open('http://127.0.0.1:%d/admin' % PORT)
        return
    print('=' * 60)
    print(' DocScan server พร้อมใช้งาน')
    print(' โฟลเดอร์เก็บไฟล์ :', CFG['dir'])
    print(' ตั้งค่า/QR บนคอม :  http://127.0.0.1:%d/admin' % PORT)
    print(' ลิงก์สำหรับ iPhone:  http://%s:%d/?k=%s' % (ip, PORT, CFG['key']))
    print(' (หยุดโปรแกรม: ปุ่ม "ปิดโปรแกรม" ในหน้าตั้งค่า หรือกด Ctrl+C)')
    print('=' * 60)
    if FROZEN and '--startup' not in sys.argv and not os.environ.get('DOCSCAN_NOBROWSER'):
        threading.Timer(0.8, lambda: webbrowser.open('http://127.0.0.1:%d/admin' % PORT)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
