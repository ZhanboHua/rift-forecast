"""Exercise no-network Docker isolation against a dedicated host listener."""
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from lolarena.offline_sandbox import command


class OfflinePreflightTest(unittest.TestCase):
    def test_rejects_symlinks_and_sockets(self):
        root = Path(__file__).resolve().parents[1] / 'tmp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            path = Path(temp)
            (path / 'link').symlink_to('/var/run/docker.sock')
            with self.assertRaises(ValueError):
                command('fixture-image', path, ['python'])
            (path / 'link').unlink()
            with socket.socket(socket.AF_UNIX) as local:
                local.bind(str(path / 'socket'))
                with self.assertRaises(ValueError):
                    command('fixture-image', path, ['python'])


@unittest.skipUnless(os.environ.get('RIFT_OFFLINE_TEST_IMAGE'), 'requires a dedicated Docker test image')
class OfflineNetworkTest(unittest.TestCase):
    def test_network_none_blocks_host_and_external_connections(self):
        root = Path(__file__).resolve().parents[1] / 'tmp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp, socket.socket() as listener, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('1.1.1.1', 53))
            host_ip = probe.getsockname()[0]
            listener.bind(('0.0.0.0', 0))
            listener.listen()
            host_port = listener.getsockname()[1]
            with socket.create_connection((host_ip, host_port), timeout=2):
                pass
            (Path(temp) / 'input.txt').write_text('fictional')
            code = '''import os,socket,sys
assert open('/workspace/input.txt').read() == 'fictional'
assert not os.path.exists('/var/run/docker.sock')
for host,port in [(sys.argv[1],int(sys.argv[2])), ('1.1.1.1',443)]:
    try:
        socket.create_connection((host,port),timeout=2)
    except OSError:
        continue
    raise SystemExit('unexpected network access')
print('offline-blocked')
'''
            result = subprocess.run(command(os.environ['RIFT_OFFLINE_TEST_IMAGE'], temp,
                                            ['python', '-c', code, host_ip, str(host_port)]),
                                    input=b'', capture_output=True, timeout=30, check=True)
            self.assertEqual(result.stdout.strip(), b'offline-blocked')

