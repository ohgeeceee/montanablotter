from __future__ import annotations

import os


def launch_browser(playwright):
    executable_path = (
        os.getenv('MB_PLAYWRIGHT_EXECUTABLE_PATH', '').strip()
        or '/usr/bin/google-chrome'
    )
    launch_kwargs = {
        'headless': True,
    }
    args = []
    if os.geteuid() == 0:
        args.extend(['--no-sandbox', '--disable-setuid-sandbox'])
    if args:
        launch_kwargs['args'] = args
    if executable_path and os.path.exists(executable_path):
        launch_kwargs['executable_path'] = executable_path
    return playwright.chromium.launch(**launch_kwargs)
