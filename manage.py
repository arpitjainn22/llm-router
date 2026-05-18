#!/usr/bin/env python3
"""
LLM Router — Management CLI

Commands:
  quickstart    Create tenant + API key in one step
  create-tenant Create a new tenant
  create-key    Generate an API key for a tenant
  list-tenants  List all tenants
  list-keys     List keys for a tenant
  revoke-key    Revoke an API key
"""

import argparse
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def cmd_quickstart(args):
    from logger.models import get_engine, get_session_factory, Base
    from gateway.auth import AuthService
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = get_session_factory(engine)
    svc = AuthService(sf)
    tenant = await svc.create_tenant(
        name=args.name, email=args.email,
        plan=getattr(args, 'plan', 'free'), budget_usd=0.01,
    )
    result = await svc.create_api_key(str(tenant.id), "default", "live")
    print(f"\n{'='*58}")
    print(f"  ✓  TENANT + API KEY CREATED")
    print(f"{'='*58}")
    print(f"  Tenant ID : {tenant.id}")
    print(f"  Name      : {tenant.name}")
    print(f"  Key       : {result['raw_key']}")
    print(f"{'='*58}")
    print(f"  ⚠  Save this key — shown ONCE, cannot be retrieved.")
    print(f"{'='*58}\n")
    await engine.dispose()


async def cmd_create_tenant(args):
    from logger.models import get_engine, get_session_factory, Base
    from gateway.auth import AuthService
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = get_session_factory(engine)
    svc = AuthService(sf)
    tenant = await svc.create_tenant(
        name=args.name, email=args.email,
        plan=getattr(args, 'plan', 'free'), budget_usd=0.01,
    )
    print(f"\n✓ Tenant created: {tenant.id}")
    print(f"  Run: python manage.py create-key --tenant-id {tenant.id}\n")
    await engine.dispose()


async def cmd_create_key(args):
    from logger.models import get_engine, get_session_factory
    from gateway.auth import AuthService
    engine = get_engine()
    sf = get_session_factory(engine)
    svc = AuthService(sf)
    result = await svc.create_api_key(
        args.tenant_id,
        getattr(args, 'name', 'default'),
        getattr(args, 'env', 'live'),
    )
    print(f"\n{'='*58}")
    print(f"  ✓  API KEY CREATED — SAVE THIS NOW")
    print(f"{'='*58}")
    print(f"  Key    : {result['raw_key']}")
    print(f"  Prefix : {result['prefix']}")
    print(f"  Tenant : {result['tenant_id']}")
    print(f"{'='*58}")
    print(f"  ⚠  Shown ONCE — store it now.\n")
    await engine.dispose()


async def cmd_list_tenants(args):
    from logger.models import get_engine, get_session_factory
    from gateway.auth import AuthService
    engine = get_engine()
    sf = get_session_factory(engine)
    svc = AuthService(sf)
    tenants = await svc.list_tenants()
    if not tenants:
        print("\n  No tenants yet.\n")
    else:
        print(f"\n  {'ID':<38} {'NAME':<20} {'EMAIL':<28} PLAN")
        print("  " + "─"*94)
        for t in tenants:
            print(f"  {str(t.id):<38} {t.name:<20} {t.email:<28} {t.plan}")
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
        print(f"\n  No keys for tenant {args.tenant_id}\n")
    else:
        print(f"\n  {'KEY ID':<38} {'PREFIX':<16} {'NAME':<14} ACTIVE  REQUESTS")
        print("  " + "─"*86)
        for k in keys:
            active = "✓" if k['is_active'] else "✗"
            print(f"  {k['key_id']:<38} {k['prefix']:<16} {k['name']:<14} {active:<8} {k['request_count']}")
        print()
    await engine.dispose()


async def cmd_revoke_key(args):
    from logger.models import get_engine, get_session_factory
    from gateway.auth import AuthService
    engine = get_engine()
    sf = get_session_factory(engine)
    svc = AuthService(sf)
    ok = await svc.revoke_key(args.key_id, args.tenant_id)
    print(f"\n  {'✓ Revoked' if ok else '✗ Not found'}: {args.key_id}\n")
    await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="LLM Router CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("quickstart")
    p.add_argument("--name", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--plan", default="free")

    p = sub.add_parser("create-tenant")
    p.add_argument("--name", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--plan", default="free")

    p = sub.add_parser("create-key")
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--name", default="default")
    p.add_argument("--env", default="live")

    sub.add_parser("list-tenants")

    p = sub.add_parser("list-keys")
    p.add_argument("--tenant-id", required=True)

    p = sub.add_parser("revoke-key")
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--key-id", required=True)

    args = parser.parse_args()
    dispatch = {
        "quickstart":    cmd_quickstart,
        "create-tenant": cmd_create_tenant,
        "create-key":    cmd_create_key,
        "list-tenants":  cmd_list_tenants,
        "list-keys":     cmd_list_keys,
        "revoke-key":    cmd_revoke_key,
    }
    asyncio.run(dispatch[args.command](args))


if __name__ == "__main__":
    main()
