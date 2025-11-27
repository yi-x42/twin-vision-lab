
import os

log_file = "logs/yolo_system_20251127.log"
output_file = "found_logs.txt"

if os.path.exists(log_file):
    with open(log_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    with open(output_file, "w", encoding="utf-8") as out:
        for i, line in enumerate(lines):
            if "資料庫中找到對應記錄" in line or "從資料庫刪除攝影機" in line or "警告" in line:
                # Write 2 lines before and 2 lines after
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                out.write(f"--- Match at line {i+1} ---\n")
                for j in range(start, end):
                    out.write(lines[j])
                out.write("\n")
    print(f"Search complete. Results written to {output_file}")
else:
    print(f"Log file {log_file} not found")
