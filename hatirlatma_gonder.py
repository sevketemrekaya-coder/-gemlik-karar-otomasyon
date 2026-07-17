# -*- coding: utf-8 -*-
"""
Gemlik Belediyesi - Karar Takip Sistemi - Otomatik Hatırlatma Gönderici
------------------------------------------------------------------------
Bu script, kararlar.json dosyasındaki kararları kontrol eder ve:
  - Termine 5 gün veya daha az kalan kararlar için,
  - Süresi geçmiş ve son hatırlatmadan bu yana 30+ gün geçmiş kararlar için
sorumlu müdürlüğe (ve varsa takip eden başkan yardımcısına CC olarak) otomatik
e-posta hatırlatması gönderir. Gönderilen her hatırlatma kararlar.json içine
işlenir (aynı kararı tekrar tekrar göndermemek için).

Bu script GitHub Actions tarafından günlük olarak otomatik çalıştırılır
(bkz. .github/workflows/hatirlatma.yml). Sizin bilgisayarınızın veya bu
sohbetin açık olması gerekmez.

Ortam değişkenleri (GitHub Secrets üzerinden gelir, koda YAZILMAZ):
  SMTP_HOST  -> örn. mail.kurumsaleposta.com
  SMTP_PORT  -> örn. 587
  SMTP_USER  -> gönderen kurumsal e-posta adresi (örn. teftis@gemlik.bel.tr)
  SMTP_PASS  -> o hesabın şifresi
"""
import json
import os
import smtplib
import ssl
from datetime import datetime, date, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

DATA_FILE = "kararlar.json"
SETTINGS_FILE = "settings.json"

SMTP_HOST = os.environ.get("SMTP_HOST", "mail.kurumsaleposta.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def find_dept(depts, ad):
    for d in depts:
        if d.get("ad") == ad:
            return d
    return None


def find_deputy(deputies, ad):
    for d in deputies:
        if d.get("ad") == ad:
            return d
    return None


def termin_of(karar):
    if not karar.get("baslangicTarihi") or not karar.get("sureGun"):
        return None
    try:
        b = date.fromisoformat(karar["baslangicTarihi"])
        return b + timedelta(days=int(karar["sureGun"]))
    except Exception:
        return None


def gun_farki(hedef_tarih):
    return (hedef_tarih - date.today()).days


def son_hatirlatma_gun_once(karar):
    log = karar.get("hatirlatmalar") or []
    if not log:
        return None
    last = log[-1]["tarih"]
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00")).date()
    except Exception:
        return None
    return (date.today() - last_dt).days


def gonderilmesi_gereken_kararlar(kararlar):
    sonuc = []
    for k in kararlar:
        if k.get("durum") in ("Tamamlandı", "İptal", "İptal Edildi"):
            continue
        termin = termin_of(k)
        if not termin:
            continue
        kalan = gun_farki(termin)
        if kalan > 5:
            continue
        if kalan >= 0:
            sonuc.append((k, termin, kalan))
            continue
        # gecikmiş -> ayda bir
        gecen = son_hatirlatma_gun_once(k)
        if gecen is None or gecen >= 30:
            sonuc.append((k, termin, kalan))
    return sonuc


def mail_gövdesi(karar, termin, dept):
    mudur = dept.get("mudur") if dept else ""
    hitap = f"Sayın {mudur}," if mudur else "Sayın Yetkili,"
    adimlar = karar.get("surecAdimlari") or []
    adim_metni = "\n".join(f"{i+1}. {a}" for i, a in enumerate(adimlar))
    return f"""{hitap}

{karar.get('toplantiTarihi','-')} tarihli müdürler toplantısında alınan aşağıdaki kararın takibi hakkında bilgilerinize sunulur:

Karar No: {karar.get('kararNo','-')}
Konu: {karar.get('gundemKonusu') or '-'}
Karar: {karar.get('kararMetni','-')}
Sorumlu Müdürlük: {karar.get('mudurluk','-')}
Termin Tarihi: {termin.strftime('%d.%m.%Y')}
Güncel Durum: {karar.get('durum','-')}

Süreç Adımları:
{adim_metni}

Bu e-posta, Gemlik Belediyesi Karar Takip Sistemi tarafından otomatik olarak gönderilmiştir.
Süreç hakkında güncel durumun sisteme işlenmesi rica olunur.

Teftiş Kurulu Müdürlüğü
"""


def mail_gonder(smtp, to_addr, cc_addr, subject, body):
    msg = MIMEMultipart()
    msg["From"] = SMTP_USER
    msg["To"] = to_addr
    if cc_addr:
        msg["Cc"] = cc_addr
    msg["Subject"] = str(Header(subject, "utf-8"))
    msg.attach(MIMEText(body, "plain", "utf-8"))
    alicilar = [to_addr] + ([cc_addr] if cc_addr else [])
    smtp.sendmail(SMTP_USER, alicilar, msg.as_string())


def main():
    if not SMTP_USER or not SMTP_PASS:
        raise SystemExit("HATA: SMTP_USER / SMTP_PASS ortam değişkenleri (GitHub Secrets) tanımlı değil.")

    kararlar = load_json(DATA_FILE, [])
    settings = load_json(SETTINGS_FILE, {"depts": [], "deputies": []})
    depts = settings.get("depts", [])
    deputies = settings.get("deputies", [])

    hedefler = gonderilmesi_gereken_kararlar(kararlar)
    if not hedefler:
        print("Bugün gönderilecek hatırlatma yok.")
        return

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls(context=context)
        smtp.login(SMTP_USER, SMTP_PASS)

        gonderilen = 0
        for karar, termin, kalan in hedefler:
            dept = find_dept(depts, karar.get("mudurluk"))
            deputy = find_deputy(deputies, karar.get("baskanYardimcisi"))
            to_addr = dept.get("email") if dept else None
            if not to_addr:
                print(f"ATLANDI (e-posta yok): {karar.get('kararNo')} - {karar.get('mudurluk')}")
                continue
            cc_addr = deputy.get("email") if deputy else None
            subject = f"[Gemlik Belediyesi] Karar Takip Hatırlatması – {karar.get('kararNo')}"
            body = mail_gövdesi(karar, termin, dept)
            try:
                mail_gonder(smtp, to_addr, cc_addr, subject, body)
                karar.setdefault("hatirlatmalar", []).append({
                    "tarih": datetime.now().isoformat(),
                    "kanal": "e-posta (otomatik)"
                })
                gonderilen += 1
                print(f"GÖNDERİLDİ: {karar.get('kararNo')} -> {to_addr}")
            except Exception as e:
                print(f"HATA ({karar.get('kararNo')}): {e}")

    save_json(DATA_FILE, kararlar)
    print(f"Toplam {gonderilen} hatırlatma gönderildi.")


if __name__ == "__main__":
    main()
