"""Forward a container-local TCP listener to a mounted Unix socket."""
import argparse
import os
import select
import socket
import socketserver
import subprocess
import sys
import threading


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as upstream:
            upstream.connect(self.server.socket_path)
            peers = [self.request, upstream]
            while True:
                readable, _, failed = select.select(peers, [], peers, 60)
                if failed or not readable:
                    return
                for peer in readable:
                    try:
                        data = peer.recv(65536)
                    except OSError:
                        return
                    if not data:
                        return
                    (upstream if peer is self.request else self.request).sendall(data)


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True)
    parser.add_argument('argv', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
    if not argv:
        parser.error('A command is required')
    server = Server(('127.0.0.1', 0), Handler)
    server.socket_path = args.socket
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = os.environ.copy()
    proxy = 'http://127.0.0.1:' + str(server.server_address[1])
    env.update(HTTPS_PROXY=proxy, https_proxy=proxy, HTTP_PROXY=proxy, http_proxy=proxy,
               ALL_PROXY=proxy, all_proxy=proxy, NO_PROXY='', no_proxy='', NODE_USE_ENV_PROXY='1')
    try:
        return subprocess.run(argv, env=env, check=False).returncode
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    sys.exit(main())
