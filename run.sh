#!/bin/bash
# PATCHWORK launcher ♡
cd "$(dirname "$0")"
if [ "$1" = "--splash" ]; then exec python3 patchwork.py --splash; fi
python3 patchwork.py
