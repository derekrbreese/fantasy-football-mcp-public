#!/usr/bin/env python3
"""
One-time Yahoo Fantasy API Authentication Setup
Run this script once to authenticate and save your token.
"""

import os
import sys
import json
import webbrowser
import base64
import requests
from pathlib import Path
from dotenv import load_dotenv

# Find project root (where .env file is located)
SCRIPT_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = SCRIPT_DIR.parent
ENV_FILE_PATH = PROJECT_ROOT / ".env"

# Load environment variables from project root
load_dotenv(dotenv_path=ENV_FILE_PATH)

print("=" * 70)
print("🏈 YAHOO FANTASY API - ONE-TIME AUTHENTICATION SETUP")
print("=" * 70)
print()

# Your credentials from .env
CLIENT_ID = os.getenv("YAHOO_CLIENT_ID")
CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    print("❌ ERROR: Yahoo credentials not found in .env file")
    print("Please make sure your .env file contains:")
    print("  YAHOO_CLIENT_ID=your_client_id")
    print("  YAHOO_CLIENT_SECRET=your_client_secret")
    sys.exit(1)

print("✅ Found Yahoo credentials")
print(f"   Client ID: {CLIENT_ID[:30]}...")
print(f"   Client Secret: {CLIENT_SECRET[:10]}...")
print()

def exchange_verification_code_for_tokens(verification_code, client_id, client_secret):
    """Exchange Yahoo OAuth verification code for access and refresh tokens."""
    token_url = "https://api.login.yahoo.com/oauth2/get_token"
    
    # Create Basic Auth header
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    
    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    data = {
        "grant_type": "authorization_code",
        "redirect_uri": "oob",
        "code": verification_code
    }
    
    try:
        response = requests.post(token_url, headers=headers, data=data)
        response.raise_for_status()
        token_data = response.json()
        return token_data
    except requests.exceptions.RequestException as e:
        print(f"❌ Error exchanging code for tokens: {e}")
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_detail = e.response.json()
                print(f"   Error details: {error_detail}")
            except:
                print(f"   Response: {e.response.text}")
        return None

def update_env_file_with_tokens(access_token, refresh_token, env_file_path, guid=None):
    """Update .env file with access and refresh tokens."""
    env_path = Path(env_file_path)
    
    # Read existing .env file
    env_lines = []
    if env_path.exists():
        with open(env_path, 'r') as f:
            env_lines = f.readlines()
    
    # Update or add token lines
    updated_access = False
    updated_refresh = False
    updated_guid = False
    new_lines = []
    
    for line in env_lines:
        if line.startswith("YAHOO_ACCESS_TOKEN="):
            new_lines.append(f"YAHOO_ACCESS_TOKEN={access_token}\n")
            updated_access = True
        elif line.startswith("YAHOO_REFRESH_TOKEN="):
            new_lines.append(f"YAHOO_REFRESH_TOKEN={refresh_token}\n")
            updated_refresh = True
        elif line.startswith("YAHOO_GUID=") and guid:
            new_lines.append(f"YAHOO_GUID={guid}\n")
            updated_guid = True
        elif line.startswith("YAHOO_GUID=") and not guid:
            # Keep existing GUID if new one not provided
            new_lines.append(line)
            updated_guid = True
        else:
            new_lines.append(line)
    
    # Add tokens if they weren't found
    if not updated_access:
        new_lines.append(f"YAHOO_ACCESS_TOKEN={access_token}\n")
    if not updated_refresh:
        new_lines.append(f"YAHOO_REFRESH_TOKEN={refresh_token}\n")
    if guid and not updated_guid:
        new_lines.append(f"YAHOO_GUID={guid}\n")
    
    # Write back to file
    with open(env_path, 'w') as f:
        f.writelines(new_lines)
    
    print(f"✅ Updated {env_path} with tokens")

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

def manual_oauth_flow(client_id, client_secret):
    """Handle the manual OAuth flow (Method 2)."""
    print("METHOD 2: Manual OAuth Flow")
    print("-" * 40)
    print()
    
    # Build authorization URL
    auth_url = (
        "https://api.login.yahoo.com/oauth2/request_auth?"
        f"client_id={client_id}&"
        "redirect_uri=oob&"
        "response_type=code&"
        "language=en-us"
    )
    
    print("Manual authentication steps:")
    print()
    print("1. Copy this URL and open it in your browser:")
    print()
    print(auth_url)
    print()
    print("2. Login to Yahoo and click 'Agree'")
    print("3. Yahoo will show you a verification code")
    print("4. Come back here and paste that code")
    print()
    
    # Try to open browser automatically
    try:
        webbrowser.open(auth_url)
        print("✅ Browser opened automatically")
    except:
        print("⚠️  Could not open browser automatically")
        print("   Please copy the URL above and open it manually")
    
    print()
    print("-" * 40)
    verification_code = input("Enter the verification code from Yahoo: ").strip()
    
    if not verification_code:
        print("❌ No verification code provided. Exiting.")
        return False
    
    print()
    print("🔄 Exchanging verification code for tokens...")
    
    token_data = exchange_verification_code_for_tokens(verification_code, client_id, client_secret)
    
    if not token_data:
        print("❌ Failed to exchange code for tokens.")
        return False
    
    # Save token to file
    token_file = PROJECT_ROOT / ".py.json"
    with open(token_file, 'w') as f:
        json.dump(token_data, f, indent=2)
    
    print(f"✅ Token saved to {token_file}")
    
    # Update .env file with tokens
    access_token = token_data.get('access_token')
    refresh_token = token_data.get('refresh_token')
    if access_token and refresh_token:
        update_env_file_with_tokens(access_token, refresh_token, ENV_FILE_PATH)
        # Also update MCP config files
        update_mcp_configs(access_token, refresh_token)
    else:
        print("⚠️  Could not extract tokens from token_data to update .env")
    
    print("   The MCP server can now use this token!")
    print()
    
    # Test the token by making a simple API call
    print("Testing connection...")
    try:
        test_url = "https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games"
        headers = {
            "Authorization": f"Bearer {token_data.get('access_token')}"
        }
        response = requests.get(test_url, headers=headers)
        if response.status_code == 200:
            print("✅ Connection test successful!")
        else:
            print(f"⚠️  Connection test returned status {response.status_code}")
    except Exception as e:
        print(f"⚠️  Connection test failed: {e}")
        print("   But token was saved successfully.")
    
    return True

