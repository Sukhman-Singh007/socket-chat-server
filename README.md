# Socket Chat Server

A multithreaded TCP chat server and terminal client in pure Python
(standard library only — no dependencies). Connect with several
clients, join rooms, send private messages, and change your name.

## Quick start

Terminal 1 — start the server:

```bash
python server.py
# Chat server listening on 127.0.0.1:65432
```

Terminal 2 and 3 — join as clients:

```bash
python client.py
# USERNAME: Enter a username ...: alice
# WELCOME alice! You are in #lobby. Type /help to see commands.
```

Type a message and it goes to everyone in your room. Open as many
client terminals as you like.

To let other machines on your local network connect, start the
server with `python server.py --host 0.0.0.0` and point the clients
at the server machine's address with `--host <address>`.

## Commands

| Command | What it does |
|---|---|
| `/help` | Show the command list. |
| `/rooms` | List rooms and how many people are in each. |
| `/users` | List users in your current room. |
| `/join <room>` | Move to a room (created if it does not exist). |
| `/msg <user> <text>` | Send a private message to one user. |
| `/me <action>` | Send an action to your room, e.g. `/me waves` → `* alice waves`. |
| `/nick <name>` | Change your username. |
| `/quit` | Leave the chat. |

Anything that is not a command is broadcast to everyone in your
current room, including yourself.

## How it works

- **One thread per client.** The server accepts connections in a
  loop and hands each one to its own thread, so a slow or idle
  client never blocks anyone else.
- **Line protocol.** Everything is plain text, one line per
  message. The server asks for a username first (`USERNAME:`),
  then the client sends lines and the server sends lines back.
  Because the protocol is just text, any TCP client (telnet,
  netcat) can connect — the Python client is only a convenience.
- **Shared state, one lock.** The list of connected users and the
  rooms they are in live in two dictionaries guarded by a single
  `threading.Lock`. Broadcasts snapshot the recipient list under
  the lock, then send outside it, so a slow socket cannot stall
  the whole server.
- **Rooms and DMs.** Every user starts in `#lobby`. `/join`
  creates a room on demand and removes it when it empties.
  `/msg` looks the target up by username and delivers only to
  them.

## Tests

The test suite starts the real server on a free localhost port and
drives it over real TCP sockets — no mocks of the networking:

```bash
python -m unittest test_chat -v
```

It covers room broadcast, room isolation, private messages, action
messages, duplicate usernames, room/user listings, leave
notifications, nickname changes, and unknown commands.

## Project structure

```
server.py      The chat server (ChatServer class + CLI entry point).
client.py      The terminal client (handshake + receive thread).
test_chat.py   Integration tests over real sockets.
README.md      This file.
```

## Notes and limits

- Traffic is **unencrypted** and there is no authentication
  beyond picking a username. This is a learning project, not a
  production chat service — run it on localhost or a trusted
  local network.
- Usernames are 1–20 characters (letters, digits, `_`, `-`).
  Room names are 1–30 characters with the same rules.
- If a message arrives while you are typing in the client, it
  prints above your half-typed line. A curses-style UI would fix
  that; it is out of scope here.
- Python 3.8 or newer. No third-party packages needed.
