"""VAULT Monthly Review — Full month-end review + tax compilation.

Compiles Schedule C tax forms, reviews monthly performance vs milestones,
and generates a forward-looking game plan via Claude.
"""

import json
from datetime import datetime, timedelta, date

from agents.shared import get_supabase, call_claude, rpc, send_email, parse_json_response


TAX_SYSTEM_PROMPT = """You are a tax preparation specialist. Given raw financial totals, produce IRS Schedule C (Form 1040) structured data. Be precise. Use actual IRS line numbers. Flag any line that requires supporting documentation. Return ONLY valid JSON."""

REVIEW_SYSTEM_PROMPT = """You are VAULT, Marcos Matthews's personal finance AI. You write monthly reviews that are direct, honest, and milestone-focused.

Marcos is a solo founder (Wryko SaaS), soccer coach, and CSUN student with tight margins (~$1,750/mo expenses, ~$2,500/mo income). He has 4 financial milestones he's working toward. Your job is to give him the full picture and a clear plan for next month.

Tone: CFO presenting a monthly board report. Celebrate wins hard, call out problems clearly. Be specific with dollar amounts and dates.
Format: Use ALL CAPS section headers. Use bullets with ' — ' separators. Use ⚡ for urgent items."""

ENTITIES = [
    {
        "rpc_name": "vault_schedule_c_wryko",
        "entity_key": "wryko",
        "entity_name": "Wryko LLC",
        "business_description": "B2B SaaS — software development and consulting",
    },
    {
        "rpc_name": "vault_schedule_c_coaching",
        "entity_key": "coaching",
        "entity_name": "Marcos Matthews Coaching",
        "business_description": "Soccer coaching and athletic training services",
    },
]


def _safe_rpc(name, params=None):
    try:
        result = rpc(name, params)
        return result or []
    except Exception as e:
        print(f"Warning: {name} failed: {e}")
        return []


def build_schedule_c_prompt(entity_name, business_description, current_year, totals_json):
    return f"""Produce IRS Schedule C data for:

ENTITY: {entity_name}
BUSINESS: {business_description}
TAX YEAR: {current_year}

FINANCIAL DATA:
{totals_json}

Return structured JSON matching Schedule C line items:
{{"form": "Schedule C", "entity": "...", "tax_year": {current_year}, "part_1_income": {{"line_1_gross_receipts": 0.00, "line_7_gross_income": 0.00}}, "part_2_expenses": {{"line_18_office_expense": 0.00, "line_27a_other_expenses": 0.00, "line_28_total_expenses": 0.00}}, "line_31_net_profit_loss": 0.00, "supporting_detail": {{"other_expenses_breakdown": [], "flags_requiring_documentation": []}}}}"""


def compile_entity(entity, current_year):
    """Compile Schedule C for a single entity."""
    print(f"  Fetching data for {entity['entity_name']}...")
    totals = rpc(entity["rpc_name"], {"target_year": current_year})
    totals_json = json.dumps(totals, default=str)

    print(f"  Generating Schedule C via Claude for {entity['entity_name']}...")
    user_message = build_schedule_c_prompt(
        entity_name=entity["entity_name"],
        business_description=entity["business_description"],
        current_year=current_year,
        totals_json=totals_json,
    )

    raw_response = call_claude(TAX_SYSTEM_PROMPT, user_message, max_tokens=2048)
    schedule_c = parse_json_response(raw_response)

    if schedule_c is None:
        print(f"  WARNING: Failed to parse Schedule C JSON for {entity['entity_name']}.")
        schedule_c = {"raw_response": raw_response, "parse_error": True}

    gross_income = 0.0
    total_expenses = 0.0
    net_profit_loss = 0.0

    if not schedule_c.get("parse_error"):
        part1 = schedule_c.get("part_1_income", {})
        part2 = schedule_c.get("part_2_expenses", {})
        gross_income = part1.get("line_1_gross_receipts", part1.get("line_7_gross_income", 0.0))
        total_expenses = part2.get("line_28_total_expenses", 0.0)
        net_profit_loss = schedule_c.get("line_31_net_profit_loss", 0.0)

    sb = get_supabase()
    row = {
        "tax_year": current_year,
        "form_type": f"Schedule_C_{entity['entity_key']}",
        "entity": entity["entity_key"],
        "status": "draft",
        "gross_income": gross_income,
        "total_expenses": total_expenses,
        "net_profit_loss": net_profit_loss,
        "document_json": schedule_c,
    }
    sb.table("pf_tax_documents").delete().eq(
        "tax_year", current_year
    ).eq(
        "form_type", f"Schedule_C_{entity['entity_key']}"
    ).execute()
    sb.table("pf_tax_documents").insert(row).execute()

    return {
        "entity_key": entity["entity_key"],
        "entity_name": entity["entity_name"],
        "gross_income": gross_income,
        "total_expenses": total_expenses,
        "net_profit_loss": net_profit_loss,
    }


