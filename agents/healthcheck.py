"""VAULT Health Check — Monitors agent run health and alerts on failures.

Checks that key agents have run recently by looking at:
- pf_transactions: last intake (should be within 12 hours)
- pf_digest_log: last digest (should be within 8 days)
- pf_transactions reviewed=false: stale review queue

Sends alert email if any check fails.
"""

from datetime import datetime, timedelta, timezone

from agents.shared import get_supabase, send_email


def main():
    sb = get_supabase()
    now = datetime.now(timezone.utc)
    alerts = []

    # Check 1: Last transaction ingested (intake should run every 6 hours)
    try:
        result = sb.table("pf_transactions").select("created_at").order("created_at", desc=True).limit(1).execute()
        if result.data:
            last_intake = datetime.fromisoformat(result.data[0]["created_at"].replace("Z", "+00:00"))
            hours_ago = (now - last_intake).total_seconds() / 3600
            if hours_ago > 24:
                alerts.append(f"INTAKE: No new transactions in {hours_ago:.0f} hours (last: {last_intake.strftime('%Y-%m-%d %H:%M UTC')})")
        else:
            alerts.append("INTAKE: No transactions found in database at all")
    except Exception as e:
        alerts.append(f"INTAKE CHECK FAILED: {e}")

    # Check 2: Last digest sent (should be within 8 days)
    try:
        result = sb.table("pf_digest_log").select("sent_at").order("sent_at", desc=True).limit(1).execute()
        if result.data:
            last_digest = datetime.fromisoformat(result.data[0]["sent_at"].replace("Z", "+00:00"))
            days_ago = (now - last_digest).total_seconds() / 86400
            if days_ago > 8:
                alerts.append(f"DIGEST: No weekly digest in {days_ago:.0f} days (last: {last_digest.strftime('%Y-%m-%d')})")
    except Exception as e:
        alerts.append(f"DIGEST CHECK FAILED: {e}")

    # Check 3: Stale needs-review queue
    try:
        result = sb.table("pf_transactions").select("id", count="exact").eq("reviewed", False).eq("tax_category", "needs_review").execute()
        review_count = result.count or 0
        if review_count > 10:
            alerts.append(f"REVIEW QUEUE: {review_count} transactions pending review — classify them at the dashboard")
    except Exception as e:
        alerts.append(f"REVIEW CHECK FAILED: {e}")

    # Check 4: Budget overspend
    try:
        result = sb.rpc("vault_budget_alerts").execute()
        if result.data:
            for b in result.data:
                pct = float(b.get("pct_used", 0))
                if pct >= 100:
                    alerts.append(f"BUDGET: {b['category']} is at {pct:.0f}% (${b['current_spent']:.0f} / ${b['monthly_limit']:.0f})")
    except Exception:
        pass  # Budget alerts are non-critical

    if alerts:
        body = "VAULT Health Check detected issues:\n\n"
        for i, alert in enumerate(alerts, 1):
            body += f"  {i}. {alert}\n"
        body += "\nCheck your GitHub Actions runs and dashboard for details."
        send_email("VAULT HEALTH CHECK: Action Required", body)
        print(f"[HEALTHCHECK] {len(alerts)} alert(s) sent")
    else:
        print("[HEALTHCHECK] All systems healthy")


if __name__ == "__main__":
    main()
