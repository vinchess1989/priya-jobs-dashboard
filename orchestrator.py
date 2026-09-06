import os
import subprocess
import json
import sys

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

def run_script(script_name):
    print(f"\n{'='*50}\nRunning {script_name}...\n{'='*50}")
    # Use global python if venv doesn't exist, else use venv
    venv_python = os.path.join(PUBLIC_DIR, "venv", "Scripts", "python.exe")
    python_exe = venv_python if os.path.exists(venv_python) else "python"
    
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    
    process = subprocess.Popen([python_exe, "-u", script_name], cwd=PUBLIC_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1)
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
    
    # Step 1: Scrape new jobs
    if not run_script("scraper.py"): return
    
    # Step 2: Curate jobs (Keyword filtering)
    if not run_script("curate_jobs.py"): return
    
    # Step 3: Evaluate jobs with local LLM
    if not run_script("evaluate_with_local_llm.py"): return
    
    print("\nOrchestrator finished successfully! (Steps 1-3)")

if __name__ == "__main__":
    main()
