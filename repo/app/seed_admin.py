"""
CLI command to seed the first system administrator.

Usage:
    python -m app.seed_admin --username admin --password 'YourP@ssw0rd!!'
"""
import argparse
import asyncio
import sys

from sqlalchemy import select

from app.auth.password import hash_password
from app.database import async_session, engine, Base
from app.models.user import User, UserRole


async def seed(username: str, password: str) -> None:
    # Ensure tables exist (for first-run without Alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        result = await session.execute(select(User).where(User.username == username))
        if result.scalar_one_or_none():
            print(f"User '{username}' already exists — skipping.")
            return

        user = User(
            username=username,
            password_hash=hash_password(password),
            role=UserRole.SYSTEM_ADMIN,
        )
        session.add(user)
        await session.commit()
        print(f"System admin '{username}' created successfully.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the initial system admin")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    # Basic password complexity check
    pwd = args.password
    errors = []
    if len(pwd) < 12:
        errors.append("at least 12 characters")
    if not any(c.isupper() for c in pwd):
        errors.append("one uppercase letter")
    if not any(c.islower() for c in pwd):
        errors.append("one lowercase letter")
    if not any(c.isdigit() for c in pwd):
        errors.append("one digit")
    if not any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?`~" for c in pwd):
        errors.append("one special character")
    if errors:
        print(f"Error: Password must contain: {', '.join(errors)}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(seed(args.username, args.password))


if __name__ == "__main__":
    main()
