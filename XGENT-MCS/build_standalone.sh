#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x venv/bin/python3 ]]; then
    python3 -m venv venv
fi
source venv/bin/activate
python3 -m pip install -r requirements.txt pyinstaller==6.22.3
bash ./make_app.sh
echo "Signed app: dist/XIDER.app. Install without changing app contents after signing."
