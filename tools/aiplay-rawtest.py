#!/usr/bin/env python3
"""Raw UDS test client for the AI-play layer (feature 002).

Usage: python3 tools/aiplay-rawtest.py [--socket PATH] 'JSON-request'
Sends one JSON-RPC-style request line and prints the response.
"""
import argparse, json, socket, sys

p = argparse.ArgumentParser()
p.add_argument('--socket', default='/tmp/homeworld-aiplay.sock')
p.add_argument('request', nargs='?', default='{"id":1,"method":"aiSession.status"}')
a = p.parse_args()

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(a.socket)
s.settimeout(3)
s.sendall((a.request + '\n').encode())
import time; time.sleep(0.5)
try:
    print(s.recv(65536).decode(), end='')
except socket.timeout:
    print('<no response>', file=sys.stderr)
    sys.exit(1)
