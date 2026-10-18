"""Permite rodar com `python -m app` (usa as variáveis HOST e PORT)."""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
