#!/usr/bin/env python3
"""Generate a 0600 production authentication bundle without printing secrets."""

import argparse
import os
import secrets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--admin-credential-file', required=True)
    parser.add_argument('--output-file', required=True)
    args = parser.parse_args()

    with open(args.admin_credential_file, encoding='utf-8') as handle:
        admin_credentials = handle.read().strip()
    if 'username=' not in admin_credentials or 'password=' not in admin_credentials:
        raise SystemExit('Invalid administrator credential file')

    portal_token = secrets.token_urlsafe(36)
    content = '\n'.join([
        admin_credentials,
        f'LOGIN_PORTAL_TOKEN={portal_token}',
        f'LOGIN_PATH=/access/{portal_token}',
        f'SECRET_KEY={secrets.token_urlsafe(48)}',
        f'JWT_SECRET_KEY={secrets.token_urlsafe(48)}',
        '',
    ])
    output = os.path.abspath(args.output_file)
    os.makedirs(os.path.dirname(output), mode=0o700, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        handle.write(content)
    os.chmod(output, 0o600)
    print(f'Production authentication bundle written to {output} (mode 0600)')


if __name__ == '__main__':
    main()
