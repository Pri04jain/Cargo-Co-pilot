#!/usr/bin/env python3
"""
Seed script: creates two demo tenants, users, and purchase orders.
Run with: make seed  (inside the api container)

This gives you a working demo without needing real documents:
- Tenant A: "Apex Freight" — admin + reviewer users
- Tenant B: "Blue Ocean Logistics" — admin user
- 5 purchase orders per tenant for cross-document validation testing

Idempotent: running it twice won't create duplicates (checks before inserting).
"""

import sys
import uuid
from datetime import date
from pathlib import Path

# Make sure app is importable when run from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.session import sync_engine
from app.core.security import hash_password
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.models.purchase_order import PurchaseOrder
from app.models.audit import AuditLog


def get_or_create_tenant(db: Session, name: str, slug: str) -> Tenant:
    result = db.execute(select(Tenant).where(Tenant.slug == slug))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(id=uuid.uuid4(), name=name, slug=slug)
        db.add(tenant)
        db.flush()
        print(f"  ✓ Created tenant: {name}")
    else:
        print(f"  · Tenant exists: {name}")
    return tenant


def get_or_create_user(
    db: Session,
    tenant_id: uuid.UUID,
    email: str,
    password: str,
    full_name: str,
    role: UserRole,
) -> User:
    result = db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            role=role,
        )
        db.add(user)
        db.flush()
        print(f"  ✓ Created user: {email} ({role})")
    else:
        print(f"  · User exists: {email}")
    return user


def seed_purchase_orders(db: Session, tenant_id: uuid.UUID, tenant_name: str) -> None:
    """Seed 5 sample POs per tenant for validation testing."""
    pos = [
        {
            "po_number": f"PO-{tenant_name[:3].upper()}-001",
            "supplier_name": "Shenzhen Electronics Co. Ltd",
            "total_amount": 15_250.00,
            "currency": "USD",
            "quantity": 500.0,
            "quantity_unit": "units",
            "issue_date": date(2024, 1, 15),
            "expected_delivery": date(2024, 3, 1),
        },
        {
            "po_number": f"PO-{tenant_name[:3].upper()}-002",
            "supplier_name": "Shanghai Textile Exports",
            "total_amount": 8_400.00,
            "currency": "USD",
            "quantity": 1200.0,
            "quantity_unit": "kg",
            "issue_date": date(2024, 2, 1),
            "expected_delivery": date(2024, 4, 15),
        },
        {
            "po_number": f"PO-{tenant_name[:3].upper()}-003",
            "supplier_name": "Vietnam Manufacturing Corp",
            "total_amount": 22_100.00,
            "currency": "EUR",
            "quantity": 300.0,
            "quantity_unit": "cartons",
            "issue_date": date(2024, 2, 20),
            "expected_delivery": date(2024, 5, 1),
        },
        {
            "po_number": f"PO-{tenant_name[:3].upper()}-004",
            "supplier_name": "Korea Parts & Components",
            "total_amount": 45_000.00,
            "currency": "USD",
            "quantity": 50.0,
            "quantity_unit": "pallets",
            "issue_date": date(2024, 3, 5),
            "expected_delivery": date(2024, 5, 20),
        },
        {
            "po_number": f"PO-{tenant_name[:3].upper()}-005",
            "supplier_name": "Indonesia Raw Materials",
            "total_amount": 6_750.00,
            "currency": "USD",
            "quantity": 2500.0,
            "quantity_unit": "kg",
            "issue_date": date(2024, 3, 10),
            "expected_delivery": date(2024, 6, 1),
        },
    ]

    for po_data in pos:
        result = db.execute(
            select(PurchaseOrder).where(
                PurchaseOrder.tenant_id == tenant_id,
                PurchaseOrder.po_number == po_data["po_number"],
            )
        )
        if result.scalar_one_or_none() is None:
            po = PurchaseOrder(id=uuid.uuid4(), tenant_id=tenant_id, **po_data)
            db.add(po)
            print(f"  ✓ Created PO: {po_data['po_number']} ({po_data['currency']} {po_data['total_amount']:,.2f})")
        else:
            print(f"  · PO exists: {po_data['po_number']}")


def main() -> None:
    print("\n🚢 Seeding Cargo Copilot demo data...\n")

    with Session(sync_engine) as db:
        # ── Tenant A: Apex Freight ─────────────────────────────────────────
        print("Tenant A: Apex Freight")
        tenant_a = get_or_create_tenant(db, "Apex Freight", "apex-freight")

        get_or_create_user(
            db, tenant_a.id,
            email="admin@apexfreight.com",
            password="demo_password_1",
            full_name="Alex Chen",
            role=UserRole.ADMIN,
        )
        get_or_create_user(
            db, tenant_a.id,
            email="reviewer@apexfreight.com",
            password="demo_password_1",
            full_name="Morgan Lee",
            role=UserRole.REVIEWER,
        )

        seed_purchase_orders(db, tenant_a.id, "Apex Freight")

        # ── Tenant B: Blue Ocean Logistics ────────────────────────────────
        print("\nTenant B: Blue Ocean Logistics")
        tenant_b = get_or_create_tenant(db, "Blue Ocean Logistics", "blue-ocean")

        get_or_create_user(
            db, tenant_b.id,
            email="admin@blueocean.com",
            password="demo_password_2",
            full_name="Jordan Kim",
            role=UserRole.ADMIN,
        )
        get_or_create_user(
            db, tenant_b.id,
            email="reviewer@blueocean.com",
            password="demo_password_2",
            full_name="Sam Rivera",
            role=UserRole.REVIEWER,
        )

        seed_purchase_orders(db, tenant_b.id, "Blue Ocean")

        # ── Audit log entry for seed ──────────────────────────────────────
        for tenant in [tenant_a, tenant_b]:
            db.add(AuditLog(
                tenant_id=tenant.id,
                actor="system",
                event_type="tenant_seeded",
                description="Demo data seeded via scripts/demo_data.py",
            ))

        db.commit()

    print("\n✅ Demo data ready!\n")
    print("  Tenant A — Apex Freight:")
    print("    admin@apexfreight.com / demo_password_1")
    print("    reviewer@apexfreight.com / demo_password_1")
    print("\n  Tenant B — Blue Ocean Logistics:")
    print("    admin@blueocean.com / demo_password_2")
    print("    reviewer@blueocean.com / demo_password_2\n")


if __name__ == "__main__":
    main()
