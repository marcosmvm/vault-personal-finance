"""VAULT Morning Briefing — Daily financial snapshot.

Sends every morning: account balances, milestone pulse, action items,
and daily budget allowance. Always sends — this is your daily orientation.
"""

import sys
import traceback
from datetime import date, datetime

from agents.shared import rpc, send_email


def _safe_rpc(name, params=None):
    """Call an RPC, return empty list on failure."""
    try:
        result = rpc(name, params)
        return result or []
    except Exception as e:
        print(f"Warning: {name} failed: {e}")
        return []


def _progress_bar(pct):
    """Return a text progress bar: ████░░░░░░ 34%"""
    filled = round(pct / 10)
    empty = 10 - filled
    return f"{'█' * filled}{'░' * empty} {pct}%"


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

    # Day-of-week context
    weekday_num = today.isoweekday()  # Mon=1, Sun=7
    week_label = f"Day {weekday_num} of your week"

    print(f"VAULT Morning Briefing: {month_day}")

    # ── Fetch all data ──────────────────────────────────────────────
    accounts = _safe_rpc("vault_account_balances")
    milestones = _safe_rpc("vault_milestone_status")
    bills = _safe_rpc("vault_upcoming_bills", {"days_ahead": 2})
    subs = _safe_rpc("vault_upcoming_subscriptions", {"days_ahead": 2})
    overdue = _safe_rpc("vault_overdue_bills")
    budget_pace = _safe_rpc("vault_daily_budget_pace")
    budget_alerts = _safe_rpc("vault_budget_alerts")
    # Brian data
    payment_schedule = _safe_rpc("vault_bill_payment_schedule", {"days_ahead": 7})
    debt_avalanche = _safe_rpc("vault_debt_avalanche_order")
    available = _safe_rpc("vault_available_for_allocation")

    # ── Build email body ────────────────────────────────────────────
    body = f"BRIAN'S DAILY FINANCIAL BRIEFING\n"
    body += f"{day_name} — {month_day} — {week_label}\n\n"

    # ━━━ 1. TODAY'S SNAPSHOT ━━━
    body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    body += "TODAY'S SNAPSHOT\n\n"

    checking_total = 0
    credit_total = 0
    savings_total = 0

    for a in accounts:
        acct_type = a.get("account_type", "")
        bal = float(a.get("current_balance", 0))
        if acct_type == "checking":
            checking_total += bal
        elif acct_type == "credit_card":
            credit_total += bal
        elif acct_type == "savings":
            savings_total += bal

    body += f"• Checking — {_fmt(checking_total)}\n"
    body += f"• Savings — {_fmt(savings_total)}\n"
    body += f"• Credit Cards — {_fmt(credit_total)}\n"

    if budget_pace:
        pace = budget_pace[0] if isinstance(budget_pace, list) else budget_pace
        spent = float(pace.get("spent_this_month", 0))
        total_budget = float(pace.get("total_monthly_budget", 0))
        days_left = int(pace.get("days_remaining", 1))
        pct_month_spent = round((spent / total_budget) * 100, 0) if total_budget > 0 else 0
        body += f"\n• Month spending — {_fmt(spent)} of {_fmt(total_budget)} budget ({pct_month_spent:.0f}% used)\n"

    body += "\n"

    # ━━━ 2. MILESTONE PULSE ━━━
    body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    body += "MILESTONE PULSE\n\n"

    if milestones:
        for ms in milestones:
            ms_name = ms.get("name", "")
            ms_type = ms.get("milestone_type", "")
            pct = float(ms.get("pct_complete", 0))
            target = float(ms.get("target_amount", 0))
            current = float(ms.get("current_amount", 0))
            proj_date = ms.get("projected_completion", "")
            days_rem = ms.get("days_remaining", 0)

            bar = _progress_bar(pct)

            if ms_type == "debt_payoff":
                body += f"• {ms_name} — {_fmt(current)} remaining — {bar}\n"
            else:
                body += f"• {ms_name} — {_fmt(current)} / {_fmt(target)} — {bar}\n"

            if proj_date:
                proj_str = proj_date if isinstance(proj_date, str) else str(proj_date)
                body += f"  Projected: {proj_str}\n"
    else:
        body += "• No active milestones found\n"

    body += "\n"

    # ━━━ 3. ACTION ITEMS ━━━
    action_count = len(bills) + len(subs) + len(overdue) + len(budget_alerts)

    body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    if action_count > 0:
        body += f"ACTION ITEMS — {action_count} ITEMS\n\n"
    else:
        body += "ACTION ITEMS\n\n"
        body += "• Nothing urgent — stay the course today\n"

    if overdue:
        for o in overdue:
            body += (
                f"⚡ OVERDUE: {o['name']} — {_fmt(o['amount'])}"
                f" since {o['next_due']}\n"
            )
        body += "\n"

    if bills:
        for b in bills:
            auto = "auto-pay" if b.get("auto_pay") else "manual"
            body += f"• {b['name']} — {_fmt(b['amount'])} due {b['next_due']} [{auto}]\n"
        body += "\n"

    if subs:
        for s in subs:
            body += f"• {s['name']} — {_fmt(s['amount'])} charging {s['next_charge']}\n"
        body += "\n"

    if budget_alerts:
        for ba in budget_alerts:
            body += (
                f"⚠ {ba['category']}: {_fmt(ba['current_spent'])} / "
                f"{_fmt(ba['monthly_limit'])} ({ba['pct_used']}% used)\n"
            )
        body += "\n"

    # ━━━ 4. TODAY'S BUDGET ━━━
    body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    body += "TODAY'S BUDGET\n\n"

    if budget_pace:
        pace = budget_pace[0] if isinstance(budget_pace, list) else budget_pace
        daily = float(pace.get("daily_allowance", 0))
        body += f"You can spend {_fmt(daily)} today and stay on track.\n"
    else:
        body += "Budget data unavailable — sync your budget.\n"

    # ━━━ 5. UPCOMING PAYMENTS (Brian) ━━━
    if payment_schedule:
        body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        body += "UPCOMING PAYMENTS (7 DAYS)\n\n"
        for ps in payment_schedule[:7]:
            rb = float(ps.get("running_balance", 0))
            marker = " ⚡" if rb < 0 else ""
            body += f"• {ps.get('due_date', '?')} — {ps.get('name', '?')} — {_fmt(ps.get('amount', 0))} — balance: {_fmt(rb)}{marker}\n"
        body += "\n"

    # ━━━ 6. DEBT ATTACK STATUS (Brian) ━━━
    if debt_avalanche:
        body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        body += "DEBT ATTACK STATUS\n\n"
        target = debt_avalanche[0]
        total_debt = sum(float(d.get("current_balance", 0)) for d in debt_avalanche)
        body += f"• Total debt — {_fmt(total_debt)}\n"
        body += f"• Avalanche target — {target.get('name', '?')} at {target.get('interest_rate', 0)}% APR\n"
        body += f"• Target balance — {_fmt(target.get('current_balance', 0))}\n"
        months = target.get("months_to_payoff", 0)
        if months and float(months) < 999:
            body += f"• Months to payoff — {float(months):.0f}\n"
        body += "\n"

    # ━━━ 7. CASH POSITION (Brian) ━━━
    if available:
        avail_data = available[0] if isinstance(available, list) else available
        surplus = float(avail_data.get("allocatable_surplus", 0))
        body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        body += "CASH POSITION\n\n"
        body += f"• Checking — {_fmt(avail_data.get('checking_balance', 0))}\n"
        body += f"• 30-day obligations — {_fmt(avail_data.get('total_obligations', 0))}\n"
        body += f"• Allocatable surplus — {_fmt(surplus)}\n"
        if surplus < 0:
            body += f"\n⚡ WARNING: Projected shortfall of {_fmt(abs(surplus))} in next 30 days\n"
        body += "\n"

    body += "\n---\nBrian — Your autonomous financial controller."

    # ── Send ────────────────────────────────────────────────────────
    subject = f"BRIAN: Daily Briefing — {day_name}, {today.strftime('%b %d')}"

    try:
        send_email(subject, body)
        print(f"Briefing sent: {len(milestones)} milestones, {action_count} actions")
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
