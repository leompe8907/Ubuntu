# Lógica de desencriptado en el Frontend (React/Vite)

Este documento describe, paso a paso, cómo descifrar `encrypted_credentials` que entrega tu backend Django para el esquema híbrido:

`AES-256-CBC + RSA-OAEP (SHA-256)` con `encrypted_data`, `encrypted_key` e `iv` en base64.

## 1) Por qué el Frontend puede (o no) desencriptar

El backend genera un payload cifrado con la función `hybrid_encrypt_for_app()`:

1. Genera una clave AES aleatoria de 32 bytes (256 bits).
2. Cifra el JSON de credenciales con `AES-256-CBC` usando un `iv` aleatorio de 16 bytes.
3. Cifra la clave AES con `RSA-OAEP` usando la `public_key_pem` embebida del dispositivo.
4. Devuelve al cliente:
   - `encrypted_data` (ciphertext AES) en base64
   - `encrypted_key` (AES key cifrada con RSA) en base64
   - `iv` en base64
   - `algorithm` y `app_type` (metadata)

Importante: tu modelo `AppCredentials` indica que `private_key_pem` es "NUNCA enviar al cliente". En consecuencia, en un navegador web (React/Vite) **no debes** incluir la clave privada. El desencriptado en front solo es correcto si:

- Estás ejecutando en un entorno seguro que sí conserva la clave privada (por ejemplo, app nativa / dispositivo con storage seguro).
- O bien tienes un mecanismo distinto para desencriptar sin exponer la private key.

Aun así, este documento muestra la lógica técnica de desencriptado usando Web Crypto.

## 2) Estructura exacta del JSON devuelto por el backend

En `udid/views.py` el backend responde con algo como:

`encrypted_credentials`:

- `encrypted_data`: string base64 (AES-CBC ciphertext)
- `encrypted_key`: string base64 (AES key cifrada con RSA-OAEP)
- `iv`: string base64 (IV de AES-CBC, 16 bytes)
- `algorithm`: `"AES-256-CBC + RSA-OAEP"`
- `app_type`: string (el tipo de app)

El JSON de credenciales (plaintext antes de cifrar) se arma en el backend como JSON serializado con `json.dumps(...)` y contiene al menos:

- `subscriber_code`
- `sn`
- `login1`
- `login2`
- `password`
- `pin`
- `packages`
- `products`
- `timestamp`

## 3) Algoritmo y parámetros criptográficos (los que debes replicar en el front)

### 3.1 RSA-OAEP

En `udid/management/commands/keyGenerator.py`:

- OAEP hash: `SHA-256`
- MGF1 hash: `SHA-256`
- `label=None`

En WebCrypto equivale a importar la clave RSA privada con:

- `importKey('pkcs8', privateKeyDer, { name: 'RSA-OAEP', hash: 'SHA-256' }, ..., ['decrypt'])`

y descifrar con:

- `subtle.decrypt({ name: 'RSA-OAEP' }, privateKey, encryptedKeyBytes)`

### 3.2 AES-256-CBC

- AES key size: 32 bytes (256 bits)
- Modo: CBC
- IV: 16 bytes
- Padding: el backend implementa un padding compatible con PKCS#7 para bloques de 16 bytes.

En WebCrypto:

- Importa AES con `importKey('raw', aesKeyRaw, { name: 'AES-CBC' }, ..., ['decrypt'])`
- Descifra con `subtle.decrypt({ name: 'AES-CBC', iv }, aesKey, encryptedDataBytes)`

WebCrypto devuelve directamente el plaintext (ya desempaquetado), siempre que el ciphertext tenga padding válido.

## 4) Formatos: base64, PEM y conversiones

### 4.1 Base64

Tu backend usa `base64.b64encode(...).decode('utf-8')`.

Por tanto, en el front necesitas convertir base64 a `Uint8Array` para WebCrypto.

### 4.2 PEM de la clave privada (RSA)

En el backend la private key se genera con:

- `format=serialization.PrivateFormat.PKCS8`
- `encryption_algorithm=serialization.NoEncryption()`

Es decir, en el front debes importar una clave PEM que corresponda a PKCS8.

