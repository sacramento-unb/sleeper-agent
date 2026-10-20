# Sleeper Fantasy League + ChatGPT/Codex Agent

## Objective

Build an assistant/agent that can connect to a Sleeper fantasy league, analyze the user's team, recommend actions, and potentially execute approved team-management actions through the Sleeper web interface.

The preferred architecture is:

1. Use Sleeper's documented/public interfaces for league intelligence and analysis.
2. Use ChatGPT/Codex for reasoning, recommendations, monitoring, and orchestration.
3. Use browser automation only for actions that require writing to Sleeper.
4. Keep high-impact actions behind explicit user approval, at least initially.

---

## Desired Capabilities

The agent should ideally be able to:

- Read the user's Sleeper leagues.
- Read league settings and scoring rules.
- Read rosters.
- Read current matchups.
- Read transactions.
- Read draft picks / draft-pick ownership.
- Identify available/free-agent players.
- Analyze waiver options.
- Recommend FAAB bids.
- Recommend start/sit decisions.
- Optimize the weekly lineup.
- Analyze trades.
- Monitor injuries, depth-chart changes, and relevant NFL news.
- Compare the user's roster against the upcoming opponent.
- Track waiver results and recommend fallback options.

Potential write actions:

- Add a player.
- Drop a player.
- Submit a waiver claim.
- Set FAAB amount.
- Change starters.
- Move players between bench / starting lineup / IR where allowed.
- Propose or accept trades.

---

## Important Sleeper Constraint

Sleeper's documented/public API should be treated primarily as a **read interface**.

Do not assume that documented Sleeper API endpoints exist for:

- changing lineups,
- adding/dropping players,
- submitting waivers,
- accepting trades,
- or modifying league data.

Before implementation, verify the current Sleeper API documentation.

If write operations are unavailable through a supported API, use browser automation against the authenticated Sleeper web application.

Avoid building the core system around undocumented/private Sleeper endpoints unless explicitly accepted as a maintenance risk.

---

## Recommended Architecture

```text
                           +----------------------+
                           |       Sleeper        |
                           |                      |
                           | League / roster      |
                           | Matchups / waivers   |
                           | Transactions         |
                           +----------+-----------+
                                      |
                             Public/read interface
                                      |
                                      v
+----------------------+     +--------------------------+
| External NFL data    | --> | ChatGPT / Codex Agent   |
|                      |     |                          |
| Injuries             |     | Roster analysis         |
| News                 |     | Start/sit decisions     |
| Projections          |     | Waiver rankings         |
| Depth charts         |     | FAAB optimization       |
| Weather              |     | Trade analysis          |
+----------------------+     +------------+-------------+
                                         |
                                  proposed actions
                                         |
                              +----------v-----------+
                              | Approval / Policy    |
                              |                      |
                              | e.g.                 |
                              | "Add Player X $17?"  |
                              +----------+-----------+
                                         |
                                    approved
                                         |
                              +----------v-----------+
                              | Browser Automation   |
                              | Authenticated Sleeper|
                              | web session          |
                              +----------+-----------+
                                         |
                                         v
                                    Sleeper team
```

---

## Suggested Safety Model

Start with these permissions:

### Fully automatic / low risk

- Read league data.
- Read roster.
- Read available players.
- Analyze matchups.
- Generate waiver rankings.
- Generate FAAB suggestions.
- Generate start/sit suggestions.
- Analyze incoming trades.
- Monitor injuries and news.
- Produce weekly reports.

### Require user approval

- Adding players.
- Dropping players.
- Submitting waiver claims.
- Spending FAAB.
- Changing the starting lineup.
- Moving players to/from IR.
- Proposing trades.
- Accepting trades.

Once reliability is established, low-risk lineup changes could optionally become automatic.

Trades and irreversible/high-impact roster moves should remain approval-gated.

---

## Weekly Fantasy GM Workflow

### Tuesday

- Read previous week's results.
- Read current roster.
- Read upcoming matchup.
- Read waiver pool.
- Rank waiver targets.
- Identify drop candidates.
- Recommend FAAB amounts.
- Generate fallback waiver order.

Example output:

```text
Priority 1:
ADD: Player A
DROP: Player B
FAAB: $18

Reason:
- projected starting role
- favorable next 3 weeks
- improves weak RB depth

Fallback:
Player C - $9
Player D - $4
```

---

### Wednesday

After waivers process:

- Read successful and failed claims.
- Detect which recommended targets are still available.
- Recommend free-agent alternatives.
- Recalculate roster needs.

---

### Thursday

Before Thursday Night Football:

- Check players involved in TNF.
- Check injury status.
- Detect questionable/out players.
- Recommend lineup adjustments.

---

### Sunday Morning

Primary lineup-management window:

