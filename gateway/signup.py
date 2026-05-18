"""
RouteEase — Self-serve signup — POST /api/signup

Flow:
  1. User submits email + at least one LLM provider key
  2. We validate the key works (test call to provider)
  3. We create a tenant in DB
  4. We store their provider key encrypted in the vault
  5. We generate a RouteEase API key
  6. We email the key to them
  7. They change one line of code and start routing
"""

import httpx
from pydantic import BaseModel, EmailStr
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Header
from gateway.config import get_settings

router = APIRouter()
settings = get_settings()


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    email:           EmailStr
    name:            str = ""
    # At least one of these is required
    google_api_key:    Optional[str] = None
    openai_api_key:    Optional[str] = None
    anthropic_api_key: Optional[str] = None


# ---------------------------------------------------------------------------
# Key validation — test call to verify the key actually works
# ---------------------------------------------------------------------------

async def validate_google_key(api_key: str) -> bool:
    """Test a Google Gemini API key with a minimal request."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
                params={"key": api_key},
                json={
                    "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                    "generationConfig": {"maxOutputTokens": 1}
                }
            )
            return resp.status_code in (200, 429)  # 429 = valid key, rate limited
    except Exception:
        return False


async def validate_openai_key(api_key: str) -> bool:
    """Test an OpenAI API key."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"}
            )
            return resp.status_code == 200
    except Exception:
        return False


async def validate_anthropic_key(api_key: str) -> bool:
    """Test an Anthropic API key."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-3-20240307",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}]
                }
            )
            return resp.status_code in (200, 429)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Email sender via Resend
# ---------------------------------------------------------------------------

async def send_welcome_email(
    email: str,
    name: str,
    api_key: str,
    tenant_id: str,
    providers: list[str],
) -> bool:
    """
    Send welcome email via Gmail SMTP or Resend.
    Gmail SMTP: set GMAIL_USER and GMAIL_APP_PASSWORD in .env
    Resend: set RESEND_API_KEY in .env
    Falls back to printing to logs if neither is configured.
    """
    gmail_user     = getattr(settings, 'gmail_user', '')
    gmail_password = getattr(settings, 'gmail_app_password', '')
    resend_key     = getattr(settings, 'resend_api_key', '')

    display_name = name or email.split('@')[0]
    providers_str = ", ".join(p.title() for p in providers)

    # ── Gmail SMTP ────────────────────────────────────────────
    if gmail_user and gmail_password:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart('alternative')
        msg['Subject'] = "Your RouteEase API key is ready 🚀"
        msg['From']    = f"RouteEase <{gmail_user}>"
        msg['To']      = email

        html_content = f"""
