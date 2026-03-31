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


def _wrap_html(subject: str, body_text: str) -> str:
    """Wrap plain text body in a professional HTML email template."""
    # Convert plain text sections to HTML
    lines = body_text.split("\n")
    html_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            html_lines.append('<div style="height:12px"></div>')
        elif stripped.startswith("━") or stripped.startswith("---"):
            html_lines.append('<hr style="border:none;border-top:1px solid #e2e8f0;margin:16px 0">')
        elif stripped.startswith("⚡") or stripped.startswith("⚠"):
            html_lines.append(f'<div style="background:#fef2f2;border-left:4px solid #ef4444;padding:8px 12px;margin:4px 0;font-size:14px">{stripped}</div>')
        elif stripped.startswith("•") or stripped.startswith("-"):
            html_lines.append(f'<div style="padding:2px 0 2px 16px;font-size:14px;color:#334155">{stripped}</div>')
        elif stripped.isupper() and len(stripped) > 3:
            html_lines.append(f'<h3 style="color:#0f172a;font-size:14px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;margin:16px 0 8px;padding-bottom:4px;border-bottom:2px solid #1e293b">{stripped}</h3>')
        elif stripped.endswith(":") and len(stripped) < 60:
            html_lines.append(f'<div style="font-weight:600;color:#1e293b;font-size:14px;margin-top:8px">{stripped}</div>')
        else:
            html_lines.append(f'<div style="font-size:14px;color:#334155;line-height:1.6">{stripped}</div>')

    body_html = "\n".join(html_lines)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:24px 0">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1)">
  <tr><td style="background:#0f172a;padding:20px 32px">
    <table width="100%"><tr>
      <td style="color:#ffffff;font-size:20px;font-weight:700;letter-spacing:0.5px">VAULT</td>
      <td align="right" style="color:#94a3b8;font-size:12px">Personal Finance Intelligence</td>
    </tr></table>
  </td></tr>
  <tr><td style="padding:24px 32px 8px">
    <div style="font-size:18px;font-weight:700;color:#0f172a;margin-bottom:4px">{subject}</div>
  </td></tr>
  <tr><td style="padding:8px 32px 32px">
    {body_html}
  </td></tr>
  <tr><td style="background:#f8fafc;padding:16px 32px;border-top:1px solid #e2e8f0">
    <div style="font-size:11px;color:#94a3b8;text-align:center">VAULT — Autonomous Financial Intelligence for Marcos Matthews<br>Powered by Claude AI &bull; github.com/marcosmvm/vault-personal-finance</div>
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
