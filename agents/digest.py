"""VAULT Weekly Review — Milestone-driven week-in-review agent.

Queries financial data + milestone progress, generates a 6-section
weekly review via Claude, snapshots milestones, emails it, and logs.
"""

import json
from datetime import datetime, timedelta, date

from agents.shared import get_supabase, call_claude, rpc, send_email


SYSTEM_PROMPT = """You are Brian, Marcos Matthews's autonomous financial controller. You write weekly reviews that are direct, honest, and milestone-focused. You don't sugarcoat — you flag problems and celebrate wins.

Marcos is a solo founder (Wryko SaaS), soccer coach, and CSUN student with tight margins (~$1,750/mo expenses, ~$2,500/mo income). Every dollar matters. Your job is to keep him locked in on his 4 financial milestones and execute his debt payoff strategy.

Tone: CFO writing to a founder. Signal, not noise. Win/loss framing.
Format: Use ALL CAPS section headers. Use bullet points with ' — ' separators for data. Use ⚡ for urgent items. Keep it scannable."""


def _safe_rpc(name, params=None):
    try:
        result = rpc(name, params)
        return result or []
    except Exception as e:
        print(f"Warning: {name} failed: {e}")
        return []


def build_user_message(week_start, week_end, data):
    return f"""Generate a weekly financial review for the week of {week_start} to {week_end}.

DATA:
Cashflow: {json.dumps(data['cashflow'], default=str)}
Income: {json.dumps(data['income'], default=str)}
Expenses: {json.dumps(data['expenses'], default=str)}
Debt: {json.dumps(data['debt'], default=str)}
Savings: {json.dumps(data['savings'], default=str)}
Budget Alerts: {json.dumps(data['budget_alerts'], default=str)}
Milestones: {json.dumps(data['milestones'], default=str)}
Subscriptions: {json.dumps(data['subscriptions'], default=str)}

Structure with these EXACT 6 sections:

1. WEEKLY SCORECARD
Income vs expenses, net cashflow. How many budget categories stayed on track vs went over.
Frame as a win or loss — be honest.

2. MILESTONE PROGRESS
For each of the 4 milestones, show:
- Current value vs target
- % complete
- Projected completion date
- Pace: ahead / on track / behind
- What changed THIS WEEK specifically
If a milestone is behind pace, say what needs to change.

3. DEBT ATTACK
Which debt is the current avalanche target (highest interest rate).
Current balance, payments made this week, months remaining at current pace.
If extra payments would shorten timeline, show the math.

4. SPENDING BREAKDOWN
Top expense categories this week with amounts.
Flag anything unusual or over budget.
Compare to budget limits.

5. INCOME TRACKER
All income sources this week.
Flag if any expected income (coaching, Wryko) looks missing.
Project monthly total based on this week's pace.

6. DEBT VELOCITY
Rate of debt reduction this week vs target pace.
Current avalanche target status.
Cascade projection: when current target is paid off, show freed payment rolling to next debt.
Data: {json.dumps(data.get('debt_avalanche', []), default=str)}
Projections: {json.dumps(data.get('debt_projection', []), default=str)}

7. FINANCIAL POSITION
Net worth snapshot from balance sheet.
Income trend from pattern analysis.
Data: {json.dumps(data.get('balance_sheet', []), default=str)}
Income pattern: {json.dumps(data.get('income_pattern', []), default=str)}

8. BRIAN'S ORDERS
2-3 specific payment instructions for this week. Exact dollar amounts, exact accounts, exact payees.
These must directly tie to the most important obligations and milestones right now.
Priority: bills due → debt extra payment → savings contribution.

Write in plain text. Use ALL CAPS for headers. Use bullets with ' — ' separators."""


def main():
    print("=== VAULT Weekly Review Agent ===")

    today = date.today()
    week_end = today.isoformat()
    week_start = (today - timedelta(days=7)).isoformat()

    print(f"Week: {week_start} to {week_end}")

    # ── Fetch data ──────────────────────────────────────────────────
    data = {
        'cashflow': _safe_rpc("vault_weekly_cashflow"),
        'income': _safe_rpc("vault_income_breakdown"),
        'expenses': _safe_rpc("vault_expense_breakdown"),
        'debt': _safe_rpc("vault_debt_status"),
        'savings': _safe_rpc("vault_savings_progress"),
        'budget_alerts': _safe_rpc("vault_budget_alerts"),
        'milestones': _safe_rpc("vault_milestone_status"),
        'subscriptions': _safe_rpc("vault_active_subscriptions"),
        # Brian data
        'debt_avalanche': _safe_rpc("vault_debt_avalanche_order"),
        'debt_projection': _safe_rpc("vault_debt_payoff_projection", {"extra_monthly": 0}),
        'income_pattern': _safe_rpc("vault_income_pattern", {"months_back": 3}),
        'balance_sheet': _safe_rpc("vault_balance_sheet"),
    }

    print("All data fetched.")

    # ── Snapshot milestones for trend tracking ──────────────────────
    print("Snapshotting milestones...")
    try:
        rpc("vault_milestone_snapshot_insert")
        print("Milestone snapshots saved.")
    except Exception as e:
        print(f"Warning: milestone snapshot failed: {e}")

    # ── Generate digest via Claude ──────────────────────────────────
    user_message = build_user_message(week_start, week_end, data)

    print("Generating weekly review via Claude...")
    digest_text = call_claude(SYSTEM_PROMPT, user_message, max_tokens=4096)
    print("Review generated.")

    # ── Subject line ────────────────────────────────────────────────
    net_cashflow = 0
    if data['cashflow']:
        net_cashflow = data['cashflow'][0].get("net_cashflow", 0)
    net_display = f"{float(net_cashflow):,.2f}"

    # Count milestones on track
    on_track = sum(1 for m in data['milestones']
                   if m.get('projected_completion') and m.get('target_date')
                   and str(m['projected_completion']) <= str(m['target_date']))
    total_ms = len(data['milestones'])

    subject = (f"BRIAN: Week in Review — {week_start} "
               f"| Net: ${net_display} "
               f"| Milestones: {on_track}/{total_ms} on track")

    # ── Send email ──────────────────────────────────────────────────
    print(f"Sending: {subject}")
    send_email(subject, digest_text)
    print("Email sent.")

    # ── Log ─────────────────────────────────────────────────────────
    print("Logging to pf_digest_log...")
    sb = get_supabase()
    sb.table("pf_digest_log").insert({
        "week_start": week_start,
        "week_end": week_end,
        "digest_text": digest_text,
        "total_income": data['cashflow'][0]["total_income"] if data['cashflow'] else 0,
        "total_expenses": data['cashflow'][0]["total_expenses"] if data['cashflow'] else 0,
        "net_cashflow": net_cashflow,
        "needs_review_count": 0,
        "sent_at": datetime.now().isoformat(),
    }).execute()
    print("Logged.")

    print("=== VAULT Weekly Review complete ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: weekly review failed: {e}")
        import traceback
        traceback.print_exc()
        import sys
        sys.exit(1)
