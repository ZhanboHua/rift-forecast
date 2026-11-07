import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from native_codex import invoke

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        msg = json.dumps([{'match_id':'fictional-1','p_team_a_win':.6,'winner_bet':{'team':'AAA','stake':0},'score_bets':{'1-0':0,'0-1':0},'rationale':'fixture','sources':[],'take_loan':False}])
        item = {'id':'msg_fixture','type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':msg,'annotations':[]}]}
        response = {'id':'resp_fixture','object':'response','created_at':int(time.time()),'model':'fixture','status':'completed','output':[item], 'usage':{'input_tokens':10,'output_tokens':10,'total_tokens':20,'input_tokens_details':{'cached_tokens':0},'output_tokens_details':{'reasoning_tokens':0}}}
        events = [{'type':'response.created','response':{**response,'status':'in_progress','output':[]}},
                  {'type':'response.output_item.added','output_index':0,'item':{**item,'status':'in_progress','content':[]}},
                  {'type':'response.output_text.delta','item_id':'msg_fixture','output_index':0,'content_index':0,'delta':msg},
                  {'type':'response.output_text.done','item_id':'msg_fixture','output_index':0,'content_index':0,'text':msg},
                  {'type':'response.output_item.done','output_index':0,'item':item},
                  {'type':'response.completed','response':response}]
        self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers()
        for event in events:
            self.wfile.write(('event: '+event['type']+'\ndata: '+json.dumps(event)+'\n\n').encode()); self.wfile.flush()

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
t=threading.Thread(target=server.serve_forever,daemon=True); t.start()
try:
    root=Path('/workspace'); home=root/'home'; home.mkdir(exist_ok=True)
    codex_home=home/'.codex'; codex_home.mkdir(exist_ok=True)
    (codex_home/'config.toml').write_text('model_provider = "fixture"\n[model_providers.fixture]\nname = "fixture"\nbase_url = "http://127.0.0.1:'+str(server.server_port)+'/v1"\nwire_api = "responses"\nenv_key = "CODEX_API_KEY"\n')
    env={'HOME':str(home),'CODEX_HOME':str(codex_home),'PATH':os.environ['PATH'],'LANG':'C.UTF-8','CODEX_API_KEY':'fixture-token'}
    task={'matches':[{'match_id':'fictional-1','team_a':'AAA','team_b':'BBB','best_of':1}]}
    first=invoke(['/usr/local/bin/codex'],'fixture',task,env,root,30)
    print(json.dumps({'phase':'first','status':first['status'],'has_session':bool(first['session_id']),'reason':first.get('reason')}),flush=True)
    if first['status'] != 'done': raise SystemExit(1)
    resumed=invoke(['/usr/local/bin/codex'],'fixture',task,env,root,30,first['session_id'])
    print(json.dumps({'phase':'resume','status':resumed['status'],'same_session':resumed['session_id']==first['session_id'],'reason':resumed.get('reason')}),flush=True)
    if resumed['status'] != 'done' or resumed['session_id'] != first['session_id']: raise SystemExit(2)
finally:
    server.shutdown(); server.server_close(); t.join()
