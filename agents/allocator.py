"""Brian — Zero-Based Income Allocation Engine.

When income is detected, generates a precise allocation plan telling
Marcos exactly where every dollar goes: bills → debt → savings → discretionary.
"""

import json
import os
import sys
import traceback
from datetime import date

from agents.shared import (
    get_supabase, call_claude, rpc, parse_json_response, send_email,
)

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "..", "prompts", "allocation-engine.md")
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


def _build_allocation_email(plan_data: dict) -> str:
    """Build a human-readable email from the allocation plan JSON."""
    body = "BRIAN: INCOME ALLOCATION PLAN\n\n"
    body += f"{plan_data.get('summary', '')}\n\n"
    body += f"Income received — {_fmt(plan_data.get('income_amount', 0))} from {plan_data.get('income_source', 'unknown')}\n\n"

    body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    body += "ALLOCATION BREAKDOWN\n\n"

    priority_labels = {
        1: "BILLS DUE",
        2: "DEBT MINIMUMS",
        3: "DEBT EXTRA (AVALANCHE)",
        4: "SAVINGS",
        5: "DISCRETIONARY",
    }

    current_priority = 0
    for alloc in plan_data.get("allocations", []):
        p = alloc.get("priority", 5)
        if p != current_priority:
            current_priority = p
            body += f"\n{priority_labels.get(p, 'OTHER')}:\n"
        body += f"• {alloc['payee']} — {_fmt(alloc['amount'])}"
        if alloc.get("due_date"):
            body += f" (due {alloc['due_date']})"
        if alloc.get("from_account"):
            body += f" [from {alloc['from_account']}]"
        body += "\n"
        if alloc.get("rationale"):
            body += f"  {alloc['rationale']}\n"

    body += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    body += f"Total Allocated — {_fmt(plan_data.get('total_allocated', 0))}\n"
    body += f"Unallocated — {_fmt(plan_data.get('unallocated', 0))}\n\n"

    if plan_data.get("warnings"):
        body += "⚡ WARNINGS\n\n"
        for w in plan_data["warnings"]:
            body += f"⚡ {w}\n"
        body += "\n"

    if plan_data.get("debt_impact"):
        body += "DEBT IMPACT\n\n"
        body += f"• {plan_data['debt_impact']}\n\n"

    if plan_data.get("next_action"):
        body += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        body += "BRIAN'S ORDER\n\n"
        body += f"• {plan_data['next_action']}\n"

    return body


def _store_plan(plan_data: dict, trigger_type: str, income_txn: dict | None) -> str:
    """Store the allocation plan and payment instructions in Supabase."""
    sb = get_supabase()

    plan_row = {
        "trigger_type": trigger_type,
        "trigger_amount": float(plan_data.get("income_amount", 0)),
        "plan_json": plan_data,
        "status": "pending",
    }
    if income_txn:
        plan_row["trigger_transaction_id"] = income_txn.get("id")

    result = sb.table("pf_allocation_plans").insert(plan_row).execute()
    plan_id = result.data[0]["id"]

    # Insert individual payment instructions
    for alloc in plan_data.get("allocations", []):
        instruction_type_map = {
            1: "bill_payment",
            2: "debt_payment",
            3: "debt_payment",
            4: "savings_transfer",
            5: "discretionary",
        }
        instruction = {
            "allocation_plan_id": plan_id,
            "instruction_type": instruction_type_map.get(alloc.get("priority", 5), "discretionary"),
            "payee": alloc.get("payee", "unknown"),
            "amount": float(alloc.get("amount", 0)),
            "from_account": alloc.get("from_account"),
            "due_date": alloc.get("due_date"),
            "priority": alloc.get("priority", 5),
            "notes": alloc.get("rationale"),
        }
        sb.table("pf_payment_instructions").insert(instruction).execute()

    return plan_id


