"""Brian — Controller Reports Agent.

Monthly professional accounting reports: P&L, Balance Sheet,
Cash Flow Statement, Debt Tracker, Net Worth Trend.
"""

import json
import os
import sys
import traceback
from datetime import date

from agents.shared import call_claude, rpc, send_email

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "controller-reports.md")
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
    print("Brian: Controller Reports starting...")
    today = date.today()

    # Determine reporting period (previous month)
    if today.month == 1:
        report_month = 12
        report_year = today.year - 1
    else:
        report_month = today.month - 1
        report_year = today.year

    month_name = date(report_year, report_month, 1).strftime("%B %Y")
    print(f"Brian: Generating reports for {month_name}")

    # Also get prior month for comparisons
    if report_month == 1:
        prior_month = 12
        prior_year = report_year - 1
    else:
        prior_month = report_month - 1
        prior_year = report_year

    # Fetch all data
    current_pl = _safe_rpc("vault_monthly_pl", {"target_month": report_month, "target_year": report_year})
    prior_pl = _safe_rpc("vault_monthly_pl", {"target_month": prior_month, "target_year": prior_year})
    current_summary = _safe_rpc("vault_monthly_summary", {"target_month": report_month, "target_year": report_year})
    prior_summary = _safe_rpc("vault_monthly_summary", {"target_month": prior_month, "target_year": prior_year})
    balance_sheet = _safe_rpc("vault_balance_sheet")
    debt_status = _safe_rpc("vault_debt_status")
    debt_avalanche = _safe_rpc("vault_debt_avalanche_order")
    savings = _safe_rpc("vault_savings_progress")
    net_worth_trend = _safe_rpc("vault_net_worth_trend", {"months_back": 6})
    milestones = _safe_rpc("vault_milestone_status")
    wryko_sc = _safe_rpc("vault_schedule_c_wryko", {"target_year": report_year})
    coaching_sc = _safe_rpc("vault_schedule_c_coaching", {"target_year": report_year})
    budget_status = _safe_rpc("vault_budget_status")
    accounts = _safe_rpc("vault_account_balances")

    # Calculate totals
    total_debt = sum(float(d.get("current_balance", 0)) for d in debt_status)
    net_worth = 0
    if balance_sheet:
        bs = balance_sheet[0] if isinstance(balance_sheet, list) else balance_sheet
        net_worth = float(bs.get("net_worth", 0))

    user_message = f"""Generate the monthly Controller Report Package for {month_name}.

REPORTING PERIOD: {month_name}
PRIOR PERIOD: {date(prior_year, prior_month, 1).strftime('%B %Y')}

CURRENT MONTH P&L DATA:
{json.dumps(current_pl, default=str)}

CURRENT MONTH SUMMARY:
{json.dumps(current_summary, default=str)}

PRIOR MONTH P&L DATA:
{json.dumps(prior_pl, default=str)}

PRIOR MONTH SUMMARY:
{json.dumps(prior_summary, default=str)}

BALANCE SHEET:
{json.dumps(balance_sheet, default=str)}

ALL ACCOUNTS:
{json.dumps(accounts, default=str)}

DEBT STATUS:
{json.dumps(debt_status, default=str)}

DEBT AVALANCHE ORDER:
{json.dumps(debt_avalanche, default=str)}

SAVINGS PROGRESS:
{json.dumps(savings, default=str)}

NET WORTH TREND (6 months):
{json.dumps(net_worth_trend, default=str)}

MILESTONES:
{json.dumps(milestones, default=str)}

WRYKO SCHEDULE C (YTD):
{json.dumps(wryko_sc, default=str)}

COACHING SCHEDULE C (YTD):
{json.dumps(coaching_sc, default=str)}

BUDGET STATUS:
{json.dumps(budget_status, default=str)}

Generate all 6 reports: Personal P&L, Wryko P&L, Coaching P&L, Balance Sheet, Debt Tracker, Net Worth Trend. End with a Controller's Note."""

    print("Brian: Generating reports via Claude...")
    report = call_claude(SYSTEM_PROMPT, user_message, max_tokens=4096)

    # Also trigger budget rotation for next month
    print("Brian: Rotating budget to next month...")
    try:
        result = rpc("vault_budget_rotate")
        print(f"  Budget rotation: {result} categories created for next month")
    except Exception as e:
        print(f"  Warning: budget rotation failed: {e}")

    # Send email
    subject = f"BRIAN: Controller Report — {month_name} | Net Worth: {_fmt(net_worth)}"
    send_email(subject, report)
    print(f"Brian: Controller Report sent for {month_name}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: controller reports failed: {e}")
        traceback.print_exc()
        sys.exit(1)
