import React from "react";
import {
  AbsoluteFill, Audio, Img, Sequence, continueRender, delayRender, interpolate, spring, staticFile,
  useCurrentFrame, useVideoConfig,
} from "remotion";
import type { Beat, CatanSeat, CatanTable, Player, Seat, Storyboard, Table } from "./types";

const DISPLAY = "'Big Shoulders Display', 'Arial Narrow', Impact, sans-serif";
const TEXT = "'Literata', Georgia, serif";
const GOLD = "#e3b956";
const INK = "#f1ead8";
const RED_SUITS = new Set(["♥", "♦"]);

const fontsReady = delayRender("fonts");
Promise.all([
  new FontFace("Big Shoulders Display", `url(${staticFile("fonts/big-shoulders-display.woff2")})`, { weight: "100 900" }).load(),
  new FontFace("Literata", `url(${staticFile("fonts/literata.woff2")})`, { weight: "200 900" }).load(),
  new FontFace("Literata", `url(${staticFile("fonts/literata-italic.woff2")})`, { weight: "200 900", style: "italic" }).load(),
]).then((faces) => {
  faces.forEach((f) => document.fonts.add(f));
  continueRender(fontsReady);
});

const CENTER = { x: 700, y: 560 };
const RADIUS = { x: 530, y: 340 };
const PANEL = { left: 1350, width: 530 };

function seatPos(i: number, n: number) {
  const a = (-90 + (360 / n) * i) * (Math.PI / 180);
  return { x: CENTER.x + RADIUS.x * Math.cos(a), y: CENTER.y + RADIUS.y * Math.sin(a) };
}

const fmt = (n: number) => n.toLocaleString("en-US");

/* ---------- cards ---------- */

function Card({ card, w = 74, faded = false, flipAt }: { card: string; w?: number; faded?: boolean; flipAt?: number }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const rank = card.slice(0, -1);
  const suit = card.slice(-1);
  const color = RED_SUITS.has(suit) ? "#c3303b" : "#1b1f24";
  const flip = flipAt === undefined ? 1 : spring({ frame: frame - flipAt, fps, config: { damping: 14, mass: 0.6 } });
  return (
    <div style={{
      width: w, height: w * 1.4, borderRadius: w * 0.1, background: "#fbf8f1", color,
      boxShadow: "0 6px 14px rgba(0,0,0,.45)", position: "relative", opacity: faded ? 0.32 : 1,
      transform: `scaleX(${flip})`, fontFamily: DISPLAY, flexShrink: 0,
    }}>
      <div style={{ position: "absolute", top: w * 0.05, left: w * 0.1, fontSize: w * 0.42, fontWeight: 800, lineHeight: 1 }}>{rank}</div>
      <div style={{ position: "absolute", top: w * 0.47, left: w * 0.1, fontSize: w * 0.34, lineHeight: 1 }}>{suit}</div>
      <div style={{ position: "absolute", right: w * 0.08, bottom: w * 0.06, fontSize: w * 0.62, lineHeight: 1, opacity: 0.9 }}>{suit}</div>
    </div>
  );
}

function CardSlot({ w = 96 }: { w?: number }) {
  return <div style={{ width: w, height: w * 1.4, borderRadius: w * 0.1, border: "2px dashed rgba(241,234,216,.18)" }} />;
}

/* ---------- table ---------- */

function Felt() {
  return (
    <AbsoluteFill style={{ background: "radial-gradient(ellipse at 50% 45%, #10241a 0%, #070c0a 75%)" }}>
      <div style={{
        position: "absolute", left: CENTER.x - RADIUS.x - 40, top: CENTER.y - RADIUS.y - 40,
        width: (RADIUS.x + 40) * 2, height: (RADIUS.y + 40) * 2, borderRadius: "50%",
        background: "linear-gradient(160deg, #6b4127, #3a2113)", boxShadow: "0 30px 80px rgba(0,0,0,.7)",
      }} />
      <div style={{
        position: "absolute", left: CENTER.x - RADIUS.x - 14, top: CENTER.y - RADIUS.y - 14,
        width: (RADIUS.x + 14) * 2, height: (RADIUS.y + 14) * 2, borderRadius: "50%",
        background: "radial-gradient(ellipse at 50% 40%, #2c7a4e 0%, #1a5634 58%, #11402a 100%)",
        boxShadow: "inset 0 0 90px rgba(0,0,0,.55), 0 0 0 3px rgba(227,185,86,.35)",
      }} />
    </AbsoluteFill>
  );
}

