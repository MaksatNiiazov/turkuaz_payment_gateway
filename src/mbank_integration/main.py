from __future__ import annotations

import uvicorn

from mbank_integration.api import create_app

app = create_app()


def run() -> None:
    uvicorn.run("mbank_integration.main:app", host="0.0.0.0", port=8010, reload=False)


if __name__ == "__main__":
    run()
