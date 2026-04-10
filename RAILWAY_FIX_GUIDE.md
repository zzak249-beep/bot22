# 🚀 RAILWAY DEPLOYMENT - Complete Fix Guide

## 🔴 Current Problem

Your bot crashed on **Apr 10, 2026 22:58:56 UTC** with:

```
ERROR | ✗ No se pudo conectar a BingX
```

The bot initialized successfully but **failed to authenticate with BingX API**.

---

## 🎯 Quick Fix (3 Steps)

### Step 1: Verify Your API Keys in BingX

Go to https://bingx.com → Settings → API Management

Your API Key should look like:
```
xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  (32+ characters)
```

Your API Secret should look like:
```
xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx  (32+ characters)
```

**DO NOT use placeholder values like:**
- ❌ `"your_api_key_here"`
- ❌ `"paste_your_key"`
- ❌ Empty string `""`

---

### Step 2: Add/Fix Whitelist in BingX

In the same API Management page, **Edit your API Key**:

Find **"IP Whitelist"** section:

**Option A (Easiest for Railway):**
```
Leave whitelist EMPTY (click "Clear")
This allows any IP to use your key
```

**Option B (More restrictive):**
```
Add: 0.0.0.0/0
This is the same as allowing any IP
```

**Option C (If you know Railway IP):**
```
Click "My IP" to add current IP
But Railway might change IP on redeploy
Not recommended
```

⚠️ **CRITICAL**: If whitelist has entries that don't include Railway's IP, the bot CANNOT connect.

---

### Step 3: Update Railway Variables

1. **Go to** https://railway.app
2. **Click** your project `amused-fascination`
3. **Click** service `bot22`
4. **Click** tab "Variables"
5. **Find** `BINGX_API_KEY`
6. **Replace** with your CORRECT key (copy from BingX)
7. **Find** `BINGX_API_SECRET`
8. **Replace** with your CORRECT secret
9. **Click** "Save" (should auto-save)

**Then redeploy:**
- Click "Deploy"
- Click "Redeploy Latest"
- Click "Confirm"
- Wait for ✅ status

---

## ✅ Detailed Checklist

### BingX API Configuration

- [ ] Login to https://bingx.com (you can access it)
- [ ] Can see "API Management" section
- [ ] API Key exists and is NOT a placeholder
- [ ] API Secret exists and is NOT a placeholder
- [ ] API Key permissions:
  - [ ] ✅ "Futures Trading" is ENABLED
  - [ ] ✅ "Reading" is ENABLED
  - [ ] ❌ "Withdrawals" is DISABLED (for security)
- [ ] IP Whitelist:
  - [ ] Either EMPTY or contains `0.0.0.0/0`

### Railway Configuration

- [ ] Logged into Railway
- [ ] Found your service `bot22`
- [ ] In "Variables" tab:
  - [ ] `BINGX_API_KEY` = your actual key (not placeholder)
  - [ ] `BINGX_API_SECRET` = your actual secret
  - [ ] `AUTO_TRADING_ENABLED=true`
  - [ ] Variables saved
- [ ] Redeployed service
- [ ] Waiting for ✅ (green status)

### Testing

- [ ] Run test script: `python test_bingx_connection.py`
- [ ] Output shows "ALL CHECKS PASSED"
- [ ] Check Railway logs for ✅ "BingX conectado"

---

## 📊 Step-by-Step Screenshots Path

### Part 1: BingX Setup

```
1. Login to BingX
   https://bingx.com/en-us

2. Navigate to API Management
   Avatar (top right) → Settings → API Management

3. Check existing API Key OR Create new one
   - If creating new: Click "Create API Key"
   - Name: "InstitutionalBot"

4. Edit API Key Permissions
   Click "Edit" on your key
   
   Permissions:
   ☑️ Futures Trading
   ☑️ Enable Reading
   ☐ Withdrawals (leave unchecked)
   
5. Configure IP Whitelist
   Clear existing IPs OR
   Add: 0.0.0.0/0 (allows any IP)

6. Save and Copy Keys
   - Copy full API KEY
   - Copy full API SECRET
   ⚠️ Secret only shows once!

7. Verify in .env format
   BINGX_API_KEY="abc123def456..."
   BINGX_API_SECRET="xyz789..."
   (32+ characters each, no spaces)
```

### Part 2: Railway Update

```
1. Go to Railway Dashboard
   https://railway.app/dashboard

2. Click your Project
   Name: "amused-fascination"

3. Click Service bot22
   Shows: bot22 | Crashed ❌

4. Click "Variables" Tab
   Shows list of environment variables

5. Find BINGX_API_KEY
   Click the VALUE field
   Paste your NEW API Key

6. Find BINGX_API_SECRET  
   Click the VALUE field
   Paste your NEW API Secret

7. Click "Save" or Ctrl+S
   Changes should auto-save

8. Redeploy
   Click "Deploy" (top right)
   Select "Redeploy Latest"
   Confirm
   Wait for ✅

9. Check Logs
   Click "Logs" tab
   Should see: ✓ BingX conectado
```

