from __future__ import annotations

import uvicorn

from payment_gateway.api import create_app

app = create_app()


def run() -> None:
    uvicorn.run("payment_gateway.main:app", host="0.0.0.0", port=8502, reload=False)


if __name__ == "__main__":
    run()