def build_review_prompt(month, year, data, tax_summaries):
    """Build the Claude prompt for the full monthly review."""
    return f"""Generate a monthly financial review for {month} {year}.

DATA:
Monthly Summary: {json.dumps(data['monthly_summary'], default=str)}
Previous Month: {json.dumps(data['prev_month'], default=str)}
Milestones: {json.dumps(data['milestones'], default=str)}
Debt Status: {json.dumps(data['debt'], default=str)}
Savings Progress: {json.dumps(data['savings'], default=str)}
Budget Status: {json.dumps(data['budget'], default=str)}
Tax — Wryko: {json.dumps(tax_summaries.get('wryko', {}), default=str)}
Tax — Coaching: {json.dumps(tax_summaries.get('coaching', {}), default=str)}

Structure with these EXACT 6 sections:

1. MONTHLY SCORECARD
Total income, total expenses, net savings for {month}.
Compare to previous month — better or worse?
Budget adherence rate (how many categories stayed under limit).
One-line verdict.

2. MILESTONE TRACKER
For each of the 4 milestones:
- Value at start of month vs end of month
- Change this month (dollars added to savings or paid off debt)
- % of monthly target achieved
- Revised projected completion date
- Pace: ahead / on track / behind
If ANY milestone is behind, explain specifically what needs to change.

3. DEBT DASHBOARD
Total debt at month start vs month end.
Which debt is the current avalanche target.
Principal reduced this month.
Months to debt freedom at current pace.
If extra payments would help, show the math.

4. TAX STATUS
Wryko P&L: gross income, expenses, net.
Coaching P&L: gross income, expenses, net.
Combined net, SE tax estimate (15.3%), Federal estimate (22%).
Quarterly tax payment estimate.

5. NEXT MONTH'S GAME PLAN
Specific dollar amounts for:
- How much to allocate to savings (which bucket)
- How much extra to debt payments (which debt)
- Budget adjustments if any categories were over
- Income targets (what to invoice, coaching sessions to book)
Make this ACTIONABLE — exact amounts, exact accounts.

6. MILESTONE CELEBRATION OR ADJUSTMENT
If a milestone was completed this month: CELEBRATE IT.
If pace is off on any milestone: recommend a specific adjustment.
Options: increase income, cut a specific expense, extend timeline.
Be honest about what's realistic.

Write in plain text. ALL CAPS for headers. Bullets with ' — ' separators. ⚡ for urgent items."""


def main():
    print("=== VAULT Monthly Review Agent ===")

    today = date.today()
    current_year = today.year
    month = today.strftime("%B")

    # For the monthly summary, look at the previous month
    # (since this runs on the 1st, we're reviewing last month)
    if today.month == 1:
        review_month = 12
        review_year = today.year - 1
        prev_month_num = 11
        prev_year = today.year - 1
    else:
        review_month = today.month - 1
        review_year = today.year
        prev_month_num = review_month - 1 if review_month > 1 else 12
        prev_year = review_year if review_month > 1 else review_year - 1

    review_month_name = date(review_year, review_month, 1).strftime("%B")
    print(f"Reviewing: {review_month_name} {review_year}")

    # ── 1. Compile tax forms (existing functionality) ───────────────
    print("Compiling Schedule C forms...")
    tax_summaries = {}
    for entity in ENTITIES:
        print(f"Processing {entity['entity_name']}...")
        summary = compile_entity(entity, current_year)
        tax_summaries[summary["entity_key"]] = summary

    # ── 2. Fetch review data ────────────────────────────────────────
    print("Fetching monthly review data...")
    data = {
        'monthly_summary': _safe_rpc("vault_monthly_summary",
                                      {"target_month": review_month, "target_year": review_year}),
        'prev_month': _safe_rpc("vault_monthly_summary",
                                 {"target_month": prev_month_num, "target_year": prev_year}),
        'milestones': _safe_rpc("vault_milestone_status"),
        'debt': _safe_rpc("vault_debt_status"),
        'savings': _safe_rpc("vault_savings_progress"),
        'budget': _safe_rpc("vault_budget_alerts"),
    }

    # ── 3. Generate review via Claude ───────────────────────────────
    print("Generating monthly review via Claude...")
    user_message = build_review_prompt(review_month_name, review_year, data, tax_summaries)
    review_text = call_claude(REVIEW_SYSTEM_PROMPT, user_message, max_tokens=4096)
    print("Review generated.")

    # ── 4. Build subject line ───────────────────────────────────────
    def fmt(val):
        return f"{val:,.2f}"

    wryko = tax_summaries.get("wryko", {})
    coaching = tax_summaries.get("coaching", {})

    monthly = data['monthly_summary']
    net_savings = 0
    if monthly:
        row = monthly[0] if isinstance(monthly, list) else monthly
        net_savings = float(row.get("net_savings", 0))

    on_track = sum(1 for m in data['milestones']
                   if m.get('projected_completion') and m.get('target_date')
                   and str(m['projected_completion']) <= str(m['target_date']))

    subject = (
        f"VAULT Monthly Review — {review_month_name} {review_year} "
        f"| Net: ${fmt(net_savings)} "
        f"| {on_track}/{len(data['milestones'])} milestones on track"
    )

    # ── 5. Send email ──────────────────────────────────────────────
    print(f"Sending: {subject}")
    send_email(subject, review_text)
    print("Email sent.")

    print("=== VAULT Monthly Review complete ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: monthly review failed: {e}")
        import traceback
        traceback.print_exc()
        import sys
        sys.exit(1)
