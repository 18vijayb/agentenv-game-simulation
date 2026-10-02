// Mineflayer bots behind a local JSON API for the env server: one bot per seat, high-level skills
// (go to, collect, craft, place, give, chat) and a world snapshot. Listens on 127.0.0.1 only.
'use strict'

const http = require('http')
const mineflayer = require('mineflayer')
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder')
const collectBlock = require('mineflayer-collectblock').plugin
const { Rcon } = require('rcon-client')
const { Vec3 } = require('vec3')

const MC_HOST = process.env.MC_HOST || '127.0.0.1'
const MC_PORT = Number(process.env.MC_PORT || 25565)
const MC_VERSION = process.env.MC_VERSION || '1.21.4'
const RCON_PASSWORD = process.env.RCON_PASSWORD || 'agentenv'
const PORT = Number(process.env.BRIDGE_PORT || 3100)
const ACTION_TIMEOUT_MS = Number(process.env.ACTION_TIMEOUT_MS || 100000)
const AIR = new Set(['air', 'cave_air', 'void_air'])
const CAMERA_NAME = process.env.CAMERA_NAME || 'Camera'

const bots = new Map() // username -> {bot, action, viewerPort}
const chat = [] // every chat line any bot heard, once
let rcon = null

function sleep (ms) { return new Promise(resolve => setTimeout(resolve, ms)) }

function withTimeout (promise, ms, onTimeout) {
  let timer
  return Promise.race([promise, new Promise((resolve, reject) => {
    timer = setTimeout(() => { onTimeout(); reject(new Error(`gave up after ${Math.round(ms / 1000)} s`)) }, ms)
  })]).finally(() => clearTimeout(timer))
}

async function command (cmd) {
  // connect lazily, and keep a connection only once it is up, so commands before the server starts just fail
  if (!rcon) {
    const r = new Rcon({ host: MC_HOST, port: 25575, password: RCON_PASSWORD, timeout: 5000 })
    await r.connect()
    r.on('end', () => { if (rcon === r) rcon = null })
    rcon = r
  }
  return rcon.send(cmd)
}

function pos (v) { return [Math.floor(v.x), Math.floor(v.y), Math.floor(v.z)] }

function inventory (bot) {
  const out = {}
  for (const item of bot.inventory.items()) out[item.name] = (out[item.name] || 0) + item.count
  return out
}

function blockIds (bot, name) {
  // an exact block name, or a suffix such as "log" for every *_log
  const reg = bot.registry
  if (reg.blocksByName[name]) return [reg.blocksByName[name].id]
  return Object.values(reg.blocksByName).filter(b => b.name.endsWith('_' + name)).map(b => b.id)
}

function itemByName (bot, name) {
  const reg = bot.registry
  if (reg.itemsByName[name]) return [reg.itemsByName[name]]
  return Object.values(reg.itemsByName).filter(i => i.name.endsWith('_' + name))
}

function observe (bot) {
  const here = bot.entity.position
  const ids = Object.values(bot.registry.blocksByName).filter(b => !AIR.has(b.name)).map(b => b.id)
  const seen = {}
  for (const p of bot.findBlocks({ matching: ids, maxDistance: 16, count: 4000 })) {
    const b = bot.blockAt(p)
    if (!b) continue
    const d = p.distanceTo(here)
    const s = seen[b.name] || (seen[b.name] = { count: 0, nearest: null, distance: Infinity })
    s.count++
    if (d < s.distance) { s.distance = d; s.nearest = [p.x, p.y, p.z] }
  }
  const blocks = Object.entries(seen).sort((a, b) => a[1].distance - b[1].distance).slice(0, 25)
    .map(([name, s]) => ({ name, count: s.count, nearest: s.nearest, distance: Math.round(s.distance) }))
  const entities = Object.values(bot.entities)
    .filter(e => e !== bot.entity && e.username !== CAMERA_NAME && e.position.distanceTo(here) < 32 && (e.type === 'player' || e.type === 'mob' || e.type === 'animal' || e.type === 'hostile'))
    .map(e => ({ name: e.username || e.name, kind: e.type, at: pos(e.position), distance: Math.round(e.position.distanceTo(here)) }))
    .sort((a, b) => a.distance - b.distance).slice(0, 12)
  return {
    you: bot.username, at: pos(here), health: Math.round(bot.health), food: bot.food,
    time: bot.time.isDay ? 'day' : 'night', inventory: inventory(bot),
    holding: bot.heldItem ? bot.heldItem.name : null, nearby_blocks: blocks, nearby: entities
  }
}