En WebCrypto normalmente haces:

1. PEM -> base64 -> DER bytes
2. `importKey('pkcs8', derBytes, ...)`

## 5) Implementación completa (TypeScript + WebCrypto)

A continuación tienes una implementación lista para pegar en tu proyecto React/Vite (sin librerías externas).

### 5.1 Utilidades: base64 -> bytes y PEM -> DER

```ts
export function base64ToBytes(b64: string): Uint8Array {
  const cleaned = b64.replace(/\\s+/g, "");
  const bin = atob(cleaned);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

export function pemPkcs8ToDer(pem: string): ArrayBuffer {
  const b64 = pem
    .replace(/-----BEGIN [^-]+-----/g, "")
    .replace(/-----END [^-]+-----/g, "")
    .replace(/\\s+/g, "");
  const derBytes = base64ToBytes(b64);
  return derBytes.buffer;
}
```

### 5.2 Función principal: descifrar `encrypted_credentials`

```ts
export type EncryptedCredentials = {
  encrypted_data: string; // base64
  encrypted_key: string;  // base64
  iv: string;             // base64
  algorithm?: string;
  app_type?: string;
};

export async function decryptEncryptedCredentials(
  encrypted: EncryptedCredentials,
  privateKeyPemPkcs8: string
) {
  const encryptedDataBytes = base64ToBytes(encrypted.encrypted_data);
  const encryptedKeyBytes = base64ToBytes(encrypted.encrypted_key);
  const ivBytes = base64ToBytes(encrypted.iv);

  // 1) RSA-OAEP (SHA-256): desencriptar AES key (raw 32 bytes)
  const der = pemPkcs8ToDer(privateKeyPemPkcs8);
  const rsaPrivateKey = await crypto.subtle.importKey(
    "pkcs8",
    der,
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["decrypt"]
  );

  const aesKeyRaw = await crypto.subtle.decrypt(
    { name: "RSA-OAEP" },
    rsaPrivateKey,
    encryptedKeyBytes
  );

  // 2) AES-CBC: descifrar encrypted_data
  const aesKey = await crypto.subtle.importKey(
    "raw",
    aesKeyRaw,
    { name: "AES-CBC" },
    false,
    ["decrypt"]
  );

  const plaintextBuf = await crypto.subtle.decrypt(
    { name: "AES-CBC", iv: ivBytes },
    aesKey,
    encryptedDataBytes
  );

  const plaintext = new TextDecoder("utf-8").decode(plaintextBuf);

  // El backend cifró json.dumps(...) => texto JSON
  return JSON.parse(plaintext);
}
```

## 6) Flujo completo de consumo en tu React/Vite

1. Haces la llamada al endpoint que retorna `encrypted_credentials`.
2. Tomas `response.encrypted_credentials`.
3. Llamas `decryptEncryptedCredentials(encrypted, privateKeyPemPkcs8)`.
4. El resultado es el objeto JSON de credenciales.

Nota de seguridad: la clave privada solo debe existir en un entorno que puedas proteger. Si tu código corre en un browser estándar, cualquier private key embebida puede ser extraída.

## 7) Errores comunes (y cómo detectarlos)

1. Error `OperationError: The operation failed for an operation-specific reason`
   - Normalmente significa que la clave RSA privada no corresponde a esa `public_key_pem`, o que el padding/parámetros no coinciden.

2. `SyntaxError: JSON.parse ...`
   - Sucede si el AES decrypt devolvió bytes corruptos o si hubo un problema con IV/base64.

3. `DataError` al importar la clave RSA
   - Verifica que el PEM sea PKCS8 (`BEGIN PRIVATE KEY`) y no otro formato.

4. WebCrypto no funciona en HTTP plano
   - Usa `https://` o `localhost` (por políticas del navegador).

## 8) Recomendación (arquitectura segura)

Si lo que buscas es seguridad real, evita que React/Vite desencripte con `private_key_pem`:

- Mantén la private key en la capa segura del dispositivo (app nativa/entorno seguro).
- O transforma el diseño para que el server no entregue material que requiera privada en el cliente.

Este documento te da la lógica técnica, pero la seguridad depende de dónde guardes la private key.

