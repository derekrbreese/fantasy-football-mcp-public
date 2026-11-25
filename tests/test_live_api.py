#!/usr/bin/env python3
"""
Live API Testing Script for Phase 2b Refactoring Verification

Tests all MCP tool handlers against real Yahoo Fantasy Sports API.
Organized by handler domain to match refactoring architecture.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

# Import the legacy call_tool function
from fantasy_football_multi_league import call_tool

# Color codes for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


class LiveAPITester:
    def __init__(self):
        self.results = []
        self.total_time = 0
        self.api_calls = 0
        self.league_key = None
        self.team_key = None
        self.teams = []
        self.current_week = None

    async def setup_context(self):
        """Get league context for testing."""
        print(f"\n{CYAN}🔍 Setting up test context...{RESET}")
        
        # Get leagues
        result, success = await self.run_test(
            "Get leagues for context",
            "ff_get_leagues",
            {},
            verbose=False
        )
        
        if success and result:
            try:
                data = json.loads(result[0].text)
                # Handle both status-based and direct league responses
                leagues = data.get("leagues", [])
                if leagues or (data.get("status") == "success" and data.get("leagues")):
                    if not leagues:
                        leagues = data.get("leagues", [])
                    if leagues:
                        first_league = leagues[0]
                        self.league_key = first_league.get("key")
                        self.current_week = first_league.get("current_week", 1)
                        print(f"  {GREEN}✓{RESET} Using league: {first_league.get('name')} ({self.league_key})")
                        print(f"  {GREEN}✓{RESET} Current week: {self.current_week}")
                        
                        # Get teams in this league
                        teams_result, teams_success = await self.run_test(
                            "Get teams for context",
                            "ff_get_teams",
                            {"league_key": self.league_key},
                            verbose=False
                        )
                        
                        if teams_success and teams_result:
                            teams_data = json.loads(teams_result[0].text)
                            # Handle both status-based and direct team responses
                            self.teams = teams_data.get("teams", [])
                            if self.teams and len(self.teams) >= 1:
                                self.team_key = self.teams[0].get("team_key")
                                print(f"  {GREEN}✓{RESET} Found {len(self.teams)} teams")
                                print(f"  {GREEN}✓{RESET} Using team: {self.teams[0].get('name')} ({self.team_key})")
                        
                        return True
            except Exception as e:
                print(f"  {RED}✗{RESET} Error parsing context: {str(e)}")
        
        print(f"  {YELLOW}⚠{RESET} Could not set up full context, some tests may fail")
        return False

    async def test_category(self, name: str, tests: List):
        """Run a category of tests."""
        print(f"\n{BLUE}{BOLD}📋 Testing {name}{RESET}")
        print("─" * 60)
        
        for test_func in tests:
            await test_func()
            # Small delay between tests to be nice to the API
            await asyncio.sleep(0.2)

    async def run_test(
        self,
        name: str,
        tool_name: str,
        arguments: Dict[str, Any],
        verbose: bool = True
    ) -> Tuple[Optional[Any], bool]:
        """Execute a single tool test."""
        start = time.time()
        try:
            result = await call_tool(tool_name, arguments)
            elapsed = time.time() - start

            # Check if result indicates success
            text = ""
            if result:
                if isinstance(result, list) and len(result) > 0:
                    if hasattr(result[0], "text"):
                        text = result[0].text
                    else:
                        text = str(result[0])
                else:
                    text = str(result)

            # Parse JSON to check status
            success = False
            error_msg = None
            try:
                if text:
                    data = json.loads(text)
                    # Check for explicit status field or detect success by presence of data
                    if data.get("status") == "success":
                        success = True
                    elif data.get("status") == "error":
                        success = False
                        error_msg = data.get("message", "Unknown error")
                    else:
                        # No status field - consider it success if we got data back
                        success = bool(data and not data.get("error"))
                        if not success:
                            error_msg = data.get("error") or data.get("message", "Unknown error")
            except json.JSONDecodeError:
                # If not JSON, check for error keywords
                success = "error" not in text.lower()

            if verbose:
                status = f"{GREEN}✓{RESET}" if success else f"{RED}✗{RESET}"
                print(f"  {status} {name} ({elapsed:.2f}s)")
                
                if not success and error_msg:
                    print(f"    {YELLOW}└─ {error_msg}{RESET}")

            self.results.append({
                "name": name,
                "success": success,
                "time": elapsed,
                "tool": tool_name,
                "error": error_msg if not success else None
            })
            self.total_time += elapsed
            self.api_calls += 1

            return result, success

        except Exception as e:
            elapsed = time.time() - start
            error_msg = str(e)
            
            if verbose:
                print(f"  {RED}✗{RESET} {name} ({elapsed:.2f}s)")
                print(f"    {YELLOW}└─ Exception: {error_msg[:80]}{RESET}")
            
            self.results.append({
                "name": name,
                "success": False,
                "time": elapsed,
                "tool": tool_name,
                "error": error_msg
            })
            self.total_time += elapsed
            self.api_calls += 1
            
            return None, False

    def print_summary(self):
        """Print test summary."""
        passed = sum(1 for r in self.results if r["success"])
        failed = len(self.results) - passed

        print(f"\n{'═' * 60}")
        print(f"{BLUE}{BOLD}📊 Test Summary{RESET}")
        print(f"{'═' * 60}")
        print(f"Total Tests:    {len(self.results)}")
        print(f"{GREEN}✓ Passed:       {passed}{RESET}")
        if failed > 0:
            print(f"{RED}✗ Failed:       {failed}{RESET}")
        print(f"⏱  Total Time:   {self.total_time:.2f}s")
        print(f"📡 API Calls:   {self.api_calls}")
        
        # Calculate pass rate
        pass_rate = (passed / len(self.results) * 100) if self.results else 0
        print(f"📈 Pass Rate:    {pass_rate:.1f}%")

        if failed > 0:
            print(f"\n{YELLOW}{BOLD}Failed Tests:{RESET}")
            for r in self.results:
                if not r["success"]:
                    print(f"  {RED}✗{RESET} {r['name']}")
                    if r.get("error"):
                        print(f"    └─ {r['error'][:100]}")

    def generate_results_doc(self) -> str:
        """Generate markdown results document."""
        passed = sum(1 for r in self.results if r["success"])
        failed = len(self.results) - passed
        pass_rate = (passed / len(self.results) * 100) if self.results else 0
        
        doc = f"""# Live API Test Results - Phase 2b Refactoring

