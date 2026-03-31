"""VAULT Transaction Ingestion Agent.

Searches Gmail for financial emails, uses Claude to classify each
transaction, and stores results in Supabase.
"""

import os
import imaplib
import email
import traceback
from email.header import decode_header
from datetime import datetime, timedelta

from agents.shared import get_supabase, call_claude, rpc, parse_json_response

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are VAULT, a financial parsing engine for Marcos Matthews. He files two Schedule C businesses.

IMPORTANT CONTEXT: All transactions flow through Marcos's PERSONAL Bank of America accounts (checking ••7288, savings ••7327, Grey Card ••3224, Red Card ••9027) and Apple Card. He does NOT have separate business bank accounts for coaching — all income and expenses are mixed in personal accounts.

SCHEDULE C #1 — WRYKO (B2B SaaS platform, EIN 41-4275726)
ALL digital subscriptions and SaaS tools are Wryko expenses. This includes:
AI APIs (Anthropic, OpenAI, xAI, Perplexity), cloud infrastructure (Vercel, Supabase, Railway, Render),
automation tools (n8n, Make.com, Trigger.dev, Lindy.ai), email tools (Instantly.ai, Reacher),
design tools (Figma, Canva, Adobe, Mobbin, Tailwind UI, 21st.dev, Beautiful.ai),
dev tools (Replit, Windsurf, Lovable, Bolt/StackBlitz, Cursor),
domains & hosting (GoDaddy, Wix, Squarespace, Hostinger, Porkbun, Cloudflare),
freelancers (Upwork), payments (Stripe, Shopify, Lemon Squeezy),
workspace (Notion, GitHub, Zoom, Google Workspace),
monitoring (Sentry, BetterUptime), and any other software/SaaS subscription.

SCHEDULE C #2 — SOCCER COACHING (youth teams G2012, B2015 + private training)
Income: ANY Venmo or Zelle payment >= $40 from a person (not a company) is coaching income.
Sources: Ventura Football Club stipends, Simi Valley Soccer Club training fees, private sessions.
Deductible expenses: training equipment, field rental (Next Soccer Park), coaching gear, sports apps.

