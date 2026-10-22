import os
import subprocess
import json
import sys
import time

class TeeLogger:
    def __init__(self, log_path):
        self.terminal = sys.stdout
        self.log_path = log_path
        
    def write(self, message):
        try:
            self.terminal.write(message)
        except UnicodeEncodeError:
            clean_msg = message.encode(self.terminal.encoding or 'ascii', errors='replace').decode(self.terminal.encoding or 'ascii', errors='replace')
            self.terminal.write(clean_msg)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(message)
        self.flush()
        
    def flush(self):
        self.terminal.flush()

sys.stdout = TeeLogger("orchestrator.log")
sys.stderr = sys.stdout

PUBLIC_DIR = r"C:\Users\vinee\priya_jobs"

def run_script(script_name, *args):
    print(f"\n{'='*50}\nRunning {script_name}...\n{'='*50}")
    # Use global python if venv doesn't exist, else use venv
    venv_python = os.path.join(PUBLIC_DIR, "venv", "Scripts", "python.exe")
    python_exe = venv_python if os.path.exists(venv_python) else "python"
    
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    
    process = subprocess.Popen([python_exe, "-u", script_name, *args], cwd=PUBLIC_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)
    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break
        if line:
            print(line, end='')
        
    process.wait()
    if process.returncode != 0:
        print(f"ERROR: {script_name} failed with return code {process.returncode}")
        return False
    return True

def pull_from_git():
    print(f"\n{'='*50}\nPulling latest changes from GitHub...\n{'='*50}")
    try:
        subprocess.run(["git", "stash"], cwd=PUBLIC_DIR, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result = subprocess.run(["git", "pull", "--rebase"], cwd=PUBLIC_DIR, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            print(f"Warning: git pull --rebase failed: {result.stderr.strip()}")
        else:
            print("Successfully pulled remote changes.")
        subprocess.run(["git", "stash", "pop"], cwd=PUBLIC_DIR, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"Git operations failed: {e}")

def main():
    print("Starting Autonomous Local LLM Pipeline Orchestrator (Priya Jobs)...")

    # Step 0: Sync with remote
    pull_from_git()

    # Step 1: scraper.py is a self-contained pipeline (scrape + LLM review via
    # _call_llm_with_fallback + git commit/push all in one) -- unlike vineeth_jobs's
    # orchestrator, there are no separate curate_jobs.py/evaluate_with_local_llm.py
    # steps here; those don't exist in this project and would just fail if called.
    # --once: the scraper does one full scrape pass, drains the review queue and exits 0, so
    # run_scrapers_sequential.ps1 can run each dashboard in turn and then unload the model.
    # A non-zero exit is a crash or its own watchdog killing a hung run (exit 3); restart it,
    # backing off so a crash loop doesn't spin.
    for attempt in range(1, 11):
        if run_script("scraper.py", "--once"):
            break
        if float(os.environ.get("SCRAPER_DEADLINE") or 0) and time.time() >= float(os.environ["SCRAPER_DEADLINE"]):
            print("Time budget is used up - not restarting the scraper; the next scheduled run continues.")
            return
        print(f"Scraper exited abnormally (attempt {attempt}/10) - restarting in {min(60 * attempt, 600)}s...")
        time.sleep(min(60 * attempt, 600))
    else:
        print("ERROR: scraper kept failing - giving up until the next scheduled run.")
        return

    print("\nOrchestrator finished successfully!")

if __name__ == "__main__":
    main()
