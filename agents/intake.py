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

SYSTEM_PROMPT = """You are VAULT, a financial parsing engine for Marcos Matthews. He files two Schedule C businesses:

SCHEDULE C #1 — WRYKO (B2B SaaS platform)
Deductible expenses include: AI APIs (Anthropic, OpenAI), cloud infrastructure (Vercel, Supabase, Railway, Render), automation tools (n8n, Trigger.dev), email tools (Instantly.ai, Reacher), domain registrations, SSL, monitoring, any SaaS used to build or run the platform.

SCHEDULE C #2 — SOCCER COACHING (youth teams + private training)
Income: coaching payments via Venmo, Zelle, PayPal, cash (noted in email)
Deductible expenses: training equipment (cones, pinnies, balls, goals), field rental fees, coaching software, sports apps, fuel to/from fields, coaching certifications and licensing fees.

PERSONAL (not deductible): food, clothing, entertainment, personal subscriptions not related to either business, personal travel.

HOME OFFICE: if utilities or rent — flag as home_office for partial deduction review.

RULES:
- Never split a single transaction across both Schedule Cs without explicit reason
- If unsure between wryko_expense and personal, choose needs_review
- Coaching Venmo payments are ALWAYS business_income_coaching
- Stripe/Vercel/Anthropic charges are ALWAYS wryko_expense
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


def _search_financial_emails(mail: imaplib.IMAP4_SSL) -> list[bytes]:
    """Search for financial emails from the last 24 hours. Returns deduplicated UIDs."""
    since_date = (datetime.now() - timedelta(days=1)).strftime("%d-%b-%Y")
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
        "email_id": message_id,
        "date": data.get("date"),
        "amount": amount,
        "vendor": data.get("vendor"),
        "description": data.get("description"),
        "category": data.get("category"),
        "type": data.get("type"),
        "account": data.get("account", "unknown"),
        "tax_category": data.get("tax_category"),
        "schedule_c_entity": data.get("schedule_c_entity"),
        "deductible_pct": deductible_pct,
        "deductible_amount": deductible_amount,
        "tax_note": data.get("tax_note"),
        "is_subscription": data.get("is_subscription", False),
        "subscription_name": data.get("subscription_name"),
        "subscription_cycle": data.get("subscription_cycle"),
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
        "cycle": data.get("subscription_cycle"),
        "category": data.get("category"),
        "tax_category": data.get("tax_category"),
        "schedule_c_entity": data.get("schedule_c_entity"),
        "last_seen": data.get("date"),
        "active": True,
    }

    sb.table("pf_subscriptions").upsert(row, on_conflict="name").execute()
    print(f"  [OK] Upserted subscription: {name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
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
    uids = _search_financial_emails(mail)
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
    main()
