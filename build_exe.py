# -*- coding: utf-8 -*-
"""สร้าง DocScan.exe (PyInstaller, ไฟล์เดียว) แล้วแพ็กเป็นโฟลเดอร์/zip สำหรับแจกลูกค้า

ใช้: python build_exe.py        → release\\DocScan\\ (DocScan.exe + อ่านก่อน.txt) และ release\\DocScan.zip
ไม่ต้องรันถ้าไม่ได้จะแจกใหม่ — build เฉพาะเมื่อ index.html / server.py เปลี่ยนและต้องการส่งเวอร์ชันใหม่ให้ลูกค้า
"""
import os, re, shutil, subprocess, sys, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REL = os.path.join(HERE, sys.argv[1] if len(sys.argv) > 1 else 'release')   # python build_exe.py release_new = สร้างลงโฟลเดอร์อื่น (ใช้เมื่อ release\DocScan\DocScan.exe กำลังรันอยู่ ลบทับไม่ได้)
WORK = os.path.join(HERE, 'build_tmp')


def version():
    s = open(os.path.join(HERE, 'index.html'), encoding='utf-8').read()
    m = re.search(r'v\d{4}-\d{2}-\d{2}[a-z]?', s)
    return m.group(0) if m else 'unknown'


README = u"""DocScan — สแกนเอกสารด้วย iPhone แล้วส่งเข้าคอม ({ver})

วิธีใช้
1) ดับเบิลคลิก DocScan.exe  (โปรแกรมทำงานอยู่เบื้องหลัง ไม่มีหน้าต่าง — ปิดได้จากปุ่ม "ปิดโปรแกรม" ในหน้าตั้งค่า)
   - ครั้งแรก Windows อาจถามเรื่อง Firewall ให้กด "อนุญาต" (เครือข่ายส่วนตัว)
   - ถ้าขึ้น "Windows protected your PC" ให้กด "More info" แล้ว "Run anyway"
2) เบราว์เซอร์จะเปิดหน้า "DocScan — ตั้งค่า" ให้อัตโนมัติ
   - เลือกโฟลเดอร์ที่จะเก็บไฟล์สแกนได้ (ค่าเริ่มต้น: Documents\\DocScan)
   - ติ๊ก "เปิด DocScan อัตโนมัติเมื่อเปิดเครื่อง" ถ้าไม่อยากมาเปิดเองทุกครั้ง (ย้าย DocScan.exe ไปไว้โฟลเดอร์ถาวรก่อน)
3) ใช้ iPhone สแกน QR ในหน้านั้น (iPhone ต้องอยู่ Wi-Fi เดียวกับคอม) แล้วเปิดใน Safari
   - ถ้าสะดวกกว่า กดปุ่ม "คัดลอกลิงก์" แล้วส่งเข้า LINE ให้ iPhone — แต่ให้กดค้างที่ลิงก์แล้วเลือกเปิดใน Safari (ไม่ใช่เบราว์เซอร์ใน LINE)
   - กด "เพิ่มไปยังหน้าจอโฮม" ใน Safari จะเปิดใช้ครั้งต่อไปได้เร็ว
4) ถ่ายเอกสาร → ปรับขอบ → เลือก JPG หรือ PDF → กด "ส่งเข้าคอม"
   ไฟล์จะไปอยู่ในโฟลเดอร์ที่เลือกบนคอม

หมายเหตุ
- ข้อมูลทั้งหมดอยู่ในเครื่องของคุณเอง ไม่ได้ส่งขึ้นอินเทอร์เน็ต
- ใช้ได้ในเครือข่าย Wi-Fi บ้าน/ร้านเท่านั้น ไม่ควรเปิดพอร์ตออกอินเทอร์เน็ต
- ลิงก์/QR มีรหัสเฉพาะเครื่อง คนที่ไม่มีลิงก์จะส่งไฟล์เข้ามาไม่ได้
- ค่าตั้งค่า (รหัสและโฟลเดอร์) เก็บที่ %APPDATA%\\DocScan\\config.json — ลบไฟล์นี้ถ้าต้องการเริ่มใหม่ (QR/ลิงก์จะเปลี่ยน)
"""


def main():
    py = [sys.executable, '-m', 'PyInstaller']
    for d in (WORK, os.path.join(HERE, 'dist')):
        shutil.rmtree(d, ignore_errors=True)
    cmd = py + ['--noconfirm', '--clean', '--onefile', '--noconsole', '--name', 'DocScan',
                '--distpath', os.path.join(WORK, 'dist'), '--workpath', os.path.join(WORK, 'work'), '--specpath', WORK,
                '--add-data', os.path.join(HERE, 'index.html') + os.pathsep + '.',
                '--hidden-import', 'tkinter', '--hidden-import', 'qrcode',
                '--exclude-module', 'PIL', '--exclude-module', 'numpy', '--exclude-module', 'matplotlib',
                os.path.join(HERE, 'server.py')]
    subprocess.check_call(cmd, cwd=HERE)
    out = os.path.join(REL, 'DocScan')
    shutil.rmtree(REL, ignore_errors=True)
    os.makedirs(out)
    shutil.copy(os.path.join(WORK, 'dist', 'DocScan.exe'), os.path.join(out, 'DocScan.exe'))
    with open(os.path.join(out, u'อ่านก่อน.txt'), 'w', encoding='utf-8-sig', newline='\r\n') as f:
        f.write(README.format(ver=version()))
    zp = os.path.join(REL, 'DocScan.zip')
    with zipfile.ZipFile(zp, 'w', zipfile.ZIP_DEFLATED) as z:
        for n in os.listdir(out):
            z.write(os.path.join(out, n), os.path.join('DocScan', n))
    shutil.rmtree(WORK, ignore_errors=True)
    print('OK', version(), os.path.getsize(os.path.join(out, 'DocScan.exe')) // 1024, 'KB exe;', os.path.getsize(zp) // 1024, 'KB zip ->', zp)


if __name__ == '__main__':
    main()
