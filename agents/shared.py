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


def _render_pipe_table(rows: list[list[str]]) -> str:
    """Render pipe-delimited rows as a styled HTML table."""
    if not rows:
        return ""
    header = rows[0]
    data = rows[1:]
    cols = len(header)

    html = (
        '<table width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:12px 0;border-collapse:collapse;border-radius:8px;overflow:hidden;'
        'border:1px solid #e2e8f0">'
    )
    # Header row
    html += '<tr>'
    for i, cell in enumerate(header):
        align = 'left' if i == 0 else 'right'
        html += (
            f'<td style="padding:10px 14px;font-size:11px;font-weight:700;'
            f'text-transform:uppercase;letter-spacing:1px;color:#64748b;'
            f'background:#f8fafc;border-bottom:2px solid #d4a574;'
            f'text-align:{align}">{cell}</td>'
        )
    html += '</tr>'
    # Data rows
    for row_idx, row in enumerate(data):
        bg = '#ffffff' if row_idx % 2 == 0 else '#fafaf8'
        html += '<tr>'
        for i, cell in enumerate(row + [''] * (cols - len(row))):
            if i == 0:
                html += (
                    f'<td style="padding:9px 14px;font-size:13px;font-weight:600;'
                    f'color:#1e293b;background:{bg};border-bottom:1px solid #f1f5f9;'
                    f'text-align:left">{cell}</td>'
                )
            else:
                html += (
                    f'<td style="padding:9px 14px;font-size:13px;color:#334155;'
                    f'font-variant-numeric:tabular-nums;background:{bg};'
                    f'border-bottom:1px solid #f1f5f9;text-align:right">{cell}</td>'
                )
        html += '</tr>'
    html += '</table>'
    return html


def _wrap_html(subject: str, body_text: str, date_display: str | None = None) -> str:
    """Wrap plain text body in a clean fintech-style HTML email template."""
    from datetime import date as _date
    if date_display is None:
        date_display = _date.today().strftime("%B %-d, %Y")
    lines = body_text.split("\n")
    html_lines = []
    in_bullet_list = False
    pipe_table_rows = []  # accumulate pipe-delimited table lines

    for line in lines:
        stripped = line.strip()

        # Detect bullet items
        is_bullet = stripped.startswith(("• ", "- ", "* ")) and len(stripped) > 2
        # Detect pipe-delimited table rows
        is_pipe = stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3

        # Close open list if switching away from bullets
        if in_bullet_list and not is_bullet:
            html_lines.append("</table>")
            html_lines.append("</td></tr></table>")
            in_bullet_list = False

        # Flush pipe table when switching away
        if pipe_table_rows and not is_pipe:
            html_lines.append(_render_pipe_table(pipe_table_rows))
            pipe_table_rows = []

        if is_pipe:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            pipe_table_rows.append(cells)

        elif not stripped:
            html_lines.append('<div style="height:12px"></div>')

        elif stripped.startswith("━") or stripped.startswith("---"):
            html_lines.append(
                '<table width="100%" cellpadding="0" cellspacing="0" style="margin:18px 0">'
                '<tr><td style="height:1px;background:linear-gradient(90deg,#d4a574 0%,#e8c99b 50%,transparent 100%);'
                'opacity:0.3"></td></tr></table>'
            )

        elif stripped.startswith(("⚡", "⚠")):
            # Warm amber notification card -- professional, not alarming
            html_lines.append(
                f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:10px 0">'
                f'<tr><td style="background:#fffbeb;border-left:4px solid #f59e0b;'
                f'border-radius:0 8px 8px 0;padding:14px 18px;'
                f'box-shadow:0 1px 3px rgba(245,158,11,0.1)">'
                f'<div style="font-size:13px;color:#92400e;line-height:1.6;font-weight:500">'
                f'{stripped}</div>'
                f'</td></tr></table>'
            )

        elif is_bullet:
            content = stripped[2:]
            if not in_bullet_list:
                # Bullet list wrapper with subtle left accent
                html_lines.append(
                    '<table width="100%" cellpadding="0" cellspacing="0" style="margin:6px 0">'
                    '<tr><td style="border-left:3px solid #d4a574;padding-left:16px;border-radius:0 4px 4px 0">'
                    '<table width="100%" cellpadding="0" cellspacing="0">'
                )
                in_bullet_list = True
            # Split on " — " or " - " for two-column layout
            if " — " in content:
                left, right = content.split(" — ", 1)
                html_lines.append(
                    f'<tr>'
                    f'<td width="16" valign="top" style="padding:5px 0;color:#d4a574;'
                    f'font-size:14px;font-weight:bold">&#x2022;</td>'
                    f'<td style="padding:5px 0;font-size:13px;color:#1e293b;font-weight:600">'
                    f'{left}</td>'
                    f'<td align="right" style="padding:5px 0;font-size:13px;color:#64748b;'
                    f'font-variant-numeric:tabular-nums">'
                    f'{right}</td></tr>'
                )
            else:
                html_lines.append(
                    f'<tr>'
                    f'<td width="16" valign="top" style="padding:5px 0;color:#d4a574;'
                    f'font-size:14px;font-weight:bold">&#x2022;</td>'
                    f'<td colspan="2" style="padding:5px 0;font-size:13px;color:#334155;'
                    f'line-height:1.7">{content}</td></tr>'
                )

        elif stripped.isupper() and len(stripped) > 3:
            # Section header -- pill-style accent bar
            html_lines.append(
                f'<table width="100%" cellpadding="0" cellspacing="0" style="margin:24px 0 12px">'
                f'<tr>'
                f'<td width="4" style="background:linear-gradient(180deg,#d4a574,#e8c99b);'
                f'border-radius:4px"></td>'
                f'<td style="padding-left:12px">'
                f'<div style="font-size:11px;font-weight:800;text-transform:uppercase;'
                f'letter-spacing:1.5px;color:#0f172a">{stripped}</div>'
                f'</td></tr></table>'
            )

        elif stripped.endswith(":") and len(stripped) < 60:
            html_lines.append(
                f'<div style="font-weight:600;color:#1e293b;font-size:13px;'
                f'margin-top:12px;margin-bottom:2px">{stripped}</div>'
            )

        else:
            html_lines.append(
                f'<div style="font-size:13px;color:#475569;line-height:1.75">{stripped}</div>'
            )

    if in_bullet_list:
        html_lines.append("</table>")
        html_lines.append("</td></tr></table>")
    if pipe_table_rows:
        html_lines.append(_render_pipe_table(pipe_table_rows))

    body_html = "\n".join(html_lines)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f2;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f2;padding:40px 0">
<tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(15,23,42,0.08),0 1px 3px rgba(15,23,42,0.04)">

  <!-- Header with light design -->
  <tr><td style="background:#ffffff;padding:0">
    <!-- Vibrant gradient stripe at top -->
    <div style="height:5px;background:linear-gradient(90deg,#d4a574,#e8956a,#d4a574)"></div>
    <table width="100%" cellpadding="0" cellspacing="0" style="padding:24px 36px 20px">
      <tr>
        <td>
          <div style="font-size:26px;font-weight:800;letter-spacing:3px;color:#0f172a">VAULT</div>
          <div style="font-size:13px;letter-spacing:0.5px;color:#1e293b;margin-top:4px;font-weight:600">Marcos Matthews</div>
        </td>
        <td align="right" style="vertical-align:bottom">
          <div style="font-size:12px;color:#64748b;font-weight:500;letter-spacing:0.3px">{date_display}</div>
        </td>
      </tr>
    </table>
  </td></tr>

  <!-- Subject line -->
  <tr><td style="padding:28px 36px 6px">
    <div style="font-size:18px;font-weight:700;color:#0f172a;letter-spacing:-0.2px">{subject}</div>
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:14px 36px 28px">
    {body_html}
  </td></tr>

  <!-- Tax Dashboard CTA Button -->
  <tr><td style="padding:0 36px 32px">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr><td align="center" style="padding:0">
        <table cellpadding="0" cellspacing="0">
          <tr><td align="center" style="border-radius:12px;background:linear-gradient(135deg,#d4a574 0%,#e8956a 100%);box-shadow:0 4px 14px rgba(212,165,116,0.3),0 2px 6px rgba(232,149,106,0.2)">
            <a href="{TAX_DASHBOARD_URL}" target="_blank" style="display:inline-block;padding:14px 36px;font-size:14px;font-weight:700;color:#ffffff;text-decoration:none;letter-spacing:0.5px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
              View My Taxes
              <span style="color:#ffffff;margin-left:6px">&rarr;</span>
            </a>
          </td></tr>
        </table>
      </td></tr>
    </table>
  </td></tr>

  <!-- Footer -->
  <tr><td style="padding:0">
    <div style="height:1px;background:linear-gradient(90deg,transparent,#e2e8f0,transparent)"></div>
    <table width="100%" cellpadding="0" cellspacing="0" style="padding:18px 36px">
      <tr>
        <td style="font-size:10px;color:#94a3b8;letter-spacing:0.3px">VAULT v2.0</td>
        <td align="right" style="font-size:10px;color:#94a3b8;letter-spacing:0.3px">Powered by Claude AI</td>
      </tr>
    </table>
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
