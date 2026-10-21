# Opaque transport with connected Sheets

Run from the repository root. Load tool schemas before using their current arguments. Read only metadata into model context. Both source values and base64 are secret-bearing.

## Prepare a backup

```bash
python3 .agents/skills/immich-secret-backup/scripts/secret_files.py backup \
  --output "$HOME/.local/state/immich-secret-backup/outgoing.json"
```

For one file, add `--file immich-env`, `--file aws-config`, or `--file aws-credentials`. The helper emits identifiers only. Its exact snapshot preserves comments, quoting, whitespace, and newlines.

Inside `functions.exec`, call `tools.exec_command` to read the private JSON into a JavaScript variable. Do not print the returned object or its output. Example below omits the metadata/header checks, which must already have established the fixed spreadsheet, tabs, and A:F schema.

```javascript
const r = await tools.exec_command({
  cmd: "python3 -c 'from pathlib import Path; import sys; sys.stdout.write((Path.home()/\".local/state/immich-secret-backup/outgoing.json\").read_text())'",
  max_output_tokens: 20000
});
if (r.exit_code !== 0) throw Error('Private transport read failed');
const payload = JSON.parse(r.output);
if (payload.schema !== 'immich-secret-files-v1' ||
    payload.spreadsheet_id !== '1dclAy3Y_6RFP1VyFlS3NIM-fOhppjQa7rGSDbWso_ZU') {
  throw Error('Unexpected transport');
}
const cell = value => ({userEnteredValue: typeof value === 'number'
  ? {numberValue: value} : {stringValue: value}});
const requests = payload.files.map(f => ({updateCells: {
  range: {sheetId: f.sheet_id, startRowIndex: f.row - 1,
          endRowIndex: f.row, startColumnIndex: 0, endColumnIndex: 6},
  rows: [{values: [f.id, f.updated_at, f.bytes, f.sha256, f.encoding, f.content].map(cell)}],
  fields: 'userEnteredValue'
}}));
const write = await tools.mcp__codex_apps__google_drive_batch_update_spreadsheet({
  spreadsheet_id: payload.spreadsheet_id, requests,
  include_spreadsheet_in_response: false, response_include_grid_data: false
});
if (write.isError) throw Error('Sheet update failed; details withheld');
// Keep payload only in store() if the next exec must compare opaque readback.
// Never text(payload), text(r), text(write), or raw connector content.
text({written: true, files: payload.files.map(f => f.id)});
```

In normal updates, do not touch headers or formatting. If the live schema is absent or different, stop and resolve it without overwriting unrelated data.

## Readback and restore

Inside `functions.exec`, call `get_spreadsheet_range` with separate sheet/range arguments:

- `.env`: `sheet_name: '.env', range: 'A2:F2'`.
- AWS config: `sheet_name: 'AWS', range: 'A2:F2'`.
- AWS credentials: `sheet_name: 'AWS', range: 'A3:F3'`.
- Both AWS files: `sheet_name: 'AWS', range: 'A2:F3'`.
- `value_render_option: 'UNFORMATTED_VALUE'`.

Use `result.structuredContent.values` opaquely. Reject `isError`, missing/duplicate rows, wrong IDs, unexpected numbers of columns, invalid types, or formulas/changed headers discovered during metadata checks. Do not print the result. Never use `fetch` or export the workbook, since those would expose other secret cells.

For every selected row, construct a file entry from `[id, updated_at, bytes, sha256, encoding, content]`, adding only the fixed tab/sheet ID/row from SKILL.md. Wrap it in:

```javascript
const transport = {
  schema: 'immich-secret-files-v1',
  spreadsheet_id: '1dclAy3Y_6RFP1VyFlS3NIM-fOhppjQa7rGSDbWso_ZU',
  files: entries
};
```

On backup readback, compare all six fields against the outgoing entries before proceeding. Keep values only in the exec isolate or `store()`. Expose only a boolean and IDs.

Transfer `transport` to the local helper without putting it in a shell command, environment variable, patch, or displayed output:

1. Start `python3 .agents/skills/immich-secret-backup/scripts/secret_files.py receive --output /home/ms/.local/state/immich-secret-backup/readback.json` with `tools.exec_command({tty:true,yield_time_ms:1000,...})`.
2. It disables echo and canonical input before printing `Opaque receiver ready`. Only send if that marker is present and a session ID was returned. Store the session ID and opaque transport if the operation spans exec calls.
3. Inside `functions.exec`, call `tools.write_stdin({session_id, chars:JSON.stringify(transport)+'\n', ...})`. Output contains completion flags only. Noncanonical mode permits payloads longer than a TTY's usual line limit.
4. For backup verification, run helper `verify --input .../readback.json`. It checks integrity and equality with local files without writing source files.
5. For a requested restore, run helper `restore --input .../readback.json --file <selected-id>`. Repeat `--file` only for files the user requested. Do not restore just to test a backup.
6. Clean up outgoing/readback JSON via a local Python script using `Path.unlink(missing_ok=True)`. Clear secret-bearing `store()` entries. Never print their contents.

The TTY is only a non-echoing byte transport. The API still processes secret data, and this workflow does not promise that provider-side operational/audit logs contain no request bodies. It keeps values out of model-visible responses and application-level logs. Do not claim a stronger guarantee.

## Helper checks

Run `python3 .agents/skills/immich-secret-backup/scripts/test_secret_files.py` for isolated tests using dummy values. Never test restore against live `.env` or AWS files. An expired/revoked key can be faithfully restored; byte equality does not imply credential validity.