async function goTo (bot, args) {
  let goal
  if (args.player) {
    const other = bot.players[args.player] && bot.players[args.player].entity
    if (!other) throw new Error(`can't see a player named ${args.player} nearby; known players: ${Object.keys(bot.players).join(', ')}`)
    goal = new goals.GoalNear(other.position.x, other.position.y, other.position.z, 2)
  } else if (args.block) {
    const ids = blockIds(bot, args.block)
    if (!ids.length) throw new Error(`no block called ${args.block}`)
    const found = bot.findBlock({ matching: ids, maxDistance: 64 })
    if (!found) throw new Error(`no ${args.block} within 64 blocks; explore somewhere else`)
    goal = new goals.GoalNear(found.position.x, found.position.y, found.position.z, 2)
  } else if (args.x !== undefined && args.z !== undefined) {
    goal = args.y === undefined || args.y === null
      ? new goals.GoalNearXZ(Number(args.x), Number(args.z), 1)
      : new goals.GoalNear(Number(args.x), Number(args.y), Number(args.z), 1)
  } else {
    throw new Error('give a player, a block, or x and z (y optional)')
  }
  await bot.pathfinder.goto(goal)
  return `You are at ${pos(bot.entity.position).join(' ')}.`
}

async function collect (bot, args) {
  // one block at a time, nearest and lowest first, skipping any it can't reach, until the count or the clock runs out
  const count = Math.max(1, Math.min(Number(args.count || 1), 64))
  const ids = blockIds(bot, args.block)
  if (!ids.length) throw new Error(`no block called ${args.block}; use the names observe reports, such as oak_log or stone`)
  const deadline = Date.now() + ACTION_TIMEOUT_MS - 10000
  const before = inventory(bot)
  const skipped = new Set()
  let mined = 0
  let failures = 0
  let last = ''
  while (mined < count && failures < 6 && Date.now() < deadline) {
    const here = bot.entity.position
    const cost = p => p.distanceTo(here) + 4 * Math.max(0, p.y - here.y - 1)
    const found = bot.findBlocks({ matching: ids, maxDistance: 48, count: 128 })
      .filter(p => !skipped.has(p.toString())).sort((a, b) => cost(a) - cost(b))
    if (!found.length) {
      if (!mined) throw new Error(`no ${args.block} within 48 blocks that you can reach; go somewhere else first`)
      break
    }
    const block = bot.blockAt(found[0])
    const tools = block.harvestTools
    if (tools && !bot.inventory.items().some(i => tools[i.type])) {
      const names = Object.keys(tools).map(id => bot.registry.items[id].name)
      throw new Error(`${block.name} drops nothing without the right tool (one of: ${names.slice(0, 4).join(', ')}${names.length > 4 ? ', ...' : ''}); craft one first`)
    }
    try {
      await withTimeout(bot.collectBlock.collect(block), Math.min(25000, deadline - Date.now()), () => {
        bot.pathfinder.stop()
        bot.collectBlock.cancelTask()
      })
      mined++
    } catch (e) {
      skipped.add(found[0].toString())
      failures++
      last = e.message
    }
  }
  const after = inventory(bot)
  const gained = Object.entries(after).map(([k, v]) => [k, v - (before[k] || 0)]).filter(([, d]) => d > 0)
  const got = gained.length ? `Collected ${gained.map(([k, d]) => `${d} ${k}`).join(', ')}.` : 'Picked nothing up.'
  return mined < count ? `${got} Stopped after ${mined} of ${count}${last ? ` (last problem: ${last})` : ''}.` : got
}

