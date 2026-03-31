"""VAULT Subscription Manager Agent.

Runs weekly to audit all active subscriptions.
Can also process cancel/pause/flag actions via command-line arguments.

Usage:
    python -m agents.subscription_manager                          # weekly audit
    python -m agents.subscription_manager cancel <uuid> "reason"   # cancel
    python -m agents.subscription_manager pause  <uuid> "reason"   # pause
    python -m agents.subscription_manager flag   <uuid> "reason"   # flag as zombie
"""

import sys
import json
from datetime import datetime

from agents.shared import get_supabase, rpc, send_email


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lookup_subscription(sub_id: str):
    """Fetch a single subscription by ID. Returns None if not found."""
    sb = get_supabase()
    try:
        result = (
            sb.table("pf_subscriptions")
            .select("*")
            .eq("id", sub_id)
            .single()
            .execute()
        )
        return result.data
    except Exception as exc:
        print(f"[subscription_manager] Subscription {sub_id} not found: {exc}")
        return None


def _format_currency(amount) -> str:
    """Format a number as USD currency string."""
    try:
        return f"${float(amount):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


# ---------------------------------------------------------------------------
# Weekly Audit
# ---------------------------------------------------------------------------

def audit():
    """Weekly subscription audit — fetch all active subs, build report, email it."""
    print("[subscription_manager] Starting weekly subscription audit...")

    subscriptions = rpc("vault_active_subscriptions")

    if not subscriptions:
        print("[subscription_manager] No active subscriptions returned.")
        send_email(
            subject="VAULT Weekly Subscription Audit — No Active Subscriptions",
            body="No active subscriptions were found during this week's audit.",
        )
        return

    print(f"[subscription_manager] Found {len(subscriptions)} active subscriptions.")

    # Calculate total monthly cost
    total_monthly = 0.0
    for sub in subscriptions:
        amount = float(sub.get("amount", 0) or 0)
        cycle = (sub.get("cycle") or "monthly").lower()
        if cycle == "yearly" or cycle == "annual":
            total_monthly += amount / 12
        elif cycle == "weekly":
            total_monthly += amount * 4.33
        elif cycle == "quarterly":
            total_monthly += amount / 3
        else:
            total_monthly += amount

    # Separate zombies and low-priority
    zombies = [s for s in subscriptions if (s.get("priority") or "").lower() == "zombie"]
    low_priority = [s for s in subscriptions if (s.get("priority") or "").lower() == "low"]

    # Build report
    lines = [
        "VAULT Weekly Subscription Audit",
        "=" * 40,
        "",
        f"Total Active Subscriptions: {len(subscriptions)}",
        f"Total Monthly Cost: {_format_currency(total_monthly)}",
        "",
    ]

    if zombies:
        lines.append("ZOMBIE SUBSCRIPTIONS (recommend cancel):")
        for s in zombies:
            name = s.get("name", "Unknown")
            amount = _format_currency(s.get("amount", 0))
            cycle = s.get("cycle", "monthly")
            purpose = s.get("purpose", "N/A")
            lines.append(f"  * {name} — {amount}/{cycle} — {purpose}")
        lines.append("")

    if low_priority:
        lines.append("LOW PRIORITY (review needed):")
        for s in low_priority:
            name = s.get("name", "Unknown")
            amount = _format_currency(s.get("amount", 0))
            cycle = s.get("cycle", "monthly")
            purpose = s.get("purpose", "N/A")
            lines.append(f"  * {name} — {amount}/{cycle} — {purpose}")
        lines.append("")

    lines.append("ALL ACTIVE:")
    for s in subscriptions:
        name = s.get("name", "Unknown")
        amount = _format_currency(s.get("amount", 0))
        cycle = s.get("cycle", "monthly")
        priority = s.get("priority", "normal")
        next_charge = s.get("next_charge", "N/A")
        lines.append(f"  * {name} — {amount}/{cycle} [{priority}] — next: {next_charge}")

    body = "\n".join(lines)
    print("[subscription_manager] Sending audit email...")
    send_email(subject="VAULT Weekly Subscription Audit", body=body)
    print("[subscription_manager] Audit complete.")


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

