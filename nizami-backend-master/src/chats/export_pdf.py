"""
PDF generation for chat exports — Nizami branded.

Uses WeasyPrint (HTML → PDF via headless rendering).
Install: weasyprint>=62.0
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Nizami brand
# ---------------------------------------------------------------------------

# Inline SVG logo (59×35 viewBox — black + #009C4B green accent)
_LOGO_SVG = """<svg width="118" height="70" viewBox="0 0 59 35" fill="none" xmlns="http://www.w3.org/2000/svg">
<g clip-path="url(#clip0)">
<path d="M7.63035 26.2205C7.7657 26.0701 7.88977 25.9329 8.01571 25.7919C8.58154 26.2694 9.25264 26.3727 9.95193 26.3727C16.8396 26.3727 23.7273 26.3709 30.6149 26.3784C31.412 26.3784 31.9966 26.0382 32.4139 25.3972C32.6771 24.9931 32.8989 24.5608 33.1207 24.1304C33.4986 23.3954 33.8463 22.6436 34.3295 21.9669C34.6321 21.544 34.9818 21.1737 35.416 20.8805C36.2901 20.2884 37.888 20.3091 38.6963 21.6098C39.0422 22.1681 39.2114 22.7827 39.2677 23.4274C39.3805 24.6943 39.1324 25.8953 38.5779 27.0381C38.5252 27.1471 38.4613 27.1885 38.3354 27.1904C37.5421 27.2129 36.7507 27.2092 35.9611 27.1002C34.8408 26.946 33.8445 26.525 33.0061 25.7544C32.976 25.728 32.9459 25.7017 32.9064 25.6679C32.7993 25.8051 32.6978 25.9386 32.5906 26.0701C32.0887 26.6848 31.4402 26.9723 30.6431 26.9705C23.857 26.9667 17.0708 26.9686 10.2828 26.9686H10.0704C10.0854 26.9968 10.0892 27.0156 10.1004 27.0269C11.1362 27.9629 10.9896 29.5418 10.1625 30.4026C9.63236 30.9552 8.97254 31.2785 8.25069 31.4891C7.14912 31.8086 6.0231 31.8612 4.88769 31.7748C4.1132 31.7165 3.3594 31.5586 2.6507 31.224C1.39874 30.6319 0.695689 29.6395 0.513346 28.273C0.408076 27.4892 0.466351 26.713 0.637415 25.9423C0.644934 25.9085 0.658092 25.8765 0.671251 25.8314C0.842315 25.8634 1.00774 25.8953 1.17504 25.9292C1.12993 26.26 1.07917 26.5776 1.04722 26.8972C0.983302 27.5343 1.00022 28.1678 1.19572 28.7862C1.51717 29.8068 2.22774 30.4477 3.20337 30.8218C3.92898 31.1 4.68843 31.1958 5.45916 31.2146C6.42726 31.2372 7.38785 31.1658 8.31273 30.8519C8.87479 30.662 9.39174 30.3951 9.79027 29.9403C10.2189 29.4516 10.3674 28.8839 10.217 28.2505C10.1174 27.8294 9.84666 27.5156 9.49513 27.2938C9.14173 27.072 8.76388 26.8896 8.38604 26.7092C8.11534 26.5795 7.85593 26.4404 7.62471 26.2186L7.63035 26.2205ZM33.2016 25.1341C33.8482 25.7656 34.572 26.1942 35.4235 26.3991C36.26 26.6021 37.1116 26.6209 37.9669 26.6133C38.1286 26.6133 38.1944 26.5532 38.2508 26.416C38.6098 25.5551 38.7846 24.6642 38.7433 23.7319C38.7188 23.1567 38.6136 22.6022 38.3297 22.0891C37.6511 20.8636 36.1454 20.8561 35.3916 21.6324C35.1058 21.9275 34.8201 22.2395 34.6114 22.591C34.117 23.4218 33.6715 24.2807 33.2034 25.1322L33.2016 25.1341Z" fill="#1a1a2a"/>
<path d="M46.9356 23.9574C47.1348 23.527 47.3021 23.1172 47.507 22.7262C47.9826 21.8184 48.5955 21.0233 49.4696 20.4632C50.6275 19.7207 51.8664 19.5835 53.1465 20.0704C54.0883 20.4275 54.6767 21.1643 55.0639 22.0684C55.3515 22.7413 55.4831 23.448 55.4906 24.1773C55.4982 24.8483 55.4906 25.5212 55.4906 26.1923C55.4906 26.2468 55.4906 26.3013 55.4906 26.3652H58.2201V19.8091H58.7653V26.961H42.6853V15.4728H43.2267V26.3633H46.3923V15.4709H46.9337V23.9574H46.9356ZM54.9323 26.3689C54.9399 26.3407 54.9455 26.3276 54.9455 26.3125C54.9455 25.6208 54.9587 24.9273 54.9417 24.2375C54.9323 23.8653 54.8929 23.4912 54.8252 23.1247C54.6842 22.3785 54.3665 21.7131 53.8026 21.1906C52.9642 20.4106 51.9566 20.2621 50.8776 20.4782C49.9301 20.6681 49.197 21.2207 48.5879 21.9462C47.6894 23.0195 47.1931 24.2732 46.9826 25.6472C46.9469 25.884 46.9337 26.1227 46.9093 26.3689H54.9323Z" fill="#1a1a2a"/>
<path d="M48.5372 7.02033C48.9564 6.52975 49.3624 6.05421 49.7684 5.57867C51.0975 4.02423 52.4265 2.46979 53.7593 0.919109C53.8044 0.86648 53.889 0.832647 53.9604 0.823249C54.0902 0.808212 54.2217 0.81949 54.3665 0.81949V12.4787H53.8458V1.71794C53.8345 1.71231 53.8251 1.70667 53.8138 1.70103C53.78 1.73862 53.7424 1.77433 53.7104 1.81192C52.1032 3.69342 50.4978 5.57679 48.8924 7.45828C48.7439 7.6312 48.3661 7.63496 48.2176 7.46204C46.5991 5.57679 44.9805 3.69154 43.362 1.80629C43.3319 1.77245 43.3019 1.73862 43.2436 1.67283V12.4806H42.7097V0.81949C42.8676 0.81949 43.0274 0.813851 43.1872 0.823249C43.2285 0.825129 43.2737 0.86648 43.3037 0.902193C44.5595 2.36641 45.8114 3.83439 47.0653 5.30048C47.5503 5.86813 48.0353 6.43577 48.5372 7.02033Z" fill="#1a1a2a"/>
<path d="M0.531982 0.819349C0.688008 0.819349 0.834634 0.811831 0.97938 0.823109C1.02826 0.826868 1.08841 0.86634 1.12037 0.909571C2.2915 2.48656 3.45887 4.06544 4.62811 5.64431C6.06054 7.58031 7.49296 9.51631 8.92727 11.4504C8.94606 11.4749 8.96486 11.4974 9.00246 11.5463V0.830627H9.54573V12.488C9.3803 12.488 9.21488 12.4955 9.05133 12.4823C9.00998 12.4786 8.96486 12.4203 8.93291 12.379C7.75426 10.7888 6.57749 9.19866 5.39884 7.60663C3.98709 5.69882 2.57535 3.79289 1.1636 1.88509C1.13916 1.85314 1.11473 1.82306 1.06209 1.75728V12.4805H0.533862V0.819349H0.531982Z" fill="#1a1a2a"/>
<path d="M25.5018 1.35891H17.1723V0.828857H26.1879C26.1691 1.08073 26.2311 1.31756 26.0413 1.55815C23.3381 4.97716 20.6481 8.40557 17.9543 11.8321C17.9299 11.8622 17.9073 11.8941 17.8659 11.9543H26.1804V12.4787H17.1648C17.1648 12.3114 17.1591 12.146 17.1685 11.9825C17.1704 11.9392 17.2099 11.8923 17.24 11.8547C19.9544 8.40745 22.6708 4.96024 25.3871 1.51116C25.4172 1.47168 25.4473 1.43221 25.5018 1.35891Z" fill="#1a1a2a"/>
<path d="M39.2489 12.4656C39.2245 12.475 39.2207 12.4768 39.2151 12.4768C38.8817 12.4906 38.6524 12.3453 38.5271 12.0408C38.2113 11.272 37.8917 10.5032 37.5815 9.7326C37.5327 9.6123 37.4725 9.56719 37.339 9.56719C35.2731 9.57095 33.2072 9.57095 31.1394 9.56719C31.0116 9.56719 30.9552 9.6123 30.9082 9.72508C30.5548 10.5935 30.1938 11.4581 29.8423 12.3265C29.7953 12.4449 29.737 12.4919 29.6111 12.4806C29.487 12.4693 29.3611 12.4787 29.2107 12.4787C29.2483 12.3791 29.2784 12.2945 29.3122 12.2137C30.8386 8.4883 32.365 4.7629 33.8858 1.03563C33.9516 0.873988 34.0306 0.8176 34.2035 0.825118C34.5043 0.840155 34.5062 0.828877 34.6152 1.0939C36.1435 4.83621 37.6699 8.57664 39.1963 12.3189C39.2151 12.3641 39.2283 12.4129 39.2471 12.4656H39.2489ZM34.2712 1.60892C34.258 1.60892 34.2449 1.60892 34.2317 1.60704C33.2147 4.08248 32.1977 6.55981 31.1751 9.05406H37.3146C36.2957 6.55793 35.2844 4.08436 34.2731 1.6108L34.2712 1.60892Z" fill="#009C4B"/>
<path d="M13.2942 0.825195H13.7961V12.4788H13.2942V0.825195Z" fill="#1a1a2a"/>
<path d="M58.2727 12.4787V0.830774C58.4175 0.830774 58.5622 0.827015 58.7069 0.834533C58.7314 0.834533 58.7652 0.881523 58.7727 0.911597C58.784 0.952949 58.7784 1.00182 58.7784 1.04693C58.7784 4.7836 58.7784 8.52026 58.7784 12.2569C58.7784 12.5276 58.7746 12.475 58.5603 12.4787C58.4701 12.4787 58.3799 12.4787 58.2746 12.4787H58.2727Z" fill="#009C4B"/>
</g>
<defs>
<clipPath id="clip0"><rect width="58.82" height="33.3707" fill="white" transform="translate(0.180176 0.814697)"/></clipPath>
</defs>
</svg>"""

# Nizami brand palette
_GREEN      = "#009C4B"   # primary green (logo accent)
_GREEN_PALE = "#E8F6ED"   # very light tint for user message background
_GREEN_TINT = "#C0E8D4"   # light green border / dividers
_DARK       = "#232A36"   # primary text
_NAVY       = "#263755"   # secondary text / labels
_GRAY_BG    = "#F6F6F6"   # assistant message background
_PAGE_BG    = "#FFFFFF"

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_CHAT_MESSAGE_TEMPLATE = """
<div class="message message-{role}">
  <div class="message-meta">
    <span class="message-label">{label}</span>
    <span class="message-time">{timestamp}</span>
  </div>
  <div class="message-body">{content}</div>
