#!/usr/bin/env python3
"""
🧪 BINGX CONNECTION DIAGNOSTICS
Diagnóstico automático para problemas de conexión con BingX
"""

import os
import sys
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode

def clean_env(key):
    """Clean environment variable"""
    v = os.getenv(key, '').strip()
    if v.startswith('"') and v.endswith('"'): 
        v = v[1:-1]
    elif v.startswith("'") and v.endswith("'"): 
        v = v[1:-1]
    return v

# ════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════════

API_KEY = clean_env('BINGX_API_KEY')
API_SECRET = clean_env('BINGX_API_SECRET')
BASE_URL = "https://open-api.bingx.com"

# ════════════════════════════════════════════════════════════════════
# COLORS FOR OUTPUT
# ════════════════════════════════════════════════════════════════════

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*80}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'='*80}{Colors.ENDC}\n")

def print_ok(text):
    print(f"{Colors.GREEN}✅ {text}{Colors.ENDC}")

def print_error(text):
    print(f"{Colors.RED}❌ {text}{Colors.ENDC}")

def print_warning(text):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.ENDC}")

def print_info(text):
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.ENDC}")

# ════════════════════════════════════════════════════════════════════
# DIAGNOSTICS
# ════════════════════════════════════════════════════════════════════

print_header("🧪 BINGX CONNECTION DIAGNOSTICS")

# ──────────────────────────────────────────────────────────────────
# CHECK 1: API Keys configured?
# ──────────────────────────────────────────────────────────────────

print(f"{Colors.BOLD}[1/5] Checking API Keys...{Colors.ENDC}")

if not API_KEY:
    print_error("BINGX_API_KEY is empty or not set")
    print_info("Set it in .env or Railway variables")
    sys.exit(1)

if not API_SECRET:
    print_error("BINGX_API_SECRET is empty or not set")
    sys.exit(1)

print_ok("Both API_KEY and API_SECRET are configured")
print(f"   {Colors.CYAN}API_KEY length: {len(API_KEY)} characters{Colors.ENDC}")
print(f"   {Colors.CYAN}API_SECRET length: {len(API_SECRET)} characters{Colors.ENDC}")

# ──────────────────────────────────────────────────────────────────
# CHECK 2: API Key format valid?
# ──────────────────────────────────────────────────────────────────

print(f"\n{Colors.BOLD}[2/5] Checking API Key format...{Colors.ENDC}")

if len(API_KEY) < 20:
    print_error("API_KEY is too short (should be 32+ characters)")
    print_info("Your key might be incomplete or truncated")
    sys.exit(1)

if len(API_SECRET) < 20:
    print_error("API_SECRET is too short (should be 32+ characters)")
    sys.exit(1)

# Check for common placeholder strings
if 'your' in API_KEY.lower() or 'example' in API_KEY.lower():
    print_error("API_KEY looks like a placeholder (contains 'your' or 'example')")
    print_info("Copy your actual key from BingX API Management")
    sys.exit(1)

print_ok("API Key formats look valid")

# ──────────────────────────────────────────────────────────────────
# CHECK 3: Can we reach BingX API?
# ──────────────────────────────────────────────────────────────────

print(f"\n{Colors.BOLD}[3/5] Testing BingX API connectivity...{Colors.ENDC}")

try:
    response = requests.get(
        f"{BASE_URL}/openApi/swap/v2/quote/contracts",
        timeout=10
    )
    
    if response.status_code == 200:
        print_ok("Can reach BingX API server")
    else:
        print_warning(f"API returned status {response.status_code}")
        
except requests.exceptions.Timeout:
    print_error("Timeout connecting to BingX (network issue?)")
    print_info("Check your internet connection or firewall")
    sys.exit(1)
    
except requests.exceptions.ConnectionError:
    print_error("Cannot reach BingX API server")
    print_info("Possible causes:")
    print_info("  - No internet connection")
    print_info("  - Firewall blocking BingX domain")
    print_info("  - BingX API temporarily down")
    sys.exit(1)
    
except Exception as e:
    print_error(f"Unexpected error: {e}")
    sys.exit(1)

# ──────────────────────────────────────────────────────────────────
# CHECK 4: Test authentication
# ──────────────────────────────────────────────────────────────────

print(f"\n{Colors.BOLD}[4/5] Testing API authentication...{Colors.ENDC}")

