#!/bin/bash
cd "$(dirname "$0")"
echo ""
echo "  Starting DJ AB Dashboard..."
echo ""
/usr/bin/python3 server.py
