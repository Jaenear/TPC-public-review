# Public static preview

The public review site is an anonymous static export of the frozen schema-33
application. It runs on the hosting service without a Mac, tunnel or Python
process. The full Python/SQLite application remains in this source branch for
the next server-hosted release; the public artifact does not contain it.

## Rebuild

```sh
python3 -m unittest deployment.test_static_preview deployment.test_wsgi -v
node --test deployment/test_static_preview.mjs
python3 deployment/build_static_preview.py --output _site --base /TPC-public-review/
```

For a host at the domain root use `--base /`. The exporter requires an empty
output directory, creates a fresh temporary database, reads only an explicit
anonymous GET allowlist and copies only public assets, browser modules and the
owned sample MP4. It never reads an existing member database. Source files and
stable course/lesson identifiers are preserved. A generated authentication
guard prevents credential entry; all API mutations return 503. Private reads
retain 401. Missing APIs never fall back to another person's data.

The sample MP4 SHA-256 is
`1d2cf29f94ecab4cdf0f1af93f28515b6da7f5c2e2a4f82f397d46ef9535dc39`.
The small sample is loaded from static public media, SHA-256 checked, and played
from a browser Blob URL so seek/reload works on hosts without Range support.
This bounded sample path rejects files above 4 MiB and is not a full-course
streaming strategy. Current
position and maximum reached percentages can be viewed while playing. They do
not represent verified continuous viewing or cross-visit member persistence.

## Available scope

- Course library, category/search filters, course detail and free public lessons.
- Owned preflop sample video, seek/reload controls and current position display.
- Browser-local range study and public concept practice.
- Public coach profiles, empty public community boards, policy readiness screen.
- Explicit information for login, account recovery and server-only actions.

Member registration/login, durable progress, Q&A and community writing, private
content, merchant payments, operator workflows and mail need a server release.
They are not completed by this static publication. Real-user records, private
project controls, environment files, logs, database files and server source
are excluded from the hosting artifact.

## GitHub Pages option

The generated subpath artifact is compatible with GitHub Pages. An administrator
must enable Pages in this repository before a Pages deployment can be activated.
The current connected browser is signed out, so no Pages setting was changed.
The independent hosted preview uses the same export at `/` and requires no new
hosting account. Do not upload the repository root to a static host.

Hosting scope and release gates are separate from production readiness. The
frozen application's full verification and the focused export/WSGI checks are
tracked independently in the local project report.
