"""Brian — Decision Engine Agent.

On-demand financial analysis: affordability checks, subscription
optimization, and spending pattern analysis.
"""

import json
import sys
import traceback
from datetime import date

from agents.shared import call_claude, rpc, send_email


SYSTEM_PROMPT = """You are Brian, Marcos Matthews's financial controller. You make data-driven financial decisions.

When asked "Can I afford X?", you check:
1. Current checking balance minus all obligations in the next 30 days
2. Whether it pushes balance below $500 safety floor
3. Impact on debt payoff timeline (delay in months)
4. Impact on savings milestone pace

When asked to optimize subscriptions:
1. List all active subscriptions by monthly cost
2. Categorize: critical for business, nice-to-have, potential cut
3. Identify overlapping tools
4. Calculate total savings from recommended cuts

When asked for spending analysis:
1. Show 90-day spending trends by category
2. Flag categories increasing month-over-month
3. Identify anomalies (unusual amounts or new vendors)
4. Recommend specific actions

Be direct. Use numbers. Show your math. End with a clear YES/NO recommendation or specific action list."""


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


def affordability_check(amount: float, description: str):
    """Check if Marcos can afford a specific purchase."""
    print(f"Brian: Checking affordability of {_fmt(amount)} for '{description}'...")

    check = _safe_rpc("vault_affordability_check", {"proposed_amount": amount})
    debt = _safe_rpc("vault_debt_avalanche_order")
    savings = _safe_rpc("vault_savings_progress")
    milestones = _safe_rpc("vault_milestone_status")
    obligations = _safe_rpc("vault_upcoming_obligations", {"days_ahead": 30})

    user_message = f"""Can Marcos afford to spend {_fmt(amount)} on "{description}"?

AFFORDABILITY CHECK:
{json.dumps(check, default=str)}

UPCOMING OBLIGATIONS (30 days):
{json.dumps(obligations, default=str)}

DEBT STATUS:
{json.dumps(debt, default=str)}

SAVINGS PROGRESS:
{json.dumps(savings, default=str)}

MILESTONES:
{json.dumps(milestones, default=str)}

Give a clear YES or NO recommendation with full reasoning. Show the impact on debt payoff and savings milestones in specific days/months of delay."""

    report = call_claude(SYSTEM_PROMPT, user_message, max_tokens=2048)

    subject = f"BRIAN: Can I Afford {_fmt(amount)} for {description}?"
    send_email(subject, report)
    print("Brian: Affordability analysis sent.")


def subscription_audit():
    """Analyze all subscriptions and recommend optimizations."""
    print("Brian: Running subscription optimization analysis...")

    subs = _safe_rpc("vault_active_subscriptions")
    budget = _safe_rpc("vault_budget_status")

    total_monthly = sum(float(s.get("monthly_cost", 0) or s.get("amount", 0)) for s in subs)

    user_message = f"""Optimize Marcos's subscriptions.

ACTIVE SUBSCRIPTIONS:
{json.dumps(subs, default=str)}

TOTAL MONTHLY COST: {_fmt(total_monthly)}

BUDGET STATUS:
{json.dumps(budget, default=str)}

For each subscription, categorize as:
- KEEP (critical for Wryko or coaching business)
- REVIEW (useful but could be downgraded or paused)
- CUT (low value, overlapping, or zombie)

Calculate total monthly savings from recommended cuts. Prioritize cuts by ROI — biggest savings with least impact first."""

    report = call_claude(SYSTEM_PROMPT, user_message, max_tokens=4096)

    subject = f"BRIAN: Subscription Audit — {_fmt(total_monthly)}/mo across {len(subs)} subs"
    send_email(subject, report)
    print(f"Brian: Subscription audit sent. Total: {_fmt(total_monthly)}/mo")


def spending_analysis():
    """Analyze 90-day spending patterns."""
    print("Brian: Running spending pattern analysis...")

    expenses = _safe_rpc("vault_expense_breakdown")
    budget = _safe_rpc("vault_budget_status")
    # Get last 3 months of summaries
    today = date.today()
    summaries = []
    for i in range(3):
        month = today.month - i
        year = today.year
        if month <= 0:
            month += 12
            year -= 1
        s = _safe_rpc("vault_monthly_summary", {"target_month": month, "target_year": year})
        summaries.append({"month": f"{year}-{month:02d}", "data": s})

    user_message = f"""Analyze Marcos's spending patterns over the last 90 days.

THIS WEEK'S EXPENSES BY CATEGORY:
{json.dumps(expenses, default=str)}

BUDGET STATUS:
{json.dumps(budget, default=str)}

MONTHLY SUMMARIES (last 3 months):
{json.dumps(summaries, default=str)}

Identify:
1. Categories where spending is increasing month-over-month
2. Budget categories at risk of going over
3. Any unusual patterns or anomalies
4. Top 3 specific actions to reduce spending"""

    report = call_claude(SYSTEM_PROMPT, user_message, max_tokens=4096)

    subject = "BRIAN: Spending Pattern Analysis"
    send_email(subject, report)
    print("Brian: Spending analysis sent.")


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m agents.decision_engine <command> [args]")
        print("Commands:")
        print("  afford <amount> <description>  — Can I afford this?")
        print("  subscriptions                  — Subscription audit")
        print("  spending                       — 90-day spending analysis")
        sys.exit(1)

    command = sys.argv[1]

    if command == "afford":
        if len(sys.argv) < 4:
            print("Usage: afford <amount> <description>")
            sys.exit(1)
        amount = float(sys.argv[2])
        description = " ".join(sys.argv[3:])
        affordability_check(amount, description)

    elif command == "subscriptions":
        subscription_audit()

    elif command == "spending":
        spending_analysis()

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: decision engine failed: {e}")
        traceback.print_exc()
        sys.exit(1)
