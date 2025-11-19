#!/usr/bin/env python3
"""
Re-authenticate with Yahoo Fantasy Sports
Full OAuth2 flow when refresh token expires
"""

import os
import json
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import requests
from dotenv import load_dotenv
import threading
from pathlib import Path

# Find project root (where .env file is located)
SCRIPT_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = SCRIPT_DIR.parent
ENV_FILE_PATH = PROJECT_ROOT / ".env"

# Load environment from project root
load_dotenv(dotenv_path=ENV_FILE_PATH)

# Global to store the auth code
auth_code = None


class CallbackHandler(BaseHTTPRequestHandler):
    """Handle OAuth callback."""

    def do_GET(self):
        global auth_code

        # Parse the callback URL
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if "code" in params:
            auth_code = params["code"][0]

            # Send success response
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()

            success_html = """
            <html>
            <head><title>Success!</title></head>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h1 style="color: green;">✅ Authentication Successful!</h1>
                <p>You can close this window and return to the terminal.</p>
                <script>window.setTimeout(function(){window.close();}, 3000);</script>
            </body>
            </html>
            """
            self.wfile.write(success_html.encode())
        else:
            # Error response
            self.send_response(400)
            self.send_header("Content-type", "text/html")
            self.end_headers()

            error_html = """
            <html>
            <head><title>Error</title></head>
            <body style="font-family: Arial; text-align: center; padding: 50px;">
                <h1 style="color: red;">❌ Authentication Failed</h1>
                <p>No authorization code received.</p>
            </body>
            </html>
            """
            self.wfile.write(error_html.encode())

    def log_message(self, format, *args):
        # Suppress default logging
        pass


def run_callback_server(port=8000):
    """Run the callback server in a thread."""
    server = HTTPServer(("localhost", port), CallbackHandler)
    server.timeout = 60  # 60 second timeout
    server.handle_request()  # Handle one request then stop


def reauth_yahoo():
    """Complete re-authentication flow."""

    print("=" * 60)
    print("Yahoo Fantasy Sports Re-Authentication")
    print("=" * 60)
    print()

    # Get credentials
    client_id = os.getenv("YAHOO_CLIENT_ID")
    client_secret = os.getenv("YAHOO_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("❌ Missing YAHOO_CLIENT_ID or YAHOO_CLIENT_SECRET in .env")
        return False

    # OAuth URLs
    auth_url = "https://api.login.yahoo.com/oauth2/request_auth"
    token_url = "https://api.login.yahoo.com/oauth2/get_token"

    # Callback configuration
    callback_port = 8000
    # redirect_uri = f"https://localhost:{callback_port}/callback"
    redirect_uri = "oob"

    print(f"📌 Using redirect URI: {redirect_uri}")
    print()
    print("⚠️  IMPORTANT: Make sure this matches your Yahoo App settings!")
    print()

    # For "oob" redirect, we don't need a callback server
    # If using callback server in the future, uncomment:
    # print(f"🌐 Starting callback server on port {callback_port}...")
    # server_thread = threading.Thread(target=run_callback_server, args=(callback_port,))
    # server_thread.daemon = True
    # server_thread.start()

    # Build authorization URL
    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "language": "en-us",
    }

    auth_url_full = auth_url + "?" + "&".join([f"{k}={v}" for k, v in auth_params.items()])

    print("🔗 Opening browser for Yahoo login...")
    print()
    print("If browser doesn't open, manually visit:")
    print(auth_url_full)
    print()

    # Open browser
    webbrowser.open(auth_url_full)

    # Wait for callback - for "oob" flow, get code manually
    print("⏳ Waiting for authorization code...")
    print("   (For 'oob' flow, you'll need to copy the code from the browser)")

    # For "oob" redirect, manually input the code
    auth_code = input("\nEnter the authorization code from Yahoo: ").strip()

    if not auth_code:
        print("❌ No authorization code received. Timeout or user cancelled.")
        return False

    print(f"✅ Authorization code received!")
    print()

    # Exchange code for tokens
    print("🔄 Exchanging code for tokens...")

    token_data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "code": auth_code,
        "grant_type": "authorization_code",
    }

    try:
        response = requests.post(token_url, data=token_data)

        if response.status_code == 200:
            tokens = response.json()

            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            expires_in = tokens.get("expires_in", 3600)

            print("✅ Tokens received successfully!")
            print(f"   Token expires in: {expires_in} seconds ({expires_in/3600:.1f} hours)")
            print()

            # Get user GUID
            guid = get_user_guid(access_token)

            # Save to files
            save_tokens(access_token, refresh_token, guid)

            print("✅ Authentication complete!")
            print()
            print("📝 Tokens saved to:")
            print("   - .env file")
            print("   - MCP config files (Claude Desktop, Cursor, and/or Antigravity)")
            print()
            print("⚠️  IMPORTANT: Restart your MCP client to use the new tokens")

            return True

        else:
            print(f"❌ Failed to get tokens: {response.status_code}")
            print(f"   Response: {response.text}")
            return False

    except Exception as e:
        print(f"❌ Error getting tokens: {e}")
        return False


def get_user_guid(access_token):
    """Get the user's Yahoo GUID."""

    url = "https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1?format=json"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()

            # Navigate the response to find GUID
            users = data.get("fantasy_content", {}).get("users", {})
            if "0" in users:
                user = users["0"]["user"]
                if isinstance(user, list) and len(user) > 0:
                    guid = user[0].get("guid")
                    if guid:
                        print(f"📌 Found user GUID: {guid}")
                        return guid
    except:
        pass

    return None


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

def save_tokens(access_token, refresh_token, guid=None):
    """Save tokens to .env and MCP configs."""

    # Update .env in project root
    env_path = ENV_FILE_PATH

    # Read existing file
    env_lines = []
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            env_lines = f.readlines()

    # Track which variables we've updated
    updated_access = False
    updated_refresh = False
    updated_guid = False
    new_lines = []

    # Update existing lines in place
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

    # Only add at end if they weren't found (first time setup)
    if not updated_access:
        new_lines.append(f"YAHOO_ACCESS_TOKEN={access_token}\n")
    if not updated_refresh:
        new_lines.append(f"YAHOO_REFRESH_TOKEN={refresh_token}\n")
    if guid and not updated_guid:
        new_lines.append(f"YAHOO_GUID={guid}\n")

    # Write back to file
    with open(env_path, "w") as f:
        f.writelines(new_lines)

    # Update MCP configs (Claude Desktop and Cursor)
    update_mcp_configs(access_token, refresh_token, guid)


if __name__ == "__main__":
    reauth_yahoo()
