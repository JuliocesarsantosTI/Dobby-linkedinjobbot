from __future__ import annotations

import html as _html
import json
import logging
import random
import re
import textwrap
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import asyncio
import itertools
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import os
from dotenv import load_dotenv
load_dotenv()


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
FIREWORKS_MODEL = "accounts/fireworks/models/llama-v3p1-70b-instruct"

MAX_RESULTS = 6
ANALYZE_DELAY_SEC = 1.0


logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
LOGGER = logging.getLogger("jobbot")


FALLBACK_SNIPPET = "(no snippet provided)"

@dataclass
class JobHit:
    title: str
    link: str
    snippet: str
    source: str = "LinkedIn"

@dataclass
class JobAnalysis:
    languages: List[str]
    job_type: str
    experience_level: str
    seniority: Optional[str]
    must_have: List[str]
    nice_to_have: List[str]
    relevance: float  # 0..1
    recommended: bool = False
    reason: str = ""
    explanation: str = ""   
    star_rating: Optional[int] = None 


SYSTEM_PROMPT = (
    "You are an expert HR analyst. Examine ALL provided fields for a job listing and produce a "
    "complete, independent analysis using ONLY the given text (no browsing or external lookups). "
    "Return STRICTLY valid, compact JSON with this EXACT schema:\n"
    "{\n"
    '  "languages": string[],\n'
    '  "job_type": string,\n'
    '  "experience_level": string,\n'
    '  "seniority": string|null,\n'
    '  "must_have": string[],\n'
    '  "nice_to_have": string[],\n'
    '  "relevance": number,\n'
    '  "recommended": boolean,\n'
    '  "star_rating": integer,\n'
    '  "reason": string,\n'
    '  "explanation": string\n'
    "}\n"
    "Scoring & Explanation guidelines:\n"
    "- Treat KEYWORDS as the candidate's target and calibrate scores consistently across results.\n"
    "- If info is not present in the input, use Unknown/empty; do NOT invent facts.\n"
    "- recommended=true if relevance >= 0.6 or the title clearly matches core KEYWORDS.\n"
    "- star_rating is 1..5 (5=best fit) reflecting overall fit and confidence.\n"
    "- explanation MUST be thorough (4–8 sentences): describe role focus, responsibilities, expected stack, seniority/level, "
    "employment type (remote/hybrid/on-site if hinted), any visa/relocation cues, compensation if hinted, impact/scope, "
    "why this matches or misses KEYWORDS, and call out risks/ambiguities or missing information. "
    "Write neutrally and concisely; no markdown, no bullets.\n"
    "Output JSON ONLY — no extra text, no code fences."
)