---

## 🧪 Testing the Connection

After redeploying, run the test script:

### Option 1: In Railway Web Terminal

```bash
# Go to your service → Tools → Web Terminal
python test_bingx_connection.py

# Should output:
# ✅ Both API_KEY and API_SECRET are configured
# ✅ API Key formats look valid
# ✅ Can reach BingX API server
# ✅ Authentication successful!
# ✅ Your Futures equity: $100.00
# ✅ Successfully loaded 247 contracts
# ✅ ALL CHECKS PASSED - Bot should work!
```

### Option 2: Locally (if you have Python)

```bash
# Copy .env from Railway to your machine
# Run in your local terminal:
python test_bingx_connection.py
```

---

## 🚨 Common Errors and Solutions

### Error 1: "Authentication FAILED - Invalid credentials"

**Cause:** API Key or Secret is wrong

**Solution:**
1. Delete the old API Key in BingX
2. Create a NEW one
3. Copy it EXACTLY to Railway
4. Don't include spaces or quotes

### Error 2: "Insufficient permissions on BingX API Key"

**Cause:** Missing "Futures Trading" permission

**Solution:**
1. Go to BingX API Management
2. Edit your key
3. Check ✅ "Futures Trading"
4. Check ✅ "Reading"
5. Save

### Error 3: "Cannot reach BingX API server"

**Cause:** Whitelist blocking Railway IP

**Solution:**
1. Go to BingX API Management
2. Edit your key
3. Clear IP Whitelist (or add 0.0.0.0/0)
4. Save
5. Redeploy bot

### Error 4: "Unexpected API response" with error code

**Cause:** Various API issues

**Solution:**
1. Check BingX status page: https://status.bingx.com/
2. Try creating NEW API key
3. Make sure you have funds in Futures wallet

---

## 🎯 Verification Checklist

After redeploying, you should see in Railway logs:

```
═══════════════════════════════════════════════════════════════════
🏆 INSTITUTIONAL BOT v2.0
═══════════════════════════════════════════════════════════════════
Capital: $10.0 × 2 posiciones | 2×
Filtros: Funding=✓ | OI=✓ | Session=✓
TPs: 35%@1.2RR | 35%@2.2RR | 30%@trail
Min Edge: 3.0× costes | Circuit Breaker: 6.0%
═══════════════════════════════════════════════════════════════════

✓ BingX conectado | Equity: $100.00
✓ Contratos cargados: 247
✓ Símbolos activos: 50
✓ Posiciones recuperadas: 0

═══════════════════════════════════════════════════════════════════
🚀 Institutional Bot v2.0 RUNNING
```

If you see this, **BOT IS WORKING** ✅

---

## 📝 Important Notes

### About Funds

- Bot scans for signals 24/7
- Only trades when conditions are met
- Starts with small position size ($10 default)
- Max 4 simultaneous positions
- Total exposure: $40 max
- Make sure you have AT LEAST $50 in Futures wallet

### About Signals

- Not all symbols will generate signals
- Session filter limits trading hours (13:00-22:00 UTC = US session)
- Funding filter skips when market is overheated
- Usually 2-8 signals per day

### About Updates

If you need to modify the bot code:
1. Update in Railway
2. Push changes to Git (if using Git integration)
3. Redeploy automatically triggers on git push
4. Or manually redeploy from Dashboard

---

## 🆘 If Still Doesn't Work

Collect this info:

1. **Output of test script:**
   ```bash
   python test_bingx_connection.py 2>&1 | tee test_output.txt
   ```
   (Share the entire output)

2. **First error line from Railway logs:**
   - Go to Railway → bot22 → Logs
   - Look for first ❌ ERROR message
   - Share exactly what it says

3. **Verify in BingX directly:**
   - Can you login?
   - Can you see your balance?
   - Do you have API keys?
   - Are they 32+ characters?

4. **Double-check variables:**
   - Railway → bot22 → Variables
   - Look at BINGX_API_KEY value
   - Look at BINGX_API_SECRET value
   - Are they your actual keys or placeholders?

5. **Check IP Whitelist:**
   - BingX API Management
   - Edit your key
   - Is whitelist empty or 0.0.0.0/0?

---

## 📞 Support Resources

- **BingX API Docs:** https://bingx-api.github.io/docs/
- **BingX Status:** https://status.bingx.com/
- **Railway Docs:** https://docs.railway.app/
- **Test Script:** Run `python test_bingx_connection.py`

---

**Bottom line: Your bot crashed because API keys weren't properly configured in Railway. Fix the steps above and it should work.**

*Last updated: 2026-04-10*
