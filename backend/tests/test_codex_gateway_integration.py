"""Run the actual Codex CLI against a fictional TLS gateway through the sandbox."""
import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from lolarena.gateway_proxy import Gateway
from lolarena.offline_sandbox import command


@unittest.skipUnless(os.environ.get('RIFT_CODEX_TLS_IMAGE'), 'requires a dedicated Codex TLS fixture image')
class CodexGatewayIntegrationTest(unittest.TestCase):
    def test_codex_session_and_resume_through_only_gateway(self):
        seen = []
        hello_errors = []
        import lolarena.gateway_proxy as gateway_module
        original_check = gateway_module.checked_client_hello
        def checked(*args):
            try:
                return original_check(*args)
            except (ValueError, OSError) as exc:
                hello_errors.append(str(exc))
                raise
        gateway_module.checked_client_hello = checked
        self.addCleanup(setattr, gateway_module, 'checked_client_hello', original_check)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                seen.append(self.path)
                message = json.dumps([{'match_id':'fictional-1','p_team_a_win':.6,'winner_bet':{'team':'AAA','stake':0},
                                       'score_bets':{'1-0':0,'0-1':0},'rationale':'fixture','sources':[],'take_loan':False}])
                item = {'id':'msg_fixture','type':'message','role':'assistant','status':'completed',
                        'content':[{'type':'output_text','text':message,'annotations':[]}]}
                response = {'id':'resp_fixture','object':'response','created_at':int(time.time()),'model':'fixture',
                            'status':'completed','output':[item], 'usage':{'input_tokens':10,'output_tokens':10,
                            'total_tokens':20,'input_tokens_details':{'cached_tokens':0},'output_tokens_details':{'reasoning_tokens':0}}}
                events = [{'type':'response.created','response':{**response,'status':'in_progress','output':[]}},
                          {'type':'response.output_item.added','output_index':0,'item':{**item,'status':'in_progress','content':[]}},
                          {'type':'response.output_text.delta','item_id':'msg_fixture','output_index':0,'content_index':0,'delta':message},
                          {'type':'response.output_text.done','item_id':'msg_fixture','output_index':0,'content_index':0,'text':message},
                          {'type':'response.output_item.done','output_index':0,'item':item},
                          {'type':'response.completed','response':response}]
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                for event in events:
                    self.wfile.write(('event: '+event['type']+'\ndata: '+json.dumps(event)+'\n\n').encode())
                    self.wfile.flush()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(os.environ['RIFT_TLS_CERT'], os.environ['RIFT_TLS_KEY'])
        server.socket = context.wrap_socket(server.socket, server_side=True)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='rift-gateway-cli-', dir=os.environ['RIFT_OFFLINE_CONTROL_PARENT']) as tmp:
                base = Path(tmp)
                workspace = base / 'work'
                workspace.mkdir(mode=0o700)
                shutil.copy(Path(__file__).resolve().parents[1] / 'lolarena/native_codex.py', workspace / 'native_codex.py')
                script = '''import json,os
from pathlib import Path
from native_codex import invoke
root=Path('/workspace'); home=root/'home'; home.mkdir()
ch=home/'.codex'; ch.mkdir()
(ch/'config.toml').write_text('model_provider = "fixture"\\n[model_providers.fixture]\\nname = "fixture"\\nbase_url = "https://gateway.example/v1"\\nwire_api = "responses"\\nenv_key = "CODEX_API_KEY"\\n')
env={k:v for k,v in os.environ.items() if k in ('PATH','HTTPS_PROXY','https_proxy','HTTP_PROXY','http_proxy','ALL_PROXY','all_proxy','NO_PROXY','no_proxy','SSL_CERT_FILE')}
env.update(HOME=str(home),CODEX_HOME=str(ch),LANG='C.UTF-8',CODEX_API_KEY='fixture-token')
task={'matches':[{'match_id':'fictional-1','team_a':'AAA','team_b':'BBB','best_of':1}]}
a=invoke(['/usr/local/bin/codex'],'fixture',task,env,root,30)
assert a['status']=='done', a.get('reason')
b=invoke(['/usr/local/bin/codex'],'fixture',task,env,root,30,a['session_id'])
assert b['status']=='done' and a['session_id']==b['session_id'], b.get('reason')
print('real-cli-gateway-resume-ok')
'''
                (workspace / 'check.py').write_text(script)
                gateway = Gateway(base / 'model.sock', 'gateway.example',
                                  lambda _: socket.create_connection(server.server_address, timeout=3))
                gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
                gateway_thread.start()
                try:
                    result = subprocess.run(command(os.environ['RIFT_CODEX_TLS_IMAGE'], workspace,
                                                    ['python3', '/workspace/check.py'], base / 'model.sock'),
                                            input=b'', capture_output=True, timeout=90, check=False)
                    self.assertEqual(result.returncode, 0, (result.stdout.decode(errors='replace'), result.stderr.decode(errors='replace'), seen, hello_errors))
                    self.assertEqual(result.stdout.strip(), b'real-cli-gateway-resume-ok')
                    self.assertGreaterEqual(len(seen), 2)
                finally:
                    gateway.shutdown()
                    gateway.server_close()
                    gateway_thread.join()
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join()
