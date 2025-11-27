
import os

log_file = "logs/yolo_system_20251127.log"
if os.path.exists(log_file):
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            if "ID:" in line:
                print(line.strip())
else:
    print(f"Log file {log_file} not found")