try:
    # Create signature
    timestamp = str(int(time.time() * 1000))
    params = {'timestamp': timestamp}
    query = urlencode(sorted(params.items()))
    
    signature = hmac.new(
        API_SECRET.encode(), 
        query.encode(), 
        hashlib.sha256
    ).hexdigest()
    
    url = f"{BASE_URL}/openApi/swap/v2/user/balance?{query}&signature={signature}"
    headers = {
        'X-BX-APIKEY': API_KEY,
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    
    response = requests.get(url, headers=headers, timeout=15)
    data = response.json()
    
    # Success case
    if data.get('code') == 0:
        print_ok("Authentication successful!")
        
        balance_info = data.get('data', {})
        equity = balance_info.get('equity')
        balance = balance_info.get('balance')
        
        if equity:
            print(f"   {Colors.CYAN}Your Futures equity: ${float(equity):.2f}{Colors.ENDC}")
        elif balance:
            print(f"   {Colors.CYAN}Your Futures balance: ${float(balance):.2f}{Colors.ENDC}")
        else:
            print_warning("Could not retrieve balance")
    
    # Unauthorized case
    elif data.get('code') == 401 or 'Unauthorized' in str(data) or 'Invalid' in str(data):
        print_error("Authentication FAILED - Invalid credentials")
        print_info("Possible causes:")
        print_info("  1. BINGX_API_KEY is incorrect or truncated")
        print_info("  2. BINGX_API_SECRET is incorrect or truncated")
        print_info("  3. API Key was deleted or revoked from BingX")
        print_info("\n  → Generate NEW keys in BingX API Management")
        print_info("  → Copy them EXACTLY (no extra spaces)")
        print_info(f"\nBingX response: {data}")
        sys.exit(1)
    
    # Permission case
    elif 'permission' in str(data).lower() or 'forbid' in str(data).lower():
        print_error("Insufficient permissions on BingX API Key")
        print_info("Your API Key is missing required permissions:")
        print_info("  ✓ Ensure 'Futures Trading' is ENABLED")
        print_info("  ✓ Ensure 'Reading' is ENABLED")
        print_info("  ✓ IP Whitelist should be empty or 0.0.0.0/0")
        print_info(f"\nBingX response: {data}")
        sys.exit(1)
    
    # Other errors
    else:
        print_error(f"Unexpected API response")
        print_info(f"Code: {data.get('code')}")
        print_info(f"Message: {data.get('msg')}")
        print(f"\nFull response: {data}")
        sys.exit(1)
        
except requests.exceptions.Timeout:
    print_error("Timeout during authentication (network issue)")
    sys.exit(1)
    
except requests.exceptions.ConnectionError:
    print_error("Connection error during authentication")
    sys.exit(1)
    
except Exception as e:
    print_error(f"Unexpected error during auth: {e}")
    sys.exit(1)

# ──────────────────────────────────────────────────────────────────
# CHECK 5: Load contracts
# ──────────────────────────────────────────────────────────────────

print(f"\n{Colors.BOLD}[5/5] Testing contract loading...{Colors.ENDC}")

try:
    response = requests.get(
        f"{BASE_URL}/openApi/swap/v2/quote/contracts",
        timeout=10
    )
    data = response.json()
    
    if data.get('code') == 0:
        contracts = data.get('data', [])
        print_ok(f"Successfully loaded {len(contracts)} contracts")
        
        # Show some examples
        if len(contracts) > 0:
            examples = [c.get('symbol') for c in contracts[:5]]
            print_info(f"Example contracts: {', '.join(examples)}...")
    else:
        print_warning(f"Could not load contracts: {data.get('msg')}")
        
except Exception as e:
    print_warning(f"Contract loading test failed: {e}")

# ════════════════════════════════════════════════════════════════════
# FINAL VERDICT
# ════════════════════════════════════════════════════════════════════

print_header("✅ ALL CHECKS PASSED - Bot should work!")

print(f"{Colors.GREEN}{Colors.BOLD}NEXT STEPS:{Colors.ENDC}")
print(f"  1. If running on Railway, redeploy the service")
print(f"  2. Check that AUTO_TRADING_ENABLED=true in .env")
print(f"  3. Start the bot: python institutional_bot_v2.py")
print(f"\n{Colors.CYAN}If bot still fails, check the logs for other errors.{Colors.ENDC}\n")
