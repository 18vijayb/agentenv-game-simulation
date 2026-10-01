import { Composition } from "remotion";
import { Episode } from "./Episode";
import storyboard from "../public/storyboard.json";
import type { Storyboard } from "./types";

const board = storyboard as Storyboard;

export const Root = () => (
  <Composition
    id="Episode"
    component={Episode}
    durationInFrames={board.frames}
    fps={board.fps}
    width={1920}
    height={1080}
    defaultProps={{ board }}
  />
);
