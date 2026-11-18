#!/usr/bin/env python3
"""socket-chat-server — a multithreaded TCP chat server.

Run:
    python server.py [--host 127.0.0.1] [--port 65432]

Clients connect with the matching client (see client.py) or with any
TCP client such as telnet / netcat. The protocol is plain text, one
line per message:

  * The server asks for a username first (must be unique).
  * Any line that is not a command is broadcast to everyone in the
    sender's current room, including the sender.
  * Lines starting with "/" are commands — type /help in the client
    for the full list.

Rooms are created on demand with /join. Private messages go with
/msg <user> <text>. One thread handles each connected client; all
shared state (who is in which room) is guarded by a single lock.

This is a learning / portfolio project, not a production chat
service: traffic is unencrypted and there is no authentication
beyond picking a username. Run it on localhost, or on a trusted
local network only.
"""

import argparse
import re
import socket
import threading

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 65432
LOBBY = "lobby"

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{1,30}$")

HELP_LINES = [
    "Commands:",
    "  /help                  Show this list.",
    "  /rooms                 List rooms and how many people are in each.",
    "  /users                 List users in your current room.",
    "  /join <room>           Move to a room (created if it does not exist).",
    "  /msg <user> <text>     Send a private message to one user.",
    "  /nick <name>           Change your username.",
    "  /quit                  Leave the chat.",
    "Anything else is sent to everyone in your current room.",
]


