"""Create a local-only .env with bootstrap credentials and a biometric encryption key."""
from getpass import getpass
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / '.env'


def configure():
    if ENV_FILE.exists():
        print(f'{ENV_FILE.name} already exists; leaving it unchanged.')
        return
    while True:
        password = getpass('Create the initial admin password (12+ characters): ')
        if len(password) < 12:
            print('Use at least 12 characters.')
            continue
        confirm = getpass('Confirm admin password: ')
        if password != confirm:
            print('Passwords did not match.')
            continue
        break
    escaped = password.replace('\\', '\\\\').replace('"', '\\"')
    content = '\n'.join([
        f'FACETRACK_ADMIN_PASSWORD="{escaped}"',
        f'FACETRACK_BIOMETRIC_KEY={Fernet.generate_key().decode("ascii")}',
        'FACETRACK_TIMEZONE=UTC',
        'COOKIE_SECURE=0',
        'FACETRACK_MATCH_THRESHOLD=0.363',
        '',
    ])
    ENV_FILE.write_text(content, encoding='utf-8')
    print(f'Created {ENV_FILE.name}. Keep it private and back up the biometric key securely.')


if __name__ == '__main__':
    configure()