const UNUSUAL = /pale_oak|cherry|bamboo|crimson|warped|mangrove|deepslate|blackstone|jungle|acacia|dark_oak|birch/

function describeNeeds (bot, recipe) {
  return recipe.delta.filter(d => d.count < 0).map(d => `${-d.count} ${bot.registry.items[d.id].name}`).join(', ')
}

function closestRecipe (bot, candidates) {
  // the recipe the bot is nearest to affording; among equals, the plain oak and cobblestone variants
  const have = inventory(bot)
  let best = null
  for (const item of candidates) {
    for (const recipe of bot.recipesAll(item.id, null, true)) {
      const needs = recipe.delta.filter(d => d.count < 0)
      const missing = needs.reduce((n, d) => n + Math.max(0, -d.count - (have[bot.registry.items[d.id].name] || 0)), 0)
      const odd = needs.some(d => UNUSUAL.test(bot.registry.items[d.id].name)) || UNUSUAL.test(item.name) ? 0.5 : 0
      if (!best || missing + odd < best.score) best = { item, recipe, score: missing + odd }
    }
  }
  return best
}

async function craft (bot, args) {
  const count = Math.max(1, Math.min(Number(args.count || 1), 64))
  const candidates = itemByName(bot, args.item)
  if (!candidates.length) throw new Error(`no item called ${args.item}`)
  let table = bot.findBlock({ matching: bot.registry.blocksByName.crafting_table.id, maxDistance: 32 })
  const tryCraft = async (useTable) => {
    // one craft at a time, re-reading the recipe and waiting for the inventory, so batches add up
    const before = inventory(bot)
    let made = null
    for (const item of candidates) {
      while (true) {
        const have = (inventory(bot)[item.name] || 0) - (before[item.name] || 0)
        if (have >= count) break
        const recipe = bot.recipesFor(item.id, null, 1, useTable)[0]
        if (!recipe) break
        await bot.craft(recipe, 1, useTable || undefined)
        await sleep(250)
        made = item
      }
      if (made) break
    }
    if (!made) return null
    const n = (inventory(bot)[made.name] || 0) - (before[made.name] || 0)
    return n >= count ? `Crafted ${n} ${made.name}.` : `Crafted ${n} ${made.name}, all your materials allow.`
  }
  const done = await tryCraft(null)
  if (done) return done
  if (table) {
    if (table.position.distanceTo(bot.entity.position) > 4) {
      await bot.pathfinder.goto(new goals.GoalNear(table.position.x, table.position.y, table.position.z, 2))
      table = bot.blockAt(table.position)
    }
    const withTable = await tryCraft(table)
    if (withTable) return withTable
  }
  const best = closestRecipe(bot, candidates)
  if (best) {
    const where = best.recipe.requiresTable && !table ? ' at a crafting table (place one within 32 blocks)' : ''
    throw new Error(`can't craft ${best.item.name} yet: one craft needs ${describeNeeds(bot, best.recipe)}${where}; you have ${JSON.stringify(inventory(bot))}`)
  }
  throw new Error(`${args.item} has no crafting recipe`)
}

function solid (block) { return block && block.boundingBox === 'block' }

async function reach (bot, at) {
  // get within arm's reach of the block at ``at``, measured from the eyes; walk only as close as needed sideways
  const eyes = () => bot.entity.position.offset(0, 1.62, 0)
  if (eyes().distanceTo(at.offset(0.5, 0.5, 0.5)) <= 4.2) return
  await bot.pathfinder.goto(new goals.GoalNearXZ(at.x + 0.5, at.z + 0.5, 2))
  if (eyes().distanceTo(at.offset(0.5, 0.5, 0.5)) > 4.5) throw new Error(`${at.x} ${at.y} ${at.z} is out of reach from the ground; stand closer or higher`)
}

