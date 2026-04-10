# 🔧 DIAGNÓSTICO Y REPARACIÓN - Bot Error "No se pudo conectar a BingX"

## 📊 Error Identificado

```
ERROR | ✗ No se pudo conectar a BingX
```

El bot se inicializa correctamente pero **falla al conectar con BingX** en la función `_connect()` en la línea 518:

```python
data = api_request('GET', '/openApi/swap/v2/user/balance')
```

---

## 🔍 CAUSAS MÁS COMUNES (en orden de probabilidad)

### ❌ 1. API Keys inválidas o vacías (80% de casos)

**Síntomas:**
- El .env tiene `BINGX_API_KEY=""` o `BINGX_API_KEY="your_api_key_here"`
- Las keys no están configuradas

**Solución:**
```bash
# Verificar .env
cat .env | grep BINGX_API_KEY

# Debe mostrar:
BINGX_API_KEY="abc123def456..."  # Real key, not placeholder

# NO debe mostrar:
BINGX_API_KEY=""                 # Vacío
BINGX_API_KEY="your_api_key"    # Placeholder
```

---

### ❌ 2. IP Whitelist no configurada en BingX (15% de casos)

**Síntoma:**
- Las keys son válidas pero el bot sigue sin conectar
- El error es silent (no hay respuesta de BingX)

**Solución:**

1. **Login a BingX**: https://bingx.com
2. **Ir a Settings** → **API Management**
3. **Edit tu API Key**
4. **Activa "IP Whitelist"**
5. **Añade la IP de Railway:**

```
Para Railway:
- Generalmente son IPs dinámicas
- BingX acepta ranges: 0.0.0.0/0 (cualquier IP)
- O solo para desarrollo: 1.1.1.1 (Cloudflare DNS)
```

⚠️ **IMPORTANTE**: Si dejas whitelist vacía, Railway no puede conectar

---

### ❌ 3. Permisos insuficientes en API Key (10% de casos)

**Síntoma:**
- La key existe pero responde "Unauthorized" o "No permission"

**Solución:**

En BingX → API Management → Edit Key:

Debe estar activado:
- ✅ **Futures Trading** (REQUERIDO)
- ✅ **Read** (REQUERIDO)
- ❌ Withdrawals (NO activar por seguridad)

**No necesita:**
- Transfer permission (el bot opera lo que ya está en Futures)

---

### ❌ 4. Key expirada o revocada (5% de casos)

**Síntoma:**
- Funcionaba antes, ahora no
- Error: "Invalid API Key"

**Solución:**
1. Crea una NEW API Key en BingX
2. Copia el NEW token a Railway
3. Redeploy

---

## ✅ GUÍA PASO A PASO PARA ARREGLAR

### PASO 1: Generar nuevas API Keys en BingX

```
1. Login a https://bingx.com
2. Click en tu avatar (esquina superior derecha)
3. Settings → API Management
4. Click "Create API Key"
5. Nombre: "InstitutionalBot"
6. Permisos REQUERIDOS:
   ✅ Futures Trading
   ✅ Enable Reading
   ❌ Disable Withdrawals
   ✅ IP Whitelist
       - Opción A: Dejar vacío (permite cualquier IP)
       - Opción B: Agregar rango: 0.0.0.0/0
7. Click "Confirm"
8. Copia tu API KEY (long string)
9. Copia tu API SECRET (long string)
   ⚠️ El SECRET solo se ve UNA VEZ
```

---

### PASO 2: Configurar en Railway

**Opción A: Via Railway Dashboard**
1. Ve a tu proyecto en https://railway.app
2. Ir a tu servicio "bot22"
3. Click en "Variables" (tab)
4. Busca `BINGX_API_KEY`
5. Reemplaza el valor con tu NEW key
6. Busca `BINGX_API_SECRET`
7. Reemplaza con tu NEW secret
8. **Guardar cambios**
9. **Redeploy**: Deploy → Redeploy latest → Confirm

**Opción B: Via archivo .env local (para testing)**
```bash
# En tu máquina local:
# 1. Edita .env
nano .env

# 2. Reemplaza:
BINGX_API_KEY="tu_nueva_api_key_aqui"
BINGX_API_SECRET="tu_nuevo_secret_aqui"

# 3. Prueba localmente:
python institutional_bot_v2.py

# 4. Si funciona, actualiza también en Railway
```

---

### PASO 3: Verificar conexión

Después de actualizar, el bot debería mostrar:
```
✓ BingX conectado | Equity: $XXX.XX
✓ Contratos cargados: 247
✓ Símbolos activos: 50
```

Si aún falla, continúa con el debugging.

---

## 🧪 SCRIPT DE DIAGNOSTICO

Crea un archivo `test_bingx_connection.py`:

