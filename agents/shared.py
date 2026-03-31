"""Shared utilities for all VAULT agents."""

import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import anthropic
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()


def get_supabase() -> Client:
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_KEY"],
    )


def get_claude() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def call_claude(system: str, user_message: str, max_tokens: int = 2048) -> str:
    """Call Claude API and return the text response."""
    client = get_claude()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


def rpc(name: str, params: dict | None = None):
    """Call a Supabase RPC function and return the result."""
    sb = get_supabase()
    result = sb.rpc(name, params or {}).execute()
    return result.data


TAX_DASHBOARD_URL = "https://marcosmvm.github.io/vault-personal-finance/"


def _wrap_html(subject: str, body_text: str) -> str:
    """Wrap plain text body in a professional HTML email template."""
    lines = body_text.split("\n")
    html_lines = []
    in_bullet_list = False

    for line in lines:
        stripped = line.strip()

        # Detect bullet items
        is_bullet = stripped.startswith(("• ", "- ", "* ")) and len(stripped) > 2

        # Close open list if switching away from bullets
        if in_bullet_list and not is_bullet:
            html_lines.append("</table>")
            in_bullet_list = False

        if not stripped:
            html_lines.append('<div style="height:10px"></div>')

        elif stripped.startswith("━") or stripped.startswith("---"):
            html_lines.append('<hr style="border:none;border-top:1px solid #e2e8f0;margin:20px 0">')

        elif stripped.startswith(("⚡", "⚠")):
            # Professional muted alert — not full red
            html_lines.append(
                f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:6px 0"><tr>'
                f'<td style="background:#fafafa;border-left:3px solid #94a3b8;padding:10px 14px;'
                f'border-radius:0 4px 4px 0;font-size:13px;color:#334155;line-height:1.5">'
                f'{stripped}</td></tr></table>'
            )

        elif is_bullet:
            content = stripped[2:]
            if not in_bullet_list:
                html_lines.append(
                    '<table width="100%" cellpadding="0" cellspacing="0" '
                    'style="margin:4px 0 4px 4px">'
                )
                in_bullet_list = True
            # Split on " — " or " - " for two-column layout
            if " — " in content:
                left, right = content.split(" — ", 1)
                html_lines.append(
                    f'<tr><td width="16" valign="top" style="padding:3px 0;color:#64748b;'
                    f'font-size:13px">&#x2022;</td>'
                    f'<td style="padding:3px 0;font-size:13px;color:#1e293b;font-weight:500">'
                    f'{left}</td>'
                    f'<td align="right" style="padding:3px 0;font-size:13px;color:#64748b">'
                    f'{right}</td></tr>'
                )
            else:
                html_lines.append(
                    f'<tr><td width="16" valign="top" style="padding:3px 0;color:#64748b;'
                    f'font-size:13px">&#x2022;</td>'
                    f'<td colspan="2" style="padding:3px 0;font-size:13px;color:#334155;'
                    f'line-height:1.5">{content}</td></tr>'
                )

        elif stripped.isupper() and len(stripped) > 3:
            # Section header
            html_lines.append(
                f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0 10px">'
                f'<tr><td style="font-size:12px;font-weight:700;text-transform:uppercase;'
                f'letter-spacing:1px;color:#0f172a;padding-bottom:6px;'
                f'border-bottom:2px solid #0f172a">{stripped}</td></tr></table>'
            )

        elif stripped.endswith(":") and len(stripped) < 60:
            html_lines.append(
                f'<div style="font-weight:600;color:#1e293b;font-size:13px;'
                f'margin-top:10px">{stripped}</div>'
            )

        else:
            html_lines.append(
                f'<div style="font-size:13px;color:#334155;line-height:1.7">{stripped}</div>'
            )

    if in_bullet_list:
        html_lines.append("</table>")

    body_html = "\n".join(html_lines)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:32px 0">
<tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.08)">

  <!-- Header -->
  <tr><td style="background:#0f172a;padding:20px 32px">
    <table width="100%"><tr>
      <td style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:1px">VAULT</td>
      <td align="right" style="color:#94a3b8;font-size:11px;letter-spacing:0.3px">PERSONAL FINANCE INTELLIGENCE</td>
    </tr></table>
  </td></tr>

  <!-- Subject line -->
  <tr><td style="padding:24px 32px 4px">
    <div style="font-size:17px;font-weight:700;color:#0f172a">{subject}</div>
    <div style="font-size:11px;color:#94a3b8;margin-top:4px">Prepared for Marcos Matthews</div>
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:12px 32px 24px">
    {body_html}
  </td></tr>

  <!-- Tax Dashboard Link -->
  <tr><td style="padding:0 32px 24px">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr><td style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:14px 16px;text-align:center">
        <a href="{TAX_DASHBOARD_URL}" style="color:#0f172a;font-size:13px;font-weight:600;text-decoration:none;letter-spacing:0.3px">View Tax Dashboard &rarr;</a>
        <div style="font-size:11px;color:#94a3b8;margin-top:4px">Monthly &bull; Quarterly &bull; Annual &bull; Audit-Ready</div>
      </td></tr>
    </table>
  </td></tr>

  <!-- Footer -->
  <tr><td style="background:#f8fafc;padding:16px 32px;border-top:1px solid #e2e8f0">
    <table width="100%"><tr>
      <td style="font-size:10px;color:#94a3b8">VAULT v1.0 &bull; Autonomous Agent</td>
      <td align="right" style="font-size:10px;color:#94a3b8">Powered by Claude AI</td>
    </tr></table>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""


def send_email(subject: str, body: str, to: str | None = None):
    """Send a professional HTML email via Gmail SMTP."""
    sender = os.environ["GMAIL_ADDRESS"]
    recipient = to or os.environ.get("ALERT_EMAIL", sender)
    password = os.environ["GMAIL_APP_PASSWORD"]

    msg = MIMEMultipart("alternative")
    msg["From"] = f"VAULT <{sender}>"
    msg["To"] = recipient
    msg["Subject"] = subject

    # Attach both plain text and HTML versions
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(_wrap_html(subject, body), "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())


def parse_json_response(text: str) -> dict | None:
    """Parse JSON from Claude response, handling markdown code blocks."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # remove opening ```json
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