async function place (bot, args) {
  const items = bot.inventory.items().filter(i => i.name === args.item)
  if (!items.length) throw new Error(`you have no ${args.item}`)
  let target
  if (args.x !== undefined && args.y !== undefined && args.z !== undefined) {
    target = new Vec3(Number(args.x), Number(args.y), Number(args.z))
    await reach(bot, target)
  } else {
    const feet = bot.entity.position.floored()
    for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, -1], [1, -1], [-1, 1], [2, 0], [0, 2]]) {
      const p = feet.offset(dx, 0, dz)
      if (AIR.has(bot.blockAt(p)?.name) && solid(bot.blockAt(p.offset(0, -1, 0)))) { target = p; break }
    }
    if (!target) throw new Error('no free spot next to you; move somewhere flatter or give x, y, z')
  }
  if (!AIR.has(bot.blockAt(target)?.name)) throw new Error(`${target} is not empty`)
  const faces = [[0, -1, 0], [0, 1, 0], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1]]
  const face = faces.find(([x, y, z]) => solid(bot.blockAt(target.offset(x, y, z))))
  if (!face) throw new Error(`nothing solid next to ${target.x} ${target.y} ${target.z} to place against`)
  const ref = bot.blockAt(target.offset(...face))
  await bot.equip(items[0], 'hand')
  await bot.placeBlock(ref, new Vec3(-face[0], -face[1], -face[2]))
  return `Placed ${args.item} at ${target.x} ${target.y} ${target.z}.`
}

async function give (bot, args) {
  const count = Math.max(1, Number(args.count || 1))
  const item = bot.inventory.items().find(i => i.name === args.item)
  if (!item) throw new Error(`you have no ${args.item}`)
  const other = bot.players[args.player] && bot.players[args.player].entity
  if (!other) throw new Error(`can't see ${args.player} nearby`)
  await bot.pathfinder.goto(new goals.GoalNear(other.position.x, other.position.y, other.position.z, 2))
  await bot.lookAt(other.position.offset(0, 1.6, 0))
  const n = Math.min(count, inventory(bot)[args.item] || 0)
  await bot.toss(item.type, null, n)
  return `Threw ${n} ${args.item} to ${args.player}.`
}

async function say (bot, args) {
  const text = String(args.message || '').replace(/\s+/g, ' ').trim()
  if (!text) throw new Error('say something')
  for (let i = 0; i < text.length; i += 240) { bot.chat(text.slice(i, i + 240)); await sleep(150) }
  return 'Said it.'
}

const BRIDGING = ['cobblestone', 'dirt', 'stone', 'cobbled_deepslate', 'netherrack']

function bridgingItem (bot, name) {
  const items = bot.inventory.items()
  if (name) return items.find(i => i.name === name)
  return items.find(i => BRIDGING.includes(i.name)) || items.find(i => i.name.endsWith('_planks')) ||
    items.find(i => i.name.endsWith('_log'))
}

async function bridge (bot, args) {
  // walk toward x, z one block at a time at the current height, placing a block wherever the floor ahead is missing
  if (args.x === undefined || args.z === undefined) throw new Error('give the x and z to bridge toward')
  const tx = Math.floor(Number(args.x))
  const tz = Math.floor(Number(args.z))
  const most = Math.max(1, Math.min(Number(args.max_blocks || 64), 128))
  const deadline = Date.now() + ACTION_TIMEOUT_MS - 8000
  let placed = 0
  let why = ''
  try {
    while (Date.now() < deadline) {
      const feet = bot.entity.position.floored()
      const dx = tx - feet.x
      const dz = tz - feet.z
      if (!dx && !dz) { why = 'arrived'; break }
      const step = Math.abs(dx) >= Math.abs(dz) ? new Vec3(Math.sign(dx), 0, 0) : new Vec3(0, 0, Math.sign(dz))
      const floor = bot.blockAt(feet.offset(0, -1, 0))
      if (!solid(floor)) { why = 'you are not standing on a solid block'; break }
      const next = feet.plus(step)
      if (solid(bot.blockAt(next)) || solid(bot.blockAt(next.offset(0, 1, 0)))) { why = `something blocks the way at ${next.x} ${next.y} ${next.z}`; break }
      if (!solid(bot.blockAt(next.offset(0, -1, 0)))) {
        if (placed >= most) { why = `placed the ${most} blocks you allowed`; break }
        const item = bridgingItem(bot, args.block)
        if (!item) { why = args.block ? `you have no ${args.block} left` : 'you have no blocks left to bridge with (cobblestone, dirt, planks or logs)'; break }
        await bot.equip(item, 'hand')
        bot.setControlState('sneak', true)
        await bot.lookAt(floor.position.offset(0.5 + step.x * 0.5, 0.5, 0.5 + step.z * 0.5), true)
        await bot.placeBlock(floor, step)
        placed++
      }
      await bot.pathfinder.goto(new goals.GoalBlock(next.x, next.y, next.z))
    }
  } finally {
    bot.setControlState('sneak', false)
  }
  if (!why) why = 'stopped to report back (call bridge again to go on)'
  return `Placed ${placed} blocks. You are at ${pos(bot.entity.position).join(' ')}: ${why}.`
}

