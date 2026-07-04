#!/usr/bin/env python3
"""
Reset a user's database in the cTrader auth system.

This script allows admins to completely reset a user's data by deleting:
- All accounts associated with the grant
- The slave row itself
- Any trade configurations

Usage:
  python3 reset_user_db.py <grant_id> [--admin-pin ADMIN_PIN]
  
  If --admin-pin is not provided, the script will prompt for it.
"""

import sys
import json
import requests
from getpass import getpass

def get_admin_pin():
    """Prompt for admin PIN or use environment variable."""
    import os
    admin_pin = os.environ.get('ADMIN_PIN')
    if admin_pin:
        return admin_pin
    
    while True:
        pin = getpass('Enter admin PIN: ')
        if pin:
            return pin
        print('PIN cannot be empty. Please try again.')

def get_auth_cookie(admin_pin):
    """Authenticate with the admin PIN and get session cookie."""
    auth_url = 'https://pin.mrme.tech/pin-login'
    
    payload = {
        'username': 'admin',
        'pin': admin_pin
    }
    
    try:
        response = requests.post(auth_url, json=payload)
        response.raise_for_status()
        
        # Extract the session cookie
        cookies = response.cookies
        session_cookie = None
        for cookie in cookies:
            if cookie.name.startswith('a_session_'):
                session_cookie = f"{cookie.name}={cookie.value}"
                break
        
        if not session_cookie:
            print('Error: Could not obtain session cookie')
            sys.exit(1)
        
        return session_cookie
    except requests.exceptions.RequestException as e:
        print(f'Authentication failed: {e}')
        sys.exit(1)

def reset_user_database(grant_id, session_cookie):
    """Call the reset endpoint to delete user data."""
    reset_url = f'https://auth.mrme.tech/admin/slaves/{grant_id}/reset'
    
    headers = {
        'Cookie': session_cookie,
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.post(reset_url, headers=headers)
        response.raise_for_status()
        
        result = response.json()
        if result.get('success'):
            print(f'✓ Successfully reset database for grant {grant_id}')
            print(f'  - All accounts deleted')
            print(f'  - Slave row deleted')
            print(f'  - Trade configs deleted')
            print(f'  - User will need to reconnect their cTrader account')
        else:
            print(f'Error: {result.get("error", "Unknown error")}')
            sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f'Reset failed: {e}')
        sys.exit(1)

def main():
    if len(sys.argv) < 2:
        print('Usage: python3 reset_user_db.py <grant_id> [--admin-pin ADMIN_PIN]')
        sys.exit(1)
    
    grant_id = sys.argv[1]
    admin_pin = None
    
    # Parse optional --admin-pin argument
    if len(sys.argv) > 2:
        if sys.argv[2] == '--admin-pin' and len(sys.argv) > 3:
            admin_pin = sys.argv[3]
        else:
            print('Usage: python3 reset_user_db.py <grant_id> [--admin-pin ADMIN_PIN]')
            sys.exit(1)
    
    if not admin_pin:
        admin_pin = get_admin_pin()
    
    print(f'Resetting database for grant: {grant_id}')
    print('This will PERMANENTLY DELETE all user data including:')
    print('  - All linked cTrader accounts')
    print('  - Trade configurations')
    print('  - Authentication tokens')
    print('  - The slave database record')
    print()
    
    confirm = input('Are you sure you want to continue? (yes/no): ')
    if confirm.lower() != 'yes':
        print('Operation cancelled.')
        sys.exit(0)
    
    print('Authenticating...')
    session_cookie = get_auth_cookie(admin_pin)
    
    print('Resetting database...')
    reset_user_database(grant_id, session_cookie)

if __name__ == '__main__':
    main()