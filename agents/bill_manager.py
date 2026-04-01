"""Brian — Bill Payment Lifecycle Manager.

Runs daily before the morning briefing. Manages bill reminders,
due-today alerts, overdue escalation, and insufficient funds warnings.
"""

import sys
import traceback
from datetime import date, timedelta

from agents.shared import get_supabase, rpc, send_email


def _safe_rpc(name, params=None):
    try:
        result = rpc(name, params)
        return result or []
    except Exception as e:
        print(f"Warning: {name} failed: {e}")
        return []


def _fmt(amount):
    if amount is None:
        return "$0.00"
    val = float(amount)
    if val < 0:
        return f"-${abs(val):,.2f}"
    return f"${val:,.2f}"


def main():
    print("Brian: Bill Manager starting...")
    today = date.today()

    # Fetch data
    schedule = _safe_rpc("vault_bill_payment_schedule", {"days_ahead": 14})
    bills_7d = _safe_rpc("vault_upcoming_bills", {"days_ahead": 7})
    overdue = _safe_rpc("vault_overdue_bills")
    available = _safe_rpc("vault_available_for_allocation")

    checking_balance = 0
    if available:
        row = available[0] if isinstance(available, list) else available
        checking_balance = float(row.get("checking_balance", 0))

    # Categorize bills
    due_today = []
    due_3_days = []
    due_7_days = []

    for bill in bills_7d:
        due = bill.get("next_due")
        if not due:
            continue
        due_date = date.fromisoformat(str(due)) if isinstance(due, str) else due
        days_until = (due_date - today).days

        if days_until <= 0:
            due_today.append(bill)
        elif days_until <= 3:
            due_3_days.append(bill)
        else:
            due_7_days.append(bill)

    # Check for insufficient funds
    total_14d = sum(float(s.get("amount", 0)) for s in schedule)
    shortfall = total_14d - checking_balance if total_14d > checking_balance else 0

    # Find the specific payment that causes the shortfall
    danger_payment = None
    if schedule:
        running = checking_balance
        for s in schedule:
            running -= float(s.get("amount", 0))
            if running < 0 and danger_payment is None:
                danger_payment = s

    # Determine if we need to send an alert
    has_urgent = bool(due_today) or bool(overdue) or shortfall > 0
    has_reminders = bool(due_3_days)

    if not has_urgent and not has_reminders and not due_7_days:
        print("Brian: No bill actions needed today.")
        return

    # Build email
    body = "BRIAN: DAILY BILL REPORT\n\n"
    body += f"{today.strftime('%A, %B %d, %Y')}\n\n"

    # Overdue
    if overdue:
        body += "⚡ OVERDUE — IMMEDIATE ACTION REQUIRED\n\n"
        for o in overdue:
            body += f"⚡ {o['name']} — {_fmt(o['amount'])} — was due {o['next_due']}\n"
        body += "\n"

    # Due today
    if due_today:
        body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        body += "PAY TODAY\n\n"
        for b in due_today:
            auto = "[auto-pay]" if b.get("auto_pay") else "[MANUAL — pay now]"
            body += f"• {b['name']} — {_fmt(b['amount'])} {auto}\n"
        body += "\n"

    # Due in 3 days
    if due_3_days:
        body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        body += "DUE IN 3 DAYS\n\n"
        for b in due_3_days:
            auto = "[auto-pay]" if b.get("auto_pay") else "[manual]"
            body += f"• {b['name']} — {_fmt(b['amount'])} due {b['next_due']} {auto}\n"
        body += "\n"

    # Due this week
    if due_7_days:
        body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        body += "DUE THIS WEEK\n\n"
        for b in due_7_days:
            auto = "[auto-pay]" if b.get("auto_pay") else "[manual]"
            body += f"• {b['name']} — {_fmt(b['amount'])} due {b['next_due']} {auto}\n"
        body += "\n"

    # Insufficient funds warning
    if shortfall > 0:
        body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        body += "⚡ INSUFFICIENT FUNDS WARNING\n\n"
        body += f"• Checking balance — {_fmt(checking_balance)}\n"
        body += f"• Total due (14 days) — {_fmt(total_14d)}\n"
        body += f"• Shortfall — {_fmt(shortfall)}\n"
        if danger_payment:
            body += f"• First payment at risk — {danger_payment.get('name', 'unknown')} ({_fmt(danger_payment.get('amount', 0))}) on {danger_payment.get('due_date', '?')}\n"
        body += "\n⚡ Action needed: Transfer funds or defer a non-essential payment.\n\n"

    # 14-day payment schedule with running balance
    if schedule:
        body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        body += "14-DAY PAYMENT SCHEDULE\n\n"
        body += f"Starting balance — {_fmt(checking_balance)}\n\n"
        for s in schedule:
            rb = float(s.get("running_balance", 0))
            marker = " ⚡" if rb < 0 else ""
            body += f"• {s.get('due_date', '?')} — {s.get('name', '?')} — {_fmt(s.get('amount', 0))} — balance: {_fmt(rb)}{marker}\n"
        body += "\n"

    # Auto-advance auto-pay bills that are past due
    _advance_autopay_bills(today)

    # Send
    urgency = "URGENT: " if has_urgent else ""
    action_count = len(due_today) + len(overdue)
    subject = f"BRIAN: {urgency}Bill Report — {action_count} action(s), {len(due_3_days)} upcoming"

    send_email(subject, body)
    print(f"Brian: Bill report sent. {action_count} urgent, {len(due_3_days)} reminders, shortfall: {_fmt(shortfall)}")


def _advance_autopay_bills(today: date):
    """For auto-pay bills past their due date, advance next_due to next month."""
    sb = get_supabase()
    try:
        result = sb.table("pf_bills").select("id, name, next_due, due_day, auto_pay").eq(
            "auto_pay", True
        ).lt("next_due", today.isoformat()).execute()

        for bill in (result.data or []):
            # Calculate next month's due date
            if today.month == 12:
                next_month = today.replace(year=today.year + 1, month=1, day=bill["due_day"])
            else:
                try:
                    next_month = today.replace(month=today.month + 1, day=bill["due_day"])
                except ValueError:
                    # Handle months with fewer days
                    next_month = today.replace(month=today.month + 1, day=28)

            sb.table("pf_bills").update({
                "next_due": next_month.isoformat(),
                "last_paid": today.isoformat(),
                "status": "upcoming",
            }).eq("id", bill["id"]).execute()

            print(f"  Advanced auto-pay: {bill['name']} → next due {next_month}")

    except Exception as e:
        print(f"  Warning: auto-advance failed: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: bill manager failed: {e}")
        traceback.print_exc()
        sys.exit(1)
