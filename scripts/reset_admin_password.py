#!/usr/bin/env python3
"""Safely reset/rotate an administrator credential without CLI password leakage."""

import argparse
import getpass
import os
import secrets
import sys

from dotenv import load_dotenv


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "src", "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from app import create_app  # noqa: E402
from models import db, User  # noqa: E402


def _write_credentials(path, username, password):
    absolute = os.path.abspath(path)
    os.makedirs(os.path.dirname(absolute), mode=0o700, exist_ok=True)
    descriptor = os.open(absolute, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"username={username}\npassword={password}\n")
    os.chmod(absolute, 0o600)
    return absolute


def main():
    parser = argparse.ArgumentParser(description="Rotate an existing administrator credential.")
    parser.add_argument("--username", default="admin", help="Current administrator username.")
    parser.add_argument("--new-username", help="Optional replacement username.")
    parser.add_argument("--generate", action="store_true", help="Generate a strong random password.")
    parser.add_argument("--output-file", help="0600 file used only with --generate.")
    args = parser.parse_args()

    if args.generate:
        if not args.output_file:
            raise SystemExit("--generate requires --output-file so the password is not printed")
        password = secrets.token_urlsafe(32)
    else:
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Confirm password: "):
            raise SystemExit("Passwords do not match")

    app = create_app()
    minimum = int(app.config.get("PASSWORD_MIN_LENGTH", 14))
    if len(password) < minimum:
        raise SystemExit(f"Password must be at least {minimum} characters")

    with app.app_context():
        query = User.query
        if not app.config.get("AUTH_VERSION_ENABLED", True):
            from sqlalchemy.orm import load_only
            query = query.options(load_only(
                User.id, User.username, User.email, User.password_hash, User.display_name,
                User.avatar_url, User.is_active, User.is_verified, User.is_admin, User.timezone,
                User.language, User.created_at, User.updated_at, User.last_login_at,
            ))
        user = query.filter_by(username=args.username).first()
        if not user or not user.is_admin:
            raise SystemExit("Active target administrator was not found")
        if args.new_username:
            new_username = args.new_username.strip()
            if db.session.query(User.id).filter(User.username == new_username, User.id != user.id).first():
                raise SystemExit("Replacement username already exists")
            user.username = new_username
        user.set_password(password)
        user.is_active = True
        user.is_verified = True
        user.revoke_sessions()
        user_id = user.id
        final_username = user.username
        db.session.commit()

        if args.generate:
            path = _write_credentials(args.output_file, final_username, password)
            print(f"Credential rotated for administrator id={user_id}; saved to {path} (mode 0600)")
        else:
            print(f"Credential rotated for administrator id={user_id}")


if __name__ == "__main__":
    main()
