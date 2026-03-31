"""VAULT Weekly Intelligence Digest Agent.

Queries 8 Supabase RPC functions, generates a comprehensive weekly finance
digest via Claude, emails it, and logs the result.
"""

import json
from datetime import datetime, timedelta, date

from agents.shared import get_supabase, call_claude, rpc, send_email


SYSTEM_PROMPT = """You are VAULT, Marcos Matthews's personal finance AI. You write weekly finance digests that are direct, honest, and actionable. You do not sugarcoat. You flag problems. You celebrate wins. You always end with exactly one prioritized action item.

Tone: CFO writing to a founder who is busy and needs signal, not noise.
Length: Comprehensive but scannable. Use clear section headers."""


def build_user_message(
    week_start: str,
    week_end: str,
    current_year: int,
    cashflow,
    income,
    expenses,
    tax_ledger,
    needs_review,
    debt,
    savings,
    subscriptions,
) -> str:
    return f"""Generate a weekly finance digest for the week of {week_start} to {week_end}.

DATA:
Cashflow: {json.dumps(cashflow, default=str)}
Income: {json.dumps(income, default=str)}
Expenses: {json.dumps(expenses, default=str)}
Tax Ledger: {json.dumps(tax_ledger, default=str)}
Needs Review: {json.dumps(needs_review, default=str)}
Debt: {json.dumps(debt, default=str)}
Savings: {json.dumps(savings, default=str)}
Subscriptions: {json.dumps(subscriptions, default=str)}

Structure the email with these exact sections:

1. WEEKLY SNAPSHOT - Net cash flow, income vs expense, one-line assessment.
2. INCOME BREAKDOWN - All income sources. Flag any coaching income that looks untracked.
3. EXPENSE REVIEW - Top categories. Flag any unusual spikes.
4. TAX LEDGER — YTD {current_year} - Schedule C #1 (Wryko) and #2 (Coaching) with gross income, deductions, net. Estimated quarterly tax (25% rate). List ALL needs_review items.
5. DEBT PAYOFF STATUS - Avalanche recommendation.
6. SAVINGS PROGRESS - Each bucket with % complete.
7. SUBSCRIPTION AUDIT - Total monthly cost. Flag zombies.
8. THIS WEEK'S ONE ACTION - Single most important action with exact amount, account, reason.

Write in plain text. Use clear section headers. Be direct."""


def main():
    print("=== VAULT Weekly Digest Agent ===")

    # Compute date range
    today = date.today()
    week_end = today.isoformat()
    week_start = (today - timedelta(days=7)).isoformat()
    current_year = today.year

    print(f"Week: {week_start} to {week_end}")

    # 1. Query all 8 RPC functions
    print("Fetching cashflow...")
    cashflow = rpc("vault_weekly_cashflow")

    print("Fetching income breakdown...")
    income = rpc("vault_income_breakdown")

    print("Fetching expense breakdown...")
    expenses = rpc("vault_expense_breakdown")

    print("Fetching YTD tax ledger...")
    tax_ledger = rpc("vault_ytd_tax_ledger")

    print("Fetching needs-review items...")
    needs_review = rpc("vault_needs_review")

    print("Fetching debt status...")
    debt = rpc("vault_debt_status")

    print("Fetching savings progress...")
    savings = rpc("vault_savings_progress")

    print("Fetching active subscriptions...")
    subscriptions = rpc("vault_active_subscriptions")

    print("All data fetched successfully.")

    # 2. Build the prompt and call Claude
    user_message = build_user_message(
        week_start=week_start,
        week_end=week_end,
        current_year=current_year,
        cashflow=cashflow,
        income=income,
        expenses=expenses,
        tax_ledger=tax_ledger,
        needs_review=needs_review,
        debt=debt,
        savings=savings,
        subscriptions=subscriptions,
    )

    print("Generating digest via Claude...")
    digest_text = call_claude(SYSTEM_PROMPT, user_message, max_tokens=4096)
    print("Digest generated.")

    # 3. Compute net cashflow for subject line
    net_cashflow = cashflow[0]["net_cashflow"] if cashflow else 0
    net_display = f"{net_cashflow:,.2f}" if isinstance(net_cashflow, (int, float)) else str(net_cashflow)

    subject = f"VAULT Weekly Digest — Week of {week_start} | Net: ${net_display}"

    # 4. Send email
    print(f"Sending digest email: {subject}")
    send_email(subject, digest_text)
    print("Email sent.")

    # 5. Log to pf_digest_log
    print("Logging digest to pf_digest_log...")
    sb = get_supabase()
    sb.table("pf_digest_log").insert({
        "week_start": week_start,
        "week_end": week_end,
        "digest_text": digest_text,
        "total_income": cashflow[0]["total_income"] if cashflow else 0,
        "total_expenses": cashflow[0]["total_expenses"] if cashflow else 0,
        "net_cashflow": cashflow[0]["net_cashflow"] if cashflow else 0,
        "needs_review_count": len(needs_review or []),
        "sent_at": datetime.now().isoformat(),
    }).execute()
    print("Digest logged.")

    print("=== VAULT Weekly Digest complete ===")


if __name__ == "__main__":
    main()
