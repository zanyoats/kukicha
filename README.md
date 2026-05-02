# kukicha

`kukicha` serves a local browser player backed by a SQLite music-library
database. The player can add, rescan, sync library root paths, browse albums, search the library, and play tracks in the browser.

The player database holds:

- packaged genre/style taxonomy lookup tables
- the scanned local library

## Install With pipx

Kukicha is not published to PyPI yet. Install it from a checked-out project root with `pipx`:

```bash
# from the project root
pipx ensurepath
pipx install .
```

Verify the install:

```bash
which kukicha
pipx list
kukicha --help
```

Updates can be installed using force flag:

```bash
pipx install --force .
```

For contributor setup with an editable install and test commands, see
[DEVELOPMENT.md](DEVELOPMENT.md).

## Configure The Player

By default the player reads its config from
`$XDG_CONFIG_HOME/kukicha/kukicha.toml` or `~/.config/kukicha/kukicha.toml`.
If that file is missing, Kukicha uses built-in defaults and stores the default
database at `kukicha.sqlite` in the same config directory.

Create the config directory and file:

```bash
mkdir -p ~/.config/kukicha
$EDITOR ~/.config/kukicha/kukicha.toml
```

Example config:

```toml
LogLevel = "INFO"
Roots = ["/Users/YOUR_USERNAME/Music"]
```

Supported keys:

- `LogLevel`: Python logging level name, such as `DEBUG`, `INFO`, or `WARNING`.
- `DatabasePath`: SQLite database path. Relative paths are resolved from the
  config file directory.
- `Roots`: music library folders to scan. Relative paths are resolved from the
  config file directory. Roots can also be managed from the Roots page.
- `FFmpegPath`: optional path to an executable `ffmpeg`; leave empty to unset.
- `Host`: interface to bind, defaulting to `127.0.0.1`.
- `Port`: TCP port from `1` to `65535`, defaulting to `65042`.
- `AccentColor`: palette name or matching hex code. Run `kukicha --help` for the
  full palette list.
- `Appearance`: `light`, `dark`, or `dim`.
- `ToastTimeoutMs`: positive toast timeout in milliseconds.
- `AlbumArtistSplitPatterns`: strings used when splitting album artist names.

Run `kukicha --help` to print the active config path, current values, supported
keys, accent colors, and appearance names.

## Run The Player

Launch the local browser player:

```bash
kukicha
```

Or point it at an explicit config file:

```bash
kukicha -c /path/to/config/kukicha.toml
```

The default player URL is:

```text
http://127.0.0.1:65042
```

The player runs as a foreground HTTP service so launchd, systemd, and similar
service managers can supervise it directly. Logs go to normal stdout/stderr (with
timestamps).

The player provides album browsing, playback, full-text search, and filters for
library roots, artists, genres, styles, and album properties. Search indexes
album titles, album artists, and track titles. Quoted terms match exact token
phrases, spaces mean AND, semicolons mean OR, and a leading `-` excludes a term.

## Bulk Tag Edit

Rewrite album-level tags for every supported music file under a folder:

```bash
kukicha tools bulk-tag-edit \
  --folder "/Users/YOUR_USERNAME/Library/Mobile Documents/com~apple~CloudDocs/music/downloaded2/Richard David James" \
  --album-artist "Richard David James" \
  --album "Soundcloud" \
  --genre "Electronic"
```

The command recurses with the same supported audio extensions used by the scanner
and only writes album artist, album title, and genre tags. It has been convenient for a bulk tag edit (album level) in some circumstances

## Run With launchd

Save this as `~/Library/LaunchAgents/com.kukicha.player.plist` and adjust paths
as needed:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.kukicha.player</string>

  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOUR_USERNAME/.local/bin/kukicha</string>
    <string>-c</string>
    <string>/Users/YOUR_USERNAME/.config/kukicha/kukicha.toml</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>

  <key>StandardOutPath</key>
  <string>/Users/YOUR_USERNAME/Library/Logs/kukicha-player.log</string>

  <key>StandardErrorPath</key>
  <string>/Users/YOUR_USERNAME/Library/Logs/kukicha-player.err.log</string>
</dict>
</plist>
```

Load and start it:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kukicha.player.plist
```

Show the status
```bash
launchctl print gui/$(id -u)/com.kukicha.player
```

Restart the running server:

```bash
launchctl kickstart -k gui/$(id -u)/com.kukicha.player
```

Shut it down and unload it:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.kukicha.player.plist
```

After changing the plist, unload it with `bootout`, then load it again with
`bootstrap`. The `bootout` and `kickstart -k` commands trigger normal process
shutdown, and Kukicha logs shutdown with the same timestamped stdout/stderr
logging as startup.
