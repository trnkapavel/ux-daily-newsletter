import os, smtplib, ssl, feedparser, requests, re, json, yaml
from email.message import EmailMessage
from datetime import datetime
from dateutil import tz
from bs4 import BeautifulSoup
import pytz

# --- Parametry a čas ---
TZ = os.getenv("TIMEZONE", "Europe/Prague")
now_prg = datetime.now(pytz.timezone(TZ))
today_str = now_prg.strftime("%Y-%m-%d")
date_human = now_prg.strftime("%-d. %-m. %Y")

# Volitelný guard: pokud běží workflow častěji, posílej jen kolem 08:30
GUARD = os.getenv("TIME_GUARD", "off").lower() == "on"
if GUARD and not (now_prg.hour == 8 and now_prg.minute in (29, 30, 31)):
    print("Not the scheduled minute; exiting.")
    raise SystemExit(0)

SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]
FROM_EMAIL = os.environ["FROM_EMAIL"]
TO_EMAILS = [e.strip() for e in os.environ["TO_EMAILS"].split(",")]

AREAS = [a.strip().lower() for a in os.getenv("AREAS","").split(",") if a.strip()]
SUBJECT_TEMPLATE = os.getenv("SUBJECT_TEMPLATE", "UX Daily · {{date}}")
PREHEADER_TEMPLATE = os.getenv("PREHEADER_TEMPLATE", "Denní UX přehled")

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "").strip()
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "").strip()

def render_subject(date_human):
    return SUBJECT_TEMPLATE.replace("{{date}}", date_human)

