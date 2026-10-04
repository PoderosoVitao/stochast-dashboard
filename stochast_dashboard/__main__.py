from __future__ import annotations

import uvicorn


# Binds to localhost only: an adapter spec executes arbitrary local Python,
# the same trust model as stochast's own --adapter flag.
def main() -> None:
    uvicorn.run("stochast_dashboard.app:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