function Counter({ from, to, start, frames = 18 }: { from: number; to: number; start: number; frames?: number }) {
  const frame = useCurrentFrame();
  const v = Math.round(interpolate(frame - start, [0, frames], [from, to], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }));
  return <>{fmt(v)}</>;
}

function Board({ table, prev, start }: { table: Table; prev: Table | null; start: number }) {
  const known = prev ? prev.board.length : table.board.length;
  return (
    <div style={{ position: "absolute", left: CENTER.x, top: CENTER.y - 40, transform: "translate(-50%, -50%)",
                  display: "flex", flexDirection: "column", alignItems: "center", gap: 18 }}>
      <div style={{ display: "flex", gap: 14 }}>
        {Array.from({ length: 5 }, (_, i) => table.board[i]
          ? <Card key={i} card={table.board[i]} w={96} flipAt={i >= known ? start + (i - known) * 6 : undefined} />
          : <CardSlot key={i} />)}
      </div>
      <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 44, color: INK, letterSpacing: 1,
                    textShadow: "0 2px 6px rgba(0,0,0,.6)" }}>
        Pot <span style={{ color: GOLD }}><Counter from={prev ? prev.pot : table.pot} to={table.pot} start={start} /></span>
      </div>
    </div>
  );
}

function SeatView({ seat, player, i, n, active, thinking, prev, start }: {
  seat: Seat; player: Player; i: number; n: number; active: boolean; thinking: boolean; prev: Seat | null; start: number;
}) {
  const frame = useCurrentFrame();
  const { x, y } = seatPos(i, n);
  const pulse = active ? 1 + 0.035 * Math.sin((frame - start) / 4) : 1;
  const dim = seat.out ? 0.28 : seat.folded ? 0.62 : 1;
  const toward = { x: x + (CENTER.x - x) * 0.4, y: y + (CENTER.y - y) * 0.42 };
  return (
    <>
      {seat.bet > 0 && !seat.out && (
        <div style={{ position: "absolute", left: toward.x, top: toward.y, transform: "translate(-50%, -50%)",
                      display: "flex", alignItems: "center", gap: 8, fontFamily: DISPLAY, fontWeight: 700, fontSize: 30, color: INK }}>
          <div style={{ width: 28, height: 28, borderRadius: "50%", background: player.color, border: "4px dashed rgba(255,255,255,.8)" }} />
          {fmt(seat.bet)}
        </div>
      )}
      <div style={{ position: "absolute", left: x, top: y, transform: `translate(-50%, -50%) scale(${pulse})`,
                    display: "flex", flexDirection: x > CENTER.x + 40 ? "row-reverse" : "row",
                    alignItems: "center", gap: 14, opacity: dim, filter: seat.out ? "grayscale(1)" : undefined }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
          <div style={{
            width: 104, height: 104, borderRadius: "50%", background: "#141b18", display: "grid", placeItems: "center",
            border: `6px solid ${player.color}`, fontFamily: DISPLAY, fontWeight: 800, color: player.color,
            boxShadow: active ? `0 0 0 6px ${thinking ? "rgba(241,234,216,.35)" : GOLD}, 0 0 46px ${player.color}` : "0 8px 20px rgba(0,0,0,.5)",
            fontSize: player.mono.length > 2 ? 38 : 48,
          }}>{player.mono}</div>
          <div style={{ background: player.color, color: "#0c0f0e", fontFamily: DISPLAY, fontWeight: 800, fontSize: 26,
                        padding: "0 14px", borderRadius: 14, lineHeight: "34px" }}>{player.label}</div>
          <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 28, color: INK }}>
            {seat.out ? "Out" : <Counter from={prev ? prev.chips : seat.chips} to={seat.chips} start={start} />}
          </div>
        </div>
        {!seat.out && seat.cards.length === 2 && (
          <div style={{ display: "flex", gap: 6, position: "relative" }}>
            {seat.cards.map((c, k) => <Card key={k} card={c} w={62} faded={seat.folded} />)}
            {seat.dealer && <div style={{ position: "absolute", top: -22, right: -22, width: 40, height: 40, borderRadius: "50%",
                                          background: INK, color: "#111", display: "grid", placeItems: "center",
                                          fontFamily: DISPLAY, fontWeight: 800, fontSize: 24 }}>D</div>}
            {seat.allin && <div style={{ position: "absolute", bottom: -16, left: "50%", transform: "translateX(-50%)",
                                         background: "#d6453a", color: "#fff", fontFamily: DISPLAY, fontWeight: 800, fontSize: 22,
                                         padding: "0 10px", borderRadius: 10, whiteSpace: "nowrap" }}>All in</div>}
          </div>
        )}
      </div>
    </>
  );
}

