import { up } from "./unpoly.js";
import registerPlayerControls from "./player-controls.js";
import registerPlayerEngine from "./player-engine.js";
import registerAlbumPage from "./album-page.js";
import registerPlayerQueuePanel from "./player-queue-panel.js";
import registerArtistSplitRules from "./artist-split-rules.js";

up.log.config.banner = false;
up.history.config.restoreTargets = ["[up-main]"];

registerPlayerEngine(up);
registerPlayerControls(up);
registerAlbumPage(up);
registerPlayerQueuePanel(up);
registerArtistSplitRules(up);
