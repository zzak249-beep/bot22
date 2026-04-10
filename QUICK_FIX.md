# 🆘 QUICK REFERENCE - BingX Connection Error

## 📍 Error: "No se pudo conectar a BingX"

This means the bot **initialized** but **failed to authenticate** with BingX.

---

## ⚡ 60-Second Fix

### 1️⃣ Check if your API keys are blank

```bash
# In Railway, click your service → Variables tab
# Look at: BINGX_API_KEY

❌ If it shows:
   ""
   "your_api_key_here"
   "paste_key_here"
   
✅ It should show:
   "a1b2c3d4e5f6g7h8..."  (32+ random characters)
```

### 2️⃣ If blank, get real keys from BingX

1. Login: https://bingx.com
2. Avatar → Settings → API Management
3. Click "Create API Key" (or find existing)
4. Copy the KEY and SECRET
5. In Railway, paste them in Variables
6. Redeploy

### 3️⃣ If not blank, check IP Whitelist

1. Login: https://bingx.com
2. Avatar → Settings → API Management
3. Click "Edit" on your API Key
4. Find "IP Whitelist"
5. It should be EMPTY or contain `0.0.0.0/0`
6. If not, clear it and save

### 4️⃣ Redeploy

- Railway → bot22 → Deploy → Redeploy Latest

---

## 🔍 Detailed Diagnosis

| Symptom | Cause | Solution |
|---------|-------|----------|
| `BINGX_API_KEY=""` | Empty key | Get real key from BingX |
| `Auth failed - Invalid` | Wrong key/secret | Copy from BingX, no typos |
| `Insufficient permission` | Missing Futures Trading | Enable in BingX API settings |
| `No route to host` | IP Whitelist blocking | Clear whitelist or add `0.0.0.0/0` |
| `Connection timeout` | Network issue | Check internet, try public VPN |

---

## 📋 Step-by-Step Walkthrough

### Getting API Keys from BingX

```
1. Go to https://bingx.com
2. Click your profile avatar (top right)
3. Click "Settings"
4. Click "API Management"
5. Click "Create API Key" (or edit existing)

Configuration:
   Name: "InstitutionalBot"
   
Permissions (checkboxes):
   ✅ Futures Trading       ← REQUIRED
   ✅ Enable Reading        ← REQUIRED
   ⚪ Withdrawals          ← leave UNCHECKED (safer)

IP Whitelist:
   Leave EMPTY or add 0.0.0.0/0

6. Click "Confirm"
7. You'll see:
   API Key: xxxxxxxxxxxxxxxx (copy this)
   API Secret: xxxxxxxxxxxxxxxx (copy this - appears once only!)

8. Keep these safe
```

### Updating Railway Variables

```
1. Go to https://railway.app
2. Click your project: "amused-fascination"
3. Click service: "bot22"
4. Click tab: "Variables"
5. Click in the value field for "BINGX_API_KEY"
6. Clear it and paste your API Key
7. Click in the value field for "BINGX_API_SECRET"
8. Clear it and paste your API Secret
9. Click elsewhere (auto-saves)
10. Go back to main service view
11. Click "Deploy" (top right)
12. Click "Redeploy Latest"
13. Click "Confirm"
14. Wait 1-2 minutes for deployment
15. Check logs: should show ✓ BingX conectado
```

---

## 🧪 Test Your Fix

After updating Railway, run this test:

### In Railway Web Terminal:
```bash
python test_bingx_connection.py
```

### Expected output:
```
✅ Both API_KEY and API_SECRET are configured
✅ API Key formats look valid
✅ Can reach BingX API server
✅ Authentication successful!
✅ Your Futures equity: $100.00
✅ Successfully loaded 247 contracts
✅ ALL CHECKS PASSED - Bot should work!
```

### If test fails:
- Read the error carefully
- Follow the suggestion in the error message
- Try again

---

## 🎯 What NOT to Do

❌ **Don't:**
- Leave API Key empty
- Use placeholder text
- Copy key with extra spaces
- Share your API Secret anywhere
- Use someone else's keys
- Enable "Withdrawals" permission

✅ **Do:**
- Use real keys from BingX
- Copy exactly (Ctrl+C from BingX, Ctrl+V to Railway)
- Keep Secret confidential
- Only enable Futures + Reading permissions
- Clear IP Whitelist (or use 0.0.0.0/0)
- Verify with test script

---

## 📱 Visual Checklist

```
[STEP 1] Get Keys from BingX
   □ Login to BingX
   □ Find API Management
   □ API Key is 32+ chars (not blank/placeholder)
   □ API Secret is 32+ chars (not blank/placeholder)

[STEP 2] Configure Permissions in BingX
   □ ✅ Futures Trading enabled
   □ ✅ Reading enabled
   □ ❌ Withdrawals disabled
   □ IP Whitelist empty or 0.0.0.0/0

[STEP 3] Update Railway Variables
   □ BINGX_API_KEY = real key
   □ BINGX_API_SECRET = real secret
   □ AUTO_TRADING_ENABLED = true
   □ Variables saved

[STEP 4] Redeploy Bot
   □ Click Deploy
   □ Click Redeploy Latest
   □ Click Confirm
   □ Wait for ✅

[STEP 5] Verify
   □ Run test_bingx_connection.py
   □ All checks pass
   □ Logs show "✓ BingX conectado"
```

---

## 💬 Common Questions

**Q: Where do I find my API keys?**
A: BingX → Avatar → Settings → API Management

**Q: Can I use the same key for multiple bots?**
A: Yes, but it's safer to create separate keys for each bot

**Q: What if I forget my API Secret?**
A: You can't recover it. You must create a new API Key.

**Q: Does the bot need Spot wallet funds?**
A: No. It uses Futures wallet only. Make sure you transfer funds there.

**Q: Why is bot paused at 22:00 UTC?**
A: Session filter activates only during US/London trading hours (13:00-22:00 UTC)

**Q: How long until it starts trading?**
A: 1-5 minutes. It scans every minute for signals.

---

## 🚨 Emergency Stop

If bot is running and you want to stop it:

### In Railway:
1. Go to bot22 service
2. Click "Stop Service"
3. Click "Confirm"

### Or in Railway Web Terminal:
```bash
kill $(pgrep -f "institutional_bot_v2.py")
```

---

## 📞 When to Ask for Help

Prepare this info:

1. Output of: `python test_bingx_connection.py`
2. First ERROR line from Railway logs
3. Your BINGX_API_KEY value (first 10 chars only)
4. Screenshot of BingX API Management page
5. Screenshot of Railway Variables tab

---

**Remember: 99% of "can't connect" issues = missing/wrong API keys or IP whitelist.**

*Last updated: 2026-04-10*