class ChatServer:
    """A TCP chat server. One thread per client, one lock for shared state."""

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self._sock = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # username -> {"conn": socket, "room": str}
        self._clients = {}
        # room name -> set of usernames
        self._rooms = {LOBBY: set()}
        self._accept_thread = None

    # -- lifecycle -----------------------------------------------------

    def start(self):
        """Bind, listen, and start accepting clients in a background thread."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        # If port 0 was requested, the OS picked a free one — record it.
        self.port = self._sock.getsockname()[1]
        self._sock.listen(50)
        self._sock.settimeout(0.5)
        print(f"Chat server listening on {self.host}:{self.port}")
        print("Press Ctrl+C to stop.")
        self._accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True
        )
        self._accept_thread.start()

    def serve_forever(self):
        """Start the server and block until it is stopped (Ctrl+C)."""
        self.start()
        try:
            while not self._stop.is_set():
                self._stop.wait(1)
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            self.stop()

    def stop(self):
        """Stop accepting and disconnect every client. Idempotent."""
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        with self._lock:
            conns = [info["conn"] for info in self._clients.values()]
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass

    # -- accepting clients ----------------------------------------------

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            thread = threading.Thread(
                target=self._handle_client, args=(conn,), daemon=True
            )
            thread.start()

    def _handle_client(self, conn):
        username = None
        reader = None
        try:
            reader = conn.makefile("r", encoding="utf-8", errors="replace")
            username = self._handshake(conn, reader)
            if username is None:
                return
            self._broadcast(
                LOBBY, f"* {username} joined #{LOBBY}.", exclude=username
            )
            self._send(
                username,
                f"WELCOME {username}! You are in #{LOBBY}. "
                "Type /help to see commands.",
            )
            for line in reader:
                text = line.rstrip("\r\n")
                if not text:
                    continue
                if text.startswith("/"):
                    keep_going, username = self._handle_command(username, text)
                    if not keep_going:
                        break
                else:
                    with self._lock:
                        info = self._clients.get(username)
                    if info is None:
                        break
                    self._broadcast(
                        info["room"], f"{username}: {text}"
                    )
        except (OSError, ValueError):
            # Client disconnected abruptly, or a bad socket — clean up.
            pass
        finally:
            if username is not None:
                self._unregister(username)
            if reader is not None:
                try:
                    reader.close()
                except OSError:
                    pass
            try:
                conn.close()
            except OSError:
                pass

    # -- handshake and registration --------------------------------------

    def _handshake(self, conn, reader):
        """Ask for a username until the client gives a valid, free one.

        Returns the username, or None if the client disconnected.
        """
        while True:
            self._send_raw(
                conn,
                "USERNAME: Enter a username "
                "(letters, digits, _ or -, 1-20 chars):",
            )
            line = reader.readline()
            if not line:
                return None
            name = line.strip()
            if not USERNAME_RE.match(name):
                self._send_raw(
                    conn,
                    "ERROR: Usernames are 1-20 chars: "
                    "letters, digits, _ or -.",
                )
                continue
            with self._lock:
                if name in self._clients:
                    taken = True
                else:
                    taken = False
                    self._clients[name] = {"conn": conn, "room": LOBBY}
                    self._rooms.setdefault(LOBBY, set()).add(name)
            if taken:
                self._send_raw(conn, "ERROR: That username is taken.")
                continue
            return name

    def _unregister(self, username):
        """Remove a user and tell their room they left. Safe to call twice."""
        with self._lock:
            info = self._clients.pop(username, None)
            if info is None:
                return
            room = info["room"]
            members = self._rooms.get(room)
            if members is not None:
                members.discard(username)
                if not members and room != LOBBY:
                    del self._rooms[room]
        self._broadcast(room, f"* {username} left the room.", exclude=username)

    # -- commands ---------------------------------------------------------

    def _handle_command(self, username, text):
        """Run one /command. Returns (keep_going, current_username)."""
        parts = text.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/quit":
            self._send(username, "Goodbye!")
            return False, username

        if cmd == "/help":
            for line in HELP_LINES:
                self._send(username, line)
            return True, username

        if cmd == "/rooms":
            with self._lock:
                rooms = sorted(
                    (name, len(members))
                    for name, members in self._rooms.items()
                )
            self._send(username, "Rooms:")
            for name, count in rooms:
                word = "user" if count == 1 else "users"
                self._send(username, f"  #{name} ({count} {word})")
            return True, username

        if cmd == "/users":
            with self._lock:
                info = self._clients.get(username)
                if info is None:
                    return False, username
                names = sorted(self._rooms.get(info["room"], set()))
                room = info["room"]
            self._send(username, f"Users in #{room}: {', '.join(names)}")
            return True, username

        if cmd == "/join":
            return self._join_room(username, arg)

        if cmd == "/msg":
            return self._send_private(username, arg)

        if cmd == "/nick":
            return self._change_nick(username, arg)

        self._send(username, f"Unknown command: {cmd}. Type /help for a list.")
        return True, username

    def _join_room(self, username, room):
        if not ROOM_RE.match(room):
            self._send(
                username,
                "ERROR: Room names are 1-30 chars: letters, digits, _ or -.",
            )
            return True, username
        with self._lock:
            info = self._clients.get(username)
            if info is None:
                return False, username
            old_room = info["room"]
            if old_room == room:
                already_there = True
            else:
                already_there = False
                info["room"] = room
                old_members = self._rooms.get(old_room)
                if old_members is not None:
                    old_members.discard(username)
                    if not old_members and old_room != LOBBY:
                        del self._rooms[old_room]
                self._rooms.setdefault(room, set()).add(username)
        if already_there:
            self._send(username, f"You are already in #{room}.")
            return True, username
        self._broadcast(old_room, f"* {username} left the room.", exclude=username)
        self._broadcast(room, f"* {username} joined #{room}.", exclude=username)
        self._send(username, f"You joined #{room}.")
        return True, username

    def _send_private(self, username, arg):
        parts = arg.split(None, 1)
        if len(parts) < 2:
            self._send(username, "Usage: /msg <user> <text>")
            return True, username
        target, message = parts
        with self._lock:
            exists = target in self._clients
        if not exists:
            self._send(username, f"No user named {target}.")
            return True, username
        # Send through _send, not the raw socket: if the target's
        # connection died a moment ago, the send fails silently
        # instead of raising OSError and disconnecting the sender.
        self._send(target, f"[DM from {username}] {message}")
        self._send(username, f"[DM to {target}] {message}")
        return True, username

    def _change_nick(self, username, new_name):
        if not USERNAME_RE.match(new_name):
            self._send(
                username,
                "ERROR: Usernames are 1-20 chars: letters, digits, _ or -.",
            )
            return True, username
        if new_name == username:
            self._send(username, f"You are already known as {username}.")
            return True, username
        with self._lock:
            if new_name in self._clients:
                taken = True
            else:
                taken = False
                info = self._clients.pop(username, None)
                if info is None:
                    return False, username
                room = info["room"]
                self._clients[new_name] = info
                members = self._rooms.get(room)
                if members is not None:
                    members.discard(username)
                    members.add(new_name)
        if taken:
            self._send(username, "ERROR: That username is taken.")
            return True, username
        self._broadcast(
            room, f"* {username} is now known as {new_name}.", exclude=new_name
        )
        self._send(new_name, f"You are now known as {new_name}.")
        return True, new_name

    # -- sending ----------------------------------------------------------

    def _send(self, username, message):
        """Send a message to one user. Silently drops it if they are gone."""
        with self._lock:
            info = self._clients.get(username)
            if info is None:
                return
            conn = info["conn"]
        try:
            self._send_raw(conn, message)
        except OSError:
            pass

    def _broadcast(self, room, message, exclude=None):
        """Send a message to everyone in a room. Failures are ignored —
        a dead client's own handler thread removes it on disconnect."""
        with self._lock:
            recipients = [
                self._clients[name]["conn"]
                for name in self._rooms.get(room, set())
                if name != exclude and name in self._clients
            ]
        for conn in recipients:
            try:
                self._send_raw(conn, message)
            except OSError:
                pass

    @staticmethod
    def _send_raw(conn, message):
        for line in message.split("\n"):
            conn.sendall((line + "\n").encode("utf-8"))


def main():
    parser = argparse.ArgumentParser(
        description="Multithreaded TCP chat server."
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Address to listen on (default: %(default)s). "
        "Use 0.0.0.0 to accept connections from your local network.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help="Port to listen on (default: %(default)s). Use 0 for a free port.",
    )
    args = parser.parse_args()
    ChatServer(args.host, args.port).serve_forever()


if __name__ == "__main__":
    main()
