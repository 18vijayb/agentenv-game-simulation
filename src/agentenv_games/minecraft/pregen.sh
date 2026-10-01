# Start the server once at build time, so the jar is patched and the spawn area generated before any
# deploy, then stop it.
set -e
mkfifo /tmp/console
java -Xmx2G -jar paper.jar --nogui < /tmp/console > /tmp/pregen.log 2>&1 &
pid=$!
exec 3>/tmp/console
for i in $(seq 1 300); do
  if grep -q 'Done (' /tmp/pregen.log; then break; fi
  if ! kill -0 "$pid" 2>/dev/null; then cat /tmp/pregen.log; exit 1; fi
  sleep 1
done
grep -q 'Done (' /tmp/pregen.log || { cat /tmp/pregen.log; exit 1; }
echo stop >&3
wait "$pid"
rm -f /tmp/console
tail -3 /tmp/pregen.log