- Check injury reports.
- Check active/inactive announcements.
- Check weather where relevant.
- Check depth-chart changes.
- Compare floor/upside needs versus opponent.
- Produce optimal starting lineup.

Example:

```text
Current:
WR2: Player X

Recommended:
WR2: Player Y

Reason:
- Player X questionable
- Player Y confirmed active
- Better target projection
```

If write automation is enabled:

1. Present proposed changes.
2. Ask for approval.
3. Apply approved changes through Sleeper web UI.
4. Verify that the lineup saved successfully.

---

### Sunday Afternoon

Check late-window players.

Potential tasks:

- late injury news,
- player ruled inactive,
- flex swap,
- replacement from bench.

Only modify players who are still unlocked.

---

### Monday

Review MNF-specific options and any remaining start/sit decisions.

---

## Trade Workflow

For each proposed trade:

1. Read league scoring.
2. Read both rosters.
3. Evaluate positional needs.
4. Compare rest-of-season value.
5. Evaluate depth impact.
6. Evaluate playoff schedule where useful.
7. Consider draft-pick impact in dynasty/keeper formats.
8. Produce a neutral trade analysis.

Example:

```text
Trade:

Give:
- Player A

Receive:
- Player B
- 2027 2nd-round pick

Roster impact:
RB depth: decreases
WR depth: increases
Future draft capital: increases

Main risk:
Player B has a less stable weekly role.
```

The agent should recommend the implications but should not auto-accept trades unless explicitly authorized.

---

## League Context to Cache

Once connected, persist/cache the following:

```json
{
  "league_id": "...",
  "season": 2026,
  "sport": "nfl",
  "roster_id": 0,
  "user_id": "...",
  "scoring_settings": {},
  "roster_positions": [],
  "waiver_type": "...",
  "faab_budget": 0,
  "playoff_week_start": 0
}
```

Also track:

- current roster,
- starters,
- bench,
- IR,
- taxi squad if applicable,
- FAAB balance,
- draft picks,
- recent transactions,
- waiver priority,
- upcoming opponent.

---

## Agent State

Suggested internal state:

```json
{
  "league": {},
  "team": {},
  "players": {},
  "free_agents": {},
  "matchup": {},
  "transactions": [],
  "injuries": {},
  "recommendations": [],
  "pending_actions": []
}
```

---

## Action Object

All proposed roster changes should use a structured action object.

Example:

```json
{
  "action": "waiver_claim",
  "add_player_id": "PLAYER_ID_A",
  "drop_player_id": "PLAYER_ID_B",
  "faab": 17,
  "reason": "Starting RB opportunity after injury",
  "confidence": 0.82,
  "requires_approval": true
}
```

Lineup change:

```json
{
  "action": "lineup_change",
  "bench_player_id": "PLAYER_ID_X",
  "start_player_id": "PLAYER_ID_Y",
  "slot": "FLEX",
  "reason": "Player Y confirmed active; Player X questionable",
  "requires_approval": true
}
```

---

## Browser Automation

If supported API write endpoints are unavailable, browser automation can execute approved actions.

Recommended flow:

```text
Agent determines desired action
        |
        v
Generate structured proposal
        |
        v
User approval
        |
        v
Open authenticated Sleeper session
        |
        v
Navigate to roster / waivers
        |
        v
Perform action
        |
        v
Verify resulting state
        |
        v
Report success/failure
```

Important:

- Do not store username/password directly in source code.
- Prefer an already authenticated browser session.
- Do not log authentication cookies/tokens.
- Verify every mutation after submission.
- Handle locked players and roster constraints.
- Abort if the UI state differs materially from expectations.

---

## Browser Automation Verification

After any write action:

1. Reload/read the Sleeper page.
2. Confirm target player/state.
3. Confirm roster remains valid.
4. Confirm expected transaction appears.
5. Report exactly what changed.

Example:

```text
Action completed:

Added: Player A
Dropped: Player B
FAAB bid: $17
Status: Pending waiver

Sleeper transaction verified.
```

---

## External Data

Sleeper data alone may not be sufficient for high-quality decisions.

Optional external sources:

- NFL injury reports.
- Team depth charts.
- Snap counts.
- Routes run.
- Target share.
- Carries / red-zone work.
- Weather.
- Betting totals.
- Player projections.
- Fantasy rankings.

Prefer reliable, current sources.

Keep external projections separate from factual league state.

---

## Suggested Project Structure

