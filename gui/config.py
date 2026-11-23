"""Cấu hình chung cho GUI — URL control-API của selenium_flow.py + nhịp refresh."""

import os

# URL control-API của selenium_flow.py (KHÁC với FLOW_API_URL — đó là backend chính port 13443).
API_BASE       = os.getenv('SELENIUM_FLOW_API_URL', 'http://127.0.0.1:13445')
# 'localhost' trên Windows thử ::1 trước → mỗi request Python mất thêm ~2s (server chỉ nghe IPv4).
API_BASE       = API_BASE.replace('://localhost', '://127.0.0.1')
REFRESH_MS     = 5000
LOG_REFRESH_MS = 4000
