import { up } from "./unpoly.js";
import registerPlayerControls from "./player-controls.js";
import registerPlayerEngine from "./player-engine.js";
import registerPlayerQueue from "./player-queue.js";

up.log.config.banner = false;
up.history.config.restoreTargets = ["[up-main]"];

registerPlayerEngine(up);
registerPlayerControls(up);
registerPlayerQueue(up);
