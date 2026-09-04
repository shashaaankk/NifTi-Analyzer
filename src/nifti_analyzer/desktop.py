"""Entry point: the standalone NIfTI Analyzer window.

No server, no ports. The page talks to Python over pywebview's js_api bridge
(WebKit2GTK on Linux). Closing the window kills any running job's process
group and ends the process.
"""

from __future__ import annotations

from pathlib import Path

import webview

from .runner import Api

INDEX = Path(__file__).parent / "static" / "index.html"


def main() -> None:
    api = Api()
    api.window = webview.create_window(
        "NIfTI Analyzer", str(INDEX), js_api=api, width=820, height=640)
    webview.start()
    api.cleanup()


if __name__ == "__main__":
    main()
