"""Tunnel CONNECT requests for one configured HTTPS gateway over a Unix socket."""
import argparse
import ipaddress
import os
import re
import select
import socket
import socketserver
import stat
from pathlib import Path


def valid_host(host):
    if not isinstance(host, str) or len(host) > 253 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host):
        return False
    if '.' not in host or '..' in host or host.endswith('.local'):
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return True


def connect_public(host):
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise OSError('Gateway DNS did not resolve exclusively to public addresses')
    last = None
    for family, socktype, proto, _, address in addresses:
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(10)
        try:
            sock.connect(address)
            return sock
        except OSError as exc:
            last = exc
            sock.close()
    raise OSError('Gateway connection failed') from last


def read_exact(sock, count):
    parts = []
    while count:
        part = sock.recv(count)
        if not part:
            raise ValueError('Incomplete TLS record')
        parts.append(part)
        count -= len(part)
    return b''.join(parts)


def checked_client_hello(sock, allowed_host):
    """Read TLS handshake records and require exactly the configured SNI."""
    records = []
    handshake = b''
    while len(handshake) < 4 or len(handshake) < 4 + int.from_bytes(handshake[1:4], 'big'):
        header = read_exact(sock, 5)
        size = int.from_bytes(header[3:5], 'big')
        if header[0] != 22 or not 1 <= size <= 18432 or len(handshake) + size > 65536:
            raise ValueError('Invalid TLS handshake record')
        body = read_exact(sock, size)
        records.append(header + body)
        handshake += body
    if handshake[0] != 1:
        raise ValueError('Expected TLS ClientHello')
    hello = handshake[4:4 + int.from_bytes(handshake[1:4], 'big')]
    try:
        cursor = 34
        sid = hello[cursor]; cursor += 1 + sid
        cipher_len = int.from_bytes(hello[cursor:cursor+2], 'big'); cursor += 2 + cipher_len
        compression_len = hello[cursor]; cursor += 1 + compression_len
        extension_len = int.from_bytes(hello[cursor:cursor+2], 'big'); cursor += 2
        end = cursor + extension_len
        if end != len(hello):
            raise ValueError('Malformed TLS extensions')
        names = []
        while cursor < end:
            if cursor + 4 > end:
                raise ValueError('Malformed TLS extension')
            kind = int.from_bytes(hello[cursor:cursor+2], 'big')
            size = int.from_bytes(hello[cursor+2:cursor+4], 'big')
            cursor += 4
            value = hello[cursor:cursor+size]
            if len(value) != size:
                raise ValueError('Truncated TLS extension')
            cursor += size
            if kind == 0:
                if len(value) < 5 or int.from_bytes(value[:2], 'big') != len(value) - 2:
                    raise ValueError('Malformed server name extension')
                name_size = int.from_bytes(value[3:5], 'big')
                if value[2] != 0 or name_size != len(value) - 5:
                    raise ValueError('Invalid server name')
                names.append(value[5:].decode('ascii').lower())
        if names != [allowed_host]:
            raise ValueError('TLS server name does not match gateway')
    except (IndexError, UnicodeDecodeError) as exc:
        raise ValueError('Malformed TLS ClientHello') from exc
    return b''.join(records)


class Handler(socketserver.StreamRequestHandler):
    rbufsize = 0

    def handle(self):
        self.request.settimeout(10)
        first = self.rfile.readline(4097)
        if len(first) > 4096:
            return
        try:
            method, target, version = first.decode('ascii').strip().split(' ')
        except ValueError:
            return
        if method != 'CONNECT' or target != self.server.allowed_host + ':443' or version not in ('HTTP/1.0', 'HTTP/1.1'):
            self.wfile.write(b'HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n')
            return
        for _ in range(64):
            header = self.rfile.readline(4097)
            if len(header) > 4096 or not header:
                return
            if header == b'\r\n':
                break
        else:
            return
        self.wfile.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        self.wfile.flush()
        try:
            hello = checked_client_hello(self.request, self.server.allowed_host)
            upstream = self.server.connect_gateway(self.server.allowed_host)
        except (OSError, ValueError):
            return
        with upstream:
            upstream.sendall(hello)
            peers = [self.request, upstream]
            while True:
                readable, _, failed = select.select(peers, [], peers, 60)
                if failed or not readable:
                    return
                for sock in readable:
                    data = sock.recv(65536)
                    if not data:
                        return
                    (upstream if sock is self.request else self.request).sendall(data)


class Gateway(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    def __init__(self, path, host, connector=connect_public):
        if not valid_host(host):
            raise ValueError('Use an explicit DNS hostname for the model gateway')
        self.allowed_host = host
        self.connect_gateway = connector
        super().__init__(str(path), Handler)
        os.chmod(path, 0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--host', required=True, help='Exact lowercase gateway DNS hostname')
    args = parser.parse_args()
    directory = args.socket.parent
    if directory.is_symlink() or not directory.is_dir() or directory.stat().st_mode & 0o077:
        raise ValueError('Socket directory must be private')
    if args.socket.exists() or args.socket.is_symlink():
        raise FileExistsError('Socket path already exists')
    server = Gateway(args.socket, args.host)
    identity = args.socket.lstat()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        try:
            current = args.socket.lstat()
            if stat.S_ISSOCK(current.st_mode) and (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                args.socket.unlink()
        except FileNotFoundError:
            pass


if __name__ == '__main__':
    main()
