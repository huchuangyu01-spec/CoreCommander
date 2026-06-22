import sys
import traceback
import datetime
import os

class StderrLogger:
    def __init__(self, filename):
        self.filename = filename
    def write(self, text):
        try:
            with open(self.filename, 'a', encoding='utf-8') as f:
                f.write(text)
        except Exception:
            pass
    def flush(self):
        pass

log_path = os.path.join(os.path.expanduser("~"), ".core_commander", "stderr.log")
os.makedirs(os.path.dirname(log_path), exist_ok=True)
sys.stderr = StderrLogger(log_path)
if sys.stdout is None:
    sys.stdout = sys.stderr
