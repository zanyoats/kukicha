# kukicha

[![Tests](https://github.com/zanyoats/kukicha/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/zanyoats/kukicha/actions/workflows/tests.yml)

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

## Install With uv

Kukicha is not published to PyPI yet. Install it from a checked-out project root
with `uv`:

```bash
# from the project root
uv tool install .
```

Verify the install:

```bash
which kukicha
uv tool list
kukicha --help
```

Updates can be installed using force flag:

```bash
uv tool install --force .
```

For contributor setup with an editable install and test commands, see
[DEVELOPMENT.md](DEVELOPMENT.md).

## Configure The Player

By default the player reads its config from
`$XDG_CONFIG_HOME/kukicha/kukicha.toml` or `~/.config/kukicha/kukicha.toml`.
If that file is missing, startup fails. Run `kukicha init` once to create the
config file and password hash file.

Interactive setup prompts for a username and password, stores an Argon2id
password hash at `password.hash` beside the config file, and writes a required
`[auth]` section:

```bash
kukicha init
```

Automation can provide credentials through environment variables and pipe extra
TOML config on stdin. The stdin TOML must not include `[auth]`; `kukicha init`
generates that section.

```bash
KUKICHA_USERNAME=listener KUKICHA_PASSWORD="$PASSWORD" kukicha init <<'TOML'
log_level = "INFO"
roots = ["/Users/YOUR_USERNAME/Music"]
appearance = "dim"
accent_color = "dark-orange"
TOML
```

Example config after initialization:

```toml
log_level = "INFO"
roots = ["/Users/YOUR_USERNAME/Music"]
youtube_download_path = "/Users/YOUR_USERNAME/Music/YouTube"
prefer_musicbrainz_english_aliases = true

[auth]
username = "listener"
password_hash_file = "~/.config/kukicha/password.hash"
cookie_max_age = "180d"
cookie_name = "kukicha_cookie"
```

Supported keys:

- `log_level`: Python logging level name, such as `DEBUG`, `INFO`, or `WARNING`.
- `database_path`: SQLite database path. Relative paths are resolved from the
  config file directory.
- `roots`: music library folders to scan. Relative paths are resolved from the
  config file directory. Roots can also be managed from the Roots page.
- `ffmpeg_path`: optional path to an executable `ffmpeg`; leave empty to unset.
- `youtube_download_path`: folder where YouTube chapter audio downloads are
  written. Relative paths are resolved from the config file directory.
- `prefer_musicbrainz_english_aliases`: when writing MusicBrainz album tags, prefer
  the first English artist alias from the MusicBrainz payload. Defaults to
  `true`.
- `host`: interface to bind, defaulting to `127.0.0.1`.
- `port`: TCP port from `1` to `65535`, defaulting to `4533`.
- `trusted_proxy_headers`: trust `X-Forwarded-For`, `X-Forwarded-Proto`, and
  `X-Forwarded-Host` from one reverse proxy hop. Defaults to `false`; only
  enable when direct access to Kukicha is blocked, such as when bound to
  `127.0.0.1` behind a local reverse proxy.
- `accent_color`: palette name or matching hex code. Run `kukicha --help` for the
  full palette list.
- `appearance`: `light`, `dark`, `dim`, or `system`. `system` follows the
  browser's `prefers-color-scheme`, using `light` for light mode and `dim` for
  dark mode. Defaults to `system`.
- `toast_timeout_ms`: positive toast timeout in milliseconds.
- `album_artist_split_patterns`: strings used when splitting album artist names.
- `[auth].username`: browser login username.
- `[auth].password_hash_file`: Argon2id password hash path. Relative paths are
  resolved from the config file directory; the file must be owned by the current
  user with `0600` permissions on POSIX systems.
- `[auth].cookie_max_age`: persistent login cookie age as days, such as `30d`
  or `180d`. Defaults to `180d`.
- `[auth].cookie_name`: browser login cookie name. Defaults to
  `kukicha_cookie`.
- `[opensubsonic].mount_prefix`: optional OpenSubsonic mount prefix. Use `/` for
  `/rest/ping`, or `/sonic` for `/sonic/rest/ping`.
- `[opensubsonic].secret_file`: plain shared OpenSubsonic password file. Relative
  paths are resolved from the config file directory; the file must be owned by
  the current user with `0600` permissions on POSIX systems.

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
http://127.0.0.1:4533
```

The player runs as a foreground HTTP service so launchd, systemd, and similar
service managers can supervise it directly. Logs go to normal stdout/stderr (with
timestamps).

The browser UI requires login. Successful login stores an HTTP-only
SameSite=Strict cookie for the configured age.

To change the browser login password later, run:

```bash
kukicha --config /path/to/kukicha.toml auth password
```

This creates or rewrites only the configured password hash file and invalidates
existing browser login cookies for that config. If you wrote `[auth]` into the
config before creating `password_hash_file`, use this command to bootstrap that
file.

The player provides album browsing, playback, full-text search, and filters for
library roots, artists, genres, styles, and album properties. Search indexes
album titles, album artists, and track titles. Quoted terms match exact token
phrases, spaces mean AND, semicolons mean OR, and a leading `-` excludes a term.

## Mount The OpenSubsonic API

OpenSubsonic endpoints are served by the same Kukicha HTTP server as the browser
player. By default they are not mounted and `/rest/...` returns 404.

Initialize the optional `[opensubsonic]` config:

```bash
kukicha opensubsonic init
```

For scripts, provide the password and mount prefix through the environment:

```bash
OPENSUBSONIC_PASSWORD="$PASSWORD" OPENSUBSONIC_MOUNT="/" kukicha opensubsonic init
```

This appends a section like:

```toml
[opensubsonic]
mount_prefix = "/"
secret_file = "~/.config/kukicha/opensubsonic.secret"
```

With `mount_prefix = "/"`, the ping endpoint is `/rest/ping`. With
`mount_prefix = "/sonic"`, it is `/sonic/rest/ping`.

To change the OpenSubsonic password later, run:

```bash
kukicha --config /path/to/kukicha.toml opensubsonic password
```

This creates or rewrites the configured `secret_file`, so it also supports
declarative configs where `[opensubsonic]` exists before the secret file does.

The API supports basic album and artist browsing, direct streaming, downloads,
cover art, password auth, salted token auth, JSON responses, and GET or form
POST parameters.

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

Set `youtube_download_path` in `kukicha.toml` before running this command. The
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
