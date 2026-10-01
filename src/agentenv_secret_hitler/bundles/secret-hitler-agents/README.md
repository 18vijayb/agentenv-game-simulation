Three A2A agents and four bots play one game of Secret Hitler. Each agent runs in its own sandbox
and is told only what its seat may know.

The agents are the default A2A agent (`[agents] default_a2a_agent_id`), so a model endpoint must be
configured. To seat specific agents, copy this folder and give each `deploy_agent` step an
`a2a_agent_id`, for example one for Claude Code and one for Grok Build, and rename the seats to
match. A game makes roughly 150 agent turns, so expect it to take a while; watch it live with
`agent-env up` at http://localhost:8234/secret-hitler.
