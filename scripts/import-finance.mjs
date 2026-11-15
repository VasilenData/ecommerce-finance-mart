#!/usr/bin/env node
// A complete snapshot is staged in a sibling directory, then renamed into a NEW destination.
import {createHash} from 'node:crypto';
import {open, readFile, mkdtemp, mkdir, writeFile, rename, rm, lstat, unlink} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {LIMITS, CONTRACT, validateImport, buildReport, artifacts} from '../docs/import/core.mjs';

const help = `Usage (Node 22+):
  node scripts/import-finance.mjs --invoices FILE --payments FILE --as-of YYYY-MM-DD --validate-only
  node scripts/import-finance.mjs --invoices FILE --payments FILE --as-of YYYY-MM-DD --output NEW_DIRECTORY

GBP CSV contract: docs/LOCAL_IMPORT.md. Files remain local. Never put private outputs in docs/.
Validation errors are emitted as JSON with file role, CSV record number and field; raw cells and paths are not logged.
Output must be a new directory. No append, overwrite or automatic merchant-format mapping.
`;
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
async function readBounded(filename) {
  const handle = await open(filename, 'r');
  try {
    if (!(await handle.stat()).isFile()) throw new Error('Expected a regular CSV file.');
    const buffer = Buffer.alloc(LIMITS.bytes + 1); let offset = 0;
    while (offset < buffer.length) { const {bytesRead} = await handle.read(buffer, offset, buffer.length - offset, null); if (!bytesRead) break; offset += bytesRead; }
    return new Uint8Array(buffer.subarray(0, offset));
  } finally { await handle.close(); }
}
export async function main(argv) {
  if (argv.length === 1 && argv[0] === '--help') { console.log(help); return 0; }
  const args = {}, flags = new Set(['--invoices', '--payments', '--as-of', '--output', '--validate-only']);
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (!flags.has(flag) || flag in args) throw new Error('Unknown or repeated argument. Use --help.');
    if (flag === '--validate-only') args[flag] = true;
    else { if (!argv[i + 1] || argv[i + 1].startsWith('--')) throw new Error('Missing argument value. Use --help.'); args[flag] = argv[++i]; }
  }
  if (!args['--invoices'] || !args['--payments'] || !args['--as-of'] || Boolean(args['--output']) === Boolean(args['--validate-only']))
    throw new Error('Supply both CSVs, --as-of, and exactly one of --validate-only or --output. Use --help.');
  const input = {invoices: await readBounded(args['--invoices']), payments: await readBounded(args['--payments']), asOf: args['--as-of']};
  const validation = validateImport(input);
  if (!validation.ok) { console.error(JSON.stringify({ok: false, errors: validation.errors}, null, 2)); return 2; }
  if (args['--validate-only']) { console.log(JSON.stringify({ok: true, contract: CONTRACT, validated_only: true, as_of: input.asOf, counts: validation.stats}, null, 2)); return 0; }
  const output = path.resolve(args['--output']);
  const core = await readFile(new URL('../docs/import/core.mjs', import.meta.url));
  const {report} = await buildReport(input, sha256, sha256(core));
  const files = artifacts(report);
  const manifest = {contract: CONTRACT, snapshot_sha256: report.provenance.snapshot_sha256,
    engine_sha256: sha256(core), cli_sha256: sha256(await readFile(fileURLToPath(import.meta.url))),
    artifacts: Object.fromEntries(Object.entries(files).map(([name, value]) => [name, {bytes: Buffer.byteLength(value), sha256: sha256(value)}]))};
  files['manifest.json'] = JSON.stringify(manifest, null, 2) + '\n';
  const parent = path.dirname(output); await mkdir(parent, {recursive: true});
  const lockPath = `${output}.lock`, lock = await open(lockPath, 'wx', 0o600);
  let stage;
  try {
    try { await lstat(output); throw new Error('Output already exists. Use a new directory; prior results are never overwritten.'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    stage = await mkdtemp(path.join(parent, '.finance-stage-'));
    for (const [name, contents] of Object.entries(files)) await writeFile(path.join(stage, name), contents, {flag: 'wx', mode: 0o600});
    for (const [name, contents] of Object.entries(files)) {
      if (sha256(await readFile(path.join(stage, name))) !== sha256(contents)) throw new Error('Staged output checksum mismatch.');
    }
    await rename(stage, output);
  } finally {
    if (stage) await rm(stage, {recursive: true, force: true});
    await lock.close(); await unlink(lockPath);
  }
  console.log(JSON.stringify({ok: true, snapshot_sha256: manifest.snapshot_sha256, artifacts: Object.keys(files)}, null, 2)); return 0;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then(code => { process.exitCode = code; }).catch(error => {
    // OS error messages can contain private paths. Report only an actionable category.
    const message = error.code ? `Local filesystem operation failed (${error.code}). Verify readable inputs, a writable parent, and a new output directory.` : error.message;
    console.error(JSON.stringify({ok: false, error: message})); process.exitCode = 1;
  });
}