const CHESTS = ['chest', 'trapped_chest', 'barrel']

async function take (bot, args) {
  // take an item (or everything) from the nearest chest within 32 blocks
  const ids = CHESTS.map(n => bot.registry.blocksByName[n].id)
  let chest = bot.findBlock({ matching: ids, maxDistance: 32 })
  if (!chest) throw new Error('no chest within 32 blocks')
  if (chest.position.distanceTo(bot.entity.position) > 3.5) {
    await bot.pathfinder.goto(new goals.GoalNear(chest.position.x, chest.position.y, chest.position.z, 2))
    chest = bot.blockAt(chest.position)
  }
  const box = await bot.openContainer(chest)
  try {
    const all = box.containerItems()
    const want = args.item ? all.filter(i => i.name === args.item) : all
    const list = items => Object.entries(items.reduce((o, i) => ({ ...o, [i.name]: (o[i.name] || 0) + i.count }), {}))
      .map(([k, n]) => `${n} ${k}`).join(', ') || 'nothing'
    if (!want.length) throw new Error(`the chest at ${pos(chest.position).join(' ')} has ${list(all)}`)
    const took = {}
    for (const item of want) {
      const n = args.count ? Math.min(Number(args.count) - (took[item.name] || 0), item.count) : item.count
      if (n <= 0) continue
      await box.withdraw(item.type, null, n)
      took[item.name] = (took[item.name] || 0) + n
    }
    const left = box.containerItems()
    return `Took ${Object.entries(took).map(([k, n]) => `${n} ${k}`).join(', ')} from the chest at ${pos(chest.position).join(' ')}; it still has ${list(left)}.`
  } finally {
    box.close()
  }
}

async function use (bot, args) {
  // use an item on a block's top face, such as flint_and_steel on obsidian to light a portal
  const item = bot.inventory.items().find(i => i.name === args.item)
  if (!item) throw new Error(`you have no ${args.item}`)
  const at = new Vec3(Number(args.x), Number(args.y), Number(args.z))
  await reach(bot, at)
  const block = bot.blockAt(at)
  if (!block || AIR.has(block.name)) throw new Error(`there is no block at ${at.x} ${at.y} ${at.z} to use it on`)
  await bot.equip(item, 'hand')
  await bot.lookAt(at.offset(0.5, 1, 0.5), true)
  await bot.activateBlock(block, new Vec3(0, 1, 0))
  await sleep(600)
  const portal = bot.findBlock({ matching: bot.registry.blocksByName.nether_portal.id, maxDistance: 8 })
  return portal ? `Used ${args.item} on ${block.name} at ${at.x} ${at.y} ${at.z}: a nether portal is now lit at ${pos(portal.position).join(' ')}!`
    : `Used ${args.item} on ${block.name} at ${at.x} ${at.y} ${at.z}, but no portal formed: the frame must be complete and the fire must land inside it.`
}

function blocksAt (positions) {
  // block names at positions, read from any bot that has their chunks loaded
  const viewers = [...bots.values()].map(e => e.bot).concat(camera ? [camera.bot] : [])
  return positions.map(([x, y, z]) => {
    for (const b of viewers) {
      const block = b.blockAt(new Vec3(x, y, z))
      if (block) return block.name
    }
    return null
  })
}