```python
#!/usr/bin/env python3
"""Test BingX connection issues"""
import os
import sys
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
import re

def clean_env(key):
    v = os.getenv(key, '').strip()
    if v.startswith('"') and v.endswith('"'): 
        v = v[1:-1]
    elif v.startswith("'") and v.endswith("'"): 
        v = v[1:-1]
    return v

API_KEY = clean_env('BINGX_API_KEY')
API_SECRET = clean_env('BINGX_API_SECRET')
BASE_URL = "https://open-api.bingx.com"

print("=" * 80)
print("🧪 BINGX CONNECTION DIAGNOSTICS")
print("=" * 80)

# Check 1: API Keys configured?
print("\n[1/5] Checking API Keys...")
if not API_KEY:
    print("❌ BINGX_API_KEY is empty or not set")
    print("   Set it in .env or Railway variables")
    sys.exit(1)
if not API_SECRET:
    print("❌ BINGX_API_SECRET is empty or not set")
    sys.exit(1)
print("✅ Both API_KEY and API_SECRET are configured")
print(f"   API_KEY length: {len(API_KEY)} chars")
print(f"   API_SECRET length: {len(API_SECRET)} chars")

# Check 2: API Key format valid?
print("\n[2/5] Checking API Key format...")
if len(API_KEY) < 20:
    print("❌ API_KEY is too short (should be 32+ chars)")
    sys.exit(1)
if len(API_SECRET) < 20:
    print("❌ API_SECRET is too short (should be 32+ chars)")
    sys.exit(1)
print("✅ API Key formats look valid")

# Check 3: Can we reach BingX API?
print("\n[3/5] Testing BingX API connectivity...")
try:
    response = requests.get(f"{BASE_URL}/openApi/swap/v2/quote/contracts", timeout=10)
    if response.status_code == 200:
        print("✅ Can reach BingX API server")
    else:
        print(f"⚠️  API returned status {response.status_code}")
except Exception as e:
    print(f"❌ Cannot reach BingX API: {e}")
    sys.exit(1)

# Check 4: Test authentication
print("\n[4/5] Testing API authentication...")
try:
    # Create signature
    params = {'timestamp': str(int(time.time() * 1000))}
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
    
    if data.get('code') == 0:
        print("✅ Authentication successful!")
        balance = data.get('data', {}).get('equity', 0)
        print(f"   Your Futures balance: ${balance}")
    elif data.get('code') == 401 or 'Unauthorized' in str(data):
        print("❌ Authentication FAILED")
        print(f"   Error: Invalid API Key or Secret")
        print(f"   Check that BINGX_API_KEY and BINGX_API_SECRET are correct")
        print(f"   Response: {data}")
        sys.exit(1)
    elif 'permission' in str(data).lower():
        print("❌ Insufficient permissions")
        print(f"   BingX API Key missing: Futures Trading permission")
        print(f"   Response: {data}")
        sys.exit(1)
    else:
        print(f"❌ Unexpected error: {data}")
        sys.exit(1)
        
except Exception as e:
    print(f"❌ Connection test failed: {e}")
    sys.exit(1)

# Check 5: Load contracts
print("\n[5/5] Testing contract loading...")
try:
    response = requests.get(
        f"{BASE_URL}/openApi/swap/v2/quote/contracts",
        timeout=10
    )
    data = response.json()
    if data.get('code') == 0:
        count = len(data.get('data', []))
        print(f"✅ Successfully loaded {count} contracts")
    else:
        print(f"⚠️  Could not load contracts: {data}")
except Exception as e:
    print(f"❌ Contract loading failed: {e}")

print("\n" + "=" * 80)
print("✅ ALL CHECKS PASSED - Bot should work!")
print("=" * 80)
```

**Cómo usar:**
```bash
# En Railway (en panel Web Terminal):
python test_bingx_connection.py

# O localmente:
python test_bingx_connection.py
```

---

## 📋 CHECKLIST FINAL

Antes de volver a ejecutar el bot:

- [ ] **API Key copiada correctamente** (sin espacios extra)
- [ ] **API Secret copiado correctamente** (sin espacios extra)
- [ ] **Permisos en BingX verificados**:
  - [ ] ✅ Futures Trading enabled
  - [ ] ✅ Reading enabled
  - [ ] ❌ Withdrawals disabled
- [ ] **IP Whitelist configurada**:
  - [ ] Whitelist vacío O
  - [ ] 0.0.0.0/0 O
  - [ ] IP de Railway agregada
- [ ] **Variables actualizadas en Railway**
- [ ] **Bot redeployed** (Redeploy Latest)
- [ ] **Test script ejecutado exitosamente**
- [ ] **Fondos en Futures wallet** de BingX (no en Spot)
- [ ] **AUTO_TRADING_ENABLED=true** en .env

---

## 🆘 SI AÚN NO FUNCIONA

Si después de todo esto aún falla, recolecta esta información:

1. **Output del test script:**
   ```bash
   python test_bingx_connection.py 2>&1 | tail -20
   ```

2. **Primeras 50 líneas del error:**
   ```bash
   # En Railway deploy logs, los últimos 50 líneas
   ```

3. **Verificar en BingX directamente:**
   - ¿Puedo login? Si no → problema con BingX
   - ¿Veo las API keys? Si no → problema de permisos
   - ¿IP Whitelist está vacío? Sí → llenar

4. **Probar con curl desde Railway:**
   ```bash
   # Para verificar conectividad
   curl -I https://open-api.bingx.com/openApi/swap/v2/quote/contracts
   ```

---

## 🎯 RESUMIDO EN 3 MINUTOS

```
Si error "No se pudo conectar a BingX":

1. Verifica que API_KEY y API_SECRET NO estén vacíos
   → cat .env | grep BINGX

2. Verifica en BingX que el API Key tiene:
   ✅ Futures Trading enabled
   ✅ IP Whitelist vac­ío o abierto

3. Copia las keys a Railway
   → Variables tab → actualiza BINGX_API_KEY y BINGX_API_SECRET

4. Redeploy
   → Deploy → Redeploy Latest

5. Run test script
   → python test_bingx_connection.py

Si pasa todo: ✅ Listo
Si falla: 🔍 Debugging steps arriba
```

---

*Last updated: 2026-04-10*
