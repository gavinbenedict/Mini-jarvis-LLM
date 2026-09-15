import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

class MockOllama(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        payload = json.loads(post_data)
        
        with open("ollama_payload.json", "w") as f:
            json.dump(payload, f, indent=2)
            
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"message": {"content": "logged"}}).encode())

HTTPServer(('127.0.0.1', 11434), MockOllama).serve_forever()