const ACTIONS = { go_to: goTo, collect, craft, place, give, chat: say, bridge, take, use }

function stop (entry) {
  try { entry.bot.pathfinder.stop() } catch (e) {}
  try { entry.bot.collectBlock.cancelTask() } catch (e) {}
}

async function act (entry, op, args) {
  if (op === 'observe') return observe(entry.bot)
  const fn = ACTIONS[op]
  if (!fn) throw Object.assign(new Error(`unknown action ${op}`), { status: 404 })
  if (entry.action) { stop(entry); await entry.action.catch(() => {}) }
  entry.doing = op
  entry.action = withTimeout(fn(entry.bot, args), ACTION_TIMEOUT_MS, () => stop(entry))
  try {
    return { text: await entry.action }
  } finally {
    entry.action = null
    entry.doing = null
  }
}

function spawn (username, viewerPort) {
  return new Promise((resolve, reject) => {
    const bot = mineflayer.createBot({ host: MC_HOST, port: MC_PORT, username, version: MC_VERSION, auth: 'offline' })
    const entry = { bot, action: null, doing: null, viewerPort, deaths: 0 }
    bot.loadPlugin(pathfinder)
    bot.loadPlugin(collectBlock)
    bot.once('spawn', () => {
      const moves = new Movements(bot)
      moves.allowParkour = false
      bot.pathfinder.setMovements(moves)
      bots.set(username, entry)
      if (bots.size === 1) {
        bot.on('chat', (from, text) => chat.push({ seq: chat.length, from, text, ts: Date.now() }))
      }
      bot.on('death', () => { entry.deaths++ })
      if (viewerPort) {
        try {
          require('prismarine-viewer').mineflayer(bot, { port: viewerPort, firstPerson: false, viewDistance: 4 })
        } catch (e) {
          console.error(`viewer for ${username} failed: ${e.message}`)
        }
      }
      resolve(entry)
    })
    bot.once('kicked', reason => reject(new Error(`kicked: ${JSON.stringify(reason)}`)))
    bot.once('error', reject)
    bot.on('end', () => { bots.delete(username) })
  })
}

// ---- the camera: an invisible spectator bot, and a view on CAMERA_PORT that films from a pose the director picks ----
const CAMERA_PORT = Number(process.env.CAMERA_PORT || 3099)
const CHASE = 5
let camera = null

const MAX_DOWN = 1.05 // the steepest the camera looks down, in radians

function angles (from, to) {
  const d = to.minus(from)
  const pitch = Math.atan2(d.y, Math.sqrt(d.x * d.x + d.z * d.z))
  return { yaw: Math.atan2(-d.x, -d.z), pitch: Math.max(-MAX_DOWN, pitch), look: to }
}

function clearOf (bot, p) {
  // lift a camera point out of any block it would sit inside
  let q = p.clone()
  for (let i = 0; i < 16; i++) {
    const b = bot.blockAt(q.floored())
    if (!b || b.boundingBox !== 'block') return q
    q = q.offset(0, 1, 0)
  }
  return q
}

// the chase positions to try, in order: [turn from the current side (radians), distance, height]
const SHOTS = [[0, CHASE, 2], [0, CHASE, 4], [0.7, CHASE, 2.5], [-0.7, CHASE, 2.5], [0, CHASE - 1.5, 6],
  [1.4, CHASE, 3], [-1.4, CHASE, 3], [0, 3, 8], [Math.PI, CHASE, 3]]

function sees (bot, from, to) {
  // nothing solid (leaves included) on the line from the camera to its target, nor at the camera itself
  const d = to.minus(from)
  const n = Math.ceil(d.norm() * 2)
  for (let i = 0; i < n; i++) {
    const b = bot.blockAt(from.plus(d.scaled(i / n)).floored())
    if (b && b.boundingBox === 'block') return false
  }
  return true
}

function wrap (a) { return Math.atan2(Math.sin(a), Math.cos(a)) }

