"""Load the report in Chromium at 375 and 1280 px, light and dark, fail on any
console error, and save screenshots.

    python scripts/screenshot.py docs/report/index.html --out docs/screenshots
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

WIDTHS = (375, 1280)
SCHEMES = ("light", "dark")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report")
    parser.add_argument("--out", default="docs/screenshots")
    parser.add_argument("--channel", help='use an installed browser, e.g. "chrome"')
    args = parser.parse_args()
    url = Path(args.report).resolve().as_uri()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel)
        for width in WIDTHS:
            for scheme in SCHEMES:
                page = browser.new_page(
                    viewport={"width": width, "height": 900}, color_scheme=scheme
                )
                page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url)
                page.wait_for_selector(".js-plotly-plot, .empty")
                overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
                if overflow:
                    errors.append(f"horizontal scroll at {width}px {scheme}")
                path = out / f"report-{width}-{scheme}.png"
                page.screenshot(path=str(path), full_page=width == 375)
                print(f"wrote {path}")
                page.close()
        browser.close()
    if errors:
        print("errors:\n" + "\n".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
