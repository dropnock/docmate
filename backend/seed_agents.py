"""
Seed 16 DE Indexers and 16 DE QA Agents with randomly generated names.

  Email:    firstname.lastname@doc.local
  Password: changeme123  (temporary — user must change on first login)
  Portal:   digitizing
  Org:      DOC (digitizing_entity)

Run after the main seed.py:
    python seed_agents.py
"""
import asyncio
import logging
import random

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.organization import Organization, OrgType
from app.models.tenant import Tenant
from app.models.user import Portal, User, UserRole

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PASSWORD = "changeme123"

# ── Name pools ────────────────────────────────────────────────────────────────

FIRST_NAMES = [
    "Aaron", "Abigail", "Adam", "Alexis", "Alicia", "Amanda", "Andrew",
    "Angela", "Anna", "Anthony", "Ashley", "Benjamin", "Bethany", "Brandon",
    "Brianna", "Caleb", "Camille", "Carlos", "Charlotte", "Chelsea",
    "Christopher", "Claire", "Connor", "Daniel", "Danielle", "David",
    "Diana", "Dominic", "Dylan", "Eleanor", "Elizabeth", "Emily", "Emma",
    "Ethan", "Faith", "Fiona", "Gabriel", "Grace", "Hannah", "Henry",
    "Isabella", "Jacob", "Jade", "James", "Jessica", "Jonathan", "Jordan",
    "Joshua", "Julia", "Karen", "Katherine", "Kevin", "Laura", "Lauren",
    "Liam", "Lisa", "Logan", "Lucas", "Madison", "Marcus", "Maria",
    "Matthew", "Maya", "Megan", "Michael", "Michelle", "Miles", "Monica",
    "Nathan", "Natalie", "Nicholas", "Nicole", "Noah", "Olivia", "Oscar",
    "Patrick", "Paula", "Peter", "Rachel", "Rebecca", "Richard", "Riley",
    "Robert", "Ryan", "Samantha", "Samuel", "Sandra", "Sarah", "Scott",
    "Sean", "Sophia", "Stephanie", "Steven", "Taylor", "Thomas", "Tyler",
    "Victoria", "William", "Zoe",
]

LAST_NAMES = [
    "Adams", "Allen", "Anderson", "Bailey", "Baker", "Barnes", "Bell",
    "Bennett", "Brooks", "Brown", "Butler", "Campbell", "Carter", "Clark",
    "Collins", "Cook", "Cooper", "Cox", "Davis", "Edwards", "Evans",
    "Fisher", "Foster", "Garcia", "Gonzalez", "Gray", "Green", "Griffin",
    "Hall", "Harris", "Harrison", "Hayes", "Henderson", "Hill", "Howard",
    "Hughes", "Jackson", "James", "Jenkins", "Johnson", "Jones", "Kelly",
    "King", "Lee", "Lewis", "Long", "Lopez", "Martin", "Martinez",
    "Miller", "Mitchell", "Moore", "Morgan", "Morris", "Murphy", "Nelson",
    "Parker", "Patterson", "Perez", "Perry", "Peterson", "Phillips",
    "Powell", "Price", "Reed", "Richardson", "Rivera", "Roberts", "Robinson",
    "Rodriguez", "Rogers", "Ross", "Russell", "Sanchez", "Sanders", "Scott",
    "Smith", "Stewart", "Sullivan", "Taylor", "Thomas", "Thompson",
    "Torres", "Turner", "Walker", "Ward", "Watson", "White", "Williams",
    "Wilson", "Wood", "Wright", "Young",
]


def _make_email(first: str, last: str) -> str:
    return f"{first.lower()}.{last.lower()}@doc.local"


def _generate_users(count: int, used_emails: set[str]) -> list[dict]:
    """Return `count` unique {full_name, email} dicts."""
    pool = [
        (f, l)
        for f in FIRST_NAMES
        for l in LAST_NAMES
        if _make_email(f, l) not in used_emails
    ]
    random.shuffle(pool)
    selected = pool[:count]
    if len(selected) < count:
        raise RuntimeError(
            f"Not enough unique name combinations available (need {count}, got {len(selected)})"
        )
    result = []
    for first, last in selected:
        email = _make_email(first, last)
        used_emails.add(email)
        result.append({"full_name": f"{first} {last}", "email": email})
    return result


def _try_keycloak(realm: str, email: str, full_name: str) -> str | None:
    try:
        from app.services.keycloak_service import create_user_in_realm
        sub = create_user_in_realm(realm, email, full_name, PASSWORD)
        logger.info("  Keycloak ✓  %s", email)
        return sub
    except Exception as exc:
        logger.warning("  Keycloak unavailable for %s: %s", email, exc)
        return None


async def seed():
    async with AsyncSessionLocal() as db:
        # ── Resolve tenant and DOC org ────────────────────────────────────────
        tenant = (await db.execute(select(Tenant).limit(1))).scalar_one_or_none()
        if not tenant:
            raise SystemExit("No tenant found — run seed.py first.")

        doc_org = (
            await db.execute(
                select(Organization).where(
                    Organization.tenant_id == tenant.id,
                    Organization.type == OrgType.digitizing_entity,
                )
            )
        ).scalar_one_or_none()
        if not doc_org:
            raise SystemExit("DOC organisation not found — run seed.py first.")

        # ── Collect already-used emails ───────────────────────────────────────
        existing = (await db.execute(select(User.email))).scalars().all()
        used_emails: set[str] = set(existing)

        # ── Generate names ────────────────────────────────────────────────────
        indexer_specs = _generate_users(16, used_emails)
        qa_specs = _generate_users(16, used_emails)

        specs = [
            (s, UserRole.de_indexer) for s in indexer_specs
        ] + [
            (s, UserRole.de_qa_agent) for s in qa_specs
        ]

        # ── Create users ──────────────────────────────────────────────────────
        created = 0
        for spec, role in specs:
            email = spec["email"]
            full_name = spec["full_name"]

            keycloak_sub = _try_keycloak("doc", email, full_name)

            db.add(User(
                tenant_id=tenant.id,
                email=email,
                keycloak_sub=keycloak_sub,
                full_name=full_name,
                role=role,
                portal=Portal.digitizing,
                organization_id=doc_org.id,
                is_active=True,
            ))
            created += 1
            logger.info("  DB ✓  %-40s  %s", email, role.value)

        await db.commit()

    print(f"\nSeeded {created} users into tenant '{tenant.name}':")
    print(f"  16 × de_indexer   — email: firstname.lastname@doc.local")
    print(f"  16 × de_qa_agent  — email: firstname.lastname@doc.local")
    print(f"  Password: {PASSWORD}  (temporary — must change on first login)")


if __name__ == "__main__":
    asyncio.run(seed())