function aim () {
  // where the shot wants the camera: behind and above its player, or circling everyone
  const { shot, pose } = camera
  const entry = shot.mode === 'follow' && bots.get(shot.target)
  if (entry && entry.bot.entity) {
    const e = entry.bot.entity
    const head = e.position.offset(0, 1.6, 0)
    let back = pose && !camera.cut ? pose.pos.minus(head) : new Vec3(Math.sin(e.yaw), 0, Math.cos(e.yaw))
    back = new Vec3(back.x, 0, back.z)
    if (back.norm() < 0.1) back = new Vec3(0, 0, 1)
    back = back.scaled(1 / back.norm())
    let pos = null
    for (const [turn, dist, up] of SHOTS) {
      const dir = new Vec3(back.x * Math.cos(turn) - back.z * Math.sin(turn), 0, back.x * Math.sin(turn) + back.z * Math.cos(turn))
      const p = head.plus(dir.scaled(dist)).offset(0, up, 0)
      if (sees(camera.bot, p, head)) { pos = p; break }
    }
    pos = pos || clearOf(camera.bot, head.plus(back.scaled(3)).offset(0, 8, 0))
    return { pos, ...angles(pos, head) }
  }
  const points = [...bots.values()].map(b => b.bot.entity).filter(Boolean).map(e => e.position)
    .concat((shot.frame || []).map(([x, y, z]) => new Vec3(x, y, z)))
  if (!points.length) return pose
  const c = points.reduce((a, p) => a.plus(p), new Vec3(0, 0, 0)).scaled(1 / points.length)
  const r = Math.max(4, ...points.map(p => p.distanceTo(c))) * 1.2 + 9
  const a = Date.now() / 20000 * 2 * Math.PI
  const pos = clearOf(camera.bot, c.offset(Math.sin(a) * r, r * 0.5, Math.cos(a) * r))
  return { pos, ...angles(pos, c.offset(0, 1, 0)) }
}

function frame () {
  const want = aim()
  if (!want) return
  const p = camera.pose
  if (!p || camera.cut || !sees(camera.bot, p.pos, want.look)) {
    camera.pose = want
  } else {
    const k = 0.12
    camera.pose = {
      pos: p.pos.plus(want.pos.minus(p.pos).scaled(k)),
      yaw: p.yaw + wrap(want.yaw - p.yaw) * k,
      pitch: p.pitch + (want.pitch - p.pitch) * k
    }
  }
  camera.cut = false
  const { pos, yaw, pitch } = camera.pose
  for (const s of camera.sockets) {
    s.emit('position', { pos, yaw, pitch })
    s.worldView.updatePosition(pos)
  }
}

async function follow () {
  // keep the spectator itself near the shot, so the server sends the chunks it films
  if (!camera.pose) return
  const at = camera.bot.entity.position
  const p = camera.pose.pos
  if (at.distanceTo(p) > 24) await command(`tp ${camera.bot.username} ${p.x.toFixed(1)} ${p.y.toFixed(1)} ${p.z.toFixed(1)}`)
}

function startCamera (username) {
  return new Promise((resolve, reject) => {
    const bot = mineflayer.createBot({ host: MC_HOST, port: MC_PORT, username, version: MC_VERSION, auth: 'offline' })
    bot.once('kicked', reason => reject(new Error(`camera kicked: ${JSON.stringify(reason)}`)))
    bot.once('error', reject)
    bot.once('spawn', async () => {
      await command(`gamemode spectator ${username}`)
      bot.physicsEnabled = false
      camera = { bot, shot: { mode: 'wide' }, pose: null, cut: true, sockets: [] }
      const express = require('express')
      const { WorldView } = require('prismarine-viewer/viewer')
      const { setupRoutes } = require('prismarine-viewer/lib/common')
      const app = express()
      const server = require('http').createServer(app)
      const io = require('socket.io')(server, { path: '/socket.io' })
      setupRoutes(app, '')
      io.on('connection', socket => {
        socket.emit('version', bot.version)
        const at = (camera.pose && camera.pose.pos) || bot.entity.position
        socket.worldView = new WorldView(bot.world, 6, at, socket)
        socket.worldView.init(at)
        socket.worldView.listenToBot(bot)
        camera.sockets.push(socket)
        socket.on('disconnect', () => {
          socket.worldView.removeListenersFromBot(bot)
          camera.sockets.splice(camera.sockets.indexOf(socket), 1)
        })
      })
      server.listen(CAMERA_PORT, () => console.log(`camera view on *:${CAMERA_PORT}`))
      setInterval(frame, 50)
      setInterval(() => { follow().catch(e => console.error(`camera follow: ${e.message}`)) }, 2000)
      resolve()
    })
  })
}

