# Container entrypoint: the Minecraft server, the bot bridge, then the env server in the foreground.
cd /srv/mc
mkfifo /tmp/console 2>/dev/null
sleep infinity > /tmp/console &
world=world
[ "$ENVIRONMENT_NAME" = minecraft_skyblock ] && world=skyblock
java -Xms1G -Xmx${MC_MEMORY:-2G} -jar paper.jar --nogui --world "$world" < /tmp/console > /tmp/paper.log 2>&1 &
node /app/bridge/bridge.js > /tmp/bridge.log 2>&1 &
exec python3 -m agentenv_games.minecraft.server
