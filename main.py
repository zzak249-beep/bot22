"""Entry point for Railway deployment."""
import sys, os

# Validate critical env vars early with clear error messages
def check_env():
    api_key = os.environ.get("BINGX_API_KEY", "")
    secret  = os.environ.get("BINGX_API_SECRET", "") or os.environ.get("BINGX_SECRET_KEY", "")

    if not api_key:
        print("ERROR: BINGX_API_KEY environment variable is not set!", file=sys.stderr)
        print("Set it in Railway → Variables tab.", file=sys.stderr)
        sys.exit(1)

    if not secret:
        print("ERROR: BINGX_API_SECRET environment variable is not set!", file=sys.stderr)
        print("Set it in Railway → Variables tab.", file=sys.stderr)
        sys.exit(1)

    print(f"✅ API credentials found (key ends in ...{api_key[-4:]})")

check_env()

from bot import SuperBot

if __name__ == "__main__":
    bot = SuperBot()
    bot.run()