class GatewayPolicyTest(unittest.TestCase):
    def test_rejects_private_or_invalid_targets(self):
        from unittest.mock import patch
        from lolarena.gateway_proxy import valid_host, connect_public
        self.assertFalse(valid_host('127.0.0.1'))
        self.assertFalse(valid_host('example.local'))
        self.assertFalse(valid_host('example.com:443'))
        self.assertTrue(valid_host('gateway.example'))
        with patch('socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(OSError):
                connect_public('gateway.example')


@unittest.skipUnless(os.environ.get('RIFT_OFFLINE_TEST_IMAGE'), 'requires a dedicated Docker test image')
class GatewayNetworkTest(unittest.TestCase):
    def test_gateway_allows_only_configured_host(self):
        import socketserver
        import threading
        from lolarena.gateway_proxy import Gateway
        root = Path(__file__).resolve().parents[1] / 'tmp'
        root.mkdir(exist_ok=True)
        class Echo(socketserver.BaseRequestHandler):
            def handle(self):
                if self.request.recv(4096):
                    self.request.sendall(b'pong')
        upstream = socketserver.TCPServer(('127.0.0.1', 0), Echo)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        self.addCleanup(upstream_thread.join)
        self.addCleanup(upstream.server_close)
        self.addCleanup(upstream.shutdown)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            base = Path(temp)
            workspace = base / 'work'
            workspace.mkdir(mode=0o700)
            gateway = Gateway(base / 'model.sock', 'gateway.example',
                              lambda _: socket.create_connection(upstream.server_address, timeout=3))
            gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
            gateway_thread.start()
            try:
                code = '''import os,socket,ssl,urllib.parse
def hello(host):
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
    incoming,outgoing=ssl.MemoryBIO(),ssl.MemoryBIO()
    sslobj=ctx.wrap_bio(incoming,outgoing,server_hostname=host)
    try: sslobj.do_handshake()
    except ssl.SSLWantReadError: pass
    return outgoing.read()
proxy=urllib.parse.urlsplit(os.environ['HTTPS_PROXY'])
def exchange(host):
    s=socket.create_connection((proxy.hostname,proxy.port),timeout=3)
    s.sendall(('CONNECT '+host+':443 HTTP/1.1\\r\\nHost: '+host+'\\r\\n\\r\\n').encode())
    b=b''
    while b'\\r\\n\\r\\n' not in b: b+=s.recv(1)
    return s,b
s,head=exchange('gateway.example')
assert b' 200 ' in head
s.sendall(hello('gateway.example')); assert s.recv(4)==b'pong'; s.close()
s,head=exchange('gateway.example'); assert b' 200 ' in head; s.sendall(hello('other.example')); assert s.recv(4)==b''; s.close()
s,head=exchange('other.example'); assert b' 403 ' in head; s.close()
try:
    socket.create_connection(('1.1.1.1',443),timeout=2)
except OSError: pass
else: raise SystemExit('direct egress unexpectedly available')
print('gateway-only')
'''
                result = subprocess.run(command(os.environ['RIFT_OFFLINE_TEST_IMAGE'], workspace,
                                                ['python', '-c', code], base / 'model.sock'),
                                        input=b'', capture_output=True, timeout=30, check=True)
                self.assertEqual(result.stdout.strip(), b'gateway-only')
            finally:
                gateway.shutdown()
                gateway.server_close()
                gateway_thread.join()

@unittest.skipUnless(os.environ.get('RIFT_OFFLINE_TEST_IMAGE'), 'requires a dedicated Docker test image')
class GatewayTLSTest(unittest.TestCase):
    def test_real_tls_handshake_through_gateway(self):
        import ssl
        import threading
        from lolarena.gateway_proxy import Gateway
        root = Path(__file__).resolve().parents[1] / 'tmp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            base = Path(temp)
            workspace = base / 'work'
            workspace.mkdir(mode=0o700)
            key, cert = base / 'test-key.pem', base / 'test-cert.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                            '-subj', '/CN=gateway.example', '-keyout', str(key), '-out', str(cert)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=30)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            def serve():
                client, _ = listener.accept()
                with context.wrap_socket(client, server_side=True) as tls:
                    if tls.recv(4) == b'ping':
                        tls.sendall(b'pong')
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            gateway = Gateway(base / 'model.sock', 'gateway.example',
                              lambda _: socket.create_connection(listener.getsockname(), timeout=3))
            gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
            gateway_thread.start()
            try:
                code = '''import os,socket,ssl,urllib.parse
p=urllib.parse.urlsplit(os.environ['HTTPS_PROXY'])
s=socket.create_connection((p.hostname,p.port),timeout=3)
s.sendall(b'CONNECT gateway.example:443 HTTP/1.1\\r\\nHost: gateway.example\\r\\n\\r\\n')
h=b''
while b'\\r\\n\\r\\n' not in h: h+=s.recv(1)
assert b' 200 ' in h
ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
with ctx.wrap_socket(s,server_hostname='gateway.example') as tls:
    tls.sendall(b'ping'); assert tls.recv(4)==b'pong'
print('tls-ok')
'''
                result = subprocess.run(command(os.environ['RIFT_OFFLINE_TEST_IMAGE'], workspace,
                                                ['python', '-c', code], base / 'model.sock'),
                                        input=b'', capture_output=True, timeout=30, check=True)
                self.assertEqual(result.stdout.strip(), b'tls-ok')
            finally:
                gateway.shutdown()
                gateway.server_close()
                gateway_thread.join()
                listener.close()
                thread.join(timeout=3)

@unittest.skipUnless(os.environ.get('RIFT_OFFLINE_TEST_IMAGE'), 'requires a dedicated Docker test image')
class SandboxCleanupTest(unittest.TestCase):
    def test_termination_removes_only_owned_container(self):
        import signal
        import time
        root = Path(__file__).resolve().parents[1] / 'tmp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            base = Path(temp)
            workspace, control = base / 'work', base / 'control'
            workspace.mkdir(mode=0o700)
            control.mkdir(mode=0o700)
            child = subprocess.Popen([sys.executable, '-m', 'lolarena.offline_sandbox',
                                      '--image', os.environ['RIFT_OFFLINE_TEST_IMAGE'], '--workspace', str(workspace),
                                      '--control-dir', str(control), '--', 'python', '-c', 'import time; time.sleep(60)'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                cid = None
                for _ in range(100):
                    files = list(control.glob('sandbox-*/cid'))
                    if files and files[0].is_file():
                        value = files[0].read_text().strip()
                        if len(value) == 64:
                            cid = value
                            break
                    time.sleep(.1)
                self.assertIsNotNone(cid)
                os.kill(child.pid, signal.SIGTERM)
                child.wait(timeout=30)
                result = subprocess.run(['docker', 'inspect', cid], capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=30)
