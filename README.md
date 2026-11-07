# Kukicha

A music library, organizer, and streaming server written in Go. Features include:

- Playback from local disk and S3-compatible storage
- Radio stations that play a shuffled sequence of tracks from your library for a selected artist, genre, or style (subgenre), without AI or a recommendation engine
- Genre and artwork lookup from Discogs and MusicBrainz
- Custom artist mapping rules to organize artists the way you want

## Getting started

Download and extract the release for your platform, then set a login
passphrase and a key for signing browser sessions.

```sh
export KUKICHA_PASSPHRASE='your passphrase'
export KUKICHA_JWT_SECRET="$(openssl rand -base64 32)"
./kukicha
```

Save the generated key and reuse it when restarting Kukicha to keep
existing sessions valid.

Open http://localhost:4533 and sign in with your passphrase.

## Configuration

Kukicha creates its configuration file and database on first startup.

Run `./kukicha --help` to see the configuration location and setup
instructions. Running with any argument displays help without starting
the server or creating files.

## Development

Go and ICU development libraries are required.

```sh
make run
```

The original Python prototype is available at the `python-prototype` tag.