# Method 1: Using yfpy (Recommended)
print("METHOD 1: Using yfpy Library (Recommended)")
print("-" * 40)

try:
    from yfpy import YahooFantasySportsQuery
    
    print("This will:")
    print("1. Open your browser to Yahoo login")
    print("2. You login and click 'Agree' to authorize")
    print("3. Yahoo will show a verification code")
    print("4. Come back here and paste that code")
    print()
    
    input("Press Enter to start the authentication process...")
    print()
    
    # Create token directory
    token_dir = Path(".tokens")
    token_dir.mkdir(exist_ok=True)
    
    print("🌐 Opening browser for Yahoo authorization...")
    print()
    
    # Initialize - this will trigger OAuth flow
    try:
        query = YahooFantasySportsQuery(
            league_id="",  # Empty to get all leagues
            game_code="nfl",
            game_id=449,  # 2025 NFL season
            YAHOO_CLIENT_ID=CLIENT_ID,
            YAHOO_CLIENT_SECRET=CLIENT_SECRET,
            browser_callback=True,  # Opens browser automatically
            env_file_location=ENV_FILE_PATH,  # Save token to .env in project root
            save_token_data_to_env_file=True  # Save for reuse
        )
        
        print()
        print("✅ Authentication successful!")
        print()
        
        # Test by getting user leagues
        print("Testing connection by fetching your leagues...")
        try:
            # Get user info to verify connection
            user_games = query.get_user_games()
            print(f"✅ Connected! Found {len(user_games) if user_games else 0} games")
            
            # Try to get leagues
            user_leagues = query.get_user_leagues_by_game_key("449")  # NFL 2025 season
            if user_leagues:
                print(f"✅ Found {len(user_leagues)} leagues:")
                for i, league in enumerate(user_leagues, 1):
                    league_name = getattr(league, 'name', 'Unknown')
                    league_id = getattr(league, 'league_id', 'Unknown')
                    print(f"   {i}. {league_name} (ID: {league_id})")
            
            # Save token for MCP server use
            token_file = PROJECT_ROOT / ".yahoo_token.json"
            if hasattr(query, 'oauth') and hasattr(query.oauth, 'token_data'):
                token_data = query.oauth.token_data
                with open(token_file, 'w') as f:
                    json.dump(token_data, f, indent=2)
                print(f"\n✅ Token saved to {token_file}")
                
                # Update .env file with tokens
                access_token = token_data.get('access_token') or token_data.get('yahoo_access_token')
                refresh_token = token_data.get('refresh_token') or token_data.get('yahoo_refresh_token')
                if access_token and refresh_token:
                    update_env_file_with_tokens(access_token, refresh_token, ENV_FILE_PATH)
                    # Also update MCP config files
                    update_mcp_configs(access_token, refresh_token)
                else:
                    print("⚠️  Could not extract tokens from token_data to update .env")
                    print(f"   Token data keys: {list(token_data.keys())}")
                
                print("   The MCP server can now use this token!")
            
        except Exception as e:
            print(f"⚠️  Connection test failed: {e}")
            print("   But authentication may still be successful.")
            
    except Exception as e:
        print(f"\n❌ Authentication failed: {e}")
        print()
        print("Falling back to Method 2...")
        print()
        
        # Method 2: Manual OAuth flow
        manual_oauth_flow(CLIENT_ID, CLIENT_SECRET)
        
except ImportError:
    print("❌ yfpy not installed")
    print("Install with: pip install yfpy")
    print()
    print("Falling back to Method 2...")
    print()
    
    # Method 2: Manual OAuth flow
    manual_oauth_flow(CLIENT_ID, CLIENT_SECRET)

print()
print("=" * 70)
print("NEXT STEPS")
print("=" * 70)
print()
print("Once authenticated:")
print("1. The token is saved to .yahoo_token.json")
print("2. The MCP server will use this token automatically")
print("3. Token will auto-refresh as needed")
print()
print("To use with MCP:")
print("1. Add to your MCP config (Claude, etc.)")
print("2. The server will use the saved token")
print("3. Start making Fantasy Football API calls!")
print()
print("Need help? Check YAHOO_AUTH_REALITY.md for more details.")