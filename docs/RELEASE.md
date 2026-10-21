# Releases

## Checking a release you downloaded

Every release has `SHA256SUMS`, and every file in it has a signed
build-provenance attestation.

```
sha256sum -c SHA256SUMS                       # the files are what was published
gh attestation verify Stump-1.0.0-beta.21.zip --repo ruderigo/SandBox_Stump_On
```

The second proves the file was built by this repository's release
workflow from the tagged commit (Sigstore, no key to manage: GitHub's
identity signs it). On macOS `sha256sum` is `shasum -a 256`.

When the release is also signed with the project's **Reticulum
identity**, there's a `SHA256SUMS.rnsig`:

```
pip install rns          # or rnspure
python3 scripts/sign_release.py verify SHA256SUMS SHA256SUMS.rnsig RELEASE_SIGNING_KEY.txt
```

That needs nothing but Reticulum, so it works offline, on a laptop at
the node or a handheld. From 1.0.0-beta.2 on, the release zip itself
carries `scripts/sign_release.py` and `RELEASE_SIGNING_KEY.txt` — but a
key that came inside the download proves nothing about that download:
compare it once with the one published in the repository. The release
identity is `d88b72f6e0deb5991f5a5aed5f3f3cdd`. `MANIFEST.json` gives the size and SHA-256 of
every file *inside* every zip, e.g. to check one file on a node.

**Reproducible:** `python3 scripts/build_release.py <version> dist/` on
the tagged commit gives byte-identical files: same `SHA256SUMS`.

## Making a release

1. Set `STUMP_RELEASE` in `final_firmware/example_node.py` (e.g.
   `1.0.0-beta.21`). Changing any firmware file means rebuilding the
   Provisioner's manifest (`tests/README.md`).
2. Add a `## 1.0.0-beta.21` section at the top of `CHANGELOG.md`: it
   becomes the release notes.
3. `python3 tests/run.py` passes.
4. Commit, then tag and push:

   ```
   git tag -a v1.0.0-beta.21 -m "Stump 1.0.0-beta.21"     # or -s to sign the tag with your GPG/SSH key
   git push origin main v1.0.0-beta.21
   ```

The **Release** workflow refuses a tag that doesn't match
`STUMP_RELEASE` or has no changelog section, runs the whole test suite,
builds the files, signs, attests and publishes. A version with a `-`
(`-beta.2`) is published as a pre-release.

## The release identity (optional, once)

```
pip install rns
python3 scripts/sign_release.py keygen release.identity
```

- Keep `release.identity` somewhere safe and offline: anyone with it
  can sign as the project. Losing it means a new key that users must
  learn to trust again.
- Put the base64 it prints in the repository secret
  **`STUMP_RELEASE_IDENTITY`** (Settings › Secrets and variables ›
  Actions).
- Commit the public key it prints as **`RELEASE_SIGNING_KEY.txt`** (one
  line, 128 hex digits), so users and the workflow can check signatures.

Without the secret, releases are still hashed and attested; the
workflow just skips `SHA256SUMS.rnsig` and says so.
