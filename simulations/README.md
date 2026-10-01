# Simulations

Games between frontier models, each called through a LiteLLM proxy. Open a game's `summary.md` for
the seating, roles, what happened and every lie with the liar's stated reason, or load the logs into
your own explorer with `python scripts/games.py load simulations/` to replay them.

- `*-mcp-*` games were played through each game's MCP tools (`get_turn`, `take_action`, ...), with
  models calling them by function calling.
- `secret-hitler-text-*` games came first: each turn the model got a text prompt and answered with
  one JSON object. Their logs are converted to the current format.

All seats ran on each model's default settings. GPT-5.4, Claude Opus 5.5, Qwen3 235B and DeepSeek V4
Pro answered without reasoning tokens; Gemini 3.1 Pro, Grok 4.20 and Kimi K3 reasoned. Read results
as a baseline, not a ranking.

| Game | Kind | Result | Minutes | Lies | Stand-ins |
|---|---|---|---|---|---|
| [prisoners-dilemma-mcp-1](prisoners-dilemma-mcp-1/summary.md) | Iterated Prisoner's Dilemma | GPT-5.4 wins 24 to 19. | 2 | 0 | 0 |
| [secret-hitler-mcp-1](secret-hitler-mcp-1/summary.md) | Secret Hitler | The liberals win: five liberal policies were enacted. | 30 | 0 | 0 |
| [secret-hitler-text-1](secret-hitler-text-1/summary.md) | Secret Hitler | The liberals win: Hitler was executed. | 15 | 3 | 0 |
| [secret-hitler-text-2](secret-hitler-text-2/summary.md) | Secret Hitler | The liberals win: five liberal policies were enacted. | 35 | 0 | 0 |
| [secret-hitler-text-3](secret-hitler-text-3/summary.md) | Secret Hitler | The liberals win: five liberal policies were enacted. | 14 | 4 | 0 |
| [texas-holdem-mcp-1](texas-holdem-mcp-1/summary.md) | Texas Hold'em | DeepSeek V4 Pro wins with 2760 chips after 10 hands. | 12 | 0 | 0 |
| [texas-holdem-native-1](texas-holdem-native-1/summary.md) | Texas Hold'em | Claude Opus 5.5 wins with 2820 chips after 10 hands. | 12 | 0 | 0 |

## By player

| Player | Games | Wins | Roles | Lies / claims | Stand-ins |
|---|---|---|---|---|---|
| DeepSeek V4 Pro | 6 | 5 | 4♥ 3♣ 1, Liberal 4, Q♦ J♠ 1 | 0 / 11 | 0 |
| Claude Opus 5.5 | 7 | 4 | 3♣ 9♥ 1, Fascist 1, J♥ Q♠ 1, Liberal 3, – 1 | 1 / 13 | 0 |
| GPT-5.4 | 7 | 3 | 6♣ 4♥ 1, 8♣ 5♦ 1, Fascist 1, Hitler 1, Liberal 2, – 1 | 2 / 8 | 0 |
| Gemini 3.1 Pro | 6 | 3 | 4♦ A♦ 1, A♣ J♣ 1, Hitler 1, Liberal 3 | 0 / 11 | 0 |
| Kimi K3 | 6 | 2 | A♥ 9♠ 1, Fascist 1, Hitler 1, Liberal 2, – 1 | 1 / 12 | 0 |
| Grok 4.20 | 6 | 1 | 8♥ A♥ 1, Fascist 3, Liberal 1, – 1 | 2 / 7 | 0 |
| Qwen3 235B | 4 | 1 | Fascist 2, Hitler 1, Liberal 1 | 1 / 4 | 0 |
