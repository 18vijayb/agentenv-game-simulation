import React from "react";
import { AbsoluteFill, Sequence, Audio, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { ActionBanner, Intro, Line, CENTER, DISPLAY, GOLD, INK, TEXT, seatPos } from "./Episode";
import type { Beat, Player, Seat, Storyboard } from "./types";

const FILL: Record<string, string> = { red: "#e53935", green: "#43a047", blue: "#1e88e5", yellow: "#fdd835" };
const GLYPH: Record<string, string> = { skip: "⊘", reverse: "⇄", draw2: "+2" };

/* ---------- cards ---------- */

export function UnoCard({ card, w = 60, face = true, flipAt }: { card: string | null; w?: number; face?: boolean; flipAt?: number }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const h = w * 1.45;
  const flip = flipAt === undefined ? 1 : spring({ frame: frame - flipAt, fps, config: { damping: 14, mass: 0.6 } });
  const base: React.CSSProperties = {
    width: w, height: h, borderRadius: w * 0.12, border: `${Math.max(2, w / 16)}px solid #fff`, position: "relative", flexShrink: 0,
    boxShadow: "0 6px 14px rgba(0,0,0,.5)", transform: `scaleX(${flip})`, overflow: "hidden", fontFamily: DISPLAY, fontWeight: 800,
    display: "grid", placeItems: "center", boxSizing: "border-box",
  };
  if (!card || !face) {
    return <div style={{ ...base, background: "#b71c1c" }}>
      <div style={{ width: w * 0.72, height: h * 0.58, borderRadius: "50%", background: "#fde68a", opacity: 0.9, display: "grid", placeItems: "center",
                    transform: "rotate(-25deg)", color: "#b71c1c", fontSize: w * 0.3 }}>UNO</div>
    </div>;
  }
  if (card === "wild" || card === "wild4") {
    return <div style={{ ...base, background: `conic-gradient(${FILL.red} 0 25%, ${FILL.blue} 0 50%, ${FILL.yellow} 0 75%, ${FILL.green} 0)` }}>
      <div style={{ fontSize: card === "wild" ? w * 0.42 : w * 0.6, color: "#fff", textShadow: "0 0 6px #000, 0 2px 0 #000" }}>{card === "wild" ? "WILD" : "+4"}</div>
    </div>;
  }
  const [color, rank] = card.split(" ");
  const glyph = GLYPH[rank] ?? rank;
  return <div style={{ ...base, background: FILL[color], color: color === "yellow" ? "#1b1b1b" : "#fff" }}>
    <div style={{ position: "absolute", inset: w * 0.12, borderRadius: "50% / 40%", background: "rgba(255,255,255,.2)", transform: "rotate(-25deg)" }} />
    <div style={{ position: "relative", fontSize: glyph.length === 1 ? w * 0.78 : w * 0.5, textShadow: "0 2px 0 rgba(0,0,0,.35)" }}>{glyph}</div>
  </div>;
}

/* ---------- table ---------- */

function UnoFelt() {
  return (
    <AbsoluteFill style={{ background: "radial-gradient(ellipse at 50% 45%, #0f1418 0%, #090c0f 75%)" }}>
      <div style={{ position: "absolute", left: CENTER.x - 580, top: CENTER.y - 390, width: 1160, height: 780, borderRadius: "50%",
                    background: "linear-gradient(160deg, #6b4127, #3a2113)", boxShadow: "0 30px 80px rgba(0,0,0,.7)" }} />
      <div style={{ position: "absolute", left: CENTER.x - 554, top: CENTER.y - 364, width: 1108, height: 728, borderRadius: "50%",
                    background: "radial-gradient(ellipse at 50% 40%, #2c7a4e 0%, #1b4332 58%, #143526 100%)",
                    boxShadow: "inset 0 0 90px rgba(0,0,0,.55), 0 0 0 3px rgba(227,185,86,.35)" }} />
    </AbsoluteFill>
  );
}

function Piles({ beat, start }: { beat: Beat; start: number }) {
  const u = beat.table.uno!;
  const color = u.color ? FILL[u.color] : "transparent";
  return (
    <div style={{ position: "absolute", left: CENTER.x, top: CENTER.y - 30, transform: "translate(-50%, -50%)",
                  display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
      <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 26, color: "#b7c7b5", letterSpacing: 1 }}>
        Turn {u.turn ?? "–"}{u.maxTurns ? ` of ${u.maxTurns}` : ""}{u.toPlay && u.toPlay !== "game over" ? ` · to play: ${u.toPlay}` : u.toPlay === "game over" ? " · game over" : ""}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 40 }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
          <div style={{ position: "relative", width: 120, height: 174 }}>
            <div style={{ position: "absolute", left: 0, top: 0, transform: "rotate(-6deg)" }}><UnoCard card={null} face={false} w={120} /></div>
            <div style={{ position: "absolute", left: 0, top: 0, transform: "rotate(4deg)" }}><UnoCard card={null} face={false} w={120} /></div>
            <div style={{ position: "absolute", left: 0, top: 0 }}><UnoCard card={null} face={false} w={120} /></div>
          </div>
          <div style={{ fontFamily: TEXT, fontSize: 22, color: "#cfe3d2" }}>draw pile · {u.pile}</div>
        </div>
        <div style={{ fontSize: 76, color: "#d9e7d6", lineHeight: 1, textShadow: "0 2px 8px rgba(0,0,0,.6)" }}>{u.direction === 1 ? "↻" : "↺"}</div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
          <div style={{ position: "relative", width: 120, height: 174, borderRadius: 20, boxShadow: u.top && u.top.startsWith("wild") ? `0 0 0 6px ${color}` : undefined }}>
            <div style={{ position: "absolute", left: 0, top: 0, transform: "rotate(-5deg)" }}><UnoCard card={null} face={false} w={120} /></div>
            <div style={{ position: "absolute", left: 0, top: 0 }}><UnoCard card={u.top} w={120} flipAt={start} /></div>
          </div>
          <div style={{ fontFamily: TEXT, fontSize: 22, color: "#cfe3d2" }}>discard · {u.color ?? ""}</div>
        </div>
      </div>
      <div style={{ fontFamily: TEXT, fontSize: 22, color: "#9fb59e", maxWidth: 520, textAlign: "center" }}>{u.last}</div>
    </div>
  );
}

