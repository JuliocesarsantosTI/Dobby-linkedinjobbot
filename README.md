# LinkedIn Job Bot (Telegram)  with Dobby Fireworks API  Analysis
A Telegram bot that searches for public LinkedIn job posts through Google (SerpAPI) and analyzes takes each one through Fireworks AI. It summarizes the role tasks, rates fit from 1 to 5 stars, provides a recommendation, sorts from best to worse, and deliveries everything in clean Telegram cards. 

bot that finds public **LinkedIn job postings** via Google (**SerpAPI**) and runs **Fireworks AI** to analyze each result.  
You get clean Telegram cards with a **Recommendation**, a **Fireworks-authored Explanation**, and a **1–5 star rating**. Results are sorted **best → worst**.



## ✨ Features

- `/search <keywords [location]>` — find LinkedIn job pages via Google/SerpAPI  
- AI analysis (Fireworks) returns:
  - Languages / stack (strings)
  - Job type (FT/Contract/etc.)
  - Experience level & seniority
  - Must-have & Nice-to-have
  - Relevance score (0..1)
  - ✅/⚠️ Recommendation
  - **⭐ Star rating (1–5) decided by Fireworks**
  - **Detailed Explanation written entirely by Fireworks** (no local rewriting)
- Clean HTML cards in Telegram
- Results sorted by **stars** then **relevance**

---

## 🧩 Architecture

```
Telegram user
   │
   ├─➤ python-telegram-bot (webhook/polling)
   │
   ├─➤ search_jobs() → SerpAPI (Google “site:linkedin.com/jobs/view ...”)
   │          │
   │          └─ returns top LinkedIn job links + snippets
   │
   └─➤ analyze_with_fireworks() → Fireworks Chat Completions
              └─ returns structured JSON + star_rating + full Explanation
```

---

## 🚀 Quick Start

### 1) Clone

```bash
git clone https://github.com/JuliocesarsantosTI/Dobby-linkedinjobbot.git
cd Dobby-linkedinjobbot
```

### 2) Python & deps

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install python-telegram-bot==21.4 requests python-dotenv
```

> `python-dotenv` is optional but recommended for local development with a `.env` file.

### 3) Configure secrets (strongly recommended)

Create a `.env` file **(do not commit!)**:

```
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
FIREWORKS_API_KEY=YOUR_FIREWORKS_API_KEY
SERPAPI_KEY=YOUR_SERPAPI_KEY

# Optional overrides
FIREWORKS_URL=https://api.fireworks.ai/inference/v1/chat/completions
FIREWORKS_MODEL=accounts/fireworks/models/llama-v3p1-70b-instruct
MAX_RESULTS=6
ANALYZE_DELAY_SEC=1.0
```



### 4) Run (polling)

```bash
python linkedinjobbot.py
```

You should see `Bot is running…` in logs.

---

## 🤖 Telegram Commands

- `/start` — welcome & help
- `/help` — help text
- `/setkeywords <words>` — set default keywords (e.g. `python backend django`)
- `/setlocation <place>` — set default location (e.g. `Berlin`, `Remote`)
- `/search <query>` — run a search (uses defaults if omitted)

You can also send plain text (treated like `/search your text`).

---

## 🧠 Fireworks Analysis

- The bot sends **KEYWORDS, TITLE, LINK, SOURCE, SNIPPET** to Fireworks and **requires** a structured JSON response:
  - `languages`, `job_type`, `experience_level`, `seniority`, `must_have`, `nice_to_have`, `relevance`, `recommended`, `star_rating`, `reason`, `explanation`.
- **Explanation** (4–8 sentences) is **written entirely by Fireworks** and displayed verbatim (HTML-escaped).
- **Star rating (1–5)** is **decided by Fireworks**. The bot only clamps to `[1..5]` and renders stars (`★★★★☆ (4/5)`).

If Fireworks fails (network/HTTP), the bot shows a minimal fallback so the chat still responds.

---

## 🔎 Search (SerpAPI)

- The bot queries Google with: `site:linkedin.com/jobs/view <keywords> [location]`.
- It filters out non-job pages (e.g., “Top 10 jobs…” roundups).
- Returns up to `MAX_RESULTS` (default 6).

> You will need a **SerpAPI** key: https://serpapi.com

---

