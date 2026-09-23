# Moving off nip.io onto a real domain

Why this is the fix for the college wifi blocking it, and exactly what to do.

## The problem, stated correctly

The site's TLS is not broken. The certificate is a real Let's Encrypt one,
issued for `203.0.113.10.nip.io`, and it verifies. Two separate things produce
a "not secure" or blocked experience, and they have different causes:

1. **The filter.** `nip.io` is a public wildcard-DNS service: it resolves
   `<anything>.203.0.113.10.nip.io` to that IP without anybody registering a
   name. That is exactly the property phishing and malware use to spin up
   throwaway hostnames, so enterprise and campus filters (Cisco Umbrella,
   FortiGuard, Palo Alto, Zscaler) categorise the whole `nip.io` zone as
   *Dynamic DNS* or *Proxy Avoidance* and block it wholesale. **The block is on
   the name, not on our server.** No certificate, header or server change can
   lift it — the request never arrives.

   When such a filter blocks an HTTPS site it usually has to intercept the
   connection to show a block page, and it does that with its own certificate.
   A device that does not trust the college's root CA then shows a certificate
   warning. That is very likely what "not secure" actually is here: **the
   college's interception, not our cert.**

2. **The bare IP.** `https://203.0.113.10` presents a certificate issued for
   the hostname, so the name does not match and the browser refuses. Fixed for
   the `http://` case (Caddy now redirects it to the hostname); unfixable for
   the `https://` case, because the handshake fails before any HTTP is spoken.
   **Hand people the hostname, never the IP.**

## Does buying a domain fix it?

**For the category block: yes, almost certainly.** A registered domain with its
own A record is not dynamic DNS, and it leaves the blocked category entirely.

Two caveats worth planning around:

- **Newly-registered-domain heuristics.** Several filters treat any domain
  registered in the last 14–30 days as suspicious in its own right. Register
  **well before 15 October**, not the week of the event. The domain ageing is
  the slow part; everything else here takes fifteen minutes.
- **The TLD matters.** Cheap TLDs (`.xyz`, `.top`, `.tk`, `.click`) carry poor
  reputation scores and are themselves frequently blocked on campus networks.
  Use a mainstream one: **`.in`, `.com`, `.org`**. Cost difference is a few
  hundred rupees; reputation difference is the whole point of the exercise.

**If it is still blocked afterwards**, the filter is using an allowlist rather
than categories, and no amount of DNS work will help. Ask campus IT to allow
the host. Knowing which of the two it is takes one test on college wifi once
DNS has propagated — do that test the day the domain is live, not on the night.

## The cutover

1. **Register the domain** and point an `A` record at the static IP:

   ```
   fakesangeet.example.in.   A   203.0.113.10
   ```

   No AAAA record — the VM has no IPv6. Leave the TTL low (300s) until it is
   proven, then raise it.

2. **Wait for it to resolve.** From the VM, not the laptop — the office network
   hijacks NXDOMAIN and will lie to you:

   ```bash
   gcloud compute ssh unigate-vm --zone=asia-south1-a --tunnel-through-iap \
     --command='dig +short fakesangeet.example.in'
   ```

3. **Add the redirect URI in the Google console**, *before* switching the app
   over. Google matches it exactly and sign-in breaks the moment `APP_BASE_URL`
   stops matching what is registered:

   ```
   https://fakesangeet.example.in/auth/callback
   ```

   Keep the old nip.io URI registered during the changeover so both work.

4. **Set both variables on the VM** — they must agree, and `SITE_HOST` is read
   by Caddy *and* by `vite preview` (which answers an unrecognised `Host` with
   a bare 403 and no explanation):

   ```bash
   # in ~/unigate/.env
   APP_BASE_URL=https://fakesangeet.example.in
   SITE_HOST=fakesangeet.example.in
   ```

5. **Restart**, with both compose files:

   ```bash
   cd ~/unigate && export SITE_HOST=fakesangeet.example.in
   sudo -E docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --force-recreate caddy web
   ```

   Caddy provisions a new certificate on the first request. It needs port 80
   reachable for the ACME challenge — it already is.

6. **Verify from the VM:**

   ```bash
   curl -sI https://fakesangeet.example.in/ | head -1
   echo | openssl s_client -connect fakesangeet.example.in:443 \
     -servername fakesangeet.example.in 2>/dev/null | openssl x509 -noout -issuer -dates
   ```

7. **Then tighten HSTS.** Once the real domain is serving and proven, add
   `includeSubDomains` — held back today only because `nip.io` is somebody
   else's zone. Consider `preload` only when you are certain the domain will
   stay on HTTPS for good; it is difficult to undo.

## What does not need to change

The event, the tickets, the passes and every QR already issued. The pass token
is signed over the event and pass ids, not over a hostname, so nothing that has
already been minted is invalidated by the move.
