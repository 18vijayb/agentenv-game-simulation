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
    .filter(e => e !== bot.entity && e.position.distanceTo(here) < 32 && (e.type === 'player' || e.type === 'mob' || e.type === 'animal' || e.type === 'hostile'))
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
      ? new goals.GoalNearXZ(Number(args.x), Number(args.z), 2)
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

async function place (bot, args) {
  const items = bot.inventory.items().filter(i => i.name === args.item)
  if (!items.length) throw new Error(`you have no ${args.item}`)
  let target
  if (args.x !== undefined && args.y !== undefined && args.z !== undefined) {
    target = new Vec3(Number(args.x), Number(args.y), Number(args.z))
    if (target.distanceTo(bot.entity.position) > 4) await bot.pathfinder.goto(new goals.GoalNear(target.x, target.y, target.z, 3))
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

const ACTIONS = { go_to: goTo, collect, craft, place, give, chat: say }

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
    if (req.method === 'POST' && url.pathname === '/quit') {
      for (const e of bots.values()) e.bot.quit()
      return reply(res, 200, { ok: true })
    }
    reply(res, 404, { error: 'not found' })
  } catch (e) {
    reply(res, e.status || 500, { error: e.message })
  }
}).listen(PORT, '127.0.0.1', () => console.log(`bridge on 127.0.0.1:${PORT}`))
