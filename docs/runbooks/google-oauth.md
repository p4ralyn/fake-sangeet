# Setting up Google OAuth for unigate

Project `my-gcp-project` sits inside the **snu.edu.in** organisation
(org `976072654956`), which is what makes step 1 below available. Confirmed
with `gcloud projects describe my-gcp-project`.

## Before you start: the redirect URI must be HTTPS

Google rejects a bare IP as a redirect URI. It accepts `http://localhost` for
development and `https://` for everything else — never `http://<ip>`.

Separately, `Settings` refuses to start with `APP_ENV=prod` and
`COOKIE_SECURE=false`, because a session cookie sent over plain HTTP is
interceptable. So the deployment needs TLS regardless of Google.

Three ways to get a hostname today:

| Option | Effort | Notes |
|---|---|---|
| **Wildcard DNS** — `203.0.113.10.nip.io` or `203-0-113-10.sslip.io` | none | Free services resolving `<ip>.nip.io` to that IP. Caddy gets a real Let's Encrypt cert for it. Swap to the real domain later by changing one env var. |
| Buy the domain | ~10 min | The eventual answer anyway. |
| Stay on `AUTH_PROVIDER=dev` | none | No real sign-in. Fine for testing the rest of the stack, useless for the event. |

Recommended: wildcard DNS now, real domain when you have it. Register **both**
redirect URIs in step 2 and the swap costs nothing.

## 1. Consent screen

console.cloud.google.com, signed in as **pk944@snu.edu.in**, project
**my-gcp-project**.

*APIs & Services → OAuth consent screen*

- User type: **Internal**. This is the whole reason the project lives in the
  SNU org — Internal means no verification review, no "unverified app" warning,
  no 100-user cap, and only snu.edu.in accounts can sign in at all.
- App name: `Fake Sangeet` — this is what students see on the consent screen.
- User support email: your SNU address.
- Developer contact: same.
- Scopes: leave empty. The app requests `openid email profile`, all
  non-sensitive, none requiring review.

## 2. Create the client

*APIs & Services → Credentials → Create credentials → OAuth client ID*

- Application type: **Web application**
- Name: `unigate`
- Authorised JavaScript origins: **leave empty.** The flow is server-side; the
  browser never holds a token.
- Authorised redirect URIs — add every host you will actually use:

```
http://localhost:8000/auth/callback
https://203.0.113.10.nip.io/auth/callback
https://<your-domain>/auth/callback          # once you own it
```

The URI must match `APP_BASE_URL + /auth/callback` **exactly** — scheme, host,
port and path. A trailing-slash difference is a rejected sign-in.

## 3. Put the credentials in .env

```env
AUTH_PROVIDER=google
GOOGLE_CLIENT_ID=<client id>
GOOGLE_CLIENT_SECRET=<client secret>
GOOGLE_ALLOWED_DOMAIN=snu.edu.in
APP_BASE_URL=https://203.0.113.10.nip.io    # must match the redirect URI
```

Locally, keep `APP_BASE_URL=http://localhost:8000` and `AUTH_PROVIDER=dev` so
you can work with no Google account at all.

`Settings` refuses to start if `AUTH_PROVIDER=google` and either credential is
blank — deliberate, so a half-configured deploy fails loudly at boot rather
than at a student's first sign-in.

## 4. Check it

```bash
docker compose up -d
curl -sI "$APP_BASE_URL/auth/start?next=/api/v1/me" | grep -i location
```

The `Location` header should point at `accounts.google.com` carrying
`hd=snu.edu.in`, `code_challenge_method=S256` and a `code_challenge`.

`hd` on that URL is only a hint — it pre-filters the account chooser. The real
enforcement is server-side: the provider verifies the `hd` **claim inside the
signed ID token** and rejects anything that is not snu.edu.in, because an
attacker can simply drop the parameter and sign in with a personal Gmail whose
token is still validly signed by Google.

## 5. Seeded staff must be real addresses

NetID is the email local part, so `SEED_OWNER_NET_IDS=["pk944"]` resolves to
`pk944@snu.edu.in`. Staff whose NetID does not match a real SNU account will
sign in as an ordinary VISITOR, not as staff.

## First SSH to the VM

`gcloud compute ssh` generates a key on first use and can prompt for a
passphrase, so run it once **interactively** before any script needs it:

```bash
gcloud compute ssh unigate-vm --zone=asia-south1-a --project=my-gcp-project
```

`default-allow-ssh` (tcp:22) already exists in the project, so the firewall is
not the obstacle — the missing local key is.