def html_shell(inner_html, preheader):
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width">
<title>UX Daily</title>
<style>
  body {{ margin:0; padding:0; background:#f6f6f8; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; }}
  .wrap {{ max-width:600px; margin:0 auto; background:#ffffff; }}
  .preheader {{ display:none !important; visibility:hidden; opacity:0; height:0; width:0; }}
  .inner {{ padding:24px; }}
  h1 {{ font-size:22px; margin:0 0 12px; }}
  h2 {{ font-size:18px; margin:24px 0 8px; }}
  p, li {{ font-size:15px; line-height:1.5; color:#222; }}
  a {{ color:#0b5ad9; text-decoration:none; }}
  .footer {{ color:#666; font-size:12px; padding:16px 24px 24px; }}
</style>
</head>
<body>
  <span class="preheader">{preheader}</span>
  <div class="wrap">
    <div class="inner">
      {inner_html}
    </div>
    <div class="footer">
      Odesláno automaticky • {date_human} • Pokud nechceš dostávat tento e-mail, odpověz „STOP“.
    </div>
  </div>
</body>
</html>"""

def to_plaintext(html):
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text

def try_read_prepared():
    base = f"content/{today_str}"
    html, txt, meta = None, None, {}
    try:
        with open(base + ".html", "r", encoding="utf-8") as f:
            html = f.read()
    except FileNotFoundError:
        pass
    try:
        with open(base + ".txt", "r", encoding="utf-8") as f:
            txt = f.read()
    except FileNotFoundError:
        pass
    try:
        with open(base + ".json", "r", encoding="utf-8") as f:
            meta = json.load(f)
    except FileNotFoundError:
        pass
    return html, txt, meta

def fetch_sources():
    with open("sources.yml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg["feeds"]

def pick_articles(feeds, limit_total=12):
    items = []
    for feed in feeds:
        d = feedparser.parse(feed["url"])
        for e in d.entries[:5]:
            title = e.title
            link = e.link
            summary = BeautifulSoup(getattr(e, "summary", "") or "", "html.parser").get_text(" ")
            item = {
                "source": feed["name"],
                "title": title,
                "link": link,
                "summary": summary[:280],
            }
            items.append(item)
    def score(it):
        s = 0
        full = (it["title"] + " " + it["summary"]).lower()
        for a in AREAS:
            if a and a in full:
                s += 2
        if it["source"] in ("W3C/WCAG","Figma Blog","Material Design","Apple HIG","Nielsen Norman Group"):
            s += 1
        return s
    items.sort(key=score, reverse=True)
    return items[:limit_total]

def llm_summarize(bullets):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        prompt = (
            "Shrň následující UX/UI novinky do 3–6 bodů (co se stalo, proč je to důležité, co s tím). "
            "Piš česky, stručně, bez marketingu. Vrať HTML seznam <ul><li>…</li></ul> "
            "a u každé položky ponech zdrojový odkaz:\n\n" + "\n".join(
                [f"- {b['title']} – {b['link']}" for b in bullets]
            )
        )
        rsp = client.chat.completions.create(
            model="gpt-5-instant",
            messages=[{"role":"user","content":prompt}],
            temperature=0.3,
            max_tokens=700
        )
        return rsp.choices[0].message.content
    except Exception as e:
        print("LLM summary failed:", e)
        return None

def build_fallback_html(bullets):
    lis = ""
    for b in bullets[:6]:
        lis += f"<li><strong><a href='{b['link']}'>{b['title']}</a></strong> — <em>{b['source']}</em></li>"
    return f"<h1>UX Daily · {date_human}</h1><h2>Denní UX přehled</h2><ul>{lis}</ul>"

def notion_append(subject, preheader, html_inner, plain_text, source_tag):
    if not (NOTION_TOKEN and NOTION_DATABASE_ID):
        return
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    payload = {
        "parent": { "database_id": NOTION_DATABASE_ID },
        "properties": {
            "Subject": { "title": [ { "text": { "content": subject } } ] },
            "Preheader": { "rich_text": [ { "text": { "content": preheader[:200] } } ] },
            "Date": { "date": { "start": now_prg.strftime("%Y-%m-%d") } },
            "Source": { "select": { "name": source_tag } }
        },
        "children": [
            {
                "object": "block", "type": "paragraph",
                "paragraph": { "rich_text": [ { "text": { "content": "HTML:" } } ] }
            },
            {
                "object": "block", "type": "code",
                "code": { "language": "html", "rich_text": [ { "text": { "content": html_inner } } ] }
            },
            {
                "object": "block", "type": "paragraph",
                "paragraph": { "rich_text": [ { "text": { "content": "Plain text:" } } ] }
            },
            {
                "object": "block", "type": "code",
                "code": { "language": "plain text", "rich_text": [ { "text": { "content": plain_text } } ] }
            }
        ]
    }
    try:
        r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=20)
        print("Notion status:", r.status_code, r.text[:200])
    except Exception as e:
        print("Notion append failed:", e)

def send_email(subject, preheader, html_inner):
    html = html_shell(html_inner, preheader)
    text = to_plaintext(html)

    # Notion archiv (volitelně)
    notion_append(subject, preheader, html_inner, text, "Prepared" if os.path.exists(f"content/{today_str}.html") else "Generated")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = ", ".join(TO_EMAILS)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls(context=ctx)
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)
    print("Sent:", subject, "to", TO_EMAILS)

# --- Hlavní běh ---
def main():
    base = f"content/{today_str}"
    subject = render_subject(date_human)
    preheader = PREHEADER_TEMPLATE

    html_prepared, txt_prepared, meta = None, None, {}
    try:
        with open(base + ".html", "r", encoding="utf-8") as f:
            html_prepared = f.read()
    except FileNotFoundError:
        pass
    try:
        with open(base + ".txt", "r", encoding="utf-8") as f:
            txt_prepared = f.read()
    except FileNotFoundError:
        pass
    try:
        with open(base + ".json", "r", encoding="utf-8") as f:
            meta = json.load(f)
    except FileNotFoundError:
        pass

    if meta:
        subject = meta.get("subject", subject)
        preheader = meta.get("preheader", preheader)

    if html_prepared:
        inner = html_prepared
    else:
        feeds = fetch_sources()
        picks = pick_articles(feeds)
        summarized = llm_summarize(picks)
        inner = summarized if summarized else build_fallback_html(picks)

    send_email(subject, preheader, inner)

if __name__ == "__main__":
    main()
