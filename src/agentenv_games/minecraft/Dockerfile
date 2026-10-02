# The Minecraft env: a Paper server with a pre-generated world, Mineflayer bots behind a local bridge, the
# env server that agent-env deploys (MCP tools per seat on 18765), and headless Chromium with ffmpeg to
# record the camera's view. Building it accepts the Minecraft EULA (https://aka.ms/MinecraftEULA) for this
# server.
FROM node:22-bookworm-slim AS node

FROM eclipse-temurin:21-jre-noble
ARG PAPER_URL=https://fill-data.papermc.io/v1/objects/5ee4f542f628a14c644410b08c94ea42e772ef4d29fe92973636b6813d4eaffc/paper-1.21.4-232.jar
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir "mcp>=1.25,<2" starlette pydantic httpx uvicorn \
    playwright==1.63.0
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && /opt/venv/bin/playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 player && mkdir -p /srv/mc /app && chown player /srv/mc /app
USER player
WORKDIR /srv/mc
RUN curl -fsSL -o paper.jar "$PAPER_URL" && echo "eula=true" > eula.txt
COPY --chown=player agentenv_games/minecraft/server.properties /srv/mc/server.properties
COPY --chown=player agentenv_games/minecraft/pregen.sh /srv/mc/pregen.sh
RUN sh /srv/mc/pregen.sh world && cp server.properties /tmp/server.properties \
    && printf '%s\n' 'level-type=minecraft\:flat' \
       'generator-settings={"layers"\:[{"block"\:"minecraft\:air","height"\:1}],"biome"\:"minecraft\:the_void"}' >> server.properties \
    && sh /srv/mc/pregen.sh skyblock && cp /tmp/server.properties server.properties
COPY --chown=player agentenv_games/minecraft/bridge/package.json /app/bridge/package.json
RUN cd /app/bridge && npm install --omit=dev --no-audit --no-fund --registry=https://registry.npmjs.org
COPY --chown=player agentenv_protocol /app/agentenv_protocol
COPY --chown=player agentenv_games /app/agentenv_games
RUN cp /app/agentenv_games/minecraft/bridge/bridge.js /app/bridge/bridge.js
ENV PYTHONPATH=/app PATH=/opt/venv/bin:$PATH
CMD ["sh", "/app/agentenv_games/minecraft/start.sh"]
