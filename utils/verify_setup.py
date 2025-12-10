#!/usr/bin/env python3
"""
Verify Yahoo Fantasy Football MCP Setup
Checks credentials and configuration
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import json

# Add parent directory to path
SCRIPT_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

def check_env_file():
    """Check if .env file exists and has required variables"""
    print("\n1. Checking .env file...")
    
    # Look for .env file in project root, not current working directory
    env_path = PROJECT_ROOT / '.env'
    if not env_path.exists():
        print("   ❌ .env file not found!")
        print(f"   Expected at: {env_path}")
        print("   Fix: Copy .env.example to .env")
        return False
    
    # Load .env from project root
    load_dotenv(dotenv_path=env_path)
    
    required_vars = {
        'YAHOO_CLIENT_ID': 'Your app client ID from Yahoo',
        'YAHOO_CLIENT_SECRET': 'Your app client secret from Yahoo',
    }
    
    optional_vars = {
        'YAHOO_ACCESS_TOKEN': 'OAuth access token (from setup_yahoo_auth.py)',
        'YAHOO_REFRESH_TOKEN': 'OAuth refresh token (from setup_yahoo_auth.py)',
        'YAHOO_GUID': 'Your Yahoo user ID (from setup_yahoo_auth.py)',
    }
    
    missing_required = []
    missing_optional = []
    
    print("   ✅ .env file found")
    print("\n   Required credentials:")
    
    for var, desc in required_vars.items():
        value = os.getenv(var)
        if not value or value.startswith('your_') or value.startswith('YOUR_'):
            print(f"   ❌ {var}: Not configured")
            missing_required.append(var)
        else:
            print(f"   ✅ {var}: Configured ({len(value)} chars)")
    
    print("\n   OAuth credentials (from authentication):")
    
    for var, desc in optional_vars.items():
        value = os.getenv(var)
        if not value:
            print(f"   ⚠️  {var}: Not set (run setup_yahoo_auth.py)")
            missing_optional.append(var)
        else:
            # Show partial value for verification
            if var == 'YAHOO_GUID':
                print(f"   ✅ {var}: {value}")
            else:
                print(f"   ✅ {var}: {value[:20]}... ({len(value)} chars)")
    
    if missing_required:
        print(f"\n   ❌ Missing required credentials: {', '.join(missing_required)}")
        print("   Fix: Get these from https://developer.yahoo.com/apps/")
        return False
    
    if missing_optional:
        print(f"\n   ⚠️  Missing OAuth tokens. Run: python utils/setup_yahoo_auth.py")
    
    return True

def check_yahoo_credentials():
    """Verify Yahoo credentials format"""
    print("\n2. Verifying credential format...")
    
    client_id = os.getenv('YAHOO_CLIENT_ID', '')
    client_secret = os.getenv('YAHOO_CLIENT_SECRET', '')
    
    issues = []
    
    # Check Client ID format
    if client_id:
        if len(client_id) > 100:
            issues.append("Client ID seems too long (might include extra parameters)")
            print("   ⚠️  Client ID might be base64 encoded with parameters")
            print("      Should look like: dj0yJmk9XXXXXXXXX")
        elif client_id.startswith('dj0yJmk9'):
            print("   ✅ Client ID format looks correct")
        else:
            print("   ⚠️  Client ID format might be incorrect")
    
    # Check Client Secret format
    if client_secret:
        if len(client_secret) == 40 and all(c in '0123456789abcdef' for c in client_secret.lower()):
            print("   ✅ Client Secret format looks correct (40-char hex)")
        else:
            print(f"   ⚠️  Client Secret format might be incorrect (length: {len(client_secret)})")
            issues.append("Client Secret should be a 40-character hexadecimal string")
    
    return len(issues) == 0

def check_dependencies():
    """Check if required Python packages are installed"""
    print("\n3. Checking Python dependencies...")
    
    required_packages = [
        'mcp',
        'aiohttp',
        'pydantic',
        'dotenv',
        'requests'
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
            print(f"   ✅ {package}: Installed")
        except ImportError:
            print(f"   ❌ {package}: Not installed")
            missing.append(package)
    
    if missing:
        print(f"\n   ❌ Missing packages: {', '.join(missing)}")
        print("   Fix: pip install -r requirements.txt")
        return False
    
    return True

def check_claude_config():
    """Check if Claude Desktop config exists"""
    print("\n4. Checking Claude Desktop configuration...")
    
    import platform
    system = platform.system()
    
    if system == 'Darwin':  # macOS
        config_path = Path.home() / 'Library' / 'Application Support' / 'Claude' / 'claude_desktop_config.json'
    elif system == 'Windows':
        config_path = Path(os.environ['APPDATA']) / 'Claude' / 'claude_desktop_config.json'
    else:  # Linux
        config_path = Path.home() / '.config' / 'Claude' / 'claude_desktop_config.json'
    
    if config_path.exists():
        print(f"   ✅ Claude config found: {config_path}")
        
        try:
            with open(config_path) as f:
                config = json.load(f)
                
            if 'mcpServers' in config and 'fantasy-football' in config['mcpServers']:
                print("   ✅ Fantasy Football MCP server configured")
            else:
                print("   ⚠️  Fantasy Football MCP not configured in Claude")
                print("   Fix: Add configuration to claude_desktop_config.json")
                print("        See INSTALLATION.md for details")
        except json.JSONDecodeError:
            print("   ❌ Claude config file is invalid JSON")
    else:
        print(f"   ⚠️  Claude config not found at: {config_path}")
        print("   This is OK if Claude Desktop isn't installed yet")
    
    return True

def main():
    """Run all verification checks"""
    print("=" * 60)
    print("Yahoo Fantasy Football MCP - Setup Verification")
    print("=" * 60)
    
    all_good = True
    
    # Check .env file
    if not check_env_file():
        all_good = False
    
    # Check credential format
    if not check_yahoo_credentials():
        all_good = False
    
    # Check dependencies
    if not check_dependencies():
        all_good = False
    
    # Check Claude config
    check_claude_config()
    
    print("\n" + "=" * 60)
    
    if all_good:
        print("✅ Setup looks good!")
        print("\nNext steps:")
        
        if not os.getenv('YAHOO_ACCESS_TOKEN'):
            print("1. Run: python utils/setup_yahoo_auth.py")
            print("2. Configure Claude Desktop (see INSTALLATION.md)")
        else:
            print("1. Configure Claude Desktop if not done")
            print("2. Restart Claude Desktop")
            print("3. Test with: 'Show me my fantasy football leagues'")
    else:
        print("❌ Setup needs attention")
        print("\nPlease fix the issues above and run this script again.")
        print("See INSTALLATION.md for detailed instructions.")
    
    print("=" * 60)

if __name__ == "__main__":
    main()