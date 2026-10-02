Games between frontier models, each called through a LiteLLM proxy. Open a game's `summary.md` for
the seating, roles, what happened and every lie with the liar's stated reason, or load the logs into
your own explorer with `python scripts/games.py load simulations/` to replay them.

- `*-mcp-*` games were played through each game's MCP tools (`get_turn`, `take_action`, ...), with
  models calling them by function calling.
- `minecraft-*-native-*` sessions ran in a real Minecraft server deployed as a native agent-env env, with
  every player acting at once (`play_world`). `minecraft-skyblock-native-1` (bridge the void, light a
  nether portal) and `minecraft-pickaxes-native-1` include the env camera's recording as `video.mp4`;
  `python scripts/games.py load simulations/` puts it back, and the explorer plays it in step with the log.
- `secret-hitler-text-*` games came first: each turn the model got a text prompt and answered with
  one JSON object. Their logs are converted to the current format.

All seats ran on each model's default settings. GPT-5.4, Claude Opus 5.5, Qwen3 235B and DeepSeek V4
Pro answered without reasoning tokens; Gemini 3.1 Pro, Grok 4.20 and Kimi K3 reasoned. Read results
as a baseline, not a ranking.
