"""Brian — Debt Destroyer Agent.

Weekly analysis of debt payoff progress using avalanche strategy.
Shows cascade projections and motivational math.
"""

import json
import os
import sys
import traceback
from datetime import date

from agents.shared import call_claude, rpc, send_email

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "debt-attack.md")
SYSTEM_PROMPT = open(_PROMPT_PATH).read()


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
    print("Brian: Debt Destroyer starting...")

    # Fetch debt data
    debt_status = _safe_rpc("vault_debt_status")
    avalanche_order = _safe_rpc("vault_debt_avalanche_order")
    cascade_current = _safe_rpc("vault_debt_payoff_projection", {"extra_monthly": 0})
    cascade_plus50 = _safe_rpc("vault_debt_payoff_projection", {"extra_monthly": 50})
    cascade_plus100 = _safe_rpc("vault_debt_payoff_projection", {"extra_monthly": 100})
    milestones = _safe_rpc("vault_milestone_status")
    savings = _safe_rpc("vault_savings_progress")

    if not debt_status:
        print("Brian: No active debts found. You're debt free!")
        send_email(
            "BRIAN: Debt Attack Report — YOU'RE DEBT FREE!",
            "DEBT ATTACK STATUS\n\nNo active debts remaining. Congratulations!\n\nAll freed debt payments should now flow to savings goals.",
        )
        return

    # Calculate totals
    total_debt = sum(float(d.get("current_balance", 0)) for d in debt_status)
    total_monthly_interest = sum(
        float(d.get("current_balance", 0)) * float(d.get("interest_rate", 0)) / 100 / 12
        for d in debt_status
    )
    total_min_payments = sum(float(d.get("minimum_payment", 0)) for d in debt_status)
    total_extra = sum(float(d.get("monthly_extra", 0)) for d in debt_status)

    # Find the debt-free milestone
    debt_milestone = None
    for m in milestones:
        if m.get("milestone_type") == "debt_payoff":
            debt_milestone = m
            break

    user_message = f"""Generate a weekly Debt Attack Report.

TODAY'S DATE: {date.today().isoformat()}

DEBT STATUS (avalanche order — highest rate first):
{json.dumps(avalanche_order, default=str)}

TOTALS:
- Total debt remaining: {_fmt(total_debt)}
- Total monthly interest accruing: {_fmt(total_monthly_interest)}
- Total minimum payments: {_fmt(total_min_payments)}
- Total extra payments: {_fmt(total_extra)}
- Total monthly toward debt: {_fmt(total_min_payments + total_extra)}

CASCADE PROJECTIONS:
Current pace: {json.dumps(cascade_current, default=str)}
With extra $50/month: {json.dumps(cascade_plus50, default=str)}
With extra $100/month: {json.dumps(cascade_plus100, default=str)}

DEBT-FREE MILESTONE:
{json.dumps(debt_milestone, default=str)}

SAVINGS STATUS (for context):
{json.dumps(savings, default=str)}

Generate the full Debt Attack Report with all sections."""

    print("Brian: Generating debt attack report via Claude...")
    report = call_claude(SYSTEM_PROMPT, user_message, max_tokens=4096)

    # Send email
    subject = f"BRIAN: Debt Attack — {_fmt(total_debt)} remaining"
    send_email(subject, report)
    print(f"Brian: Debt Attack Report sent. Total debt: {_fmt(total_debt)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: debt destroyer failed: {e}")
        traceback.print_exc()
        sys.exit(1)
