export type Seat = {
  name: string; cards: string[]; chips: number; bet: number;
  dealer: boolean; folded: boolean; allin: boolean; out: boolean;
};
export type Table = {
  seats: Seat[]; board: string[]; pot: number; hand: number | null; hands: number | null;
  blinds: string | null; street: string | null;
};
export type Beat = {
  from: number; frames: number; mode: "say" | "think" | "narrate" | "act"; speaker: number | null;
  text: string; action: string | null; audio: string | null; table: Table; seq: number;
};
export type Player = { name: string; color: string; label: string; mono: string };
export type Storyboard = {
  title: string; subtitle: string; fps: number; intro: number; outro: number; frames: number;
  players: Player[]; beats: Beat[]; standings: { name: string; chips: number }[]; summary: string;
};
