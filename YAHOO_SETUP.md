# Yahoo API Credentials Setup Guide

This guide will walk you through obtaining all necessary Yahoo credentials for the Fantasy Football MCP server.

## Overview of Required Credentials

You'll need the following credentials:
- **Client ID** (Consumer Key)
- **Client Secret** (Consumer Secret)  
- **Access Token** (obtained via OAuth)
- **Refresh Token** (obtained via OAuth)
- **GUID** (your Yahoo user identifier)

## Step 1: Create a Yahoo Developer Account

1. **Navigate to Yahoo Developer Portal**
   - Go to https://developer.yahoo.com/
   - Click "Apps" in the top menu
   - Sign in with your Yahoo account (the same one you use for Fantasy Sports)

2. **Verify Your Account**
   - If prompted, verify your phone number
   - Accept the Yahoo Developer Network terms

## Step 2: Create Your App

1. **Click "Create an App"** button

2. **Fill in App Information** (CRITICAL - must be exact):

   ### Application Name
   - Choose a unique name (e.g., "John's Fantasy Assistant 2025")
   - If name is taken, add numbers or make it more specific
   - ⚠️ This name must be unique across ALL Yahoo apps

   ### Description
   - Example: "Personal fantasy football management tool for lineup optimization"
   - Must be at least 25 characters

   ### Home Page URL
   - Can use: `http://localhost:8000`
   - This is just for reference, not actually used

   ### Redirect URI(s)
   - Enter: `https://localhost:8000/callback`
   - ⚠️ **CRITICAL**: 
     - Must be HTTPS not HTTP for localhost
     - Must NOT have trailing slash
     - Must match EXACTLY in your code

   ### OAuth Client Type
   - Pick Confidential Client - Choose for traditional apps.

   ### API Permissions
   - Find "Fantasy Sports" in the list
   - Click the checkbox for **"Read"** permission
   - ✅ Only select Read (not Read/Write)

4. **Create the App**
   - Click "Create App" button
   - You'll see a confirmation screen

## Step 3: Get Your Client Credentials

After creating your app, you'll see an "App Details" page:

1. **Find Your Credentials**:
   ```
   Client ID (Consumer Key):     dj0yJmk9XXXXXXXXX...
   Client Secret (Consumer Secret): XXXXXXXXXXXXXXXXX
   App ID:                       XXXXXXXX
   ```

2. **IMPORTANT**: Copy these immediately to a safe place:
   - **Client ID**: This is your `YAHOO_CLIENT_ID` or `YAHOO_CONSUMER_KEY`
   - **Client Secret**: This is your `YAHOO_CLIENT_SECRET` or `YAHOO_CONSUMER_SECRET`

3. **Security Warning**: 
   - Never share these credentials
   - Never commit them to version control
   - Store them securely

## Step 4: Understanding the Credentials

### Client ID vs Consumer Key
- These are the SAME thing
- Yahoo uses both terms interchangeably
- Format: Usually starts with `dj0yJmk9...`

### Client Secret vs Consumer Secret  
- These are the SAME thing
- This is your app's password
- Format: Usually a 40-character hexadecimal string

### Common Confusion Points
- ❌ Don't confuse App ID with Client ID
- ❌ Don't use the base64-encoded version (with extra parameters)
- ✅ Use the plain Client ID shown on the app page

## Step 5: OAuth Authentication

After setting up your app, you need to authenticate to get tokens:

1. **Update your `.env` file**:
   ```env
   YAHOO_CLIENT_ID=your_client_id_here
   YAHOO_CLIENT_SECRET=your_client_secret_here
   ```

2. **Run the authentication script**:
   ```bash
   python utils/setup_yahoo_auth.py
   ```

3. **What happens**:
   - Browser opens to Yahoo login
   - Log in with your Fantasy Sports account
   - Authorize the app
   - You'll be redirected to localhost (page won't load, that's OK)
   - Copy the `code` parameter from the URL
   - Paste it into the terminal

4. **Result**:
   - Script will obtain Access Token and Refresh Token
   - These will be saved to your `.env` file
   - Your GUID will also be retrieved

## Step 6: Understanding Token Expiration

### Access Token
- **Expires**: Every 1 hour
- **Auto-refresh**: The MCP server handles this automatically
- **Manual refresh**: `python utils/refresh_token.py`

### Refresh Token
- **Expires**: After 60 days of non-use
- **Important**: Use your app at least once every 60 days
- **If expired**: Run `python utils/reauth_yahoo.py` to re-authenticate

## Troubleshooting Common Issues

### "Invalid Client" Error
- **Cause**: Client ID is wrong or malformed
- **Fix**: 
  - Check you're using the plain Client ID (not base64 encoded)
  - Verify no extra spaces or characters
  - Ensure app is still active in Yahoo Developer Portal

### "Redirect URI Mismatch"
- **Cause**: Redirect URI doesn't match exactly
- **Fix**:
  - Must be `http://localhost:8000/callback` (exactly)
  - Check for trailing slashes (remove them)
  - Verify it matches in both Yahoo app settings and your code

### "Unauthorized" Error (401)
- **Cause**: Token expired
- **Fix**: Run `python utils/refresh_token.py`

### No Leagues Showing
- **Cause**: GUID not set or incorrect
- **Fix**: 
  - Re-run `python utils/setup_yahoo_auth.py`
  - Check `.env` has YAHOO_GUID populated
  - Verify you're logged in with account that has leagues

## Quick Checklist

Before running the MCP server, verify you have:

- [ ] Created Yahoo Developer App
- [ ] Selected "Installed Application" type
- [ ] Set redirect URI to `http://localhost:8000/callback`
- [ ] Enabled "Fantasy Sports - Read" permission
- [ ] Copied Client ID and Client Secret
- [ ] Run `setup_yahoo_auth.py` successfully
- [ ] `.env` file contains all credentials
- [ ] Tested with `python utils/refresh_token.py`

## Security Best Practices

1. **Never commit credentials**
   - Add `.env` to `.gitignore`
   - Use environment variables

2. **Rotate tokens regularly**
   - Refresh tokens when needed
   - Re-authenticate if suspicious activity

3. **Limit permissions**
   - Only use "Read" permission
   - Don't enable unnecessary APIs

4. **Monitor usage**
   - Check Yahoo Developer dashboard
   - Watch for unusual activity

## Need Help?

If you're still having issues:

1. **Verify all credentials** are copied correctly
2. **Check Yahoo app status** (might need 5-10 minutes to activate)
3. **Try re-authentication** with `python utils/reauth_yahoo.py`
4. **Check the logs** for specific error messages
5. **Open an issue** on GitHub with error details (but never include credentials!)

## Important URLs

- Yahoo Developer Portal: https://developer.yahoo.com/apps/
- Yahoo API Documentation: https://developer.yahoo.com/fantasysports/guide/
- OAuth 2.0 Flow: https://developer.yahoo.com/oauth2/guide/
