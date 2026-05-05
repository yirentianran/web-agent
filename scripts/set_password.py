#!/usr/bin/env python
"""Set passwords for existing users. Usage: python scripts/set_password.py <user_id> <password>"""

import os
import sys

from dotenv import load_dotenv

load_dotenv(override=True)

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.auth import hash_password  # noqa: E402
from src.database import Database  # noqa: E402


async def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/set_password.py <user_id> <password>")
        print("       python scripts/set_password.py --all <password>  (all users)")
        sys.exit(1)

    from pathlib import Path

    db_path = Path(os.getenv("DATA_DB_PATH", "./data/web-agent.db"))
    if not db_path.is_absolute():
        db_path = Path(__file__).parent.parent / db_path
    db = Database(db_path=db_path)
    await db.init()

    async with db.connection() as conn:
        if sys.argv[1] == "--all":
            password_hash = hash_password(sys.argv[2])
            cursor = await conn.execute(
                "UPDATE users SET password_hash = ? WHERE password_hash = '' OR password_hash IS NULL",
                (password_hash,),
            )
            await conn.commit()
            print(f"Password set for {cursor.rowcount} user(s)")
        else:
            user_id = sys.argv[1]
            password_hash = hash_password(sys.argv[2])
            cursor = await conn.execute(
                "UPDATE users SET password_hash = ? WHERE user_id = ?",
                (password_hash, user_id),
            )
            await conn.commit()
            if cursor.rowcount == 0:
                print(f"User '{user_id}' not found")
            else:
                print(f"Password set for user '{user_id}'")

    await db.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
