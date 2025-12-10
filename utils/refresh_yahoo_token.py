#!/usr/bin/env python3
"""
Refresh Yahoo Fantasy Sports OAuth2 Token
"""

import os
import json
import requests
from dotenv import load_dotenv
from datetime import datetime
from pathlib import Path

# Find project root (where .env file is located)
SCRIPT_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = SCRIPT_DIR.parent
ENV_FILE_PATH = PROJECT_ROOT / ".env"

# Load environment from project root
load_dotenv(dotenv_path=ENV_FILE_PATH)


def refresh_yahoo_token():
    """Refresh the Yahoo access token using the refresh token."""

    # Get credentials from environment
    client_id = os.getenv("YAHOO_CLIENT_ID")
    client_secret = os.getenv("YAHOO_CLIENT_SECRET")
    refresh_token = os.getenv("YAHOO_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        print("❌ Missing credentials in .env file")
        print("Required: YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET, YAHOO_REFRESH_TOKEN")
        return False

    # Yahoo token endpoint
    token_url = "https://api.login.yahoo.com/oauth2/get_token"

    # Prepare refresh request
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    print("🔄 Refreshing Yahoo token...")

    try:
        # Make refresh request
        response = requests.post(token_url, data=data)

        if response.status_code == 200:
            # Parse new tokens
            token_data = response.json()
            new_access_token = token_data.get("access_token")
            new_refresh_token = token_data.get("refresh_token", refresh_token)
            expires_in = token_data.get("expires_in", 3600)

            print("✅ Token refreshed successfully!")
            print(f"   Expires in: {expires_in} seconds ({expires_in/3600:.1f} hours)")

            # Update .env file
            update_env_file(new_access_token, new_refresh_token)

            # Also update MCP config files (Claude Desktop, Cursor, and Antigravity) if they exist
            update_mcp_configs(new_access_token, new_refresh_token)

            print("\n📝 Updated tokens in:")
            print("   - .env file")
            print("   - MCP config files (Claude Desktop, Cursor, and/or Antigravity)")
            print("\n⚠️  IMPORTANT: Restart your MCP client to use the new token")

            return True

        else:
            print(f"❌ Failed to refresh token: {response.status_code}")
            print(f"   Response: {response.text}")

            if response.status_code == 400:
                print("\n💡 If refresh token is expired, you need to re-authenticate:")
                print("   Run: python setup_yahoo_auth.py")

            return False

    except Exception as e:
        print(f"❌ Error refreshing token: {e}")
        return False


def update_env_file(access_token, refresh_token):
    """Update the .env file with new tokens."""

    # Read current .env from project root
    env_path = ENV_FILE_PATH
    lines = []

    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()

    # Update or add token lines
    updated_access = False
    updated_refresh = False
    new_lines = []

    for line in lines:
        if line.startswith("YAHOO_ACCESS_TOKEN="):
            new_lines.append(f"YAHOO_ACCESS_TOKEN={access_token}\n")
            updated_access = True
        elif line.startswith("YAHOO_REFRESH_TOKEN="):
            new_lines.append(f"YAHOO_REFRESH_TOKEN={refresh_token}\n")
            updated_refresh = True
        else:
            new_lines.append(line)

    # Add tokens if not found (first time setup)
    if not updated_access:
        new_lines.append(f"YAHOO_ACCESS_TOKEN={access_token}\n")
    if not updated_refresh:
        new_lines.append(f"YAHOO_REFRESH_TOKEN={refresh_token}\n")

    # Write back
    with open(env_path, "w") as f:
        f.writelines(new_lines)


def update_mcp_configs(access_token, refresh_token, guid=None):
    """Update Claude Desktop, Cursor, and Antigravity MCP config files with new tokens."""
    import platform
    
    updated_configs = []
    
    # 1. Update Claude Desktop config (if it exists)
    system = platform.system()
    if system == 'Darwin':  # macOS
        claude_config_path = Path.home() / 'Library' / 'Application Support' / 'Claude' / 'claude_desktop_config.json'
    elif system == 'Windows':
        claude_config_path = Path(os.environ.get('APPDATA', '')) / 'Claude' / 'claude_desktop_config.json'
    else:  # Linux
        claude_config_path = Path.home() / '.config' / 'Claude' / 'claude_desktop_config.json'
    
    if claude_config_path.exists():
        try:
            with open(claude_config_path, 'r') as f:
                config = json.load(f)
            
            # Try both possible server names
            server_names = ['fantasy-football', 'yahoo-fantasy-football']
            updated = False
            
            for server_name in server_names:
                if 'mcpServers' in config and server_name in config['mcpServers']:
                    if 'env' not in config['mcpServers'][server_name]:
                        config['mcpServers'][server_name]['env'] = {}
                    
                    config['mcpServers'][server_name]['env']['YAHOO_ACCESS_TOKEN'] = access_token
                    config['mcpServers'][server_name]['env']['YAHOO_REFRESH_TOKEN'] = refresh_token
                    if guid:
                        config['mcpServers'][server_name]['env']['YAHOO_GUID'] = guid
                    updated = True
                    break
            
            if updated:
                with open(claude_config_path, 'w') as f:
                    json.dump(config, f, indent=2)
                updated_configs.append('Claude Desktop config')
        except Exception as e:
            print(f"⚠️  Could not update Claude Desktop config: {e}")
    
    # 2. Update Cursor MCP config (if it exists)
    cursor_config_path = Path.home() / '.cursor' / 'mcp.json'
    if cursor_config_path.exists():
        try:
            with open(cursor_config_path, 'r') as f:
                config = json.load(f)
            
            # Try both possible server names
            server_names = ['yahoo-fantasy-football', 'fantasy-football']
            updated = False
            
            for server_name in server_names:
                if 'mcpServers' in config and server_name in config['mcpServers']:
                    if 'env' not in config['mcpServers'][server_name]:
                        config['mcpServers'][server_name]['env'] = {}
                    
                    config['mcpServers'][server_name]['env']['YAHOO_ACCESS_TOKEN'] = access_token
                    config['mcpServers'][server_name]['env']['YAHOO_REFRESH_TOKEN'] = refresh_token
                    if guid:
                        config['mcpServers'][server_name]['env']['YAHOO_GUID'] = guid
                    updated = True
                    break
            
            if updated:
                with open(cursor_config_path, 'w') as f:
                    json.dump(config, f, indent=2)
                updated_configs.append('Cursor MCP config')
        except Exception as e:
            print(f"⚠️  Could not update Cursor MCP config: {e}")
    
    # 3. Update Antigravity MCP config (if it exists)
    antigravity_config_path = Path.home() / '.gemini' / 'antigravity' / 'mcp_config.json'
    if antigravity_config_path.exists():
        try:
            with open(antigravity_config_path, 'r') as f:
                config = json.load(f)
            
            # Try both possible server names
            server_names = ['yahoo-fantasy-football', 'fantasy-football']
            updated = False
            
            for server_name in server_names:
                if 'mcpServers' in config and server_name in config['mcpServers']:
                    if 'env' not in config['mcpServers'][server_name]:
                        config['mcpServers'][server_name]['env'] = {}
                    
                    config['mcpServers'][server_name]['env']['YAHOO_ACCESS_TOKEN'] = access_token
                    config['mcpServers'][server_name]['env']['YAHOO_REFRESH_TOKEN'] = refresh_token
                    if guid:
                        config['mcpServers'][server_name]['env']['YAHOO_GUID'] = guid
                    updated = True
                    break
            
            if updated:
                with open(antigravity_config_path, 'w') as f:
                    json.dump(config, f, indent=2)
                updated_configs.append('Antigravity MCP config')
        except Exception as e:
            print(f"⚠️  Could not update Antigravity MCP config: {e}")
    
    if updated_configs:
        print(f"✅ Updated tokens in: {', '.join(updated_configs)}")
    else:
        print("⚠️  No MCP config files found to update")

def update_claude_config(access_token, refresh_token):
    """Update the Claude Desktop config with new tokens (deprecated - use update_mcp_configs)."""
    # Keep for backwards compatibility, but redirect to new function
    update_mcp_configs(access_token, refresh_token)


def test_new_token():
    """Test if the new token works."""

    load_dotenv(dotenv_path=ENV_FILE_PATH, override=True)  # Reload environment
    access_token = os.getenv("YAHOO_ACCESS_TOKEN")

    if not access_token:
        print("❌ No access token found")
        return False

    # Test API call
    url = "https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1?format=json"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            print("\n✅ Token test successful! API is accessible.")
            return True
        else:
            print(f"\n❌ Token test failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"\n❌ Token test error: {e}")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("Yahoo Fantasy Sports Token Refresh")
    print("=" * 60)
    print()

    if refresh_yahoo_token():
        print("\n🧪 Testing new token...")
        test_new_token()

        print("\n" + "=" * 60)
        print("✅ Token refresh complete!")
        print("=" * 60)
    else:
        print("\n" + "=" * 60)
        print("❌ Token refresh failed")
        print("=" * 60)
        print("\nTroubleshooting:")
        print("1. Check your internet connection")
        print("2. Verify your credentials in .env")
        print("3. If refresh token is expired, run: python setup_yahoo_auth.py")
        print("4. Check Yahoo Developer App settings")
