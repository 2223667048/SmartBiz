"""Demo notification destination. Logs alert state/name only. Not production paging."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/alerts':
            self.send_error(404);return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<65536:
                self.send_error(413);return
            payload=json.loads(self.rfile.read(size))
            for item in payload.get('alerts',[])[:20]:
                logging.warning('alert event=%s name=%s severity=%s',
                    str(item.get('status',''))[:30],str(item.get('labels',{}).get('alertname',''))[:100],
                    str(item.get('labels',{}).get('severity',''))[:30])
            self.send_response(204);self.end_headers()
        except (ValueError,KeyError,TypeError):
            self.send_error(400)
    def log_message(self,format,*args): pass

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    ThreadingHTTPServer(('0.0.0.0',9099),Handler).serve_forever()
