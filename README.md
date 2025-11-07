# UX Daily Newsletter (GitHub Actions + SMTP + Notion archiv)

Denní odesílání stručného UX/UI přehledu e‑mailem v **8:30 Europe/Prague**.
- Pokud je k dispozici `content/YYYY-MM-DD.html|.txt` → pošle to.
- Pokud není, stáhne RSS zdroje a (volitelně) shrne přes LLM (`OPENAI_API_KEY`).
- Volitelně uloží záznam do **Notion** databáze (datum, subject, preheader, HTML a plain text).

## 1) Secrets v repo
Přidej v **Settings → Secrets and variables → Actions**:
- `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS`
- `FROM_EMAIL` (např. `UX Daily <news@tvoje-domena.cz>`)
- `TO_EMAILS` (`a@x.cz, b@y.cz`)
- `OPENAI_API_KEY` (volitelné, pro kvalitní shrnutí)
- `AREAS` (např. `přístupnost, design systémy, Figma, AI v UX`)
- `NOTION_TOKEN` a `NOTION_DATABASE_ID` (volitelné, pro archiv)

## 2) Notion šablona
Vytvoř databázi v Notion (Table) s minimálně těmito vlastnostmi:
- `Date` (Date)
- `Subject` (Title)
- `Preheader` (Rich text)
- `Source` (Select) – `Prepared` nebo `Generated`
- `HTML` (Rich text / nebo Files & media; případně uložit do body)
- `Plain` (Rich text)

Poznamenej si `Database ID` (z URL) a vlož jako `NOTION_DATABASE_ID`. Přidej integrační token do daného Space (Connections) a vlož jako `NOTION_TOKEN`.

## 3) Lokální běh
```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SMTP_HOST=...
export SMTP_PORT=587
export SMTP_USER=...
export SMTP_PASS=...
export FROM_EMAIL="UX Daily <news@tvoje-domena.cz>"
export TO_EMAILS="ty@domena.cz"
python send_digest.py
```

## 4) Časování
GitHub Actions běží na UTC. Workflow je v 07:30 UTC, skript navíc hlídá lokální čas (`TIME_GUARD=on`) → odešle jen kolem 08:30 v `Europe/Prague`.

## 5) Bezpečnost & doručitelnost
Použij dedikovaný SMTP (Postmark/SendGrid), nastav SPF/DKIM/DMARC, double opt‑in a patičku s opt‑outem („STOP“).

---

Happy shipping!
