# kukicha

`kukicha` focuses on managing and streaming your audio library using a http server backed by single sqlite database file. It comes with a simple and fast builtin web UI.

Some noteworthy features:
- It supports both POSIX and Windows.
- Text/token based search & filters.
- Artist tag cloud page
- Albums grid page
- Easily sync library root paths
- Supports most audio formats
- Never transcodes audio streams
- Playlist are ordinary m3u, m3u8, pls files
- Genre/style taxonomy provides clean data
- Artist split patterns overrides (avoid artist names like `Brian Eno with Jon Hopkins & Leo Abrahams`)
- iTunes cover art lookup
- Musicbrainz release group & release IDs overrides
- Overwrite album-level audio tags for album artist, genre
- Overwrite track-level audio tags for artist, album title

Roadmap
- Mount remote library roots (S3, etc.)
- Support subset of Opensonic API to support different clients
- Live stream a playlist

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
# when we move to versions use `pipx upgrade kukicha`
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
YoutubeDownloadPath = "/Users/YOUR_USERNAME/Music/YouTube"
PreferMusicBrainzEnglishAliases = true
```

Supported keys:

- `LogLevel`: Python logging level name, such as `DEBUG`, `INFO`, or `WARNING`.
- `DatabasePath`: SQLite database path. Relative paths are resolved from the
  config file directory.
- `Roots`: music library folders to scan. Relative paths are resolved from the
  config file directory. Roots can also be managed from the Roots page.
- `FFmpegPath`: optional path to an executable `ffmpeg`; leave empty to unset.
- `YoutubeDownloadPath`: folder where YouTube chapter audio downloads are
  written. Relative paths are resolved from the config file directory.
- `PreferMusicBrainzEnglishAliases`: when writing MusicBrainz album tags, prefer
  the first English artist alias from the MusicBrainz payload. Defaults to
  `true`.
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

## YouTube Audio

Download audio-only YouTube media. Video URLs are split into chapter files:

```bash
kukicha tools yt-download-audio "https://www.youtube.com/watch?v=VIDEO_ID"
```

If yt-dlp does not report chapters, or you want to override them, provide a
manual chapter file:

```bash
kukicha -c ~/kukicha.toml tools yt-download-audio \
  --chapters-file chapters.txt \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

The chapter file uses one chapter per nonblank line. Lines starting with `#`
are ignored:

```text
0:00 Intro
03:12 - Track Two
1:02:03.5 Finale
```

Playlist URLs are downloaded as one audio file per playlist item. Chapters
reported inside individual playlist items are ignored, and `--chapters-file`
cannot be used with playlist URLs.

Set `YoutubeDownloadPath` in `kukicha.toml` before running this command. The
tool checks that `ffmpeg`, `ffprobe`, and Deno 2.0.0 or newer are available.
yt-dlp temporary and staged files are kept in the user's OS temp folder and are
cleaned up when the command exits.

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
