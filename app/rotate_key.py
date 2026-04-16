"""CLI command to rotate the Fernet encryption key for PII fields.

Usage:
    python -m app.rotate_key --old-key <base64-key> --new-key <base64-key>
"""
import argparse
import asyncio
import sys

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.database import async_session
from app.models.registration import Registration

_PII_FIELDS = ["applicant_id_number", "applicant_phone", "applicant_email"]


async def rotate(old_key: str, new_key: str) -> None:
    try:
        old_fernet = Fernet(old_key.encode())
    except Exception:
        print("Error: Invalid old key format (must be valid Fernet key)", file=sys.stderr)
        sys.exit(1)

    try:
        new_fernet = Fernet(new_key.encode())
    except Exception:
        print("Error: Invalid new key format (must be valid Fernet key)", file=sys.stderr)
        sys.exit(1)

    async with async_session() as session:
        result = await session.execute(select(Registration))
        registrations = result.scalars().all()

        rotated = 0
        skipped = 0
        errors = 0

        for reg in registrations:
            changed = False
            for field in _PII_FIELDS:
                value = getattr(reg, field, None)
                if value is None:
                    continue

                # Try to decrypt with old key
                try:
                    plaintext = old_fernet.decrypt(value.encode()).decode()
                except InvalidToken:
                    # Might be plaintext or already re-encrypted
                    skipped += 1
                    continue

                # Re-encrypt with new key
                new_ciphertext = new_fernet.encrypt(plaintext.encode()).decode()
                setattr(reg, field, new_ciphertext)
                changed = True

            if changed:
                rotated += 1

        await session.commit()
        print(f"Key rotation complete: {rotated} registrations updated, {skipped} fields skipped, {errors} errors")


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotate Fernet encryption key for PII fields")
    parser.add_argument("--old-key", required=True, help="Current Fernet key (base64)")
    parser.add_argument("--new-key", required=True, help="New Fernet key (base64)")
    args = parser.parse_args()

    asyncio.run(rotate(args.old_key, args.new_key))


if __name__ == "__main__":
    main()
