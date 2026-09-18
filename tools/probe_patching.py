"""Hunt for the console's input-patching addresses.

The 2026-08-09 sweep that built docs/mixer_protocol enumerated the
categories the console itself lists in /Console/Channels/?, and dumped
every parameter under each strip. Input patching is in none of them: a
full /Input_Channels/{n}/? dump carries gain, pad, phantom, phase, name
and input_type, but nothing naming a rack or a socket. So whatever
addresses carry the patch live somewhere that sweep never asked about.

This fires a list of candidates at the console and reports which ones
answer. Every message sent is a GET ("/?" appended, empty args) - nothing
here writes, so it is safe to run against a desk that is patched and
loaded, though not one mid-show: a console answering hundreds of queries
at once is still work it has to do.

Usage:
    python tools/probe_patching.py --ip 10.5.20.242 --send-port 10025 \
        --recv-port 10026

The recv port must be the one the console is configured to transmit to
(its External Control panel), exactly as CLMix's own "Rec Port" is - the
console ignores the source port of what it receives and only ever replies
there. See docs/mixer_protocol/PROTOCOL.md.
"""
import argparse
import socket
import threading
import time

from pythonosc.osc_message import OscMessage, ParseError
from pythonosc.osc_message_builder import OscMessageBuilder

# Roots worth asking about. A console that knows an address answers it and
# stays silent otherwise, so a wrong guess costs one datagram.
ROOT_CANDIDATES = [
    "/Sockets", "/Socket", "/Racks", "/Rack", "/IO", "/I_O", "/Inputs",
    "/Input_Sockets", "/Input_Patch", "/Patch", "/Patching", "/Routing",
    "/Stageboxes", "/Stagebox", "/Local_IO", "/DRack", "/SDRack",
    "/Console/Sockets", "/Console/Racks", "/Console/IO", "/Console/Inputs",
    "/Console/Patch", "/Console/Routing", "/Console/Stageboxes",
]

# Leaves under a channel's input block. The full strip dump did not show
# these, but it was taken with one query shape - worth asking directly in
# case the console answers a named leaf it does not volunteer.
CHANNEL_LEAF_CANDIDATES = [
    "Channel_Input/socket", "Channel_Input/source", "Channel_Input/input",
    "Channel_Input/patch", "Channel_Input/port", "Channel_Input/rack",
    "Channel_Input/input_socket", "Channel_Input/input_source",
    "Channel_Input/main/socket", "Channel_Input/main/source",
    "Channel_Input/main/input", "Channel_Input/main/patch",
    "Channel_Input/alt/socket", "Channel_Input/alt_source",
    "Input/socket", "Input/source", "Input/patch", "socket", "source",
    "patch", "input_socket",
]


# Sub-block dumps, which the candidate leaves above do not cover. Per
# PROTOCOL.md, appending "/?" to a bare path with no leaf makes the
# console dump every parameter underneath it - that is how the whole
# command map was built, one query per category instead of hundreds of
# guesses.
#
# "Channel_Input/main" is worth asking about specifically because it is
# a confirmed namespace, not a guess: the 2026-09-17 patching capture
# caught /Input_Channels/18/Channel_Input/main/alt_in arriving unbidden.
# Something lives under "main"; the strip dump that found no socket was
# taken one level above it.
DUMP_CANDIDATES = [
    "",
    "Channel_Input",
    "Channel_Input/main",
    "Channel_Input/alt",
    "Channel_Input/input",
]


def build(address):
    return OscMessageBuilder(address=address).build().dgram


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", required=True, help="console IP")
    ap.add_argument("--send-port", type=int, required=True)
    ap.add_argument("--recv-port", type=int, required=True)
    ap.add_argument("--channel", type=int, default=1,
                    help="which input channel to ask about (default 1)")
    ap.add_argument("--settle", type=float, default=4.0,
                    help="seconds to keep listening after the last query")
    args = ap.parse_args()

    replies = []
    stop = threading.Event()

    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    recv_sock.bind(("0.0.0.0", args.recv_port))
    recv_sock.settimeout(0.3)

    def listen():
        while not stop.is_set():
            try:
                data, _ = recv_sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return

            try:
                msg = OscMessage(data)
            except ParseError:
                continue

            replies.append((msg.address, list(msg.params)))

    listener = threading.Thread(target=listen, daemon=True)
    listener.start()

    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    queries = []
    for root in ROOT_CANDIDATES:
        # Bare, indexed, and one level in - a console that answers a
        # category at all usually answers at least one of these shapes.
        queries += [f"{root}/?", f"{root}/1/?", f"{root}/1/name/?"]

    for leaf in CHANNEL_LEAF_CANDIDATES:
        queries.append(f"/Input_Channels/{args.channel}/{leaf}/?")

    for block in DUMP_CANDIDATES:
        path = f"/Input_Channels/{args.channel}"
        queries.append(f"{path}/{block}/?" if block else f"{path}/?")

    # Re-enumerates the categories the console admits to, in case this
    # desk lists something the reference sweep did not.
    queries.append("/Console/Channels/?")

    print(f"sending {len(queries)} read-only queries to "
          f"{args.ip}:{args.send_port}, listening on :{args.recv_port}")

    for address in queries:
        send_sock.sendto(build(address), (args.ip, args.send_port))
        time.sleep(0.01)

    time.sleep(args.settle)
    stop.set()
    listener.join(timeout=1)
    recv_sock.close()

    print(f"\n{len(replies)} replies\n")

    seen = set()
    for address, params in replies:
        if address in seen:
            continue
        seen.add(address)
        print(f"  {address}  {params}")

    if not replies:
        print("  (nothing answered - either none of these addresses exist,\n"
              "   or the console is not configured to transmit to this\n"
              "   machine on this recv port)")


if __name__ == "__main__":
    main()