function UnoSeat({ seat, player, i, n, active, thinking, start, revealCards }: {
  seat: Seat; player: Player; i: number; n: number; active: boolean; thinking: boolean; start: number; revealCards: boolean;
}) {
  const frame = useCurrentFrame();
  const { x, y } = seatPos(i, n);
  const pulse = active ? 1 + 0.03 * Math.sin((frame - start) / 4) : 1;
  const shown = seat.cards.slice(0, 10);
  const w = shown.length > 7 ? 40 : 48;
  const count = seat.count ?? seat.cards.length;
  return (
    <div style={{ position: "absolute", left: x, top: y, transform: `translate(-50%, -50%) scale(${pulse})`,
                  display: "flex", flexDirection: "column", alignItems: "center", gap: 8, width: 300 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, background: "rgba(12,16,20,.86)", borderRadius: 999, padding: "6px 18px 6px 6px",
                    boxShadow: active ? `0 0 0 4px ${thinking ? "rgba(241,234,216,.35)" : GOLD}, 0 0 40px ${player.color}` : seat.winner ? `0 0 0 4px ${GOLD}` : "0 8px 20px rgba(0,0,0,.5)" }}>
        <div style={{ width: 62, height: 62, borderRadius: "50%", background: "#141b18", display: "grid", placeItems: "center",
                      border: `5px solid ${player.color}`, fontFamily: DISPLAY, fontWeight: 800, color: player.color, fontSize: player.mono.length > 2 ? 24 : 30 }}>{player.mono}</div>
        <div>
          <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 30, color: INK, lineHeight: 1.05 }}>{player.label}</div>
          <div style={{ fontFamily: TEXT, fontSize: 20, color: seat.uno ? "#f28b82" : "#9aa59f" }}>
            {seat.winner ? "winner" : `${count} card${count === 1 ? "" : "s"}${seat.uno ? " · UNO!" : ""}`}
          </div>
        </div>
      </div>
      <div style={{ display: "flex", height: w * 1.45 }}>
        {shown.map((c, k) => (
          <div key={k} style={{ marginLeft: k ? -w * 0.42 : 0 }}><UnoCard card={c} w={w} face={revealCards} /></div>
        ))}
        {count > shown.length && <div style={{ alignSelf: "center", marginLeft: 8, fontFamily: DISPLAY, fontSize: 24, color: INK }}>+{count - shown.length}</div>}
      </div>
    </div>
  );
}

function UnoLeaderboard({ board, idx }: { board: Storyboard; idx: number }) {
  const seats = board.beats[idx].table.seats.map((s, i) => ({ ...s, player: board.players[i] }))
    .sort((a, b) => (a.count ?? 99) - (b.count ?? 99));
  return (
    <div style={{ background: "rgba(10,15,13,.8)", border: "1px solid rgba(241,234,216,.12)", borderRadius: 16, padding: "12px 18px", display: "grid", gap: 4 }}>
      <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 22, color: GOLD }}>Cards in hand</div>
      {seats.map((s) => (
        <div key={s.name} style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ width: 12, height: 12, borderRadius: "50%", background: s.player.color }} />
          <div style={{ flex: 1, fontFamily: DISPLAY, fontWeight: 700, fontSize: 26, color: INK }}>{s.player.name}</div>
          <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 26, color: s.uno ? "#f28b82" : INK }}>{s.winner ? "out!" : s.count}{s.uno ? " UNO" : ""}</div>
        </div>
      ))}
    </div>
  );
}