def allocate_income(trigger_type: str = "manual"):
    """Main allocation flow."""
    print("Brian: Checking for unallocated income...")

    # Fetch all needed data
    unallocated = _safe_rpc("vault_unallocated_income")
    obligations = _safe_rpc("vault_upcoming_obligations", {"days_ahead": 30})
    available = _safe_rpc("vault_available_for_allocation")
    debt_order = _safe_rpc("vault_debt_avalanche_order")
    savings = _safe_rpc("vault_savings_progress")
    accounts = _safe_rpc("vault_account_balances")
    budget_status = _safe_rpc("vault_budget_status")

    if not unallocated:
        print("Brian: No unallocated income found. Nothing to do.")
        return

    print(f"Brian: Found {len(unallocated)} unallocated income transaction(s).")

    # Process each unallocated income
    for income_txn in unallocated:
        amount = float(income_txn.get("amount", 0))
        vendor = income_txn.get("vendor", "unknown")
        print(f"\nBrian: Allocating {_fmt(amount)} from {vendor}...")

        user_message = f"""Allocate this income:

INCOME: {_fmt(amount)} from {vendor} on {income_txn.get('date')}

CURRENT STATE:
- Checking balance: {json.dumps(available, default=str)}
- Account balances: {json.dumps(accounts, default=str)}

OBLIGATIONS (next 30 days):
{json.dumps(obligations, default=str)}

DEBTS (avalanche order):
{json.dumps(debt_order, default=str)}

SAVINGS BUCKETS:
{json.dumps(savings, default=str)}

BUDGET STATUS:
{json.dumps(budget_status, default=str)}

Generate a zero-based allocation plan for this ${amount:.2f} income."""

        raw_response = call_claude(SYSTEM_PROMPT, user_message, max_tokens=2048)
        plan_data = parse_json_response(raw_response)

        if plan_data is None:
            print(f"  [ERROR] Could not parse allocation plan from Claude")
            print(f"  Raw: {raw_response[:500]}")
            # Send raw text as email instead
            send_email(
                f"BRIAN: Allocation Plan — {_fmt(amount)} from {vendor}",
                raw_response,
            )
            continue

        # Store the plan
        plan_id = _store_plan(plan_data, trigger_type, income_txn)
        print(f"  [OK] Plan stored: {plan_id}")

        # Build and send email
        email_body = _build_allocation_email(plan_data)
        subject = f"BRIAN: Income Allocation — {_fmt(amount)} from {vendor}"
        send_email(subject, email_body)
        print(f"  [OK] Allocation email sent")


def weekly_rebalance():
    """Weekly rebalance — runs even without new income to reallocate based on current state."""
    print("Brian: Running weekly rebalance...")

    available = _safe_rpc("vault_available_for_allocation")
    obligations = _safe_rpc("vault_upcoming_obligations", {"days_ahead": 30})
    debt_order = _safe_rpc("vault_debt_avalanche_order")
    savings = _safe_rpc("vault_savings_progress")
    accounts = _safe_rpc("vault_account_balances")
    budget_status = _safe_rpc("vault_budget_status")
    schedule = _safe_rpc("vault_bill_payment_schedule", {"days_ahead": 14})

    checking_balance = 0
    if available and isinstance(available, list):
        checking_balance = float(available[0].get("checking_balance", 0))
    elif available and isinstance(available, dict):
        checking_balance = float(available.get("checking_balance", 0))

    user_message = f"""Weekly rebalance — no new income, just reviewing current allocation.

CURRENT STATE:
- Checking balance: {_fmt(checking_balance)}
- Available: {json.dumps(available, default=str)}
- Account balances: {json.dumps(accounts, default=str)}

PAYMENT SCHEDULE (next 14 days):
{json.dumps(schedule, default=str)}

OBLIGATIONS (next 30 days):
{json.dumps(obligations, default=str)}

DEBTS (avalanche order):
{json.dumps(debt_order, default=str)}

SAVINGS BUCKETS:
{json.dumps(savings, default=str)}

BUDGET STATUS:
{json.dumps(budget_status, default=str)}

Review the current financial state. If there's surplus in checking beyond obligations, generate an allocation plan for the surplus. If there's a shortfall, generate warnings. Set income_amount to the surplus amount (or 0 if none)."""

    raw_response = call_claude(SYSTEM_PROMPT, user_message, max_tokens=2048)
    plan_data = parse_json_response(raw_response)

    if plan_data is None:
        send_email("BRIAN: Weekly Rebalance Report", raw_response)
        return

    plan_id = _store_plan(plan_data, "weekly_rebalance", None)
    email_body = _build_allocation_email(plan_data)
    send_email("BRIAN: Weekly Rebalance", email_body)
    print(f"Rebalance plan stored: {plan_id}")


def main():
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "income"

    if mode == "rebalance":
        weekly_rebalance()
    else:
        allocate_income(trigger_type="income_detected")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: allocator failed: {e}")
        traceback.print_exc()
        sys.exit(1)
