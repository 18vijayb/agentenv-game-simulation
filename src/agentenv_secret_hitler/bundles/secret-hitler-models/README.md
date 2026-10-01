Seven models play Secret Hitler against each other, three games with rotated seating and fixed
seeds so each model draws different roles. Each seat calls its model through the `[model]` endpoint
(`LITELLM_BASE_URL` / `LITELLM_API_KEY`), so no sandbox or Docker is needed. The model ids are the
ones a LiteLLM proxy serves; change them to what yours serves.

Run one game with `agent-env run secret-hitler-models --task game-1`. A game takes 30 to 60 minutes.
