"""Root conftest: thêm client_tool/tests vào sys.path để import hoạt động."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tests"))
