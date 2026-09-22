"""Cấu hình chung cho GUI — URL control-API của selenium_flow.py + nhịp refresh."""

import os

# URL control-API của selenium_flow.py (KHÁC với FLOW_API_URL — đó là backend chính port 13443).
API_BASE       = os.getenv('SELENIUM_FLOW_API_URL', 'http://localhost:13445')
REFRESH_MS     = 5000
LOG_REFRESH_MS = 4000