<!DOCTYPE html>
<html>
<head>
<style>
  body {{ font-family: -apple-system, sans-serif; background: #0A0A0F; color: #F1F5F9; margin: 0; padding: 40px 20px; }}
  .container {{ max-width: 560px; margin: 0 auto; }}
  .logo {{ font-family: monospace; font-size: 20px; color: #6EE7B7; margin-bottom: 32px; }}
  h1 {{ font-size: 26px; font-weight: 700; margin: 0 0 8px; }}
  p {{ color: #94A3B8; line-height: 1.7; margin: 0 0 16px; font-size: 15px; }}
  .key-box {{ background: #111118; border: 1px solid #2A2A35; border-radius: 10px; padding: 24px; margin: 28px 0; }}
  .key-label {{ font-size: 11px; color: #6EE7B7; font-family: monospace; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 10px; }}
  .key-value {{ font-family: monospace; font-size: 15px; color: #F1F5F9; word-break: break-all; }}
  .code-box {{ background: #111118; border: 1px solid #2A2A35; border-left: 3px solid #6EE7B7; border-radius: 6px; padding: 16px 20px; margin: 20px 0; }}
  pre {{ font-family: monospace; font-size: 13px; color: #94A3B8; margin: 0; white-space: pre-wrap; }}
  .warning {{ background: rgba(245,158,11,0.08); border: 1px solid rgba(245,158,11,0.2); border-radius: 8px; padding: 14px 18px; margin: 20px 0; font-size: 13px; color: #F59E0B; }}
  .btn {{ display: inline-block; background: #6EE7B7; color: #0A0A0F; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 700; font-family: monospace; font-size: 14px; margin: 8px 0; }}
  .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #2A2A35; font-size: 13px; color: #4B5563; }}
</style>
</head>
<body>
<div class="container">
  <div class="logo">⇄ RouteEase</div>
  <h1>You're live on RouteEase, {display_name}.</h1>
  <p>Your router is connected to: <strong style="color:#6EE7B7">{providers_str}</strong></p>

  <div class="key-box">
    <div class="key-label">Your RouteEase API Key</div>
    <div class="key-value">{api_key}</div>
  </div>

  <div class="warning">
    ⚠ This key is shown once and cannot be retrieved. Save it now.
  </div>

  <p>Change one line in your existing code:</p>
  <div class="code-box">
    <pre>client = AsyncOpenAI(
    base_url="https://api.routease.io/v1",
    api_key="{api_key}",
)</pre>
  </div>

  <a href="https://github.com/arpitjainn22/routease/blob/main/SETUP.md" class="btn">Read the docs →</a>

  <div class="footer">
    <p>Tenant ID: {tenant_id}</p>
    <p>Questions? Reply to this email — I read every one.</p>
    <p>— Arpit, founder of RouteEase</p>
  </div>
</div>
</body>
</html>"""

        try:
            msg.attach(MIMEText(html_content, 'html'))
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(gmail_user, gmail_password)
                server.sendmail(gmail_user, email, msg.as_string())
            print(f"Email sent via Gmail to {email}")
            return True
        except Exception as e:
            print(f"Gmail SMTP error: {e}")
            return False

    # ── Resend fallback ───────────────────────────────────────
    if resend_key:
        display_name = name or email.split('@')[0]
        providers_str = ", ".join(p.title() for p in providers)

    html_body = f"""
<!DOCTYPE html>
<html>
<head>
<style>
  body {{ font-family: -apple-system, sans-serif; background: #0A0A0F; color: #F1F5F9; margin: 0; padding: 40px 20px; }}
  .container {{ max-width: 560px; margin: 0 auto; }}
  .logo {{ font-family: monospace; font-size: 20px; color: #6EE7B7; margin-bottom: 32px; }}
  h1 {{ font-size: 28px; font-weight: 700; margin: 0 0 8px; }}
  p {{ color: #94A3B8; line-height: 1.7; margin: 0 0 16px; font-size: 15px; }}
  .key-box {{ background: #111118; border: 1px solid #2A2A35; border-radius: 10px; padding: 24px; margin: 28px 0; }}
  .key-label {{ font-size: 11px; color: #6EE7B7; font-family: monospace; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 10px; }}
  .key-value {{ font-family: monospace; font-size: 16px; color: #F1F5F9; word-break: break-all; }}
  .provider-badge {{ display: inline-block; background: rgba(110,231,183,0.1); border: 1px solid rgba(110,231,183,0.2); color: #6EE7B7; font-family: monospace; font-size: 12px; padding: 3px 10px; border-radius: 100px; margin: 2px; }}
  .code-box {{ background: #111118; border: 1px solid #2A2A35; border-left: 3px solid #6EE7B7; border-radius: 6px; padding: 16px 20px; margin: 20px 0; }}
  pre {{ font-family: monospace; font-size: 13px; color: #94A3B8; margin: 0; white-space: pre-wrap; }}
  .hl {{ color: #6EE7B7; }}
  .warning {{ background: rgba(245,158,11,0.08); border: 1px solid rgba(245,158,11,0.2); border-radius: 8px; padding: 14px 18px; margin: 20px 0; font-size: 13px; color: #F59E0B; }}
  .btn {{ display: inline-block; background: #6EE7B7; color: #0A0A0F; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 700; font-family: monospace; font-size: 14px; margin: 8px 0; }}
  .footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #2A2A35; font-size: 13px; color: #4B5563; }}
</style>
</head>
<body>
<div class="container">
  <div class="logo">⇄ RouteEase</div>
  <h1>You're live on RouteEase, {display_name}.</h1>
  <p>Your router is connected to: {' '.join(f'<span class="provider-badge">{p}</span>' for p in providers)}</p>

  <div class="key-box">
    <div class="key-label">Your RouteEase API Key</div>
    <div class="key-value">{api_key}</div>
  </div>

  <div class="warning">
    ⚠ This key is shown once and cannot be retrieved. Save it now.
  </div>

  <p>Change one line in your existing code:</p>
  <div class="code-box">
    <pre>client = AsyncOpenAI(
<span class="hl">    base_url="https://api.routease.io/v1",</span>
    api_key="{api_key}",
)</pre>
  </div>

  <p>Your LLM provider keys are stored encrypted. The router uses them on your behalf — you never expose them to your end users.</p>

  <a href="https://github.com/arpitjainn22/routease/blob/main/SETUP.md" class="btn">Read the docs →</a>

  <div class="footer">
    <p>Tenant ID: {tenant_id}</p>
    <p>Questions? Reply to this email — I read every one.</p>
    <p>— Arpit, founder of RouteEase</p>
  </div>
</div>
</body>
</html>
"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                json={"from": "RouteEase <arpit@routease.io>", "to": [email],
                      "subject": "Your RouteEase API key is ready 🚀", "html": html_body},
                timeout=10.0,
            )
            return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Signup endpoint
# ---------------------------------------------------------------------------

@router.post("/api/signup")
async def signup(body: SignupRequest):
    """
    Self-serve signup.
    Requires at least one valid LLM provider key.
    Validates the key before creating the account.
    """
    from gateway.main import auth_service, key_vault

    if auth_service is None:
        raise HTTPException(status_code=503, detail="Service starting up — try again in a moment")

    # ── 1. Check at least one key provided ─────────────────────────────
    provided = {}
    if body.google_api_key    and len(body.google_api_key) > 10:
        provided["google"]    = body.google_api_key
    if body.openai_api_key    and len(body.openai_api_key) > 10:
        provided["openai"]    = body.openai_api_key
    if body.anthropic_api_key and len(body.anthropic_api_key) > 10:
        provided["anthropic"] = body.anthropic_api_key

    if not provided:
        raise HTTPException(
            status_code=400,
            detail="At least one LLM provider API key is required. Add your Google, OpenAI, or Anthropic key."
        )

    # ── 2. Validate each provided key ──────────────────────────────────
    validators = {
        "google":    validate_google_key,
        "openai":    validate_openai_key,
        "anthropic": validate_anthropic_key,
    }
    validated = {}
    invalid   = []

    for provider, key in provided.items():
        is_valid = await validators[provider](key)
        if is_valid:
            validated[provider] = key
        else:
            invalid.append(provider)

    if invalid and not validated:
        raise HTTPException(
            status_code=400,
            detail=f"API key validation failed for: {', '.join(invalid)}. Please check your keys and try again."
        )

    # ── 3. Create tenant ────────────────────────────────────────────────
    try:
        name = body.name or body.email.split('@')[0].replace('.', ' ').title()
        tenant = await auth_service.create_tenant(
            name=name,
            email=body.email,
            plan="free",
            budget_usd=0.01,
            provider_preference=list(validated.keys())[0],
        )
    except Exception as e:
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail="This email already has an account. Check your inbox for your API key."
            )
        raise HTTPException(status_code=500, detail="Failed to create account. Please try again.")

    tenant_id = str(tenant.id)

    # ── 4. Store provider keys encrypted ───────────────────────────────
    for provider, key in validated.items():
        await key_vault.store_key(
            tenant_id=tenant_id,
            provider=provider,
            raw_api_key=key,
        )

    # ── 5. Generate router API key ──────────────────────────────────────
    key_result = await auth_service.create_api_key(
        tenant_id=tenant_id,
        name="default",
        environment="live",
    )

    # ── 6. Send welcome email ───────────────────────────────────────────
    await send_welcome_email(
        email=body.email,
        name=name,
        api_key=key_result["raw_key"],
        tenant_id=tenant_id,
        providers=list(validated.keys()),
    )

    # ── 7. Warn about any invalid keys ─────────────────────────────────
    warning = None
    if invalid:
        warning = f"Note: key validation failed for {', '.join(invalid)} — those providers were skipped."

    return {
        "success":    True,
        "message":    "Account created. Check your email for your router API key.",
        "providers":  list(validated.keys()),
        "warning":    warning,
    }


# ---------------------------------------------------------------------------
# Key management endpoints (authenticated)
# ---------------------------------------------------------------------------

def get_authenticate():
    from gateway.main import authenticate
    return authenticate


@router.post("/v1/keys")
async def add_provider_key(
    provider: str,
    api_key:  str,
    authorization: str = Header(None),
):
    """Add or update a provider key for an existing tenant."""
    from gateway.main import key_vault, auth_service

    if auth_service is None:
        raise HTTPException(status_code=503, detail="Service starting up")

    raw_key = authorization.split(" ", 1)[1] if authorization and " " in authorization else ""
    tenant = await auth_service.authenticate(raw_key)

    if provider not in ("google", "openai", "anthropic"):
        raise HTTPException(status_code=400, detail="Provider must be google, openai, or anthropic")

    validators = {
        "google":    validate_google_key,
        "openai":    validate_openai_key,
        "anthropic": validate_anthropic_key,
    }
    is_valid = await validators[provider](api_key)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Invalid {provider} API key")

    await key_vault.store_key(
        tenant_id=str(tenant["tenant_id"]),
        provider=provider,
        raw_api_key=api_key,
    )
    return {"success": True, "provider": provider}


@router.get("/v1/keys")
async def list_provider_keys(authorization: str = Header(None)):
    """List stored provider keys (never returns decrypted key)."""
    from gateway.main import key_vault, auth_service

    if auth_service is None:
        raise HTTPException(status_code=503, detail="Service starting up")

    raw_key = authorization.split(" ", 1)[1] if authorization and " " in authorization else ""
    tenant = await auth_service.authenticate(raw_key)
    keys = await key_vault.list_keys(str(tenant["tenant_id"]))
    return {"keys": keys}


@router.delete("/v1/keys/{provider}")
async def delete_provider_key(provider: str, authorization: str = Header(None)):
    """Remove a stored provider key."""
    from gateway.main import key_vault, auth_service

    if auth_service is None:
        raise HTTPException(status_code=503, detail="Service starting up")

    raw_key = authorization.split(" ", 1)[1] if authorization and " " in authorization else ""
    tenant = await auth_service.authenticate(raw_key)
    success = await key_vault.delete_key(str(tenant["tenant_id"]), provider)
    if not success:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"success": True, "provider": provider}
