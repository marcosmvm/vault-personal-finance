"""VAULT Morning Briefing — Daily transactions + weekly contribution matrix.

Sends every morning: today's transactions and a week-to-date breakdown
by entity (Personal / Wryko / Coaching).
"""

import sys
import traceback
from datetime import date

from agents.shared import rpc, send_email


ENTITY_DISPLAY = {
    "personal": "Personal",
    "wryko": "Wryko",
    "coaching": "Coaching",
    "split": "Split",
}


def _safe_rpc(name, params=None):
    """Call an RPC, return empty list on failure."""
    try:
        result = rpc(name, params)
        return result or []
    except Exception as e:
        print(f"Warning: {name} failed: {e}")
        return []


def _fmt(amount):
    """Format a dollar amount with commas."""
    if amount is None:
        return "$0"
    val = float(amount)
    if val < 0:
        return f"-${abs(val):,.2f}"
    return f"${val:,.2f}"


def main():
    today = date.today()
    day_name = today.strftime("%A")
    month_day = today.strftime("%B %d, %Y")

    print(f"VAULT Morning Briefing: {month_day}")

    # ── Fetch data ──────────────────────────────────────────────────
    transactions = _safe_rpc("vault_daily_transactions", {"target_date": today.isoformat()})
    weekly = _safe_rpc("vault_weekly_entity_summary", {"target_date": today.isoformat()})

    # ── Build email body ────────────────────────────────────────────
    body = f"BRIAN'S DAILY FINANCIAL BRIEFING\n"
    body += f"{day_name} — {month_day}\n\n"

    # ━━━ 1. DAILY TRANSACTIONS ━━━
    body += "DAILY TRANSACTIONS\n\n"

    if transactions:
        for t in transactions:
            vendor = t.get("vendor", "Unknown")
            amount = float(t.get("amount", 0))
            tx_type = t.get("type", "")
            entity = t.get("schedule_c_entity", "personal") or "personal"

            # Sign: income positive, expenses negative
            if tx_type == "income":
                display_amt = f"+{_fmt(amount)}"
            else:
                display_amt = f"-{_fmt(amount)}"

            # Entity tag for non-personal
            tag = ""
            if entity != "personal":
                tag = f" [{ENTITY_DISPLAY.get(entity, entity)}]"

            body += f"• {vendor} — {display_amt}{tag}\n"
    else:
        body += "• No transactions today\n"

    body += "\n"

    # ━━━ 2. WEEKLY CONTRIBUTION ━━━
    body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    body += "WEEKLY CONTRIBUTION (WEEK-TO-DATE)\n\n"

    if weekly:
        # Build entity columns in consistent order
        entity_order = ["personal", "wryko", "coaching", "split"]
        present = [e for e in entity_order if any(
            r.get("entity") == e for r in weekly
        )]

        # Build lookup
        data = {r["entity"]: r for r in weekly}

        # Header row
        headers = [""] + [ENTITY_DISPLAY.get(e, e) for e in present]
        body += "| " + " | ".join(headers) + " |\n"

        # Data rows
        for metric in ["income", "expenses", "taxes", "writeoffs"]:
            label = metric.capitalize()
            row = [label]
            for e in present:
                val = float(data.get(e, {}).get(metric, 0))
                row.append(_fmt(val))
            body += "| " + " | ".join(row) + " |\n"
    else:
        body += "No transaction data this week.\n"

    body += "\n"

    # ── Send ────────────────────────────────────────────────────────
    subject = f"BRIAN: Daily Briefing — {day_name}, {today.strftime('%b %d')}"

    try:
        send_email(subject, body)
        print(f"Briefing sent: {len(transactions)} transactions, {len(weekly)} entities")
    except Exception as e:
        print(f"ERROR: failed to send briefing: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: morning briefing failed: {e}")
        traceback.print_exc()
        sys.exit(1)
