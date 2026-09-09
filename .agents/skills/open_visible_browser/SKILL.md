---
name: open_visible_browser
description: Opens a Playwright Chrome browser visibly on the user's desktop using schtasks to bypass Session 0 isolation. Useful when the user needs to manually login, solve CAPTCHAs, or see the browser automation happen live.
---

# Open Visible Browser

Because the AI IDE runs as a background service (Session 0), any Chrome window launched natively or via Playwright will run invisibly in the background. This makes it impossible for the user to see the browser to solve CAPTCHAs, perform manual logins, or review the automation.

If the user explicitly requests to see the browser, or if a manual login is required to authenticate the Automation Profile, you **MUST** use Windows Task Scheduler (`schtasks`) to inject the browser process into the user's interactive desktop session (Session 1).

## Step-by-Step Instructions

1. **Kill Existing Chrome Processes**
   Before launching, you must kill any hidden Chrome background processes. If you don't, Playwright will silently connect to the background instance and the window will remain hidden.
   ```powershell
   Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue
   taskkill /F /IM chrome.exe /T
   ```

2. **Write the Playwright Script**
   Create your python script (e.g. `automation_script.py`) with `headless=False` and point it to the persistent Automation Profile. The Automation Profile can restore old tabs from a previous session (crash recovery / "continue where you left off") — always collapse to a single tab right after launch so the user never sees a cluttered window:
   ```python
   import os
   from playwright.sync_api import sync_playwright
   
   user_data_dir = os.path.join(os.environ.get("LOCALAPPDATA"), r"Google\Chrome\Automation Profile")
   
   with sync_playwright() as p:
       context = p.chromium.launch_persistent_context(
           user_data_dir,
           channel="chrome",
           headless=False,
           viewport={"width": 1280, "height": 900}
       )
       # Enforce exactly one tab: reuse the first page, close any restored/extra ones
       page = context.pages[0] if context.pages else context.new_page()
       for extra in context.pages[1:]:
           extra.close()
       # ... perform automation ...
   ```

3. **Create a Batch Wrapper**
   Create a `.bat` file (e.g. `run_automation.bat`) to execute the python script. This avoids extreme quoting issues within `schtasks`.
   ```cmd
   @echo off
   python -u "c:\absolute\path\to\automation_script.py" > "c:\absolute\path\to\automation.log" 2>&1
   ```

4. **Launch via Schtasks**
   Execute the following command exactly using `cmd /c`. It will create a one-time interactive scheduled task and run it immediately on the user's active desktop.
   ```cmd
   cmd /c "schtasks /create /tn "AntigravityVisibleBrowser" /tr "\"c:\absolute\path\to\run_automation.bat\"" /sc once /st 00:00 /ru vinee /it /f && schtasks /run /tn "AntigravityVisibleBrowser""
   ```

### Critical Requirements
- **Always** wrap the `/tr` parameter value in escaped double quotes `\"...\"` so Windows correctly parses paths containing spaces (like `manju jobs dashboard`).
- **Always** include `/ru vinee /it` to guarantee the task executes interactively in the user's session.
