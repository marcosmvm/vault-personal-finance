"""VAULT Monthly Tax Compiler Agent.

Compiles all transactions into IRS-ready Schedule C forms for
Wryko (B2B SaaS) and Coaching (Soccer) entities.
"""

import json
from datetime import datetime, timedelta, date

from agents.shared import get_supabase, call_claude, rpc, send_email, parse_json_response


SYSTEM_PROMPT = """You are a tax preparation specialist. Given raw financial totals, produce IRS Schedule C (Form 1040) structured data. Be precise. Use actual IRS line numbers. Flag any line that requires supporting documentation. Return ONLY valid JSON."""

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


def build_schedule_c_prompt(entity_name: str, business_description: str, current_year: int, totals_json: str) -> str:
    return f"""Produce IRS Schedule C data for:

ENTITY: {entity_name}
BUSINESS: {business_description}
TAX YEAR: {current_year}

FINANCIAL DATA:
{totals_json}

Return structured JSON matching Schedule C line items:
{{"form": "Schedule C", "entity": "...", "tax_year": {current_year}, "part_1_income": {{"line_1_gross_receipts": 0.00, "line_7_gross_income": 0.00}}, "part_2_expenses": {{"line_18_office_expense": 0.00, "line_27a_other_expenses": 0.00, "line_28_total_expenses": 0.00}}, "line_31_net_profit_loss": 0.00, "supporting_detail": {{"other_expenses_breakdown": [], "flags_requiring_documentation": []}}}}"""


def compile_entity(entity: dict, current_year: int) -> dict:
    """Compile Schedule C for a single entity. Returns summary totals."""
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

    raw_response = call_claude(SYSTEM_PROMPT, user_message, max_tokens=2048)
    schedule_c = parse_json_response(raw_response)

    if schedule_c is None:
        print(f"  WARNING: Failed to parse Schedule C JSON for {entity['entity_name']}. Storing raw response.")
        schedule_c = {"raw_response": raw_response, "parse_error": True}

    # Extract summary figures from the parsed Schedule C
    gross_income = 0.0
    total_expenses = 0.0
    net_profit_loss = 0.0

    if not schedule_c.get("parse_error"):
        part1 = schedule_c.get("part_1_income", {})
        part2 = schedule_c.get("part_2_expenses", {})
        gross_income = part1.get("line_1_gross_receipts", part1.get("line_7_gross_income", 0.0))
        total_expenses = part2.get("line_28_total_expenses", 0.0)
        net_profit_loss = schedule_c.get("line_31_net_profit_loss", 0.0)

    # Save to pf_tax_documents
    print(f"  Saving Schedule C for {entity['entity_name']} to pf_tax_documents...")
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
    # Delete existing row for this year/form, then insert fresh
    sb.table("pf_tax_documents").delete().eq(
        "tax_year", current_year
    ).eq(
        "form_type", f"Schedule_C_{entity['entity_key']}"
    ).execute()
    sb.table("pf_tax_documents").insert(row).execute()
    print(f"  Schedule C saved for {entity['entity_name']}.")

    return {
        "entity_key": entity["entity_key"],
        "entity_name": entity["entity_name"],
        "gross_income": gross_income,
        "total_expenses": total_expenses,
        "net_profit_loss": net_profit_loss,
    }


def build_summary_email(results: dict, month: str, year: int) -> str:
    wryko = results["wryko"]
    coaching = results["coaching"]

    combined_net = wryko["net_profit_loss"] + coaching["net_profit_loss"]
    se_tax = combined_net * 0.153
    fed_tax = combined_net * 0.22
    total_quarterly = (se_tax + fed_tax) / 4

    def fmt(val):
        return f"{val:,.2f}"

    return f"""VAULT Tax Update — {month} {year}

YTD Schedule C Summary:

WRYKO (B2B SaaS)
  Gross Income: ${fmt(wryko['gross_income'])}
  Total Expenses: ${fmt(wryko['total_expenses'])}
  Net P&L: ${fmt(wryko['net_profit_loss'])}

COACHING (Soccer)
  Gross Income: ${fmt(coaching['gross_income'])}
  Total Expenses: ${fmt(coaching['total_expenses'])}
  Net P&L: ${fmt(coaching['net_profit_loss'])}

COMBINED
  Net Profit: ${fmt(combined_net)}
  Est. SE Tax (15.3%): ${fmt(se_tax)}
  Est. Federal Tax (22%): ${fmt(fed_tax)}
  Est. Total Quarterly: ${fmt(total_quarterly)}"""


def main():
    print("=== VAULT Tax Compiler Agent ===")

    today = date.today()
    current_year = today.year
    month = today.strftime("%B")

    print(f"Compiling Schedule C forms for {month} {current_year}...")

    # Compile each entity
    summaries = {}
    for entity in ENTITIES:
        print(f"Processing {entity['entity_name']}...")
        summary = compile_entity(entity, current_year)
        summaries[summary["entity_key"]] = summary

    # Build and send summary email
    wryko = summaries["wryko"]
    coaching = summaries["coaching"]

    email_body = build_summary_email(summaries, month, current_year)

    def fmt(val):
        return f"{val:,.2f}"

    subject = (
        f"VAULT Tax Update — {month} {current_year} "
        f"| Wryko P&L: ${fmt(wryko['net_profit_loss'])} "
        f"| Coaching P&L: ${fmt(coaching['net_profit_loss'])}"
    )

    print(f"Sending tax summary email: {subject}")
    send_email(subject, email_body)
    print("Email sent.")

    print("=== VAULT Tax Compiler complete ===")


if __name__ == "__main__":
    main()
