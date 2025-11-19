# Reddit API Credentials Setup Guide

This guide will walk you through obtaining Reddit API credentials for the Fantasy Football MCP Server's sentiment analysis feature.

## Overview

The app uses Reddit's API (via the PRAW library) to analyze fantasy football player sentiment from subreddits like:
- r/fantasyfootball
- r/DynastyFF
- r/Fantasy_Football
- r/nfl

## Step 1: Create a Reddit Application

1. **Go to Reddit's App Preferences**
   - Visit: https://www.reddit.com/prefs/apps
   - You must be logged into your Reddit account

2. **Create a New Application**
   - Scroll to the bottom and click **"create another app..."** or **"create app"**
   - Fill in the application details:
     - **Name**: `Fantasy Football MCP` (or any name you prefer)
     - **Type**: Select **"script"** (this is important!)
     - **Description**: Optional - e.g., "Fantasy football sentiment analysis"
     - **About URL**: Leave blank or add your GitHub repo URL
     - **Redirect URI**: For script apps, use: `http://localhost:8080` (required but not used for script apps)

3. **Submit the Form**
   - Click **"create app"**

## Step 2: Get Your Credentials

After creating the app, you'll see a page with your app details:

1. **Client ID** (under your app name)
   - This is a string that looks like: `abc123def456ghi789`
   - Copy this value - this is your `REDDIT_CLIENT_ID`

2. **Secret** (shown as "secret" next to your app)
   - This is a longer string that looks like: `xyz789abc123def456ghi789jkl012mno345`
   - Click "edit" or "reveal" to see it if hidden
   - Copy this value - this is your `REDDIT_CLIENT_SECRET`

3. **Username** (optional but recommended)
   - Your Reddit username (the one you're logged in as)
   - This is used in the user agent string for API requests
   - This is your `REDDIT_USERNAME`

## Step 3: Add Credentials to Your .env File

Add the following lines to your `.env` file in the project root:

```env
# Reddit API Credentials (for sentiment analysis)
REDDIT_CLIENT_ID=your_client_id_here
REDDIT_CLIENT_SECRET=your_client_secret_here
REDDIT_USERNAME=your_reddit_username
```

**Example:**
```env
REDDIT_CLIENT_ID=abc123def456ghi789
REDDIT_CLIENT_SECRET=xyz789abc123def456ghi789jkl012mno345
REDDIT_USERNAME=myusername
```

## Step 4: Verify Installation

Make sure the required packages are installed:

```bash
pip install praw textblob
```

Or if using the requirements file:

```bash
pip install -r requirements.txt
```

## Step 5: Test the Configuration

The Reddit API will be automatically initialized when you use features that require it, such as:

- `ff_analyze_reddit_sentiment` - Analyze Reddit sentiment for players

If credentials are missing or incorrect, the app will:
- Log a warning message
- Continue operating without Reddit sentiment analysis
- Use fallback sentiment analysis methods

## Important Notes

### Rate Limits
- Reddit API has rate limits (typically 60 requests per minute)
- The app includes rate limiting and error handling
- If you hit rate limits, wait a few minutes before trying again

### App Type
- **Must use "script" type** - This is the correct type for server-side applications
- "web app" and "installed app" types won't work with this setup

### Security
- **Never commit your `.env` file** to version control
- Keep your `REDDIT_CLIENT_SECRET` private
- The username is optional but helps identify your app to Reddit

### Troubleshooting

**"Reddit API credentials not configured"**
- Check that all three variables are in your `.env` file
- Verify there are no extra spaces or quotes around the values
- Restart the MCP server after adding credentials

**"Reddit API connection test failed"**
- Verify your Client ID and Secret are correct
- Check that you selected "script" as the app type
- Ensure you're using the correct Reddit account

**"PRAW not available"**
- Run: `pip install praw textblob`
- Check that you're in the correct Python environment

## Optional: Verify Reddit API Access

You can test your credentials manually with this Python snippet:

```python
import os
from dotenv import load_dotenv
import praw

load_dotenv()

reddit = praw.Reddit(
    client_id=os.getenv("REDDIT_CLIENT_ID"),
    client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
    user_agent=f"fantasy-football-mcp:v1.0 by /u/{os.getenv('REDDIT_USERNAME', 'unknown')}"
)

# Test connection
try:
    subreddit = reddit.subreddit("fantasyfootball")
    print(f"✅ Connected! Subreddit has {subreddit.subscribers} subscribers")
except Exception as e:
    print(f"❌ Connection failed: {e}")
```

## What Happens Without Reddit Credentials?

The app will still function normally, but:
- Reddit sentiment analysis will be unavailable
- The `ff_analyze_reddit_sentiment` tool will return fallback results
- You'll see warnings in the logs about missing credentials

All other features (lineup optimization, draft assistance, etc.) work independently of Reddit API.

