"""VAULT Budget Sync — Updates budget totals from transactions.

Calls vault_budget_sync to recalculate current_spent for each budget
category, then checks for any categories approaching or exceeding
their monthly limit and sends an alert email.
"""

import sys
import traceback

from agents.shared import rpc, send_email


def main():
    print("VAULT Budget Sync: starting...")

    # 1. Call vault_budget_sync RPC (updates pf_budget.current_spent)
    try:
        results = rpc("vault_budget_sync")
    except Exception as e:
        print(f"ERROR: budget sync RPC failed: {e}")
        traceback.print_exc()
        sys.exit(1)

    results = results or []
    print(f"Budget sync complete: {len(results)} categories updated")

    # 2. Check for alerts
    try:
        alerts = rpc("vault_budget_alerts")
    except Exception as e:
        print(f"Warning: failed to fetch budget alerts: {e}")
        alerts = []

    alerts = alerts or []

    if not alerts:
        print("No budget alerts.")
        return

    print(f"Found {len(alerts)} budget alert(s)")

    # 3. Build and send alert email
    body = "VAULT BUDGET ALERT\n\n"
    body += "The following categories are approaching or over budget:\n\n"

    for a in alerts:
        body += (
            f"  • {a['category']}: ${a['current_spent']} / "
            f"${a['monthly_limit']} ({a['pct_used']}% used"
            f" — ${a['remaining']} remaining)\n"
        )

    body += "\nReview your spending and adjust if needed."

    subject = f"⚠️ VAULT Budget Alert — {len(alerts)} categories over threshold"
    try:
        send_email(subject, body)
        print(f"Budget alert sent: {len(alerts)} categories")
    except Exception as e:
        print(f"ERROR: failed to send budget alert email: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: budget_sync failed: {e}")
        traceback.print_exc()
        sys.exit(1)