/* ---------- overlays ---------- */

function reveal(text: string, frame: number, frames: number) {
  const words = text.split(" ");
  const shown = Math.ceil(interpolate(frame, [0, Math.max(1, frames)], [0, words.length], { extrapolateRight: "clamp" }));
  return (
    <>
      {words.map((w, i) => <span key={i} style={{ opacity: i < shown ? 1 : 0.18 }}>{w} </span>)}
    </>
  );
}

function Line({ beat, board, local, current }: { beat: Beat; board: Storyboard; local: number; current: boolean }) {
  const { fps } = useVideoConfig();
  const pop = current ? spring({ frame: local, fps, config: { damping: 16, mass: 0.7 } }) : 1;
  const narr = beat.mode === "narrate";
  const think = beat.mode === "think";
  const player = beat.speaker !== null ? board.players[beat.speaker] : null;
  const accent = narr ? GOLD : player!.color;
  const who = narr ? "Commentary" : player!.name;
  const verb = narr ? "" : think ? "thinks" : "says";
  const text = current ? reveal(beat.text, local, beat.frames - 10) : beat.text;
  return (
    <div style={{
      opacity: (current ? 1 : 0.42) * pop, transform: `translateY(${(1 - pop) * 24}px)`,
      background: narr ? "rgba(20,17,9,.92)" : think ? "rgba(13,20,18,.94)" : "#f6efdd",
      color: think || narr ? "#e8e1cf" : "#1d1a15",
      border: think ? `3px dashed ${accent}` : `3px solid ${accent}`, borderLeft: narr ? `10px solid ${accent}` : undefined,
      borderRadius: think ? 30 : 18, padding: current ? "18px 24px 22px" : "12px 20px 14px",
      boxShadow: current ? "0 18px 50px rgba(0,0,0,.55)" : "none",
    }}>
      <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: current ? 28 : 22, color: accent, marginBottom: 4,
                    display: "flex", alignItems: "center", gap: 10 }}>
        {who}<span style={{ color: think || narr ? "#9aa59f" : "#7b725f", fontWeight: 700 }}>{verb}</span>
      </div>
      <div style={{ fontFamily: TEXT, fontStyle: think ? "italic" : "normal", fontSize: current ? (beat.text.length > 150 ? 29 : 33) : 21,
                    lineHeight: 1.36 }}>
        {think ? "“" : ""}{text}{think ? "”" : ""}
      </div>
    </div>
  );
}

const score = (s: Seat | CatanSeat) => ("vp" in s ? s.vp : s.chips + s.bet);

function Leaderboard({ board, idx }: { board: Storyboard; idx: number }) {
  const seats = (board.beats[idx].table.seats as (Seat | CatanSeat)[]).map((s, i) => ({ ...s, player: board.players[i] }))
    .sort((a, b) => score(b) - score(a));
  return (
    <div style={{ background: "rgba(10,15,13,.8)", border: "1px solid rgba(241,234,216,.12)", borderRadius: 16,
                  padding: "12px 18px", display: "grid", gap: 4 }}>
      <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 22, color: GOLD }}>{board.score ?? "Chip counts"}</div>
      {seats.map((s) => {
        const out = "out" in s && s.out;
        return (
        <div key={s.name} style={{ display: "flex", alignItems: "center", gap: 10, opacity: out ? 0.35 : 1 }}>
          <div style={{ width: 12, height: 12, borderRadius: "50%", background: s.player.color }} />
          <div style={{ flex: 1, fontFamily: DISPLAY, fontWeight: 700, fontSize: 26, color: INK }}>{s.player.name}</div>
          <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 26, color: INK }}>{out ? "Out" : fmt(score(s))}</div>
        </div>
        );
      })}
    </div>
  );
}

