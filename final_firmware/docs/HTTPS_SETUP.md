# HTTPS on the Stump's Wi-Fi (Firebase + Let's Encrypt)

**Why:** browsers only let a page use the microphone if the page is
secure (HTTPS). With HTTPS, anyone on the Stump's Wi-Fi can record voice
notes live with the ♪ button. Without it, ♪ falls back to the phone's own
recorder app.

**How it works:** you own a domain, say `labuche.org`. Your public page
lives on Firebase Hosting as usual. One name, `stump.labuche.org`, gets a
real Let's Encrypt certificate that you install on each Stump. On the
Stump's own Wi-Fi, every name points to the Stump, so
`https://stump.labuche.org` opens the Stump with a normal padlock — no
warning, even with no internet, because phones already trust Let's
Encrypt. On the regular internet, the same name shows your Firebase
page.

The examples use `labuche.org` and `stump.labuche.org`. Replace them with
yours everywhere.

> **Why not `labuche-stump.web.app`?** That name belongs to Google
> (`web.app` is Firebase's domain). You can't add DNS records to it, and
> the certificate's private key ends up on every Stump: anyone who took
> one apart could impersonate that public site anywhere. A name of your
> own, used only by Stumps, keeps that risk contained. Your Wi-Fi can
> still be *called* `LaBuche-Stump.web.app`.

---

## 0. Check the board first (5 minutes)

Put `tls_probe.py` in the same folder as `provisioner.py`, plug the CAM
in, and run from that folder:

```
mpremote connect /dev/cu.usbmodem… run tls_probe.py
```

Then, from your Mac joined to the Stump's Wi-Fi:

```
curl -sk -o /dev/null -w "handshake %{time_appconnect}s\n" https://192.168.4.1:8443/
```

Then test a burst and a crowd, because on HTTPS **every page load and
every 2-second chat poll is a new secure connection** (the Stump closes
each connection after one request):

```
for i in $(seq 10); do curl -sk -o /dev/null -w "%{time_appconnect}s\n" https://192.168.4.1:8443/; done
seq 6 | xargs -P6 -I{} curl -sk -o /dev/null -w "parallel %{time_appconnect}s\n" https://192.168.4.1:8443/
```

The probe prints, for each connection, how long it took and the free
memory after it. As a rule of thumb: handshakes under ~0.3 s and free
memory staying steady mean a handful of people chatting over HTTPS is
fine; handshakes over ~1 s, or free memory dropping with each one, mean
the CAM will struggle with more than one or two at once — tell me the
numbers before going further.

The probe ends with `RESULT: HTTPS works on this board`. If it says the
firmware can't serve TLS, stop here: the board's MicroPython needs
updating first. Reboot the CAM afterwards (the probe pauses it).

## 1. Get a domain

Buy one from any registrar that lets you edit DNS records (Squarespace
Domains, Cloudflare, Namecheap, Porkbun…).

**Pick a `.org`, `.net`, `.com`, `.ca` or similar — not `.app`, `.dev` or
`.page`.** Those TLDs are on every browser's HSTS preload list: if a
certificate ever lapsed, anyone who typed the name would get an error
page with no way past it. With an ordinary TLD, the Stump's fallback to
plain HTTP (below) always works.

If your registrar shows **CAA** records for the domain, make sure these
two exist (or that there are no CAA records at all):

```
labuche.org.  CAA  0 issue "letsencrypt.org"
labuche.org.  CAA  0 issue "pki.goog"
```

(`letsencrypt.org` for the Stump certificate, `pki.goog` because Firebase
uses Google's certificates too.)

## 2. Connect both names to Firebase Hosting

In the [Firebase console](https://console.firebase.google.com/) → your
project → **Hosting** → **Add custom domain**:

1. Add `labuche.org` (your public page). Follow Firebase's instructions:
   it gives you a TXT record (to prove ownership) and A records to add
   at your registrar.
2. Add `stump.labuche.org` the same way. Point it at the same site — it's
   what people will see if they open that name on the regular internet,
   and it's how Let's Encrypt will check that you own the name.
3. Wait until both show **Connected** (minutes to a few hours, for DNS).

## 3. Let Firebase serve the verification files

Let's Encrypt checks ownership by fetching a file under
`/.well-known/acme-challenge/` on your site. **Firebase skips files and
folders whose names start with a dot by default**, so that folder would
never be deployed.

In your Firebase project folder (the one with `firebase.json`), open
`firebase.json` and remove `"**/.*"` from `"ignore"`:

```json
{
  "hosting": {
    "public": "public",
    "ignore": ["firebase.json", "**/node_modules/**"]
  }
}
```

Keep any other settings you already have. Firebase serves, in order:
its own reserved paths, your **redirects**, then real files, then your
rewrites. So a single-page-app rewrite won't get in the way, but a
**redirect** whose pattern matches `/.well-known/…` would — if you have a
catch-all redirect, make sure it doesn't cover that path.

## 4. Get the certificate

On your Mac, with the [Firebase CLI](https://firebase.google.com/docs/cli)
already logged in (`firebase login`):

```
brew install certbot
mkdir -p ~/stump-https && cd ~/stump-https
```

Create two small scripts that certbot will run: one publishes the
verification file to Firebase, the other removes it. **Set `SITE` to the
full path of your Firebase project folder.**

`~/stump-https/firebase_auth_hook.sh`:

```sh
#!/bin/sh
SITE="$HOME/path/to/your/firebase/project"
mkdir -p "$SITE/public/.well-known/acme-challenge"
printf "%s" "$CERTBOT_VALIDATION" > "$SITE/public/.well-known/acme-challenge/$CERTBOT_TOKEN"
cd "$SITE" && firebase deploy --only hosting
sleep 10
```

`~/stump-https/firebase_cleanup_hook.sh`:

```sh
#!/bin/sh
SITE="$HOME/path/to/your/firebase/project"
rm -f "$SITE/public/.well-known/acme-challenge/$CERTBOT_TOKEN"
cd "$SITE" && firebase deploy --only hosting
```

Then:

```
chmod +x firebase_auth_hook.sh firebase_cleanup_hook.sh
certbot certonly --manual --preferred-challenges http \
  --manual-auth-hook ~/stump-https/firebase_auth_hook.sh \
  --manual-cleanup-hook ~/stump-https/firebase_cleanup_hook.sh \
  --key-type ecdsa --elliptic-curve secp256r1 --reuse-key \
  --config-dir ~/stump-https/config --work-dir ~/stump-https/work --logs-dir ~/stump-https/logs \
  -d stump.labuche.org
```

`--key-type ecdsa` matters: ECDSA makes each secure connection much
faster on the ESP32 than RSA. `--reuse-key` keeps the same key when you
renew, so only the public certificate changes from one renewal to the
next. When it finishes, your files are in:

```
~/stump-https/config/live/stump.labuche.org/fullchain.pem
~/stump-https/config/live/stump.labuche.org/privkey.pem
```

**Keep `privkey.pem` private.** Don't commit it, email it or put it in
your Firebase `public` folder.

## 5. Install it on each Stump

With the CAM plugged in:

```
python3 provisioner.py --install-cert /dev/cu.usbmodem… \
  ~/stump-https/config/live/stump.labuche.org/fullchain.pem \
  ~/stump-https/config/live/stump.labuche.org/privkey.pem
```

The provisioner checks everything on your Mac first: the key matches the
certificate, it isn't expired, the chain includes the intermediate
(always use `fullchain.pem`, never `cert.pem`), and the key type. Nothing
is copied if something's wrong. Then reboot the CAM. Its boot log should
say:

```
[web] HTTPS on 443 for stump.labuche.org
```

and `/admin` shows **HTTPS: On for stump.labuche.org** with the days left.

Repeat for each Stump (same two files for all of them).

## 6. Try it

Join the Stump's Wi-Fi with a phone and open any page (or let the
captive-portal sign-in page open). The page first checks, quietly,
whether the phone accepts the Stump's certificate; if it does, you land
on `https://stump.labuche.org/` with a padlock. Open a DM in the chat and
tap ♪: the phone asks for microphone permission, then records live; tap
■ to send.

People who reach the Stump through your **router's** network (its LAN
address) stay on plain HTTP — on the LAN, `stump.labuche.org` points to
the internet, not to the Stump — and ♪ uses the recorder-app fallback
there.

## 7. Renew before it expires

Let's Encrypt certificates last **90 days**, and are getting shorter
(Let's Encrypt has announced 45-day certificates by 2028).

**A lapsed certificate never shows visitors a warning.** The Stump
doesn't redirect to HTTPS itself: each page first asks the *phone*
whether it accepts the certificate (checked against the phone's own
clock), and only moves to HTTPS if it does. With an expired certificate
— or a phone whose clock is wrong — visitors simply stay on plain HTTP,
and ♪ uses the recorder-app fallback, until you renew. `/admin` shows
the expiry date, and the days left (bold under 21) when the node's
clock is set.

Every ~60 days:

```
certbot renew --config-dir ~/stump-https/config --work-dir ~/stump-https/work --logs-dir ~/stump-https/logs
```

(certbot remembers the two hooks.) Then run step 5 again on each Stump.

## If a Stump is lost or stolen

Its private key could be read off the board. Revoke the certificate and
issue a new one with a new key, then reinstall on the Stumps you still
have:

```
certbot revoke --cert-name stump.labuche.org --config-dir ~/stump-https/config --work-dir ~/stump-https/work --logs-dir ~/stump-https/logs
```

then repeat steps 4 and 5.

## Troubleshooting

| What you see | What it means |
|---|---|
| certbot: "Invalid response from …/.well-known/acme-challenge/…" (404) | Step 3: Firebase didn't deploy the dot-folder, a redirect caught the path, or the `SITE` path in the hook is wrong |
| certbot: "CAA record … prevents issuance" | Step 1: add `0 issue "letsencrypt.org"` |
| `--install-cert`: "key doesn't match" | You mixed files from different runs; use the pair from the same `live/` folder |
| Boot log: `[web] HTTPS off: no certificate installed` | Step 5 didn't complete, or the board was reflashed (reflashing erases it — install again) |
| Boot log: `[web] HTTPS off: this MicroPython can't serve TLS` | Step 0: the firmware needs updating |
| Visitors stay on `http://` | Expected if the certificate expired (check `/admin`) or the phone's clock is wrong — renew, or fix the phone's date |
| Phone shows a certificate warning | Someone typed the `https://` name directly after expiry; renew. (With a `.app`/`.dev` domain this error can't be bypassed — see step 1) |
| ♪ still opens the recorder app | You're on `http://`, or on the router's LAN instead of the Stump's Wi-Fi |
