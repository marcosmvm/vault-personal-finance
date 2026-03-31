"""VAULT Watchdog — Daily alert agent.

Queries Supabase for upcoming bills, subscriptions, overdue items,
and budget breaches, then sends a Gmail alert if anything needs attention.
"""

import sys
import traceback
from datetime import date

from agents.shared import rpc, send_email


def main():
    print("VAULT Watchdog: starting daily check...")

    # 1. Query all 4 RPC functions
    try:
        bills = rpc("vault_upcoming_bills", {"days_ahead": 5})
    except Exception as e:
        print(f"Warning: failed to fetch upcoming bills: {e}")
        bills = []

    try:
        subs = rpc("vault_upcoming_subscriptions", {"days_ahead": 5})
    except Exception as e:
        print(f"Warning: failed to fetch upcoming subscriptions: {e}")
        subs = []

    try:
        overdue = rpc("vault_overdue_bills")
    except Exception as e:
        print(f"Warning: failed to fetch overdue bills: {e}")
        overdue = []

    try:
        budget = rpc("vault_budget_alerts")
    except Exception as e:
        print(f"Warning: failed to fetch budget alerts: {e}")
        budget = []

    # Normalize None responses to empty lists
    bills = bills or []
    subs = subs or []
    overdue = overdue or []
    budget = budget or []

    # 2. If nothing, print status and exit
    total = len(bills) + len(subs) + len(overdue) + len(budget)
    if total == 0:
        print("No alerts today.")
        return

    print(f"Found {total} items: {len(bills)} bills, {len(subs)} subs, "
          f"{len(overdue)} overdue, {len(budget)} budget warnings")

    # 3. Build email body
    today = date.today().isoformat()
    body = f"VAULT DAILY ALERT — {today}\n\n"

    if bills:
        body += "BILLS DUE (next 5 days):\n"
        for b in bills:
            body += (
                f"  • {b['name']} — ${b['amount']} due {b['next_due']}"
                f"  [auto-pay: {b['auto_pay']}]\n"
            )
        body += "\n"

    if subs:
        body += "SUBSCRIPTIONS CHARGING SOON:\n"
        for s in subs:
            line = (
                f"  • {s['name']} — ${s['amount']} on {s['next_charge']}"
                f"  [{s['billing_cycle']}]"
            )
            if s.get("cancel_url"):
                line += f"  Cancel: {s['cancel_url']}"
            body += line + "\n"
        body += "\n"

    if overdue:
        body += "OVERDUE:\n"
        for o in overdue:
            body += (
                f"  ⚡ {o['name']} — ${o['amount']}"
                f" — OVERDUE since {o['next_due']}\n"
            )
        body += "\n"

    if budget:
        body += "BUDGET WARNINGS:\n"
        for b in budget:
            body += (
                f"  • {b['category']}: ${b['current_spent']} / "
                f"${b['monthly_limit']} ({b['pct_used']}% used"
                f" — ${b['remaining']} left)\n"
            )
        body += "\n"

    body += "Reply to this email or open VS Code Claude to take action."

    # 4. Send email
    subject = f"⚠️ VAULT Alert — {total} items need attention"
    try:
        send_email(subject, body)
        print(f"Alert sent: {total} items")
    except Exception as e:
        print(f"ERROR: failed to send alert email: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: watchdog failed: {e}")
        traceback.print_exc()
        sys.exit(1)
