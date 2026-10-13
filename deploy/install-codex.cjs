// Install only the reviewed distribution and verify the actual Linux executable.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');

const release = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
if (release.schema_version !== 1 || release.package !== '@openai/codex' ||
    !/^\d+\.\d+\.\d+$/.test(release.version) || process.platform !== 'linux' || process.arch !== 'x64') {
  throw new Error('This qualified CLI distribution requires Linux x86_64 and a stable version');
}
const options = {encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit']};
const npm = (...args) => execFileSync('npm', ['--registry=https://registry.npmjs.org', ...args], options).trim();
for (const [version, integrity] of [[release.version, release.npm_integrity],
    [release.linux_x64.package_version, release.linux_x64.npm_integrity]]) {
  if (JSON.parse(npm('view', `${release.package}@${version}`, 'dist.integrity', '--json')) !== integrity) {
    throw new Error('Published CLI distribution differs from the reviewed integrity');
  }
}
npm('install', '--global', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund',
    `${release.package}@${release.version}`);
const prefix = npm('prefix', '--global');
const binary = path.join(prefix, 'lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64',
                         'vendor/x86_64-unknown-linux-musl/bin/codex');
const digest = crypto.createHash('sha256').update(fs.readFileSync(binary)).digest('hex');
if (digest !== release.linux_x64.binary_sha256) throw new Error('CLI executable digest is not qualified');
const observed = execFileSync(path.join(prefix, 'bin/codex'), ['--version'], options).trim();
if (observed !== `codex-cli ${release.version}`) throw new Error('Installed CLI version differs from the contract');
process.stdout.write(JSON.stringify({cli_version: release.version, binary_sha256: digest}) + '\n');
