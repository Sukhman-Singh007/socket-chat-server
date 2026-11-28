#!/usr/bin/env python3
"""Integration tests for socket-chat-server.

These tests start the real server on a free localhost port and talk
to it over real TCP sockets — no mocks of the networking. Run:

    python -m unittest test_chat -v
"""

import socket
import threading
import time
import unittest

from server import ChatServer


class TestClient:
    """A small scripted client for tests: raw socket, line buffered."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port))
        self.sock.settimeout(2.0)
        self.buf = b""

    def send(self, text):
        self.sock.sendall((text + "\n").encode("utf-8"))

    def readline(self):
        """Read one line (without the newline). Raises on timeout/close."""
        while b"\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("server closed the connection")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode("utf-8", errors="replace")

    def login(self, username):
        """Complete the username handshake. Returns the WELCOME line."""
        first = self.readline()
        assert first.startswith("USERNAME:"), first
        self.send(username)
        while True:
            line = self.readline()
            if line.startswith("WELCOME"):
                return line
            if line.startswith("ERROR:"):
                raise ValueError(line)

    def try_login(self, username):
        """Like login(), but returns the ERROR line instead of raising."""
        first = self.readline()
        assert first.startswith("USERNAME:"), first
        self.send(username)
        line = self.readline()
        if line.startswith("ERROR:"):
            return line
        if line.startswith("WELCOME"):
            return line
        raise AssertionError(f"unexpected handshake reply: {line!r}")

    def expect(self, substring, timeout=2.0):
        """Read lines until one contains substring. Fails on timeout."""
        self.sock.settimeout(timeout)
        try:
            while True:
                line = self.readline()
                if substring in line:
                    return line
        except socket.timeout:
            raise AssertionError(f"did not receive {substring!r} in time")
        finally:
            self.sock.settimeout(2.0)

    def drain(self, quiet=0.3):
        """Throw away everything the server has already sent."""
        self.sock.settimeout(quiet)
        try:
            while True:
                self.readline()
        except (socket.timeout, ConnectionError, OSError):
            pass
        finally:
            self.sock.settimeout(2.0)

    def expect_nothing(self, timeout=0.5):
        """Fail if any line arrives within the timeout."""
        self.sock.settimeout(timeout)
        try:
            line = self.readline()
            raise AssertionError(f"expected silence but got {line!r}")
        except socket.timeout:
            pass
        finally:
            self.sock.settimeout(2.0)

    def close(self):
        try:
            self.send("/quit")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class ChatServerTestCase(unittest.TestCase):
    def setUp(self):
        self.server = ChatServer("127.0.0.1", 0)
        self.server.start()
        self.port = self.server.port
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.server.stop()
        time.sleep(0.05)

    def connect(self, username):
        client = TestClient(self.port)
        client.login(username)
        self.clients.append(client)
        return client

    def test_message_broadcast_in_room(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.drain()
        bob.drain()
        alice.send("hello everyone")
        bob.expect("alice: hello everyone")
        # The sender also receives their own message back.
        alice.expect("alice: hello everyone")

    def test_join_room_name_is_case_insensitive(self):
        # "/join Python" and "/join python" must be the same room —
        # two spellings of one name used to split people apart.
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.send("/join Python")
        alice.expect("You joined #python.")
        bob.send("/join python")
        bob.expect("You joined #python.")
        alice.expect("* bob joined #python.")
        alice.drain()
        bob.drain()
        alice.send("same room?")
        bob.expect("alice: same room?")
        alice.send("/users")
        alice.expect("Users in #python: alice, bob")

    def test_whitespace_only_line_is_ignored(self):
        # A line of only spaces used to be broadcast as "alice:    ".
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.drain()
        bob.drain()
        alice.send("   ")
        alice.expect_nothing()
        bob.expect_nothing()
        # Normal chat still works afterwards.
        alice.send("still here")
        bob.expect("alice: still here")

    def test_room_isolation(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        carol = self.connect("carol")
        alice.send("/join python")
        alice.expect("You joined #python.")
        carol.send("/join python")
        carol.expect("You joined #python.")
        alice.drain()
        bob.drain()
        carol.drain()
        alice.send("secret for python room")
        carol.expect("alice: secret for python room")
        bob.expect_nothing()

    def test_private_message(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        carol = self.connect("carol")
        alice.drain()
        bob.drain()
        carol.drain()
        alice.send("/msg bob hey, just us")
        bob.expect("[DM from alice] hey, just us")
        alice.expect("[DM to bob] hey, just us")
        carol.expect_nothing()

    def test_private_message_target_is_case_insensitive(self):
        # "/msg ALICE" must reach Alice — the target used to be
        # matched case-sensitively, so the DM failed with
        # "No user named ALICE." even though Alice was online.
        alice = self.connect("Alice")
        bob = self.connect("bob")
        alice.drain()
        bob.drain()
        bob.send("/msg ALICE hey, any case works")
        alice.expect("[DM from bob] hey, any case works")
        bob.expect("[DM to Alice] hey, any case works")
        # And DMing yourself under a different case is still a self-DM.
        alice.drain()
        alice.send("/msg alice hello me")
        alice.expect("can't send a private message to yourself")

    def test_private_message_to_missing_user(self):
        alice = self.connect("alice")
        alice.drain()
        alice.send("/msg nobody hello?")
        alice.expect("No user named nobody.")

    def test_private_message_to_self_is_rejected(self):
        # DMing yourself used to deliver two echoes ("[DM from alice]"
        # and "[DM to alice]"); now it is a friendly rejection and
        # nothing is delivered.
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.drain()
        bob.drain()
        alice.send("/msg alice hello me")
        alice.expect("can't send a private message to yourself")
        alice.expect_nothing()
        bob.expect_nothing()
        # Still chatting normally afterwards.
        alice.send("still here")
        bob.expect("alice: still here")

    def test_private_message_to_disconnected_user_keeps_sender_connected(self):
        # A target whose socket died a moment ago must not take the
        # sender down with it: the DM is dropped, the sender stays.
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.drain()
        bob.drain()
        dead, peer = socket.socketpair()
        peer.close()
        try:
            with self.server._lock:
                self.server._clients["ghost"] = {
                    "conn": dead, "room": "lobby"
                }
                self.server._rooms.setdefault("lobby", set()).add("ghost")
            alice.send("/msg ghost hello there")
            alice.expect("[DM to ghost] hello there")
            alice.send("still here")
            bob.expect("alice: still here")
        finally:
            with self.server._lock:
                self.server._clients.pop("ghost", None)
                members = self.server._rooms.get("lobby")
                if members is not None:
                    members.discard("ghost")
            dead.close()

    def test_username_taken(self):
        self.connect("alice")
        other = TestClient(self.port)
        self.clients.append(other)
        error = other.try_login("alice")
        self.assertIn("taken", error)
        # The server re-prompts for a username after the error.
        prompt = other.readline()
        self.assertTrue(prompt.startswith("USERNAME:"))
        other.send("alice2")
        welcome = other.readline()
        self.assertTrue(welcome.startswith("WELCOME"))

    def test_username_taken_is_case_insensitive(self):
        # "Alice" and "alice" must not both be online: /msg resolves
        # its target case-insensitively, so two spellings of one name
        # would make a DM ambiguous.
        self.connect("alice")
        other = TestClient(self.port)
        self.clients.append(other)
        error = other.try_login("ALICE")
        self.assertIn("taken", error)
        # The server re-prompts for a username after the error.
        prompt = other.readline()
        self.assertTrue(prompt.startswith("USERNAME:"))
        other.send("alice2")
        welcome = other.readline()
        self.assertTrue(welcome.startswith("WELCOME"))

    def test_rooms_and_users(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.send("/join python")
        alice.expect("You joined #python.")
        alice.send("/rooms")
        alice.expect("Rooms:")
        alice.expect("#lobby")
        alice.expect("#python")
        alice.send("/users")
        alice.expect("Users in #python: alice")
        bob.send("/users")
        bob.expect("Users in #lobby: bob")

    def test_leave_notification(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.drain()
        bob.close()
        self.clients.remove(bob)
        alice.expect("* bob left the room.")

    def test_nick_change(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.drain()
        bob.drain()
        alice.send("/nick alicia")
        alice.expect("You are now known as alicia.")
        bob.expect("* alice is now known as alicia.")
        alice.send("it worked")
        bob.expect("alicia: it worked")

    def test_nick_to_own_name_is_a_noop(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.drain()
        bob.drain()
        alice.send("/nick alice")
        alice.expect("You are already known as alice.")
        # Still alice, and still chatting under that name.
        alice.send("still me")
        bob.expect("alice: still me")

    def test_nick_taken(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.drain()
        bob.drain()
        alice.send("/nick bob")
        alice.expect("That username is taken.")
        # The server keeps working after the rejected rename.
        alice.send("still here")
        bob.expect("alice: still here")

    def test_nick_taken_is_case_insensitive(self):
        # "/nick BOB" must not steal bob's name under a different
        # case — /msg would then be ambiguous about which Bob to DM.
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.drain()
        bob.drain()
        alice.send("/nick BOB")
        alice.expect("That username is taken.")
        alice.send("still here")
        bob.expect("alice: still here")

    def test_nick_to_own_name_other_case_is_a_noop(self):
        # "/nick ALICE" when you are "alice" is still your own name.
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.drain()
        bob.drain()
        alice.send("/nick ALICE")
        alice.expect("You are already known as alice.")
        alice.send("still me")
        bob.expect("alice: still me")

    def test_me_action_broadcast(self):
        # "/me waves" is broadcast to the sender's room in third
        # person ("* alice waves"), including back to the sender —
        # it used to fall through to "Unknown command: /me".
        alice = self.connect("alice")
        bob = self.connect("bob")
        carol = self.connect("carol")
        carol.send("/join python")
        carol.expect("You joined #python.")
        alice.drain()
        bob.drain()
        carol.drain()
        alice.send("/me waves hello")
        bob.expect("* alice waves hello")
        alice.expect("* alice waves hello")
        carol.expect_nothing()

    def test_me_without_action_shows_usage(self):
        alice = self.connect("alice")
        bob = self.connect("bob")
        alice.drain()
        bob.drain()
        alice.send("/me")
        alice.expect("Usage: /me <action>")
        alice.expect_nothing()
        bob.expect_nothing()

    def test_unknown_command(self):
        alice = self.connect("alice")
        alice.drain()
        alice.send("/dance")
        alice.expect("Unknown command: /dance")

    def test_empty_username_rejected(self):
        client = TestClient(self.port)
        self.clients.append(client)
        error = client.try_login("   ")
        self.assertIn("ERROR", error)

    def test_concurrent_broadcasts_do_not_interleave(self):
        # Two threads broadcasting large messages at once used to
        # interleave bytes on the receiver's socket: sendall() from
        # two threads can split a message mid-way, so the receiver
        # saw fused lines (e.g. one 1.6 MB "line" made of two
        # messages) and lost the line framing. Outbound writes are
        # now serialized, so every line arrives intact.
        alice = self.connect("alice")
        bob = self.connect("bob")
        bob.sock.settimeout(10)
        # Alice moves to another room: the flood is broadcast to the
        # lobby, so only bob receives it — and a client that never
        # reads would stall the senders' socket buffers, which is a
        # different problem from the interleaving tested here.
        alice.send("/join python")
        alice.expect("You joined #python.")
        alice.drain()
        bob.drain()
        msg_a = "A" * (512 * 1024)
        msg_b = "B" * (512 * 1024)

        def sender(msg):
            for _ in range(8):
                self.server._broadcast("lobby", msg)

        threads = [
            threading.Thread(target=sender, args=(msg_a,), daemon=True),
            threading.Thread(target=sender, args=(msg_a,), daemon=True),
            threading.Thread(target=sender, args=(msg_b,), daemon=True),
            threading.Thread(target=sender, args=(msg_b,), daemon=True),
        ]
        for t in threads:
            t.start()
        for _ in range(32):
            line = bob.readline()
            time.sleep(0.02)
            # assertTrue (not assertIn) so a failure does not dump a
            # 512 KB line into the test output.
            self.assertTrue(
                line == msg_a or line == msg_b,
                f"interleaved line of length {len(line)}",
            )
        for t in threads:
            t.join(timeout=10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
