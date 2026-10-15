# Dependencies before source in deploy/Dockerfile (2026-10-16)

Built on the production host under test tags (`qc-test-<target>:2ec9a1afb64d`), from main 3c87dc4 plus this change.
No service, release directory or setting was touched.

- **First build** of all six targets (new dependency layers): about 10 minutes. app 115 s, autonomous-research 33 s,
  maintenance 13 s, codex 92 s, claude 39 s, video 300 s.
- **Code-only release** (one source line changed, different commit): all six in 57 s, 332 MiB of new layers.
  - Each image gets only the project install (11.4 MB), the entry point and metadata.
  - Before this change a release took about 10 minutes, about 4.7 GB of layers and fresh PyPI, Debian and Playwright
    downloads.
- **Compared with the production images** of 3c87dc4 (`compare-with-production.json`). The following are identical:
  - Python distributions and versions; the quant_company file hashes; dpkg packages.
  - uv, node, codex, claude, ffmpeg and the entry point, and the tool versions; Playwright browsers.
  - /state ownership, /app, /home/company, /opt/quant-data.
  - Environment (without the commit), entry point, command, user, working directory, ports.

  Only two things differ:
  - the compose labels, which `docker compose build` adds in a real release;
  - `/root/.cache/uv`, now a build cache mount instead of about 0.8 GB inside every image.
