# certs/: the demo's HTTPS certificate

demo.env names two files here (`DEMO_TLS_CERT`, `DEMO_TLS_KEY`). The edge (pacod-caddy) mounts
this directory at `/etc/caddy/tls` and serves every site with that one certificate: nothing is
obtained from a CA.

| File | What |
|---|---|
| `demo.pacod.io.crt` | the certificate, PEM, **the full chain** (the server's certificate first, then the intermediates) |
| `demo.pacod.io.key` | its private key, PEM, unencrypted |

The certificate must cover `demo.pacod.io` and `*.demo.pacod.io`, and `*.<DEMO_SITE_DOMAIN>`, the
CMS's websites' own domain (demo.env; e.g. `*.pacod-sites.net`): one certificate with all three
names, since the edge serves just the one. One wildcard level is enough: `app.`, `id.`,
`console.`, `gw.`, `studio.`, `kb.`, `grafana.`, `mail.`, `prometheus.` under the first, and the
websites `cms.` and `minimal.` (and any made in the studio) under the second. With
`DEMO_SITE_DOMAIN_SHARED=true` and the websites under `DEMO_DOMAIN` itself, `*.demo.pacod.io`
covers them too, and two names are enough. `make preflight` warns when it does not cover them.

**The files here now are placeholders:** a self-signed certificate for those names, so that the
edge starts. Browsers warn about it, and the products cannot verify each other's sign-in
through it (they reach the issuer by its public address), so sign-in fails until the real
certificate is in place. Replace both files, keeping the names, then:

```sh
docker restart pacod-caddy     # a running stack; `make up` does the same on its next run
```

Neither file is committed (`.gitignore`): the key must never be. On Linux, the edge runs as
root without the capability to bypass file permissions, so the key must be readable by root as
root itself: owned by root (`sudo chown root:root demo.pacod.io.key`, mode 600), or mode 644.

A new placeholder, if the files are missing (with the websites' domain in place of
`pacod-sites.net`):

```sh
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 365 \
  -subj "/CN=demo.pacod.io/O=PLACEHOLDER" \
  -addext "subjectAltName=DNS:demo.pacod.io,DNS:*.demo.pacod.io,DNS:*.pacod-sites.net" \
  -keyout demo.pacod.io.key -out demo.pacod.io.crt
```
