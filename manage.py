#!/usr/bin/env python3
"""
LLM Router — API key management CLI

Usage (run from project root):

  # Create a new tenant
  python manage.py create-tenant --name "Acme Corp" --email "dev@acme.com"

  # Generate an API key for that tenant
  python manage.py create-key --tenant-id <id>

  # List all tenants
  python manage.py list-tenants

  # List all keys for a tenant
  python manage.py list-keys --tenant-id <id>

  # Revoke a key
  python manage.py revoke-key --tenant-id <id> --key-id <id>

  # Quick setup: create tenant + key in one command
  python manage.py quickstart --name "My App" --email "me@myapp.com"
"""

import argparse
import asyncio
import hashlib
import secrets
import sys
import os
import uuid
from datetime import datetime, timezone

# ── Minimal key generation (no DB needed for simple generation) ─────────────

def generate_key(environment: str = "live") -> str:
    return f"rk-{environment}-{secrets.token_hex(16)}"

def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()

def prefix(raw_key: str) -> str:
    return raw_key[:12] + "..."


# ── DB-backed commands (require running Postgres) ───────────────────────────

async def cmd_create_tenant(args):
    from logger.models import get_engine, get_session_factory, Base
    from gateway.auth import AuthService

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = get_session_factory(engine)
    svc = AuthService(sf)

    tenant = await svc.create_tenant(
        name=args.name,
        email=args.email,
        company=getattr(args, 'company', None),
        budget_usd=getattr(args, 'budget', 0.01),
        plan=getattr(args, 'plan', 'free'),
    )
    print("\n✓  Tenant created")
    print(f"   Tenant ID : {tenant.id}")
    print(f"   Name      : {tenant.name}")
    print(f"   Email     : {tenant.email}")
    print(f"   Plan      : {tenant.plan}")
    print(f"\n→  Next: python manage.py create-key --tenant-id {tenant.id}")
    await engine.dispose()
    return str(tenant.id)


async def cmd_create_key(args):
    from logger.models import get_engine, get_session_factory, Base
    from gateway.auth import AuthService

    engine = get_engine()
    sf = get_session_factory(engine)
    svc = AuthService(sf)

    result = await svc.create_api_key(
        tenant_id=args.tenant_id,
        name=getattr(args, 'name', 'default'),
        environment=getattr(args, 'env', 'live'),
    )

    print("\n" + "═" * 58)
    print("  ✓  API KEY CREATED — SAVE THIS NOW")
    print("═" * 58)
    print(f"  Key      :  {result['raw_key']}")
    print(f"  Prefix   :  {result['prefix']}")
    print(f"  Key ID   :  {result['key_id']}")
    print(f"  Tenant   :  {result['tenant_id']}")
    print(f"  Env      :  {result['environment']}")
    print("═" * 58)
    print("  ⚠  This key is shown ONCE and cannot be retrieved again.")
    print("     Store it in your .env or password manager now.")
    print("═" * 58)
    print(f"\n  Usage:")
    print(f"    curl http://localhost:8000/v1/chat/completions \\")
    print(f"      -H 'Authorization: Bearer {result['raw_key']}' \\")
    print(f"      -H 'Content-Type: application/json' \\")
    print(f"      -d '{{\"model\": \"auto\", \"messages\": [{{\"role\": \"user\", \"content\": \"Hello\"}}]}}'")
    print()
    await engine.dispose()


async def cmd_list_tenants(args):
    from logger.models import get_engine, get_session_factory
    from gateway.auth import AuthService

    engine = get_engine()
    sf = get_session_factory(engine)
    svc = AuthService(sf)
    tenants = await svc.list_tenants()

    if not tenants:
        print("\n  No tenants yet. Run: python manage.py create-tenant")
        await engine.dispose()
        return

    print(f"\n  {'TENANT ID':<38} {'NAME':<20} {'EMAIL':<30} PLAN")
    print("  " + "─" * 96)
    for t in tenants:
        print(f"  {str(t.id):<38} {t.name:<20} {t.email:<30} {t.plan}")
    print()
    await engine.dispose()