```text
sleeper-agent/
|
+-- README.md
+-- .env.example
+-- pyproject.toml
|
+-- src/
|   |
|   +-- sleeper/
|   |   +-- client.py
|   |   +-- leagues.py
|   |   +-- rosters.py
|   |   +-- matchups.py
|   |   +-- players.py
|   |   +-- transactions.py
|   |
|   +-- fantasy/
|   |   +-- lineup.py
|   |   +-- waivers.py
|   |   +-- trades.py
|   |   +-- projections.py
|   |   +-- scoring.py
|   |
|   +-- agent/
|   |   +-- state.py
|   |   +-- planner.py
|   |   +-- policies.py
|   |   +-- actions.py
|   |
|   +-- browser/
|       +-- sleeper_ui.py
|       +-- auth.py
|       +-- verification.py
|
+-- tests/
    +-- test_sleeper_client.py
    +-- test_lineup.py
    +-- test_waivers.py
    +-- test_actions.py
```

---

## Suggested Implementation Phases

### Phase 1 - Read-only Sleeper integration

Implement:

- identify Sleeper user,
- list leagues,
- select target league,
- load league configuration,
- load roster,
- load matchups,
- load players,
- load transactions,
- identify free agents.

Deliverable:

```bash
python -m sleeper_agent status
```

Example result:

```text
League: My Fantasy League
Record: 3-1

QB: ...
RB: ...
WR: ...
TE: ...
FLEX: ...

FAAB Remaining: $73

Next opponent:
Team XYZ
```

---

### Phase 2 - Analysis engine

Implement:

```bash
python -m sleeper_agent lineup
python -m sleeper_agent waivers
python -m sleeper_agent matchup
python -m sleeper_agent trade
```

Outputs should be structured and machine-readable where possible.

---

### Phase 3 - LLM/Codex orchestration

Create agent tools such as:

```text
get_league()
get_roster()
get_matchup()
get_free_agents()
get_transactions()
analyze_lineup()
analyze_waivers()
analyze_trade()
```

The LLM should call tools instead of reasoning from stale league snapshots.

---

### Phase 4 - Browser write automation

Implement only after read/analysis workflows are reliable.

Functions could include:

```python
set_starter(...)
add_player(...)
drop_player(...)
submit_waiver(...)
move_to_ir(...)
propose_trade(...)
```

All should default to:

```python
requires_approval = True
```

---

### Phase 5 - Scheduled Fantasy GM

Potential schedule:

```text
Tuesday morning    waiver report
Wednesday morning  waiver results
Thursday afternoon TNF lineup check
Sunday morning     injury + lineup report
Sunday afternoon   late-game status check
Monday afternoon   MNF check
```

---

## ChatGPT Integration

A ChatGPT fantasy integration may be useful for read-only access.

A plugin called **Flaim Fantasy** was identified during the discussion.

It supports fantasy league analysis for:

- Sleeper,
- ESPN,
- Yahoo.

Its advertised capabilities include access to:

- leagues,
- standings,
- rosters,
- matchups,
- drafts,
- draft-pick ownership,
- available players,
- recent transactions,
- player search,
- league information,
- past seasons.

It should be treated as **read-only for league management**.

It does not claim to:

- make trades,
- add/drop players,
- edit lineups,
- modify Sleeper league data.

Therefore it can be useful as an analysis interface but not as the execution layer.

---

## Preferred Final System

Target architecture:

```text
                  SLEEPER
                     |
             league/read data
                     |
                     v
             Sleeper Data Layer
                     |
                     v
+------------> Fantasy Engine <-------------+
|                    |                      |
|                    v                      |
|              Codex / ChatGPT              |
|                    |                      |
|              decision engine              |
|                    |                      |
|             proposed actions              |
|                    |                      |
|             approval policy               |
|                    |                      |
|                    v                      |
|           Browser automation              |
|                    |                      |
+--------------------+----------------------+
                     |
                 SLEEPER UI
```

---

## Initial Codex Task

Start by creating a **read-only implementation**.

Codex should:

1. Verify Sleeper's current official/public API documentation.
2. Create a Python project.
3. Implement a Sleeper API client.
4. Ask for or configure a Sleeper username/user ID.
5. Discover the user's current NFL leagues.
6. Allow selection of one league.
7. Fetch:
   - league settings,
   - rosters,
   - users,
   - matchups,
   - transactions,
   - NFL players.
8. Determine which roster belongs to the user.
9. Identify available players.
10. Produce a structured league summary.
11. Add unit tests.
12. Keep all write/browser actions out of Phase 1.

Expected first milestone:

```bash
python -m sleeper_agent status
```

should produce a complete current snapshot of the user's fantasy team.

---

## Later Codex Task

Once read-only integration works reliably:

> Implement an approval-gated browser automation layer for Sleeper. The agent may propose roster changes, lineup changes, waiver claims, drops, adds, and trades, but it must not submit any mutation until explicit approval is provided. After every mutation, independently verify the resulting Sleeper state and report the result.

---

## Key Design Principle

Use:

```text
API for intelligence
+
LLM for reasoning
+
browser automation for execution
+
human approval for consequential actions
```

This provides the best balance between capability, reliability, and control.
