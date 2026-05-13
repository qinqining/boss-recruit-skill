#!/usr/bin/env python3
"""
Test script - Opens browser and waits for manual login.
Use this to inspect DOM elements and find correct selectors.
DO NOT close this script while testing!
"""
import time
import sys
from pathlib import Path

# Fix Windows stdout encoding
if sys.platform == 'win32':
    import io
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

FIXED_SEED = "boss-recruit-agent-2026"

def main():
    print("[TEST] Starting browser for manual inspection...")

    import random
    random.seed(FIXED_SEED)
    from camoufox.fingerprints import generate_fingerprint
    fp = generate_fingerprint()
    print(f"[TEST] UA: {fp.navigator.userAgent[:70]}...")

    from camoufox import Camoufox, launch_options
    from camoufox.addons import DefaultAddons
    import camoufox.sync_api as sync_api

    profile_dir = Path(__file__).parent.parent / "recruit_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    print(f"[TEST] Profile: {profile_dir}")

    opts = launch_options(
        headless=False,
        humanize=True,
        fingerprint=fp,
        window_size=(1280, 720),
        i_know_what_im_doing=True,
        exclude_addons=[DefaultAddons.UBO],
    )
    opts.update({
        "persistent_context": True,
        "user_data_dir": str(profile_dir.resolve()),
    })

    # Monkey-patch
    _original = sync_api.NewBrowser
    def patched(playwright, *, headless=None, from_options=None, persistent_context=False, debug=None, **kwargs):
        if from_options and from_options.get('persistent_context'):
            from_options = {k: v for k, v in from_options.items()
                           if k not in ('persistent_context', 'fingerprint', 'humanize', 'geoip',
                                        'os', 'block_images', 'i_know_what_im_doing', 'seed',
                                        'window_size', 'debug')}
            context = playwright.firefox.launch_persistent_context(**from_options)
            return context
        return _original(playwright, headless=headless, from_options=from_options,
                         persistent_context=persistent_context, debug=debug, **kwargs)
    sync_api.NewBrowser = patched

    browser = Camoufox(from_options=opts)
    context = browser.__enter__()
    page = context.pages[0] if context.pages else context.new_page()

    # Go to login page
    page.goto("https://www.zhipin.com/web/geek/login", wait_until="domcontentloaded")
    print("[TEST] Please log in manually in the browser...")
    print("[TEST] After login, navigate to any page and press Ctrl+C to exit")

    # Keep alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[TEST] Exiting...")
    finally:
        browser.__exit__(None, None, None)

if __name__ == "__main__":
    main()