function Panel({ board, idx, local }: { board: Storyboard; idx: number; local: number }) {
  const spoken = board.beats.slice(0, idx + 1).filter((b) => b.mode !== "act");
  const beat = board.beats[idx];
  const recent = spoken.slice(-3);
  const current = beat.mode !== "act" ? recent.pop() : undefined;
  return (
    <div style={{ position: "absolute", left: PANEL.left, width: PANEL.width, top: 40, bottom: 40, display: "flex",
                  flexDirection: "column", gap: 16, zIndex: 5 }}>
      <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 30, color: "rgba(241,234,216,.55)", textAlign: "right" }}>
        {board.title}
      </div>
      <Leaderboard board={board} idx={idx} />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", gap: 14, overflow: "hidden" }}>
        {recent.map((b) => <Line key={b.from} beat={b} board={board} local={0} current={false} />)}
        {current && <Line key={current.from} beat={current} board={board} local={local} current />}
      </div>
    </div>
  );
}

function ActionBanner({ beat, player, n, local }: { beat: Beat; player: Player; n: number; local: number }) {
  const { fps } = useVideoConfig();
  const { x, y } = seatPos(beat.speaker!, n);
  const pop = spring({ frame: local - (beat.mode === "act" ? 0 : 6), fps, config: { damping: 11, mass: 0.6 } });
  const text = beat.action!.charAt(0).toUpperCase() + beat.action!.slice(1);
  const big = /all-in/.test(beat.action!);
  return (
    <div style={{
      position: "absolute", left: x, top: y + (y < CENTER.y - 200 ? -150 : y < CENTER.y ? -96 : 96),
      transform: `translate(-50%, -50%) scale(${pop})`,
      background: big ? "#d6453a" : "#0f1412", color: big ? "#fff" : INK, border: `3px solid ${big ? "#fff" : player.color}`,
      fontFamily: DISPLAY, fontWeight: 800, fontSize: big ? 46 : 38, padding: "4px 22px", borderRadius: 14,
      boxShadow: "0 10px 30px rgba(0,0,0,.6)", whiteSpace: "nowrap", zIndex: 6,
    }}>{text}</div>
  );
}

function Header({ table }: { table: Table }) {
  return (
    <>
      <div style={{ position: "absolute", left: 56, top: 40, color: INK, zIndex: 4 }}>
        <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 58, lineHeight: 1 }}>
          {table.hand ? `Hand ${table.hand} of ${table.hands}` : "Texas Hold'em"}
        </div>
        <div style={{ fontFamily: TEXT, fontSize: 26, color: "#b9c2bc", marginTop: 6 }}>
          Blinds {table.blinds}{table.street && table.street !== "game over" ? `, ${table.street}` : ""}
        </div>
      </div>
    </>
  );
}

/* ---------- CATAN ---------- */

const CATAN_CARD = { left: 980, width: 350, top: 150, height: 206, gap: 12 };

function CatanSeatCard({ seat, player, i, active, thinking }: {
  seat: CatanSeat; player: Player; i: number; active: boolean; thinking: boolean;
}) {
  const top = CATAN_CARD.top + i * (CATAN_CARD.height + CATAN_CARD.gap);
  return (
    <div style={{ position: "absolute", left: CATAN_CARD.left, top, width: CATAN_CARD.width, height: CATAN_CARD.height,
                  background: "rgba(10,15,13,.85)", borderRadius: 16, borderLeft: `8px solid ${player.color}`,
                  boxShadow: active ? `0 0 0 3px ${player.color}, 0 0 28px ${player.color}66` : "0 6px 18px rgba(0,0,0,.4)",
                  padding: "12px 16px", boxSizing: "border-box", display: "flex", flexDirection: "column", gap: 8, zIndex: 3 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{ width: 46, height: 46, borderRadius: "50%", border: `4px solid ${player.color}`, display: "grid",
                      placeItems: "center", fontFamily: DISPLAY, fontWeight: 800, fontSize: 19, color: player.color,
                      background: "#141b18" }}>{player.mono}</div>
        <div style={{ flex: 1, fontFamily: DISPLAY, fontWeight: 800, fontSize: 30, color: INK, lineHeight: 1 }}>
          {player.name}{thinking && <span style={{ color: GOLD, fontSize: 22 }}> · thinking</span>}
        </div>
        <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 44, color: GOLD, lineHeight: 1 }}>
          {seat.vp}<span style={{ fontSize: 22, color: "#b9c2bc" }}> VP</span>
        </div>
      </div>
      <div style={{ display: "flex", gap: 14, fontFamily: DISPLAY, fontWeight: 700, fontSize: 28, color: INK }}>
        {Object.entries(seat.hand).map(([icon, n]) => (
          <span key={icon} style={{ opacity: n ? 1 : 0.35 }}>{icon} {n}</span>
        ))}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {seat.awards.map((a) => (
          <span key={a} style={{ background: GOLD, color: "#2a2008", fontFamily: DISPLAY, fontWeight: 800, fontSize: 18,
                                 borderRadius: 8, padding: "1px 9px" }}>{a}</span>
        ))}
        {seat.devs.map((d, k) => (
          <span key={k} style={{ background: "#24406b", color: "#dbe7ff", fontFamily: TEXT, fontSize: 16, borderRadius: 8,
                                 padding: "1px 9px" }}>{d}</span>
        ))}
        {seat.knights > 0 && (
          <span style={{ color: "#b9c2bc", fontFamily: TEXT, fontSize: 16 }}>{seat.knights} knight{seat.knights === 1 ? "" : "s"} played</span>
        )}
      </div>
    </div>
  );
}