def _extract_json(text: str) -> str:
    """Strip possible code fences and isolate the outer JSON object."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1 :].strip()
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start : end + 1]
    return t

def _post_fireworks_with_retry(url: str, headers: dict, payload: dict,
                               max_retries: int = 4, base_delay: float = 1.0):
    """Exponential backoff + jitter for 429/5xx. Raises for other errors."""
    attempt = 0
    while True:
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            try:
                resp.raise_for_status()
            except requests.HTTPError:
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                    LOGGER.warning("Fireworks %s. Retrying in %.2fs (attempt %d/%d)...",
                                   resp.status_code, delay, attempt + 1, max_retries)
                    time.sleep(delay)
                    attempt += 1
                    continue
                raise
            return resp
        except (requests.Timeout, requests.ConnectionError):
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                LOGGER.warning("Fireworks network error. Retrying in %.2fs (attempt %d/%d)...",
                               delay, attempt + 1, max_retries)
                time.sleep(delay)
                attempt += 1
                continue
            raise

def clamp_star(n: Optional[int]) -> int:
    try:
        n = int(n) if n is not None else 0
    except Exception:
        n = 0
    return min(5, max(1, n if n else 1))

def render_stars(n: int) -> str:
    n = max(1, min(5, int(n)))
    return "★" * n + "☆" * (5 - n)


def analyze_with_fireworks(keywords: str, hit: JobHit) -> JobAnalysis:
    if not FIREWORKS_API_KEY:
        raise RuntimeError("FIREWORKS_API_KEY is not set.")

    user_prompt = textwrap.dedent(f"""
    KEYWORDS: {keywords}

    TITLE: {hit.title}
    LINK: {hit.link}
    SOURCE: {hit.source}
    SNIPPET: {hit.snippet or FALLBACK_SNIPPET}
    """)

    headers = {
        "Authorization": f"Bearer {FIREWORKS_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": FIREWORKS_MODEL,
        "temperature": 0.0,
        "max_tokens": 700,  
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }

    try:
        resp = _post_fireworks_with_retry(FIREWORKS_URL, headers, payload)
        j = resp.json()
        content = j["choices"][0]["message"]["content"].strip()
        content = _extract_json(content)
        data = json.loads(content)
    except Exception as e:
        LOGGER.warning("Fireworks analysis failed for '%s' (%s). Using conservative fallback.",
                       hit.title, type(e).__name__)
        return JobAnalysis(
            languages=[],
            job_type="Unknown",
            experience_level="Unknown",
            seniority=None,
            must_have=[],
            nice_to_have=[],
            relevance=0.0,
            recommended=False,
            reason=f"API error: {type(e).__name__}",
            explanation="Could not analyze this listing.",
            star_rating=1,
        )

    langs = [str(x) for x in data.get("languages", [])]
    job_type = str(data.get("job_type", "Unknown"))
    experience_level = str(data.get("experience_level", "Unknown"))
    seniority = data.get("seniority")
    seniority = (str(seniority) if seniority not in (None, "") else None)
    must_have = [str(x) for x in data.get("must_have", [])]
    nice_to_have = [str(x) for x in data.get("nice_to_have", [])]
    relevance = float(data.get("relevance", 0.0))
    recommended = bool(data.get("recommended", False))
    reason = str(data.get("reason", ""))
    explanation = str(data.get("explanation", ""))  
    star = clamp_star(data.get("star_rating"))

    return JobAnalysis(
        languages=langs,
        job_type=job_type,
        experience_level=experience_level,
        seniority=seniority,
        must_have=must_have,
        nice_to_have=nice_to_have,
        relevance=relevance,
        recommended=recommended,
        reason=reason,
        explanation=explanation,   
        star_rating=star,
    )


def search_jobs(query: str, location: Optional[str] = None, limit: int = MAX_RESULTS) -> List[JobHit]:
    if not SERPAPI_KEY:
        raise RuntimeError("SERPAPI_KEY not set. Get one at serpapi.com and set SERPAPI_KEY.")

    q_parts = ["site:linkedin.com/jobs/view", query]
    if location:
        q_parts.append(location)
    q = " ".join(q_parts)

    params = {
        "engine": "google",
        "q": q,
        "num": max(20, limit), 
        "api_key": SERPAPI_KEY,
        "safe": "active",
    }
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
    r.raise_for_status()
    data = r.json()

    results: List[JobHit] = []
    for item in (data.get("organic_results") or [])[: max(20, limit)]:
        link = (item.get("link") or "").strip()
        title = (item.get("title") or "Untitled").strip()
        snippet = (item.get("snippet") or FALLBACK_SNIPPET).strip()

        if "linkedin.com/jobs/view/" not in link:
            continue
        if re.search(r"Today.?s top|Top \d+|jobs in ", title, flags=re.I):
            continue

        results.append(JobHit(title=title, link=link, snippet=snippet))
        if len(results) >= limit:
            break
    return results


class Session:
    def __init__(self) -> None:
        self.keywords: str = ""
        self.location: Optional[str] = None
        self.last_results: List[Tuple[JobHit, JobAnalysis]] = []

SESSIONS: Dict[int, Session] = {}

def get_session(chat_id: int) -> Session:
    if chat_id not in SESSIONS:
        SESSIONS[chat_id] = Session()
    return SESSIONS[chat_id]


def format_job_card(hit: JobHit, ana: JobAnalysis) -> str:
    must = ", ".join(ana.must_have[:8]) or "Not specified"
    langs = ", ".join(ana.languages) or "Not specified"
    nice = ", ".join(ana.nice_to_have[:8]) or "Not specified"
    rel = f"{ana.relevance:.0%}"
    status = "✅ Suitable" if ana.recommended else "⚠️ Not a fit"

    rating_num = clamp_star(ana.star_rating)
    rating_stars = render_stars(rating_num)

    title = _html.escape(hit.title or "Untitled")
    snippet = _html.escape(hit.snippet or FALLBACK_SNIPPET)
    link = _html.escape(hit.link or "#", quote=True)
    level = _html.escape(ana.experience_level or "Unknown")
    job_type = _html.escape(ana.job_type or "Unknown")
    seniority = f" ({_html.escape(ana.seniority)})" if ana.seniority else ""
    reason = _html.escape(ana.reason or "")
    explanation = _html.escape(ana.explanation or "")  # use Fireworks text as-is

    html = textwrap.dedent(f"""
    <b>{title}</b> — <i>{hit.source}</i>
    <a href="{link}">Open on LinkedIn</a>

    <b>— Recommendation —</b>
    {status}
    <i>{reason}</i>

    <b>— Rating —</b>
    {rating_stars} ({rating_num}/5)

    <b>— Explanation —</b>
    {explanation}

    <b>— Analysis —</b>
    <b>Languages:</b> {langs}
    <b>Type:</b> {job_type}  <b>Level:</b> {level}{seniority}
    <b>Must-have:</b> {must}
    <b>Nice-to-have:</b> {nice}
    <b>Relevance:</b> {rel}

    <b>— Snippet —</b>
    {snippet}
    """)
    return html.strip()


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = textwrap.dedent(
        """
        👋 Hi I search LinkedIn job postings by keywords and run AI analysis via DOBBY API Fireworks.

        Commands:
        • /setkeywords <words> – set default keywords
        • /setlocation <place> – set default location (e.g., Berlin, Remote)
        • /search <query> – search now (uses defaults if omitted)
        • /help – show help

        Examples:
        /setkeywords python backend django
        /setlocation Berlin
        /search senior python developer
        """
    ).strip()
    await update.message.reply_text(msg)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)

async def cmd_setkeywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    sess = get_session(chat_id)
    if context.args:
        sess.keywords = " ".join(context.args)
        await update.message.reply_text(
            f"✅ Keywords set to: <b>{_html.escape(sess.keywords)}</b>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("Usage: /setkeywords <words>")

async def cmd_setlocation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    sess = get_session(chat_id)
    if context.args:
        sess.location = " ".join(context.args)
        await update.message.reply_text(
            f"✅ Location set to: <b>{_html.escape(sess.location)}</b>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("Usage: /setlocation <place>")

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    sess = get_session(chat_id)

    query_text = " ".join(context.args) if context.args else sess.keywords
    if not query_text:
        await update.message.reply_text("Please provide keywords or set defaults with /setkeywords <words>.")
        return

    await update.message.reply_text("🔎 Searching LinkedIn jobs and analyzing (Fireworks)…")
    try:
        loop = asyncio.get_running_loop()
        hits = await loop.run_in_executor(None, search_jobs, query_text, sess.location, MAX_RESULTS)
    except Exception as e:
        LOGGER.exception("Search error")
        await update.message.reply_text(f"❌ Search error: {e}")
        return

    analyzed: List[Tuple[JobHit, JobAnalysis]] = []
    for hit in hits:
        try:
            ana = await loop.run_in_executor(None, analyze_with_fireworks, query_text, hit)
            LOGGER.info(
                "Analyzed: %s -> stars=%s relevance=%.2f %s",
                hit.title,
                ana.star_rating,
                ana.relevance,
                "✅" if ana.recommended else "⚠️"
            )
        except Exception as e:
            LOGGER.warning("Analysis failed on one item: %s", e)
            ana = JobAnalysis(
                languages=[], job_type="Unknown", experience_level="Unknown", seniority=None,
                must_have=[], nice_to_have=[], relevance=0.0, recommended=False,
                reason="analysis failed", explanation="Could not analyze this listing.", star_rating=1
            )
        analyzed.append((hit, ana))
        await asyncio.sleep(ANALYZE_DELAY_SEC)

    analyzed.sort(key=lambda t: (t[1].star_rating or 0, t[1].relevance), reverse=True)
    sess.last_results = analyzed

    if not analyzed:
        await update.message.reply_text("No results found. Try broader keywords or another location.")
        return

    top = analyzed[:5]
    for hit, ana in top:
        await update.message.reply_html(format_job_card(hit, ana), disable_web_page_preview=False)

    if len(analyzed) > 5:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Show more", callback_data="show_more")]])
        await update.message.reply_text("More results available:", reply_markup=kb)

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    sess = get_session(chat_id)

    if query.data == "show_more":
        rest = sess.last_results[5:10]
        if not rest:
            await query.edit_message_text("No more results.")
            return
        for hit, ana in rest:
            await query.message.reply_html(format_job_card(hit, ana), disable_web_page_preview=False)
        await query.edit_message_text("Shown more results.")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.args = update.message.text.split()
    await cmd_search(update, context)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set.")
    if not FIREWORKS_API_KEY:
        LOGGER.warning("FIREWORKS_API_KEY is not set – analysis will fail.")
    if not SERPAPI_KEY:
        LOGGER.warning("SERPAPI_KEY is not set – search will fail.")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("setkeywords", cmd_setkeywords))
    app.add_handler(CommandHandler("setlocation", cmd_setlocation))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    LOGGER.info("Bot is running…")
    app.run_polling()

if __name__ == "__main__":
    main()