def cancel(sub_id: str, reason: str):
    """Cancel a subscription."""
    print(f"[subscription_manager] Cancelling subscription {sub_id}...")

    sub = _lookup_subscription(sub_id)
    if sub is None:
        print(f"[subscription_manager] Abort — subscription {sub_id} not found.")
        return

    sb = get_supabase()
    now = datetime.now().isoformat()

    # Update subscription
    sb.table("pf_subscriptions").update({
        "active": False,
        "cancellation_notes": f"Cancelled on {now}. Reason: {reason}",
    }).eq("id", sub_id).execute()

    print(f"[subscription_manager] Subscription {sub.get('name')} marked inactive.")

    # Log to pf_transactions
    sb.table("pf_transactions").insert({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "description": f"Subscription cancelled: {sub.get('name', 'Unknown')}",
        "amount": 0,
        "type": "transfer",
        "notes": f"Reason: {reason}",
    }).execute()

    print("[subscription_manager] Transaction logged.")

    # Confirmation email
    cancel_url = sub.get("cancel_url", "")
    url_line = f"\nCancel URL: {cancel_url}" if cancel_url else ""

    body = (
        f"Subscription Cancelled\n"
        f"{'=' * 40}\n\n"
        f"Name: {sub.get('name', 'Unknown')}\n"
        f"Amount: {_format_currency(sub.get('amount', 0))}/{sub.get('cycle', 'monthly')}\n"
        f"Reason: {reason}\n"
        f"Cancelled at: {now}\n"
        f"{url_line}\n\n"
        f"This subscription has been marked inactive in VAULT."
    )

    send_email(subject=f"VAULT — Subscription Cancelled: {sub.get('name')}", body=body)
    print("[subscription_manager] Confirmation email sent.")


# ---------------------------------------------------------------------------
# Pause
# ---------------------------------------------------------------------------

def pause(sub_id: str, reason: str):
    """Pause a subscription."""
    print(f"[subscription_manager] Pausing subscription {sub_id}...")

    sub = _lookup_subscription(sub_id)
    if sub is None:
        print(f"[subscription_manager] Abort — subscription {sub_id} not found.")
        return

    sb = get_supabase()
    now = datetime.now().isoformat()

    sb.table("pf_subscriptions").update({
        "active": False,
        "cancellation_notes": f"Paused on {now}. Reason: {reason}",
    }).eq("id", sub_id).execute()

    print(f"[subscription_manager] Subscription {sub.get('name')} paused.")

    body = (
        f"Subscription Paused\n"
        f"{'=' * 40}\n\n"
        f"Name: {sub.get('name', 'Unknown')}\n"
        f"Amount: {_format_currency(sub.get('amount', 0))}/{sub.get('cycle', 'monthly')}\n"
        f"Reason: {reason}\n"
        f"Paused at: {now}\n\n"
        f"This subscription has been marked inactive (paused) in VAULT.\n"
        f"Run the agent again to reactivate when ready."
    )

    send_email(subject=f"VAULT — Subscription Paused: {sub.get('name')}", body=body)
    print("[subscription_manager] Confirmation email sent.")


# ---------------------------------------------------------------------------
# Flag
# ---------------------------------------------------------------------------

def flag(sub_id: str, reason: str):
    """Flag a subscription as zombie."""
    print(f"[subscription_manager] Flagging subscription {sub_id} as zombie...")

    sub = _lookup_subscription(sub_id)
    if sub is None:
        print(f"[subscription_manager] Abort — subscription {sub_id} not found.")
        return

    sb = get_supabase()
    now = datetime.now().isoformat()

    sb.table("pf_subscriptions").update({
        "priority": "zombie",
        "cancellation_notes": f"Flagged as zombie on {now}. Reason: {reason}",
    }).eq("id", sub_id).execute()

    print(f"[subscription_manager] Subscription {sub.get('name')} flagged as zombie.")

    body = (
        f"Subscription Flagged as Zombie\n"
        f"{'=' * 40}\n\n"
        f"Name: {sub.get('name', 'Unknown')}\n"
        f"Amount: {_format_currency(sub.get('amount', 0))}/{sub.get('cycle', 'monthly')}\n"
        f"Reason: {reason}\n"
        f"Flagged at: {now}\n\n"
        f"This subscription has been marked as a zombie in VAULT.\n"
        f"It will appear in the next weekly audit under ZOMBIE SUBSCRIPTIONS."
    )

    send_email(subject=f"VAULT — Subscription Flagged: {sub.get('name')}", body=body)
    print("[subscription_manager] Confirmation email sent.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2 or sys.argv[1] == "audit":
        audit()
    elif sys.argv[1] == "cancel" and len(sys.argv) >= 3:
        reason = sys.argv[3] if len(sys.argv) > 3 else "No reason provided"
        cancel(sys.argv[2], reason)
    elif sys.argv[1] == "pause" and len(sys.argv) >= 3:
        reason = sys.argv[3] if len(sys.argv) > 3 else "No reason provided"
        pause(sys.argv[2], reason)
    elif sys.argv[1] == "flag" and len(sys.argv) >= 3:
        reason = sys.argv[3] if len(sys.argv) > 3 else "No reason provided"
        flag(sys.argv[2], reason)
    else:
        print(
            "Usage: python -m agents.subscription_manager "
            "[audit|cancel|pause|flag] [subscription_id] [reason]"
        )


if __name__ == "__main__":
    main()
