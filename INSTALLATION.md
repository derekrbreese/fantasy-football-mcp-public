# Fantasy Football MCP Server - Installation Guide

## Prerequisites

- Python 3.8 or higher
- Claude Desktop application
- Yahoo Fantasy Sports account with active leagues
- Git (for cloning the repository)

## Step 1: Clone the Repository

```bash
git clone https://github.com/derekrbreese/fantasy-football-mcp-public.git
cd fantasy-football-mcp-public
```

## Step 2: Install Python Dependencies

```bash
pip install -r requirements.txt
```

Or if you prefer using a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Step 3: Yahoo API Setup

### 3.1 Create a Yahoo Developer App

1. Go to https://developer.yahoo.com/apps/
2. Click "Create an App"
3. Fill in the application details:
   - **Application Name**: Fantasy Football MCP (or your choice)
   - **Application Type**: Web Application
   - **Redirect URI(s)**: `http://localhost:8000/callback`
   - **API Permissions**: Fantasy Sports (Read)
4. Click "Create App"
5. Save your **Client ID (Consumer Key)** and **Client Secret (Consumer Secret)**

### 3.2 Initial Authentication

Run the authentication script to get your tokens:

```bash
python reauth_yahoo.py
```

This will:
1. Open your browser for Yahoo login
2. Ask you to authorize the app
3. Automatically save your tokens to `.env` file
4. Display your team information to confirm it's working

## Step 4: Environment Configuration

The `.env` file should be automatically created after authentication. Verify it contains:

```env
# Yahoo API Credentials (Required)
YAHOO_CONSUMER_KEY=your_consumer_key_here
YAHOO_CONSUMER_SECRET=your_consumer_secret_here
YAHOO_ACCESS_TOKEN=your_access_token_here
YAHOO_REFRESH_TOKEN=your_refresh_token_here
YAHOO_GUID=your_yahoo_guid_here

# Reddit API Credentials (Optional - for sentiment analysis)
# See docs/REDDIT_API_SETUP.md for setup instructions
REDDIT_CLIENT_ID=your_reddit_client_id
REDDIT_CLIENT_SECRET=your_reddit_client_secret
REDDIT_USERNAME=your_reddit_username
```

**Note**: Since this is a private repository, the `.env` file is tracked for backup purposes.

### 4.1 Reddit API Setup (Optional)

If you want to use Reddit sentiment analysis features (`ff_analyze_reddit_sentiment`), you'll need Reddit API credentials:

1. See the detailed guide: [Reddit API Setup Guide](docs/REDDIT_API_SETUP.md)
2. Create a Reddit application at https://www.reddit.com/prefs/apps
3. Add the credentials to your `.env` file as shown above

The app will work without Reddit credentials, but sentiment analysis will use fallback methods.

## Step 5: Claude Desktop Configuration

### 5.1 Locate Claude Desktop Config

The configuration file location depends on your operating system:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

### 5.2 Add MCP Server Configuration

Add the following to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "fantasy-football": {
      "command": "python",
      "args": [
        "/absolute/path/to/fantasy_football_multi_league.py"
      ],
      "env": {
        "YAHOO_ACCESS_TOKEN": "your_access_token",
        "YAHOO_CONSUMER_KEY": "your_consumer_key",
        "YAHOO_CONSUMER_SECRET": "your_consumer_secret",
        "YAHOO_REFRESH_TOKEN": "your_refresh_token",
        "YAHOO_GUID": "your_yahoo_guid"
      }
    }
  }
}
```

**Important**: 
- Replace `/absolute/path/to/` with the actual path to your installation
- Copy the credentials from your `.env` file
- If you have other MCP servers configured, add this as an additional entry

### 5.3 Alternative: Use the Provided Config

You can also copy the provided config template:

```bash
cp claude_desktop_config.json ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

Then edit it to update the file paths and credentials.

## Step 6: Test the Installation

### 6.1 Test Python Server Directly

```bash
python test_multi_league_server.py
```

Expected output:
- Should find all your active leagues
- Should identify your team in each league

### 6.2 Restart Claude Desktop

After updating the configuration:
1. Completely quit Claude Desktop
2. Restart Claude Desktop
3. The MCP tools should now be available

### 6.3 Verify in Claude Desktop

Ask Claude: "Use the fantasy football tools to show me my leagues"

Claude should be able to use the `ff_get_leagues` tool and show your active leagues.

## Step 7: Token Management

### Automatic Token Refresh

The server includes automatic token refresh capability. You can also manually refresh:

**Through Claude Desktop**: 
- Ask Claude to "refresh my Yahoo token"

**Through Command Line**:
```bash
python refresh_yahoo_token.py
```

### Full Re-authentication

If tokens are completely expired (after ~60 days):

```bash
python reauth_yahoo.py
```

## Available MCP Tools

Once installed, you'll have access to 12 tools:

1. **ff_get_leagues** - List all your fantasy football leagues
2. **ff_get_league_info** - Get detailed league information with your team name
3. **ff_get_standings** - View current standings
4. **ff_get_roster** - Get a team roster with team name (accepts optional `team_key`)
5. **ff_compare_teams** - Compare two teams' rosters
6. **ff_get_matchup** - View matchup details
7. **ff_get_players** - Browse available players
8. **ff_build_lineup** - Build optimal lineup with positional constraints
9. **ff_refresh_token** - Refresh Yahoo access token
10. **ff_get_draft_results** - View draft results and grades
11. **ff_get_waiver_wire** - Find top waiver wire pickups
12. **ff_get_draft_rankings** - Get pre-draft player rankings

## Troubleshooting

### "Failed to connect to MCP server"
- Verify Python path in Claude Desktop config
- Ensure all Python dependencies are installed
- Check that file paths are absolute, not relative

### "Token expired" errors
- Run `python refresh_yahoo_token.py`
- Restart Claude Desktop after refreshing

### "No leagues found"
- Verify you have active leagues for the current season
- Check that YAHOO_GUID is set correctly in `.env`
- Ensure your Yahoo account has fantasy leagues

### "Cannot find team"
- Make sure YAHOO_GUID is set in both `.env` and Claude config
- Verify you're a member of the leagues

### Python Import Errors
- Ensure all requirements are installed: `pip install -r requirements.txt`
- If using virtual environment, make sure it's activated

## Testing Your Installation

Run the test suite to verify everything is working:

```bash
# Test league discovery
python test_all_leagues.py

# Test team name retrieval
python test_team_names.py  

# Test waiver wire and rankings
python test_waiver_draft.py
```

## Updating

To get the latest updates:

```bash
git pull origin main
pip install -r requirements.txt --upgrade
```

Then restart Claude Desktop.

## Support

For issues or questions:
1. Check the [GitHub repository](https://github.com/derekrbreese/fantasy-football-mcp)
2. Review the CLAUDE.md file for development details
3. Ensure your Yahoo tokens are current

## Security Notes

- Never share your Yahoo API credentials
- The `.env` file contains sensitive tokens
- This repository should remain private
- Tokens expire after 1 hour (auto-refresh available)
- Refresh tokens last ~60 days if used regularly