function setShot (shot) {
  if (shot.mode !== camera.shot.mode || shot.target !== camera.shot.target) camera.cut = true
  camera.shot = { mode: shot.mode === 'follow' ? 'follow' : 'wide', target: shot.target, frame: shot.frame }
}

function state () {
  const out = {}
  for (const [name, e] of bots) {
    const b = e.bot
    if (!b.entity) continue
    out[name] = { at: pos(b.entity.position), health: Math.round(b.health), food: b.food, inventory: inventory(b),
                  doing: e.doing, deaths: e.deaths, viewer: e.viewerPort || null }
  }
  const any = bots.values().next().value
  return { bots: out, time: any ? (any.bot.time.isDay ? 'day' : 'night') : null, chat: chat.length }
}

async function body (req) {
  let raw = ''
  for await (const chunk of req) raw += chunk
  return raw ? JSON.parse(raw) : {}
}

function reply (res, status, data) {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(data))
}

http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://bridge')
  const parts = url.pathname.split('/').filter(Boolean)
  try {
    if (req.method === 'GET' && url.pathname === '/health') {
      await command('list')
      return reply(res, 200, { ok: true, bots: bots.size })
    }
    if (req.method === 'GET' && url.pathname === '/state') return reply(res, 200, state())
    if (req.method === 'GET' && url.pathname === '/chat') {
      return reply(res, 200, { messages: chat.slice(Number(url.searchParams.get('since') || 0)) })
    }
    if (req.method === 'POST' && url.pathname === '/command') {
      return reply(res, 200, { output: await command((await body(req)).command) })
    }
    if (req.method === 'POST' && url.pathname === '/bots') {
      const { username, viewer_port: viewerPort } = await body(req)
      if (bots.has(username)) return reply(res, 409, { error: `${username} is already in the world` })
      await spawn(username, viewerPort)
      return reply(res, 200, { ok: true })
    }
    if (req.method === 'POST' && parts[0] === 'bots' && parts.length === 3) {
      const entry = bots.get(decodeURIComponent(parts[1]))
      if (!entry) return reply(res, 404, { error: `no bot ${parts[1]}` })
      try {
        return reply(res, 200, await act(entry, parts[2], await body(req)))
      } catch (e) {
        if (e.status) throw e
        return reply(res, 200, { error: e.message })
      }
    }
    if (req.method === 'POST' && url.pathname === '/blocks') {
      return reply(res, 200, { blocks: blocksAt((await body(req)).positions || []) })
    }
    if (req.method === 'POST' && url.pathname === '/camera') {
      if (camera) return reply(res, 409, { error: 'the camera is already running' })
      await startCamera(CAMERA_NAME)
      return reply(res, 200, { ok: true, port: CAMERA_PORT })
    }
    if (req.method === 'POST' && url.pathname === '/camera/shot') {
      if (!camera) return reply(res, 409, { error: 'no camera' })
      setShot(await body(req))
      return reply(res, 200, { ok: true })
    }
    if (req.method === 'POST' && url.pathname === '/quit') {
      for (const e of bots.values()) e.bot.quit()
      if (camera) camera.bot.quit()
      return reply(res, 200, { ok: true })
    }
    reply(res, 404, { error: 'not found' })
  } catch (e) {
    reply(res, e.status || 500, { error: e.message })
  }
}).listen(PORT, '127.0.0.1', () => console.log(`bridge on 127.0.0.1:${PORT}`))