**Test Date:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}  
**Branch:** consolidate-fastmcp  
**Python Version:** {sys.version.split()[0]}

## Executive Summary

- **Total Tests:** {len(self.results)}
- **✓ Passed:** {passed}
- **✗ Failed:** {failed}
- **Pass Rate:** {pass_rate:.1f}%
- **Total Time:** {self.total_time:.2f}s
- **API Calls:** {self.api_calls}

"""

        # Determine assessment
        if pass_rate >= 90:
            assessment = "✅ **EXCELLENT** - Refactoring is solid, ready to merge"
        elif pass_rate >= 80:
            assessment = "✅ **GOOD** - Most handlers work correctly, minor issues to investigate"
        elif pass_rate >= 70:
            assessment = "⚠️ **FAIR** - Several issues found, needs review"
        else:
            assessment = "❌ **NEEDS WORK** - Significant issues, refactoring needs fixes"
        
        doc += f"**Assessment:** {assessment}\n\n"

        # Group results by category
        categories = {
            "Admin Handlers": [],
            "League Handlers": [],
            "Roster Handlers": [],
            "Matchup Handlers": [],
            "Player Handlers": [],
            "Draft Handlers": [],
            "Analytics Handlers": []
        }
        
        for r in self.results:
            tool = r["tool"]
            if "api_status" in tool or "clear_cache" in tool or "refresh_token" in tool:
                categories["Admin Handlers"].append(r)
            elif "league" in tool or "standings" in tool or "teams" in tool:
                categories["League Handlers"].append(r)
            elif "roster" in tool:
                categories["Roster Handlers"].append(r)
            elif "matchup" in tool or "compare" in tool or "build_lineup" in tool:
                categories["Matchup Handlers"].append(r)
            elif "player" in tool or "waiver" in tool:
                categories["Player Handlers"].append(r)
            elif "draft" in tool:
                categories["Draft Handlers"].append(r)
            elif "reddit" in tool or "sentiment" in tool:
                categories["Analytics Handlers"].append(r)

        doc += "## Detailed Results by Category\n\n"
        
        for category, tests in categories.items():
            if not tests:
                continue
                
            passed_in_cat = sum(1 for t in tests if t["success"])
            doc += f"### {category} ({passed_in_cat}/{len(tests)} passed)\n\n"
            
            for test in tests:
                status = "✓" if test["success"] else "✗"
                doc += f"- **{status} {test['name']}** - {test['time']:.2f}s\n"
                doc += f"  - Tool: `{test['tool']}`\n"
                
                if not test["success"] and test.get("error"):
                    doc += f"  - Error: {test['error']}\n"
                
                doc += "\n"

        # Performance statistics
        doc += "## Performance Metrics\n\n"
        doc += f"- **Average Response Time:** {self.total_time / len(self.results):.2f}s\n"
        doc += f"- **Fastest Test:** {min(r['time'] for r in self.results):.2f}s ({min(self.results, key=lambda x: x['time'])['name']})\n"
        doc += f"- **Slowest Test:** {max(r['time'] for r in self.results):.2f}s ({max(self.results, key=lambda x: x['time'])['name']})\n"
        doc += "\n"

        # Recommendations
        doc += "## Recommendations\n\n"
        
        if pass_rate >= 90:
            doc += "✅ **Ready to merge** - All critical handlers working correctly\n"
            doc += "- Consider merging consolidate-fastmcp branch to main\n"
            doc += "- Update documentation with any new features\n"
        elif pass_rate >= 80:
            doc += "⚠️ **Review failures** - Most handlers work but some issues found\n"
            doc += "- Investigate failed tests to determine if they're code or data issues\n"
            doc += "- Fix any critical failures before merging\n"
        else:
            doc += "❌ **Fix issues** - Significant problems detected\n"
            doc += "- Review handler implementations\n"
            doc += "- Check dependency injection\n"
            doc += "- Verify API integration\n"
        
        doc += "\n---\n\n"
        doc += f"*Generated by `test_live_api.py` on {datetime.now().strftime('%Y-%m-%d')}*\n"
        
        return doc


async def main():
    """Main test execution."""
    load_dotenv()

    print(f"{BLUE}{BOLD}🚀 Live API Testing - Phase 2b Verification{RESET}")
    print("═" * 60)
    print(f"Testing all MCP handlers against Yahoo Fantasy API")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═" * 60)

    # Verify environment
    required_vars = ["YAHOO_CONSUMER_KEY", "YAHOO_ACCESS_TOKEN", "YAHOO_GUID"]
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        print(f"\n{RED}✗ Missing required environment variables:{RESET}")
        for var in missing:
            print(f"  - {var}")
        print(f"\n{YELLOW}Please ensure .env file is configured correctly{RESET}")
        return

    print(f"\n{GREEN}✓ Environment variables configured{RESET}")

    tester = LiveAPITester()

    # Setup test context
    await tester.setup_context()
    
    if not tester.league_key:
        print(f"\n{RED}✗ Could not discover leagues - cannot continue tests{RESET}")
        return

    # Test Admin Handlers
    async def test_admin():
        await tester.test_category("Admin Handlers", [
            lambda: tester.run_test(
                "ff_get_api_status",
                "ff_get_api_status",
                {}
            ),
            lambda: tester.run_test(
                "ff_clear_cache",
                "ff_clear_cache",
                {}
            ),
            lambda: tester.run_test(
                "ff_refresh_token",
                "ff_refresh_token",
                {}
            ),
        ])

    # Test League Handlers
    async def test_leagues():
        await tester.test_category("League Handlers", [
            lambda: tester.run_test(
                "ff_get_leagues",
                "ff_get_leagues",
                {}
            ),
            lambda: tester.run_test(
                "ff_get_league_info",
                "ff_get_league_info",
                {"league_key": tester.league_key}
            ),
            lambda: tester.run_test(
                "ff_get_standings",
                "ff_get_standings",
                {"league_key": tester.league_key}
            ),
            lambda: tester.run_test(
                "ff_get_teams",
                "ff_get_teams",
                {"league_key": tester.league_key}
            ),
        ])

    # Test Roster Handlers
    async def test_rosters():
        if not tester.team_key:
            print(f"\n{YELLOW}⚠ Skipping roster tests - no team key available{RESET}")
            return
            
        await tester.test_category("Roster Handlers", [
            lambda: tester.run_test(
                "ff_get_roster (basic)",
                "ff_get_roster",
                {
                    "league_key": tester.league_key,
                    "team_key": tester.team_key,
                    "data_level": "basic"
                }
            ),
            lambda: tester.run_test(
                "ff_get_roster (standard)",
                "ff_get_roster",
                {
                    "league_key": tester.league_key,
                    "team_key": tester.team_key,
                    "data_level": "standard"
                }
            ),
            lambda: tester.run_test(
                "ff_get_roster (full)",
                "ff_get_roster",
                {
                    "league_key": tester.league_key,
                    "team_key": tester.team_key,
                    "data_level": "full"
                }
            ),
        ])

    # Test Matchup Handlers
    async def test_matchups():
        if not tester.team_key:
            print(f"\n{YELLOW}⚠ Skipping matchup tests - no team key available{RESET}")
            return
            
        tests = [
            lambda: tester.run_test(
                "ff_get_matchup",
                "ff_get_matchup",
                {
                    "league_key": tester.league_key,
                    "week": tester.current_week
                }
            ),
            lambda: tester.run_test(
                "ff_build_lineup",
                "ff_build_lineup",
                {
                    "league_key": tester.league_key,
                    "week": tester.current_week,
                    "strategy": "balanced"
                }
            ),
        ]
        
        # Add compare_teams if we have at least 2 teams
        if len(tester.teams) >= 2:
            tests.append(
                lambda: tester.run_test(
                    "ff_compare_teams",
                    "ff_compare_teams",
                    {
                        "league_key": tester.league_key,
                        "team_key_a": tester.teams[0]["team_key"],
                        "team_key_b": tester.teams[1]["team_key"]
                    }
                )
            )
        
        await tester.test_category("Matchup Handlers", tests)

    # Test Player Handlers
    async def test_players():
        await tester.test_category("Player Handlers", [
            lambda: tester.run_test(
                "ff_get_players (QB)",
                "ff_get_players",
                {
                    "league_key": tester.league_key,
                    "position": "QB",
                    "status": "FA",
                    "count": 10
                }
            ),
            lambda: tester.run_test(
                "ff_get_waiver_wire",
                "ff_get_waiver_wire",
                {
                    "league_key": tester.league_key,
                    "position": "RB"
                }
            ),
        ])

    # Test Draft Handlers
    async def test_drafts():
        await tester.test_category("Draft Handlers", [
            lambda: tester.run_test(
                "ff_get_draft_results",
                "ff_get_draft_results",
                {"league_key": tester.league_key}
            ),
            lambda: tester.run_test(
                "ff_get_draft_rankings",
                "ff_get_draft_rankings",
                {
                    "league_key": tester.league_key,
                    "position": "RB"
                }
            ),
            lambda: tester.run_test(
                "ff_get_draft_recommendation",
                "ff_get_draft_recommendation",
                {
                    "league_key": tester.league_key,
                    "strategy": "balanced",
                    "num_recommendations": 5
                }
            ),
            lambda: tester.run_test(
                "ff_analyze_draft_state",
                "ff_analyze_draft_state",
                {
                    "league_key": tester.league_key,
                    "strategy": "balanced"
                }
            ),
        ])

    # Test Analytics Handlers (optional - may not have Reddit API)
    async def test_analytics():
        await tester.test_category("Analytics Handlers", [
            lambda: tester.run_test(
                "ff_analyze_reddit_sentiment",
                "ff_analyze_reddit_sentiment",
                {
                    "players": ["Josh Allen", "Christian McCaffrey"],
                    "time_window_hours": 48
                }
            ),
        ])

    # Run all test categories
    await test_admin()
    await test_leagues()
    await test_rosters()
    await test_matchups()
    await test_players()
    await test_drafts()
    await test_analytics()

    # Print summary
    tester.print_summary()

    # Generate results document
    print(f"\n{CYAN}📝 Generating results document...{RESET}")
    results_doc = tester.generate_results_doc()
    
    with open("LIVE_API_TEST_RESULTS.md", "w") as f:
        f.write(results_doc)
    
    print(f"{GREEN}✓ Results saved to LIVE_API_TEST_RESULTS.md{RESET}")

    # Return exit code based on pass rate
    passed = sum(1 for r in tester.results if r["success"])
    pass_rate = (passed / len(tester.results) * 100) if tester.results else 0
    
    if pass_rate >= 90:
        print(f"\n{GREEN}{BOLD}✅ SUCCESS - All tests passed!{RESET}")
        sys.exit(0)
    elif pass_rate >= 80:
        print(f"\n{YELLOW}{BOLD}⚠️  PARTIAL SUCCESS - Most tests passed{RESET}")
        sys.exit(0)
    else:
        print(f"\n{RED}{BOLD}❌ FAILURE - Too many tests failed{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
