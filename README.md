[![MSeeP.ai Security Assessment Badge](https://mseep.net/pr/derekrbreese-fantasy-football-mcp-public-badge.png)](https://mseep.ai/app/derekrbreese-fantasy-football-mcp-public)

# Fantasy Football MCP Server

A Model Context Protocol (MCP) server for Yahoo Fantasy Football integration with Claude Desktop. Features advanced lineup optimization, draft assistance, and comprehensive league management.

## Features

- **Multi-League Support**: Manage all your Yahoo Fantasy Football leagues
- **AI-Powered Optimization**: Advanced lineup recommendations with multiple strategies
- **Draft Assistant**: Real-time draft recommendations and analysis
- **Waiver Wire Analysis**: Find top pickups with trending data
- **Position Normalization**: Smart FLEX decisions with position-adjusted scoring
- **Rate Limiting & Caching**: Efficient API usage with built-in optimization

## Quick Start

### Prerequisites

- Python 3.11+
- Claude Desktop
- Yahoo Fantasy Sports account
- Yahoo Developer App credentials

### Installation

1. Clone the repository:
```bash
git clone https://github.com/derekrbreese/fantasy-football-mcp-public.git
cd fantasy-football-mcp-public
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure API credentials:
   - **Yahoo API**: Follow [YAHOO_SETUP.md](YAHOO_SETUP.md) for detailed setup
   - **Reddit API** (optional): See [REDDIT_SETUP.md](REDDIT_SETUP.md) for player sentiment analysis
   - Copy `.env.example` to `.env`
   - Add your API credentials
   - Run authentication: `python utils/setup_yahoo_auth.py`

4. Configure Claude Desktop:
   - Add the MCP server configuration (see [INSTALLATION.md](INSTALLATION.md))
   - Restart Claude Desktop

## Available MCP Tools

### League Management
- `ff_get_leagues` - List all your fantasy football leagues
- `ff_get_league_info` - Get detailed league information
- `ff_get_standings` - View current standings

### Team Management  
- `ff_get_roster` - Get your team roster
- `ff_get_matchup` - View matchup details
- `ff_get_optimal_lineup` - Get AI lineup recommendations

### Player Discovery
- `ff_get_players` - Browse available players
- `ff_get_waiver_wire` - Find top waiver pickups
- `ff_get_draft_rankings` - Get pre-draft rankings

### Draft & Admin
- `ff_get_draft_results` - View draft results
- `ff_get_draft_recommendation` - Get live draft advice
- `ff_refresh_token` - Refresh Yahoo access token

## Project Structure

```
fantasy-football-mcp-public/
├── fantasy_football_multi_league.py  # Main MCP server
├── src/                               # Core modules
│   ├── agents/                        # Specialized agents
│   ├── models/                        # Data models
│   ├── strategies/                    # Lineup strategies
│   └── utils/                         # Utility functions
├── utils/                             # Authentication utilities
├── config/                            # Configuration
└── requirements.txt                   # Dependencies
```

## Configuration

See [INSTALLATION.md](INSTALLATION.md) for detailed setup instructions.

## Security

- Never commit `.env` files or API credentials
- See [SECURITY.md](SECURITY.md) for security policy
- Regularly rotate access tokens

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT - See [LICENSE](LICENSE) file

## Support

For issues or questions, please use the [GitHub Issues](https://github.com/derekrbreese/fantasy-football-mcp-public/issues) page.
