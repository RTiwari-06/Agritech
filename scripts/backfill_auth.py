"""Backfill legacy user documents with demo credentials + normalized integer ids.

Seeds from the pre-auth era stored users with ``ObjectId`` ``_id`` and no
``password_hash``.  This script:

  1. Assigns integer ids to any user whose ``_id`` is not an int (rewriting
     references in orders/products/reviews/chat/streams), and
  2. Sets a demo ``password_hash`` (``demo-password-123``) on any user that
     lacks one, so demo accounts are log-in-able via ``/api/auth/login``.

**This script is unsafe for production or shared databases.** It performs a
mass credential reset and should only be run on development databases where
you explicitly want to assign the demo password to all users.

Usage (three modes):

  1. Default (no flags) — dry-run mode: shows what would be changed,
     no modifications.

  2. ``--dry-run`` — same as default, shows output, no modifications.

  3. ``--allow-demo-password-reset --confirm`` — applies the changes
     (rekeys integer IDs and sets ``demo-password-123`` as the password
     for all users lacking a password hash).

Example::

    # Step 1: See what would change
    python scripts/backfill_auth.py

    # Step 2: Apply the changes (requires both flags)
    python scripts/backfill_auth.py --allow-demo-password-reset --confirm

Warning: The demo password ``demo-password-123`` is development-only.
Never use it in production — it creates a master backdoor into all accounts.
"""
import argparse
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import get_db, hash_password
from database.db import session_scope

COLLECTIONS = ["users", "products", "live_streams", "chat_messages", "reviews", "orders"]

WARNING_MSG = (
    "WARNING: This script assigns the demo password 'demo-password-123' "
    "to every user missing a password hash. This is a development-only "
    "operation and should NOT be run on production or shared databases.\n"
    "It may rewrite legacy IDs and overwrite existing credentials, creating "
    "a backdoor.  Always review the output below before applying changes."
)


def _int_id(db, obj):
    from database.db import _next_id
    return _next_id(db)


def ensure_int_ids(db, *, apply: bool = True) -> int:
    users = list(db["users"].find({}))
    rekeyed = 0
    for user in users:
        uid = user.get("_id")
        if isinstance(uid, int):
            continue
        new_id = _int_id(db) if apply else None
        if apply:
            db["users"].update_one({"_id": uid}, {"$set": {"_id": new_id}})
        for coll in ["products", "live_streams", "reviews"]:
            if apply:
                db[coll].update_many({"user_id": uid}, {"$set": {"user_id": new_id}})
                db[coll].update_many({"seller_id": uid}, {"$set": {"seller_id": new_id}})
                db[coll].update_many({"buyer_id": uid}, {"$set": {"buyer_id": new_id}})
        if apply:
            db["orders"].update_many({"buyer_id": uid}, {"$set": {"buyer_id": new_id}})
            db["chat_messages"].update_many({"user_id": uid}, {"$set": {"user_id": new_id}})
        rekeyed += 1
    return rekeyed


def backfill_passwords(db, password: str, *, apply: bool = True) -> int:
    pwhash = hash_password(password)
    updated = 0
    for user in db["users"].find({}):
        if user.get("password_hash"):
            warnings.warn(
                f"Skipping user {user.get('username')} / {user.get('email')}: "
                "already has a password hash."
            )
            continue
        if apply:
            db["users"].update_one(
                {"_id": user["_id"]}, {"$set": {"password_hash": pwhash}}
            )
        updated += 1
    return updated


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--password",
        default="demo-password-123",
        help="Password to hash for users lacking one (default: demo-password-123)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without modifying the database (default)",
    )
    parser.add_argument(
        "--allow-demo-password-reset",
        action="store_true",
        help="Allow resetting passwords to the demo value (requires --confirm)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm the changes shown in dry-run mode",
    )
    args = parser.parse_args(argv)

    db = get_db()
    apply = args.allow_demo_password_reset and args.confirm and not args.dry_run
    rekeyed = ensure_int_ids(db, apply=apply)
    updated = backfill_passwords(db, args.password, apply=apply)

    # Default mode (no flags, or --dry-run): show output, no modifications.
    if not apply:
        print(f"[DRY-RUN] Would rekey user ids: {rekeyed}")
        print(f"[DRY-RUN] Would backfill passwords (skip existing): {updated}")
        print(
            "[DRY-RUN] Login-ready users would be: "
            f"{sum(1 for _ in db['users'].find({'role': {'$in': ['buyer', 'seller']}}))}"
        )
        print(
            "\nRun with --allow-demo-password-reset --confirm "
            "(without --dry-run) to apply changes."
        )
        return 0

    # Both --allow-demo-password-reset and --confirm were passed — apply changes
    print(WARNING_MSG)
    print(f"Rekeyed user ids: {rekeyed}")
    print(f"Password backfill: {updated}")
    print(f"Login-ready users: {sum(1 for _ in db['users'].find({'role': {'$in': ['buyer', 'seller']}}))}")
    print(
        f"Demo login -> identifier: <username> or <email>  password: {args.password}"
    )
    print("\nNote: demo-password-123 is a development-only password.")
    print("For production environments, use individual user accounts with "
          "strong, unique passwords.")


if __name__ == "__main__":
    sys.exit(main())