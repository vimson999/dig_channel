#!/usr/bin/env python3
"""Reset a user's password in the configured project database.

Usage:
    python scripts/reset_admin_password.py admin123
    python scripts/reset_admin_password.py admin123 --username admin
"""

import argparse
import os
import sys

from dotenv import load_dotenv


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "src", "backend")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from app import create_app  # noqa: E402
from models import db, User  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Reset a user's password.")
    parser.add_argument("password", help="New password, at least 6 characters.")
    parser.add_argument("--username", default="admin", help="Target username. Defaults to admin.")
    args = parser.parse_args()

    if len(args.password) < 6:
        raise SystemExit("Password must be at least 6 characters.")

    app = create_app()
    with app.app_context():
        user = User.query.filter_by(username=args.username).first()
        if not user:
            raise SystemExit(f"User not found: {args.username}")

        user.set_password(args.password)
        user.is_active = True
        db.session.commit()
        print(f"Password reset complete for user: {user.username} (id={user.id})")


if __name__ == "__main__":
    main()