</div>
"""

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Nizami — Chat Report</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    font-size: 11pt;
    line-height: 1.65;
    color: {dark};
    background: {page_bg};
  }}

  /* ── Page layout ── */
  @page {{
    size: A4;
    margin: 18mm 16mm 20mm 16mm;
    @bottom-center {{
      content: "Page " counter(page) " of " counter(pages);
      font-size: 8.5pt;
      color: #9ca3af;
    }}
  }}

  /* ── Header ── */
  .report-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    border-bottom: 2.5px solid {green};
    padding-bottom: 14px;
    margin-bottom: 28px;
  }}
  .header-right {{
    text-align: right;
  }}
  .report-title {{
    font-size: 18pt;
    font-weight: 700;
    color: {green};
    letter-spacing: -0.3px;
    line-height: 1.2;
  }}
  .report-meta {{
    font-size: 8.5pt;
    color: #6b7280;
    margin-top: 3px;
  }}

  /* ── Section headings ── */
  .section-title {{
    font-size: 12pt;
    font-weight: 700;
    color: {green};
    margin: 26px 0 12px;
    padding-bottom: 5px;
    border-bottom: 1.5px solid {green_tint};
    break-after: avoid;
  }}

  /* ── Messages ── */
  .transcript {{ margin-top: 6px; }}

  .message {{
    margin-bottom: 12px;
    padding: 10px 13px;
    border-radius: 7px;
    break-inside: avoid;
  }}
  .message-user {{
    background: {green_pale};
    border-left: 3.5px solid {green};
    margin-right: 36px;
  }}
  .message-assistant {{
    background: {gray_bg};
    border-left: 3.5px solid {navy};
    margin-left: 36px;
  }}
  .message-meta {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 4px;
  }}
  .message-label {{
    font-weight: 700;
    font-size: 8.5pt;
    text-transform: uppercase;
    letter-spacing: 0.6px;
  }}
  .message-user .message-label {{ color: {green}; }}
  .message-assistant .message-label {{ color: {navy}; }}
  .message-time {{ font-size: 7.5pt; color: #9ca3af; }}
  .message-body {{
    white-space: pre-wrap;
    word-break: break-word;
    font-size: 10.5pt;
  }}
  .message-body pre {{
    background: #1e1e2e;
    color: #cdd6f4;
    padding: 9px 11px;
    border-radius: 5px;
    font-size: 9pt;
    margin: 7px 0;
    overflow-x: auto;
  }}
  .message-body code {{
    font-family: 'Courier New', monospace;
    font-size: 9pt;
    background: #e8eaf0;
    padding: 1px 4px;
    border-radius: 3px;
  }}

  /* ── Summary section ── */
  .summary-block {{
    break-inside: avoid;
    margin-bottom: 16px;
  }}
  .summary-label {{
    font-weight: 700;
    font-size: 9.5pt;
    color: {navy};
    margin-bottom: 4px;
    text-transform: uppercase;
    letter-spacing: 0.4px;
  }}
  .summary-content {{
    color: #374151;
    padding: 8px 12px;
    background: {green_pale};
    border-left: 3px solid {green_tint};
    border-radius: 0 5px 5px 0;
    white-space: pre-wrap;
    font-size: 10.5pt;
  }}
  .next-steps-list {{
    list-style: none;
    padding: 8px 12px;
    background: {green_pale};
    border-left: 3px solid {green_tint};
    border-radius: 0 5px 5px 0;
  }}
  .next-steps-list li {{
    padding: 3px 0 3px 20px;
    position: relative;
    color: #374151;
    font-size: 10.5pt;
  }}
  .next-steps-list li::before {{
    content: "→";
    position: absolute;
    left: 0;
    color: {green};
    font-weight: 700;
  }}

  /* ── Page breaks ── */
  .summary-section {{ page-break-before: always; }}
</style>
</head>
<body>

<!-- ── HEADER ── -->
<div class="report-header">
  <div>{logo}</div>
  <div class="header-right">
    <div class="report-title">Chat Report</div>
    <div class="report-meta">Generated {generated_at} &nbsp;·&nbsp; {message_count} messages</div>
  </div>
</div>

<!-- ── TRANSCRIPT ── -->
<div class="section-title">Chat Transcript</div>
<div class="transcript">
  {messages_html}
</div>

<!-- ── SUMMARY ── -->
<div class="summary-section">
  <div class="section-title">Conversation Summary</div>

  <div class="summary-block">
    <div class="summary-label">Overview</div>
    <div class="summary-content">{overview}</div>
  </div>
  <div class="summary-block">
    <div class="summary-label">Problem</div>
    <div class="summary-content">{problem}</div>
  </div>
  <div class="summary-block">
    <div class="summary-label">Root Cause</div>
    <div class="summary-content">{root_cause}</div>
  </div>
  <div class="summary-block">
    <div class="summary-label">Solution</div>
    <div class="summary-content">{solution}</div>
  </div>
  <div class="summary-block">
    <div class="summary-label">Next Steps</div>
    <ul class="next-steps-list">{next_steps_html}</ul>
  </div>
</div>

</body>
</html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_timestamp(ts: str | None) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    except Exception:
        return ts


def _escape_content(content: str) -> str:
    """Escape HTML while preserving ```code``` fences as <pre><code> blocks."""
    parts = re.split(r"(```[\s\S]*?```)", content)
    result = []
    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            inner = part[3:-3]
            lines = inner.split("\n", 1)
            code = lines[1] if len(lines) > 1 else lines[0]
            result.append(f"<pre><code>{html.escape(code)}</code></pre>")
        else:
            result.append(html.escape(part))
    return "".join(result)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_pdf_html(
    chat: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    generated_at: str | None = None,
    user_name: str | None = None,
) -> str:
    """Return the full branded HTML string. Useful for testing / preview."""
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")

    user_label = user_name.strip() if user_name and user_name.strip() else "User"

    messages_parts: list[str] = []
    for msg in chat:
        role = msg.get("role", "user")
        # Normalise DB role 'ai' → 'assistant' so CSS class .message-assistant applies.
        css_role = "assistant" if role in ("ai", "assistant") else "user"
        label = user_label if role == "user" else "Nizami"
        messages_parts.append(
            _CHAT_MESSAGE_TEMPLATE.format(
                role=html.escape(css_role),
                label=label,
                timestamp=_fmt_timestamp(msg.get("timestamp")),
                content=_escape_content(str(msg.get("content", ""))),
            )
        )

    next_steps: list = summary.get("next_steps") or []
    next_steps_html = "\n".join(
        f"<li>{html.escape(str(s))}</li>" for s in next_steps
    ) or "<li>—</li>"

    return _PAGE_TEMPLATE.format(
        # brand tokens
        green=_GREEN, green_pale=_GREEN_PALE,
        green_tint=_GREEN_TINT, dark=_DARK, navy=_NAVY,
        gray_bg=_GRAY_BG, page_bg=_PAGE_BG,
        # content
        logo=_LOGO_SVG,
        generated_at=html.escape(generated_at),
        message_count=len(chat),
        messages_html="\n".join(messages_parts),
        overview=html.escape(str(summary.get("overview") or "")),
        problem=html.escape(str(summary.get("problem") or "")),
        root_cause=html.escape(str(summary.get("root_cause") or "")),
        solution=html.escape(str(summary.get("solution") or "")),
        next_steps_html=next_steps_html,
    )


def generate_pdf_bytes(
    chat: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    user_name: str | None = None,
) -> bytes:
    """
    Render branded Nizami chat PDF.
    Requires: pip install weasyprint>=62.0
    """
    try:
        from weasyprint import HTML as WeasyHTML  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "WeasyPrint is not installed. Add 'weasyprint>=62.0' to requirements.txt."
        ) from exc

    html_source = render_pdf_html(chat, summary, user_name=user_name)
    pdf_bytes: bytes = WeasyHTML(string=html_source).write_pdf()
    logger.info("Nizami PDF generated: %d bytes, %d messages", len(pdf_bytes), len(chat))
    return pdf_bytes
