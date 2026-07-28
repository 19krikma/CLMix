import os
import platform
import socket
import subprocess


def get_ethernet_ip():
    """Best-effort IPv4 address of a wired Ethernet adapter.

    Returns None if no up Ethernet adapter with an IPv4 address was found
    (e.g. only Wi-Fi is connected, or the cable is unplugged).
    """
    system = platform.system()

    if system == "Linux":
        return _linux_ethernet_ip()

    if system == "Windows":
        return _windows_ethernet_ip()

    return None


def _linux_ethernet_ip():
    net_dir = "/sys/class/net"

    try:
        interfaces = os.listdir(net_dir)
    except OSError:
        return None

    # Prefer conventionally-named wired adapters (eth0, enp3s0, ...) over
    # virtual interfaces (docker0, veth..., br-...) that also report as
    # Ethernet-type at the kernel level but aren't a physical connection.
    interfaces.sort(key=lambda name: not name.startswith(("eth", "en")))

    for iface in interfaces:
        if iface == "lo":
            continue

        iface_dir = os.path.join(net_dir, iface)

        # Wireless adapters expose a "wireless" subdirectory; wired
        # Ethernet adapters don't - this is the standard way Linux
        # networking tools tell them apart without extra libraries.
        if os.path.isdir(os.path.join(iface_dir, "wireless")):
            continue

        if _read_sysfs(iface_dir, "type") != "1":  # ARPHRD_ETHER
            continue

        if _read_sysfs(iface_dir, "operstate") != "up":
            continue

        ip = _ipv4_for_interface(iface)
        if ip:
            return ip

    return None


def _read_sysfs(iface_dir, filename):
    try:
        with open(os.path.join(iface_dir, filename)) as f:
            return f.read().strip()
    except OSError:
        return None


def _ipv4_for_interface(iface):
    import fcntl
    import struct

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        packed = fcntl.ioctl(
            sock.fileno(),
            0x8915,  # SIOCGIFADDR
            struct.pack("256s", iface.encode("utf-8")[:15])
        )
        return socket.inet_ntoa(packed[20:24])
    except OSError:
        return None
    finally:
        sock.close()


def _windows_ethernet_ip():
    # MediaType "802.3" is Ethernet's actual (non-localized) media type
    # code, unlike adapter display names which vary by Windows language -
    # this is what lets Get-NetAdapter tell wired apart from Wi-Fi
    # ("Native 802.11") reliably regardless of locale.
    command = (
        "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { "
        "(Get-NetAdapter -InterfaceIndex $_.InterfaceIndex "
        "-ErrorAction SilentlyContinue).MediaType -eq '802.3' -and "
        "$_.IPAddress -notlike '169.254.*' } "
        "| Select-Object -First 1 -ExpandProperty IPAddress"
    )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        ip = result.stdout.strip()
        return ip or None
    except (OSError, subprocess.SubprocessError):
        return None
