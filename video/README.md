# Episode videos

Turns a game's event log into a narrated video: the table and cards on the left, and on the right
what each player says and privately thinks, voiced, with commentary between beats.

```bash
cd video && npm install                      # Remotion and React
export LITELLM_BASE_URL=... LITELLM_API_KEY=...
python storyboard.py ../simulations/texas-holdem-mcp-1 cuts/texas-holdem-mcp-1.json
npx remotion studio                          # preview and scrub in the browser
npx remotion render Episode out/episode.mp4  # about 5 minutes of video
```

- **Cut list** (`cuts/<game>.json`): which events make the episode, in order, plus narrator lines.
  `{"seq": N}` takes event N as it happened (a spoken line, a private thought or an action);
  `{"seq": N, "text": ...}` trims it, and should only ever cut the model's words, marking cuts with
  "…"; `{"narrate": ..., "seq": N}` adds commentary over the table as of event N. `cast` gives each
  player a voice, a persona, a colour, a label and a monogram.
- **Voices**: `openai/gpt-4o-mini-tts-2025-12-15`, one voice and persona per player. Thoughts are
  whispered and filtered into an inner voice; all lines are tightened to 1.2 to 1.25 times speed with
  the pitch kept. Audio is cached by line, so editing a cut list only generates new lines.
- **Rendering**: Remotion (React to MP4). `src/Episode.tsx` draws the table from each event's
  recorded state, so every card, bet and chip count is the real one from the game.