function CatanScene({ board, beat, idx, local }: { board: Storyboard; beat: Beat; idx: number; local: number }) {
  const { fps } = useVideoConfig();
  const t = beat.table as CatanTable;
  const pop = spring({ frame: local, fps, config: { damping: 11, mass: 0.6 } });
  const speaker = beat.speaker !== null ? board.players[beat.speaker] : null;
  return (
    <AbsoluteFill style={{ background: "radial-gradient(ellipse at 30% 45%, #1d3a5c 0%, #0b1622 70%)" }}>
      <div style={{ position: "absolute", left: 56, top: 36, color: INK, zIndex: 4 }}>
        <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 58, lineHeight: 1 }}>
          {typeof t.turn === "number" ? `Turn ${t.turn}` : "Set-up"}
        </div>
        <div style={{ fontFamily: TEXT, fontSize: 24, color: "#b9c2bc", marginTop: 6 }}>
          {t.now}{t.dice && t.dice !== "–" ? ` · rolled ${t.dice}` : ""}
        </div>
      </div>
      {t.map && <Img src={t.map} style={{ position: "absolute", left: 36, top: 150, width: 920 }} />}
      {t.seats.map((s, i) => (
        <CatanSeatCard key={i} seat={s} player={board.players[i]} i={i} active={beat.speaker === i}
                       thinking={beat.speaker === i && beat.mode === "think"} />
      ))}
      <Panel board={board} idx={idx} local={local} />
      {speaker && beat.action && beat.mode !== "think" && (
        <div style={{ position: "absolute", left: 496, top: 132, transform: `translate(-50%, 0) scale(${pop})`,
                      background: "#0f1412", color: INK, border: `3px solid ${speaker.color}`, fontFamily: DISPLAY,
                      fontWeight: 800, fontSize: 34, padding: "4px 22px", borderRadius: 14, whiteSpace: "nowrap",
                      boxShadow: "0 10px 30px rgba(0,0,0,.6)", zIndex: 6 }}>
          {speaker.label}: {beat.action}
        </div>
      )}
    </AbsoluteFill>
  );
}

/* ---------- scenes ---------- */

function TableScene({ board }: { board: Storyboard }) {
  const frame = useCurrentFrame() - board.intro;
  const idx = Math.max(0, board.beats.findIndex((b) => frame >= b.from && frame < b.from + b.frames));
  const beat = board.beats[idx];
  const local = frame - beat.from;
  if (beat.table.kind === "catan") return <CatanScene board={board} beat={beat} idx={idx} local={local} />;
  const prev = idx > 0 ? (board.beats[idx - 1].table as Table) : null;
  const n = board.players.length;
  const t = beat.table;
  return (
    <AbsoluteFill>
      <Felt />
      <Header table={t} />
      <Board table={t} prev={prev && prev.hand === t.hand ? prev : null} start={beat.from + board.intro} />
      {t.seats.map((s, i) => (
        <SeatView key={i} seat={s} player={board.players[i]} i={i} n={n} start={beat.from + board.intro}
                  active={beat.speaker === i} thinking={beat.mode === "think"}
                  prev={prev ? prev.seats[i] : null} />
      ))}
      <Panel board={board} idx={idx} local={local} />
      {beat.speaker !== null && beat.action && beat.mode !== "think" && (
        <ActionBanner beat={beat} player={board.players[beat.speaker]} n={n} local={local} />
      )}
    </AbsoluteFill>
  );
}

