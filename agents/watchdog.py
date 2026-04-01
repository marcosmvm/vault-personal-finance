"""VAULT Daily Email — Clean 3-section financial snapshot.

Sends every morning: today's transactions, fixed expenses status,
and variable expenses vs weekly budget.
"""

import os
import sys
import traceback
from datetime import date

from agents.shared import rpc, send_email


# Categories classified as fixed (predictable, recurring)
FIXED_CATEGORIES = {"housing", "utilities", "debt", "subscriptions", "coaching", "tools", "groceries"}

# Everything else is variable (discretionary)
# food (eating out), transport, entertainment, other, etc.


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
    month_day = today.strftime("%B %-d, %Y")

    print(f"VAULT Daily Email: {month_day}")

    # ── Fetch data (4 RPCs) ───────────────────────────────────────
    transactions = _safe_rpc("vault_daily_transactions", {"target_date": today.isoformat()})
    bills = _safe_rpc("vault_bills_overview")
    subscriptions = _safe_rpc("vault_active_subscriptions")
    budgets = _safe_rpc("vault_budget_status")

    # ── Build email body ──────────────────────────────────────────
    body = ""

    # ━━━ Section 1: TODAY'S TRANSACTIONS ━━━
    body += "TODAY'S TRANSACTIONS\n\n"

    if transactions:
        body += "| Vendor | Amount | Category |\n"
        for t in transactions:
            vendor = t.get("vendor", "Unknown")
            amount = float(t.get("amount", 0))
            category = (t.get("category") or "—").capitalize()
            tx_type = t.get("type", "")

            if tx_type == "income":
                display_amt = f"+{_fmt(amount)}"
            else:
                display_amt = f"-{_fmt(amount)}"

            body += f"| {vendor} | {display_amt} | {category} |\n"
    else:
        body += "No transactions recorded today.\n"

    body += "\n"

    # ━━━ Section 2: FIXED EXPENSES ━━━
    body += "FIXED EXPENSES\n\n"

    fixed_items = []

    # Add bills
    for b in bills:
        name = b.get("name", "Unknown")
        amount = float(b.get("amount", 0))
        status_raw = (b.get("status") or "upcoming").lower()
        next_due = b.get("next_due", "—")

        if status_raw == "paid":
            status = "Paid"
        elif status_raw == "overdue":
            status = "Overdue"
        else:
            status = "Not Paid"

        fixed_items.append({
            "name": name,
            "amount": amount,
            "status": status,
            "date": next_due,
            "sort_date": next_due or "9999",
        })

    # Add subscriptions as fixed expenses
    for s in subscriptions:
        name = s.get("name", "Unknown")
        amount = float(s.get("amount", 0))
        next_charge = s.get("next_charge", "—")

        fixed_items.append({
            "name": name,
            "amount": amount,
            "status": "Active",
            "date": next_charge,
            "sort_date": next_charge or "9999",
        })

    # Sort by date
    fixed_items.sort(key=lambda x: x["sort_date"])

    if fixed_items:
        body += "| Item | Amount | Status | Date |\n"
        for item in fixed_items:
            body += f"| {item['name']} | {_fmt(item['amount'])} | {item['status']} | {item['date']} |\n"
    else:
        body += "No fixed expenses tracked.\n"

    body += "\n"

    # ━━━ Section 3: VARIABLE EXPENSES ━━━
    body += "VARIABLE EXPENSES\n\n"

    variable_budgets = []
    for b in budgets:
        category = (b.get("category") or "other").lower()
        if category not in FIXED_CATEGORIES:
            monthly_limit = float(b.get("monthly_limit", 0))
            current_spent = float(b.get("current_spent", 0))
            weekly_budget = monthly_limit / 4.33

            variable_budgets.append({
                "category": category.capitalize(),
                "spent": current_spent,
                "weekly_budget": weekly_budget,
            })

    if variable_budgets:
        body += "| Category | Spent | Weekly Budget |\n"
        for v in variable_budgets:
            body += f"| {v['category']} | {_fmt(v['spent'])} | {_fmt(v['weekly_budget'])} |\n"
    else:
        body += "No variable expense categories tracked.\n"

    # ── Send ──────────────────────────────────────────────────────
    subject = f"VAULT — {day_name}, {today.strftime('%b %-d')}"

    try:
        send_email(subject, body)
        print(f"Daily email sent: {len(transactions)} transactions, {len(fixed_items)} fixed expenses, {len(variable_budgets)} variable categories")
    except Exception as e:
        print(f"ERROR: failed to send daily email: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: daily email failed: {e}")
        traceback.print_exc()
        sys.exit(1)