PERSONAL (NOT deductible — per CPA Courtney Matthews):
- Xbox/Microsoft gaming, Quizlet, Spotify, Discord Nitro — all personal
- Food/dining is personal UNLESS flagged as business meal (22.5% rule applies separately)
- Venmo payments < $40 are personal (Starbucks, splitting bills, etc.)
- Gym memberships (Gold's Gym, EOS) — personal
- Clothing, entertainment, personal travel

ACCOUNT MAPPING (use these exact account names):
- Anthropic, Supabase, GoDaddy, Canva, Cloudflare, Google, Vercel, n8n, Replit, Figma, Adobe, Zoom, Notion, Perplexity, xAI, Wix, Squarespace → "BofA Gray Card"
- Instantly, Upwork, LinkedIn Premium, Tailwind UI, Beautiful.ai, Mobbin, Windsurf, Lovable, 21st.dev → "BofA Red Card"
- Toyota, Verizon, AAA Insurance, Zelle income, Venmo income, Chaminade payroll → "BofA Adv Plus Banking"
- Apple subscriptions, Apple.com/bill → "Apple Card"
- Stripe deposits → "BofA Advantage Savings"
- Mercury charges → "Mercury Credit Card"
- If truly unknown, use "unknown" but try to match the vendor first

RULES:
- ALL SaaS/digital subscriptions = wryko_expense unless explicitly personal (Xbox, Spotify, Discord)
- Venmo/Zelle >= $40 from a person = business_income_coaching
- Venmo/Zelle < $40 = personal (likely Starbucks or splitting food)
- Stripe deposits = business_income_wryko
- If unsure, choose needs_review — never guess
- Always include a tax_note explaining your reasoning

Return ONLY valid JSON. No markdown. No explanation outside the JSON."""

USER_PROMPT_TEMPLATE = """Parse this email and return structured financial data:

Subject: {subject}
From: {sender}
Date: {date}
Body: {body}

Return this exact JSON structure:
{{"skip": false, "date": "YYYY-MM-DD", "amount": 0.00, "vendor": "company or person name", "description": "one sentence plain english", "category": "tools|coaching|subscriptions|utilities|food|transport|debt|savings|other", "type": "income|expense|transfer|bill", "account": "which account if identifiable, else unknown", "tax_category": "business_income_wryko|business_income_coaching|wryko_expense|coaching_expense|home_office|vehicle_mileage|personal|needs_review", "schedule_c_entity": "wryko|coaching|personal|split", "deductible_pct": 100.00, "tax_note": "explanation of tax categorization decision", "is_subscription": false, "subscription_name": "name if subscription detected", "subscription_cycle": "monthly|annual|weekly|quarterly or null"}}

If this is not a financial email, return: {{"skip": true}}"""

# IMAP search terms — split into individual queries because IMAP OR syntax
# is limited. We run each query separately and deduplicate by message UID.
SUBJECT_SEARCHES = [
    'SUBJECT "receipt"',
    'SUBJECT "payment"',
    'SUBJECT "invoice"',
    'SUBJECT "statement"',
    'SUBJECT "payment received"',
    'SUBJECT "you paid"',
    'SUBJECT "money received"',
    'SUBJECT "subscription renewal"',
    'SUBJECT "your order"',
]

FROM_SEARCHES = [
    'FROM "paypal"',
    'FROM "venmo.com"',
    'FROM "notify.zelle.com"',
    'FROM "no-reply@stripe.com"',
    'FROM "billing@anthropic.com"',
    'FROM "billing@vercel.com"',
    'FROM "billing@supabase.io"',
    'FROM "noreply@railway.app"',
    'FROM "billing@n8n.io"',
    'FROM "billing@trigger.dev"',
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decode_header_value(value: str | None) -> str:
    """Decode an email header value into a plain string."""
    if value is None:
        return ""
    parts = decode_header(value)
    decoded_parts = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded_parts.append(part)
    return " ".join(decoded_parts)


def _get_email_body(msg: email.message.Message) -> str:
    """Extract plain-text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fallback: try text/html if no plain text found
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/html" and "attachment" not in disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def _connect_imap() -> imaplib.IMAP4_SSL:
    """Connect and authenticate to Gmail via IMAP."""
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(os.environ["GMAIL_ADDRESS"], os.environ["GMAIL_APP_PASSWORD"])
    mail.select("inbox")
    return mail


def _search_financial_emails(mail: imaplib.IMAP4_SSL, lookback_days: int = 1) -> list[bytes]:
    """Search for financial emails. Returns deduplicated UIDs."""
    since_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
    print(f"  Searching emails since {since_date} ({lookback_days} days back)...")
    seen_uids: set[bytes] = set()
    all_uids: list[bytes] = []

    all_queries = SUBJECT_SEARCHES + FROM_SEARCHES
    for query in all_queries:
        search_criteria = f'({query} SINCE {since_date})'
        try:
            status, data = mail.search(None, search_criteria)
            if status != "OK":
                continue
            uids = data[0].split()
            for uid in uids:
                if uid and uid not in seen_uids:
                    seen_uids.add(uid)
                    all_uids.append(uid)
        except imaplib.IMAP4.error as exc:
            print(f"  [WARN] IMAP search failed for '{query}': {exc}")

    return all_uids


def _fetch_message(mail: imaplib.IMAP4_SSL, uid: bytes) -> email.message.Message | None:
    """Fetch and parse a single email message by UID."""
    status, data = mail.fetch(uid, "(RFC822)")
    if status != "OK" or not data or not data[0]:
        return None
    raw_email = data[0][1]
    return email.message_from_bytes(raw_email)


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------


def process_email(msg: email.message.Message, message_id: str) -> dict | None:
    """Send an email to Claude for classification. Returns parsed data or None."""
    subject = _decode_header_value(msg.get("Subject"))
    sender = _decode_header_value(msg.get("From"))
    date_str = _decode_header_value(msg.get("Date"))
    body = _get_email_body(msg)

    # Truncate very long bodies to stay within token limits
    if len(body) > 8000:
        body = body[:8000] + "\n...[truncated]"

    user_prompt = USER_PROMPT_TEMPLATE.format(
        subject=subject,
        sender=sender,
        date=date_str,
        body=body,
    )

    raw_response = call_claude(SYSTEM_PROMPT, user_prompt, max_tokens=1024)
    parsed = parse_json_response(raw_response)

    if parsed is None:
        print(f"  [ERROR] Could not parse Claude response for message {message_id}")
        print(f"  Raw response: {raw_response[:500]}")
        return None

    return parsed


def store_transaction(data: dict, message_id: str) -> None:
    """Insert a classified transaction into pf_transactions."""
    sb = get_supabase()

    amount = float(data.get("amount", 0))
    deductible_pct = float(data.get("deductible_pct", 0))
    deductible_amount = round(amount * deductible_pct / 100, 2)

    row = {
        "source_email_id": message_id,
        "date": data.get("date"),
        "amount": amount,
        "vendor": data.get("vendor"),
        "description": data.get("description"),
        "category": data.get("category"),
        "type": data.get("type"),
        "account": data.get("account", "unknown"),
        "tax_category": data.get("tax_category"),
        "tax_year": int(data.get("date", "2026")[:4]) if data.get("date") else 2026,
        "schedule_c_entity": data.get("schedule_c_entity"),
        "deductible_pct": deductible_pct,
        "deductible_amount": deductible_amount,
        "tax_note": data.get("tax_note"),
        "reviewed": False,
    }

    sb.table("pf_transactions").insert(row).execute()
    print(f"  [OK] Stored transaction: {data.get('vendor')} — ${amount}")


def store_subscription(data: dict) -> None:
    """Upsert a detected subscription into pf_subscriptions."""
    sb = get_supabase()

    name = data.get("subscription_name")
    if not name:
        return

    row = {
        "name": name,
        "vendor": data.get("vendor"),
        "amount": float(data.get("amount", 0)),
        "billing_cycle": data.get("subscription_cycle"),
        "category": data.get("category"),
        "tax_category": data.get("tax_category"),
        "schedule_c_entity": data.get("schedule_c_entity"),
        "last_charged": data.get("date"),
        "active": True,
    }

    sb.table("pf_subscriptions").upsert(row, on_conflict="name").execute()
    print(f"  [OK] Upserted subscription: {name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(lookback_days: int = 1) -> None:
    """Run the VAULT intake pipeline."""
    print("=" * 60)
    print("VAULT Intake Agent — Transaction Ingestion")
    print(f"Run time: {datetime.now().isoformat()}")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. Connect to Gmail and search for financial emails
    # ------------------------------------------------------------------
    print("\n[1/4] Connecting to Gmail via IMAP...")
    try:
        mail = _connect_imap()
    except Exception as exc:
        print(f"[FATAL] Could not connect to Gmail: {exc}")
        return

    print("[2/4] Searching for financial emails from the last 24 hours...")
    uids = _search_financial_emails(mail, lookback_days=lookback_days)
    print(f"  Found {len(uids)} candidate email(s).")

    if not uids:
        print("  No financial emails found. Nothing to process.")
        mail.logout()
        _run_budget_sync()
        return

    # ------------------------------------------------------------------
    # 2-5. Process each email
    # ------------------------------------------------------------------
    print(f"\n[3/4] Processing {len(uids)} email(s)...")
    processed = 0
    skipped_dedup = 0
    skipped_non_financial = 0
    errors = 0

    for i, uid in enumerate(uids, 1):
        try:
            msg = _fetch_message(mail, uid)
            if msg is None:
                print(f"  [{i}/{len(uids)}] Could not fetch message. Skipping.")
                errors += 1
                continue

            # Use the Message-ID header as the unique identifier
            message_id = msg.get("Message-ID", "").strip()
            if not message_id:
                message_id = f"imap-uid-{uid.decode()}"

            subject = _decode_header_value(msg.get("Subject"))
            print(f"  [{i}/{len(uids)}] \"{subject[:60]}\"")

            # Dedup check
            is_duplicate = rpc("vault_dedup_check", {"email_id": message_id})
            if is_duplicate:
                print(f"    -> Already processed. Skipping.")
                skipped_dedup += 1
                continue

            # Classify with Claude
            data = process_email(msg, message_id)
            if data is None:
                errors += 1
                continue

            # Check if Claude says to skip (not financial)
            if data.get("skip"):
                print(f"    -> Not a financial email. Skipping.")
                skipped_non_financial += 1
                continue

            # Store transaction
            store_transaction(data, message_id)
            processed += 1

            # Store subscription if detected
            if data.get("is_subscription"):
                store_subscription(data)

        except Exception:
            print(f"  [{i}/{len(uids)}] ERROR processing email:")
            traceback.print_exc()
            errors += 1

    mail.logout()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "-" * 40)
    print("Intake Summary:")
    print(f"  Processed:            {processed}")
    print(f"  Skipped (duplicate):  {skipped_dedup}")
    print(f"  Skipped (non-fin.):   {skipped_non_financial}")
    print(f"  Errors:               {errors}")
    print("-" * 40)

    # ------------------------------------------------------------------
    # 6. Trigger budget sync
    # ------------------------------------------------------------------
    _run_budget_sync()


def _run_budget_sync() -> None:
    """Import and run the budget_sync agent."""
    print("\n[4/4] Triggering budget sync...")
    try:
        from agents.budget_sync import main as budget_sync_main
        budget_sync_main()
    except ImportError:
        print("  [WARN] budget_sync agent not found. Skipping budget sync.")
    except Exception:
        print("  [ERROR] Budget sync failed:")
        traceback.print_exc()


if __name__ == "__main__":
    import sys
    # Support --backfill N to scan last N days instead of default 1
    if len(sys.argv) >= 3 and sys.argv[1] == "--backfill":
        days = int(sys.argv[2])
        print(f"BACKFILL MODE: scanning last {days} days")
        main(lookback_days=days)
    else:
        main()