function UnoPanel({ board, idx, local }: { board: Storyboard; idx: number; local: number }) {
  const spoken = board.beats.slice(0, idx + 1).filter((b) => b.mode !== "act");
  const beat = board.beats[idx];
  const recent = spoken.slice(-3);
  const current = beat.mode !== "act" ? recent.pop() : undefined;
  return (
    <div style={{ position: "absolute", left: 1350, width: 530, top: 40, bottom: 40, display: "flex", flexDirection: "column", gap: 16, zIndex: 5 }}>
      <div style={{ fontFamily: DISPLAY, fontWeight: 700, fontSize: 30, color: "rgba(241,234,216,.55)", textAlign: "right" }}>{board.title}</div>
      <UnoLeaderboard board={board} idx={idx} />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", gap: 14, overflow: "hidden" }}>
        {recent.map((b) => <Line key={b.from} beat={b} board={board} local={0} current={false} />)}
        {current && <Line key={current.from} beat={current} board={board} local={local} current />}
      </div>
    </div>
  );
}

function UnoHeader({ beat }: { beat: Beat }) {
  const u = beat.table.uno!;
  return (
    <div style={{ position: "absolute", left: 56, top: 40, color: INK, zIndex: 4 }}>
      <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 58, lineHeight: 1 }}>UNO</div>
      <div style={{ fontFamily: TEXT, fontSize: 26, color: "#b9c2bc", marginTop: 6 }}>
        {u.color ? `Colour in play: ${u.color}` : ""}{u.direction === 1 ? " · clockwise" : " · counter-clockwise"}
      </div>
    </div>
  );
}

function UnoTableScene({ board }: { board: Storyboard }) {
  const frame = useCurrentFrame() - board.intro;
  const idx = Math.max(0, board.beats.findIndex((b) => frame >= b.from && frame < b.from + b.frames));
  const beat = board.beats[idx];
  const local = frame - beat.from;
  const n = board.players.length;
  const t = beat.table;
  return (
    <AbsoluteFill>
      <UnoFelt />
      <UnoHeader beat={beat} />
      <Piles beat={beat} start={beat.from + board.intro} />
      {t.seats.map((s, i) => (
        <UnoSeat key={i} seat={s} player={board.players[i]} i={i} n={n} start={beat.from + board.intro}
                 active={beat.speaker === i} thinking={beat.mode === "think"} revealCards />
      ))}
      <UnoPanel board={board} idx={idx} local={local} />
      {beat.speaker !== null && beat.action && beat.mode !== "think" && (
        <ActionBanner beat={beat} player={board.players[beat.speaker]} n={n} local={local} />
      )}
    </AbsoluteFill>
  );
}

function UnoOutro({ board }: { board: Storyboard }) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const most = Math.max(1, ...board.standings.map((s) => s.count ?? 0));
  const color = (name: string) => board.players.find((p) => p.name === name)!.color;
  return (
    <AbsoluteFill style={{ background: "rgba(5,8,7,.92)", display: "flex", flexDirection: "column", alignItems: "center",
                           justifyContent: "center", gap: 18, opacity: spring({ frame, fps, config: { damping: 20 } }) }}>
      <div style={{ fontFamily: DISPLAY, fontWeight: 800, fontSize: 84, color: INK, marginBottom: 16 }}>Cards left</div>
      {board.standings.map((s, i) => {
        const grow = spring({ frame: frame - 8 - i * 6, fps, config: { damping: 18 } });
        const c = s.count ?? 0;
        return (
          <div key={s.name} style={{ display: "flex", alignItems: "center", gap: 22, width: 1250 }}>
            <div style={{ width: 300, textAlign: "right", fontFamily: DISPLAY, fontWeight: 800, fontSize: 40, color: color(s.name) }}>{s.name}</div>
            <div style={{ flex: 1, height: 48, background: "rgba(255,255,255,.06)", borderRadius: 10 }}>
              <div style={{ width: `${(c / most) * 100 * grow}%`, height: "100%", borderRadius: 10, background: color(s.name), minWidth: c ? 8 : 0 }} />
            </div>
            <div style={{ width: 170, fontFamily: DISPLAY, fontWeight: 800, fontSize: 40, color: c === 0 ? GOLD : INK }}>{c === 0 ? "Winner" : Math.round(c * grow)}</div>
          </div>
        );
      })}
      <div style={{ fontFamily: TEXT, fontSize: 34, color: GOLD, marginTop: 24, maxWidth: 1500, textAlign: "center" }}>{board.summary}</div>
    </AbsoluteFill>
  );
}

export const UnoEpisode = ({ board }: { board: Storyboard }) => {
  const outroAt = board.frames - board.outro;
  return (
    <AbsoluteFill style={{ background: "#090c0f" }}>
      <Sequence from={0} durationInFrames={outroAt}><UnoTableScene board={board} /></Sequence>
      <Sequence from={0} durationInFrames={board.intro}><Intro board={board} /></Sequence>
      <Sequence from={outroAt}><UnoOutro board={board} /></Sequence>
      {board.beats.map((b, i) => b.audio && (
        <Sequence key={i} from={board.intro + b.from} durationInFrames={b.frames}><Audio src={staticFile(`audio/${b.audio}`)} /></Sequence>
      ))}
    </AbsoluteFill>
  );
};