function Intro({ board }: { board: Storyboard }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const fade = interpolate(frame, [board.intro - 12, board.intro], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ opacity: fade, background: "rgba(5,8,7,.86)", display: "flex", flexDirection: "column",
                           alignItems: "center", justifyContent: "center", gap: 26, zIndex: 20 }}>
      <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 132, color: INK, lineHeight: 1,
                    transform: `translateY(${(1 - spring({ frame, fps })) * 40}px)` }}>{board.title}</div>
      <div style={{ fontFamily: TEXT, fontSize: 36, color: GOLD }}>{board.subtitle}</div>
      <div style={{ display: "flex", gap: 34, marginTop: 30 }}>
        {board.players.map((p, i) => {
          const s = spring({ frame: frame - 10 - i * 5, fps, config: { damping: 12 } });
          return (
            <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10, transform: `scale(${s})` }}>
              <div style={{ width: 120, height: 120, borderRadius: "50%", border: `7px solid ${p.color}`, display: "grid",
                            placeItems: "center", fontFamily: DISPLAY, fontWeight: 800, color: p.color,
                            background: "#141b18", fontSize: p.mono.length > 2 ? 44 : 56 }}>{p.mono}</div>
              <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 30, color: INK }}>{p.name}</div>
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
}

function Outro({ board }: { board: Storyboard }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const top = board.standings[0].chips;
  const color = (name: string) => board.players.find((p) => p.name === name)!.color;
  return (
    <AbsoluteFill style={{ background: "rgba(5,8,7,.92)", display: "flex", flexDirection: "column", alignItems: "center",
                           justifyContent: "center", gap: 18, opacity: spring({ frame, fps, config: { damping: 20 } }) }}>
      <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 84, color: INK, marginBottom: 16 }}>
        {board.score ? board.score : "Final chips"}
      </div>
      {board.standings.map((s, i) => {
        const grow = spring({ frame: frame - 8 - i * 6, fps, config: { damping: 18 } });
        return (
          <div key={s.name} style={{ display: "flex", alignItems: "center", gap: 22, width: 1250 }}>
            <div style={{ width: 300, textAlign: "right", fontFamily: DISPLAY, fontWeight: 800, fontSize: 40, color: color(s.name) }}>{s.name}</div>
            <div style={{ flex: 1, height: 48, background: "rgba(255,255,255,.06)", borderRadius: 10 }}>
              <div style={{ width: `${(s.chips / top) * 100 * grow}%`, height: "100%", borderRadius: 10, background: color(s.name),
                            minWidth: s.chips ? 8 : 0 }} />
            </div>
            <div style={{ width: 170, fontFamily: DISPLAY, fontWeight: 800, fontSize: 40, color: INK }}>
              {s.chips ? fmt(Math.round(s.chips * grow)) : "Out"}
            </div>
          </div>
        );
      })}
      <div style={{ fontFamily: TEXT, fontSize: 34, color: GOLD, marginTop: 24 }}>{board.summary}</div>
    </AbsoluteFill>
  );
}

export const Episode = ({ board }: { board: Storyboard }) => {
  const tableStart = 0;
  const outroAt = board.frames - board.outro;
  return (
    <AbsoluteFill style={{ background: "#070c0a" }}>
      <Sequence from={tableStart} durationInFrames={outroAt}>
        <TableScene board={board} />
      </Sequence>
      {board.intro > 0 && (
        <Sequence from={0} durationInFrames={board.intro}>
          <Intro board={board} />
        </Sequence>
      )}
      {board.outro > 0 && (
        <Sequence from={outroAt}>
          <Outro board={board} />
        </Sequence>
      )}
      {board.beats.map((b, i) => b.audio && (
        <Sequence key={i} from={board.intro + b.from} durationInFrames={b.frames}>
          <Audio src={staticFile(`audio/${b.audio}`)} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
