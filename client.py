#!/usr/bin/env python3
"""socket-chat-server — terminal client.

Run:
    python client.py [--host 127.0.0.1] [--port 65432]

Connects to a running server (see server.py), asks for a username,
then prints everything the server sends while you type. Type /help
in the chat for the command list, /quit (or Ctrl+C) to leave.

Note: this is a plain terminal client. If a message arrives while
you are typing, it prints on the next line and your half-typed text
stays on the line below — a curses-style UI would fix that, but it
is out of scope for this project.
"""

import argparse
import os
import socket
import sys
import threading

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 65432


def _receive_loop(reader):
    """Print every line the server sends, until it disconnects."""
    try:
        for line in reader:
            print(line.rstrip("\n"))
    except OSError:
        pass
    print("\nDisconnected from server.")
    # The main thread may be blocked waiting for input(), so exit
    # the whole process here instead of waiting for it.
    os._exit(0)


def main():
    parser = argparse.ArgumentParser(description="TCP chat client.")
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help="Server address (default: %(default)s)."
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="Server port (default: %(default)s)."
    )
    args = parser.parse_args()

    try:
        sock = socket.create_connection((args.host, args.port))
    except OSError as exc:
        print(f"Could not connect to {args.host}:{args.port} — {exc}")
        print("Is the server running? Start it with: python server.py")
        sys.exit(1)

    reader = sock.makefile("r", encoding="utf-8", errors="replace")

    # Handshake: answer the server's username prompt (retrying if the
    # name is taken) until it sends the WELCOME line.
    while True:
        line = reader.readline()
        if not line:
            print("Server closed the connection.")
            sys.exit(1)
        line = line.rstrip("\n")
        if line.startswith("USERNAME:"):
            try:
                name = input(line + " ")
            except (KeyboardInterrupt, EOFError):
                print("\nBye!")
                sock.close()
                sys.exit(0)
            sock.sendall((name.strip() + "\n").encode("utf-8"))
        elif line.startswith("ERROR:"):
            print(line)
        elif line.startswith("WELCOME"):
            print(line)
            break
        else:
            print(line)

    threading.Thread(target=_receive_loop, args=(reader,), daemon=True).start()

    try:
        while True:
            text = input()
            if not text.strip():
                continue
            sock.sendall((text + "\n").encode("utf-8"))
            if text.strip().lower() == "/quit":
                break
    except (KeyboardInterrupt, EOFError):
        try:
            sock.sendall(b"/quit\n")
        except OSError:
            pass
        print("\nBye!")
    finally:
        try:
            sock.close()
        except OSError:
            pass


if __name__ == "__main__":
    main()
