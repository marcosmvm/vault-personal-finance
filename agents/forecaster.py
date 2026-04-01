"""Brian — Cash Flow Forecaster Agent.

Weekly 30/60/90 day cash flow projections. Identifies danger zones
where projected balance drops below safety threshold.
"""

import json
import os
import sys
import traceback
import uuid
from datetime import date, timedelta

from agents.shared import get_supabase, call_claude, rpc, send_email

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "cash-flow-forecast.md")
SYSTEM_PROMPT = open(_PROMPT_PATH).read()

SAFETY_THRESHOLD = 500  # Danger zone: below $500


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


def _build_projection(accounts, obligations, income_pattern, schedule):
    """Build a day-by-day 90-day projection."""
    today = date.today()
    batch_id = str(uuid.uuid4())

    # Get starting checking balance
    checking_balance = 0
    for a in accounts:
        if a.get("account_type") == "checking":
            checking_balance += float(a.get("current_balance", 0))

    # Build daily projection
    projections = []
    balance = checking_balance
    danger_zones = []

    # Estimate monthly income from patterns
    monthly_income = 0
    for ip in income_pattern:
        monthly_income += float(ip.get("avg_monthly", 0))

    # Distribute income across typical pay dates (1st and 15th)
    daily_income = monthly_income / 30.0

    # Build obligation map: day of month -> total due
    monthly_obligations = {}
    for ob in obligations:
        due = ob.get("due_date")
        if due:
            due_date = date.fromisoformat(str(due)) if isinstance(due, str) else due
            day = due_date.day
            monthly_obligations.setdefault(day, []).append(ob)

    for day_offset in range(90):
        proj_date = today + timedelta(days=day_offset)
        day_of_month = proj_date.day

        # Income: distribute on 1st and 15th
        inflow = 0
        confidence = "estimated"
        source = ""
        if day_of_month == 1 or day_of_month == 15:
            inflow = monthly_income / 2.0
            confidence = "expected"
            source = "Estimated bi-monthly income"

        # Outflows: known obligations
        outflow = 0
        if day_of_month in monthly_obligations:
            for ob in monthly_obligations[day_of_month]:
                outflow += float(ob.get("amount", 0))
            confidence = "confirmed"
            source = ", ".join(ob.get("name", "") for ob in monthly_obligations.get(day_of_month, []))

        balance = balance + inflow - outflow

        projections.append({
            "projection_date": proj_date.isoformat(),
            "account_name": "BofA Adv Plus Banking",
            "projected_balance": round(balance, 2),
            "inflow": round(inflow, 2),
            "outflow": round(outflow, 2),
            "confidence": confidence,
            "source_description": source or "Daily projection",
            "batch_id": batch_id,
        })

        if balance < SAFETY_THRESHOLD:
            danger_zones.append({
                "date": proj_date.isoformat(),
                "balance": round(balance, 2),
                "shortfall": round(SAFETY_THRESHOLD - balance, 2),
            })

    return projections, danger_zones, batch_id


def main():
    print("Brian: Cash Flow Forecaster starting...")

    # Fetch data
    accounts = _safe_rpc("vault_account_balances")
    obligations = _safe_rpc("vault_upcoming_obligations", {"days_ahead": 90})
    income_pattern = _safe_rpc("vault_income_pattern", {"months_back": 3})
    schedule = _safe_rpc("vault_bill_payment_schedule", {"days_ahead": 30})
    debt = _safe_rpc("vault_debt_status")
    savings = _safe_rpc("vault_savings_progress")

    # Build projections
    projections, danger_zones, batch_id = _build_projection(
        accounts, obligations, income_pattern, schedule
    )

    # Store projections in Supabase
    sb = get_supabase()
    if projections:
        # Store weekly summary points (every 7 days) to avoid too many rows
        weekly_points = [p for i, p in enumerate(projections) if i % 7 == 0 or i == len(projections) - 1]
        for proj in weekly_points:
            sb.table("pf_cashflow_projections").insert(proj).execute()
        print(f"Brian: Stored {len(weekly_points)} projection points (batch: {batch_id[:8]})")

    # Generate narrative via Claude
    user_message = f"""Generate a cash flow forecast report.

TODAY: {date.today().isoformat()}

CURRENT ACCOUNTS:
{json.dumps(accounts, default=str)}

INCOME PATTERN (last 3 months):
{json.dumps(income_pattern, default=str)}

OBLIGATIONS (next 90 days):
{json.dumps(obligations, default=str)}

PAYMENT SCHEDULE (next 30 days):
{json.dumps(schedule, default=str)}

DEBT STATUS:
{json.dumps(debt, default=str)}

SAVINGS:
{json.dumps(savings, default=str)}

DANGER ZONES (balance < ${SAFETY_THRESHOLD}):
{json.dumps(danger_zones, default=str) if danger_zones else 'None detected — forecast looks healthy.'}

90-DAY PROJECTION SUMMARY:
- Starting balance: {_fmt(projections[0]['projected_balance'] if projections else 0)}
- 30-day balance: {_fmt(projections[29]['projected_balance'] if len(projections) > 29 else 0)}
- 60-day balance: {_fmt(projections[59]['projected_balance'] if len(projections) > 59 else 0)}
- 90-day balance: {_fmt(projections[89]['projected_balance'] if len(projections) > 89 else 0)}
- Danger zones found: {len(danger_zones)}

Generate the full Cash Flow Forecast report."""

    print("Brian: Generating forecast via Claude...")
    report = call_claude(SYSTEM_PROMPT, user_message, max_tokens=4096)

    # Send email - always on danger, otherwise just weekly
    if danger_zones:
        subject = f"⚡ BRIAN: Cash Flow Warning — {len(danger_zones)} danger zone(s) detected"
    else:
        subject = "BRIAN: 90-Day Cash Flow Forecast — All Clear"

    send_email(subject, report)
    print(f"Brian: Forecast sent. Danger zones: {len(danger_zones)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: forecaster failed: {e}")
        traceback.print_exc()
        sys.exit(1)