async def cmd_list_keys(args):
    from logger.models import get_engine, get_session_factory
    from gateway.auth import AuthService

    engine = get_engine()
    sf = get_session_factory(engine)
    svc = AuthService(sf)
    keys = await svc.list_keys(args.tenant_id)

    if not keys:
        print(f"\n  No keys for tenant {args.tenant_id}")
        print(f"  Run: python manage.py create-key --tenant-id {args.tenant_id}")
        await engine.dispose()
        return

    print(f"\n  Keys for tenant {args.tenant_id}:")
    print(f"  {'KEY ID':<38} {'PREFIX':<16} {'NAME':<16} {'ENV':<6} {'ACTIVE':<8} REQUESTS")
    print("  " + "─" * 100)
    for k in keys:
        active = "✓" if k['is_active'] else "✗ revoked"
        print(f"  {k['key_id']:<38} {k['prefix']:<16} {k['name']:<16} {k['environment']:<6} {active:<8} {k['request_count']}")
    print()
    await engine.dispose()


async def cmd_revoke_key(args):
    from logger.models import get_engine, get_session_factory
    from gateway.auth import AuthService

    engine = get_engine()
    sf = get_session_factory(engine)
    svc = AuthService(sf)
    success = await svc.revoke_key(args.key_id, args.tenant_id)
    if success:
        print(f"\n  ✓  Key {args.key_id} revoked. It will no longer authenticate.")
    else:
        print(f"\n  ✗  Key not found.")
    print()
    await engine.dispose()


async def cmd_quickstart(args):
    """Create a tenant + key in one step — perfect for getting started."""
    print(f"\n  Setting up tenant for {args.name}...")
    tenant_id = await cmd_create_tenant(args)
    args.tenant_id = tenant_id
    args.name = "default"
    args.env = "live"
    await cmd_create_key(args)


# ── Offline key generation (no DB needed) ───────────────────────────────────

def cmd_generate_offline(args):
    """
    Generate a key without a database connection.
    Useful for testing — but this key won't authenticate against the
    real gateway since it's not stored in the DB.
    """
    env = getattr(args, 'env', 'test')
    key = generate_key(env)
    print(f"\n  Generated key (OFFLINE — for testing only):")
    print(f"  {key}")
    print(f"\n  To use for real, run: python manage.py quickstart")
    print()


# ── CLI setup ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="LLM Router — API key management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # quickstart
    p = sub.add_parser("quickstart", help="Create tenant + key in one step")
    p.add_argument("--name",    required=True, help="Tenant name")
    p.add_argument("--email",   required=True, help="Tenant email")
    p.add_argument("--company", help="Company name")
    p.add_argument("--plan",    default="free", choices=["free","starter","pro","enterprise"])

    # create-tenant
    p = sub.add_parser("create-tenant", help="Create a new tenant")
    p.add_argument("--name",    required=True)
    p.add_argument("--email",   required=True)
    p.add_argument("--company")
    p.add_argument("--budget",  type=float, default=0.01, help="Per-request cost budget in USD")
    p.add_argument("--plan",    default="free")

    # create-key
    p = sub.add_parser("create-key", help="Generate an API key for a tenant")
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--name",      default="default", help="Key label (e.g. 'production')")
    p.add_argument("--env",       default="live", choices=["live","test"])

    # list-tenants
    sub.add_parser("list-tenants", help="List all tenants")

    # list-keys
    p = sub.add_parser("list-keys", help="List keys for a tenant")
    p.add_argument("--tenant-id", required=True)

    # revoke-key
    p = sub.add_parser("revoke-key", help="Revoke an API key")
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--key-id",    required=True)

    # generate (offline)
    p = sub.add_parser("generate", help="Generate a key offline (testing only)")
    p.add_argument("--env", default="test", choices=["live","test"])

    args = parser.parse_args()

    # Add project root to path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    if args.command == "generate":
        cmd_generate_offline(args)
        return

    # All other commands need asyncio + DB
    dispatch = {
        "quickstart":     cmd_quickstart,
        "create-tenant":  cmd_create_tenant,
        "create-key":     cmd_create_key,
        "list-tenants":   cmd_list_tenants,
        "list-keys":      cmd_list_keys,
        "revoke-key":     cmd_revoke_key,
    }
    asyncio.run(dispatch[args.command](args))


if __name__ == "__main__":
    main()
