const HTML_FILE_ID = '1Ds2eRd0tLGgCUJ7P96trH_YdTRgPskOX';

function doGet() {
  let file;
  try {
    file = DriveApp.getFileById(HTML_FILE_ID);
  } catch (e) {
    return HtmlService.createHtmlOutput(
      '<p style="font-family:sans-serif;padding:40px;">' +
      "You don't have access to this page. Ask the owner to share it with your Google account." +
      '</p>'
    ).setTitle('Access denied');
  }

  const html = file.getBlob().getDataAsString();
  const cleanTitle = file.getName()
    .replace(/\.html?$/i, '')
    .replace(/_/g, ' ');

  return HtmlService.createHtmlOutput(html).setTitle(cleanTitle);
}

/**
 * Server-side endpoints for the Social Calendar preview page.
 *
 * Add these to the SAME Apps Script project as the existing doGet() that serves the
 * preview HTML. The preview page (built by content_hub/social/preview.py) calls them
 * from the browser via google.script.run:
 *
 *   - setPostStatus(sheetId, rowId, status)  writes one row's Status cell AND fills it
 *                                            with the status colour (matching the preview
 *                                            palette: Draft=yellow, Approved=green,
 *                                            Awaiting Asset=gray, Wiah Review=purple,
 *                                            other=red, blank=cleared).
 *   - getPostStatuses(sheetId)               returns {rowId: status} for every row, so
 *                                            the page can show the sheet's CURRENT
 *                                            statuses on load (the served HTML is a
 *                                            static snapshot and would otherwise be stale).
 *   - getViewerInfo(sheetId)                 returns {email, canOpenSheet, ...} for the
 *                                            "Signed in as …" badge, so a teammate hitting
 *                                            access errors can see which account they're on.
 *
 * sheetId is baked into the page at build time and is the living Google Sheet's Drive id
 * (a Google Sheet's file id IS its spreadsheetId). It is only present for the live view —
 * snapshots render read-only.
 *
 * IMPORTANT: after adding or changing these functions you must push a NEW VERSION of the
 * web-app deployment (Deploy > Manage deployments > edit > Version: New version),
 * otherwise the browser keeps calling the old code.
 *
 * PERMALINK: the web-app URL ending in /exec is already a permanent link — bookmark and
 * share it. It stays the same as long as you keep updating the SAME deployment (Manage
 * deployments > edit ✏️ > Version: New version). "New deployment" mints a DIFFERENT /exec
 * URL, so don't use it to publish updates. And because doGet() reads the HTML live from
 * Drive (HTML_FILE_ID), the page refreshes on every `social preview` rebuild with NO
 * redeploy — you only need a New version when THIS .gs server code changes. (The
 * editor-only /dev URL always runs the newest code but can't be shared with teammates.)
 *
 * TROUBLESHOOTING — "Specified permissions are not sufficient to call
 * SpreadsheetApp.openById. Required permissions: .../auth/spreadsheets":
 * this is an OAuth SCOPE problem, not sheet sharing. openById needs the full
 * https://www.googleapis.com/auth/spreadsheets scope, and the identity RUNNING the
 * script hasn't granted it. Two ways to fix, depending on the deployment's "Execute as":
 *   (A) Recommended — set "Execute as: Me (owner)" and "Who has access: Anyone in the
 *       domain". The script then runs with the owner's authorization, so teammates need
 *       neither the Sheets scope nor edit access to the sheet — just the web-app URL.
 *       Redeploy a New version; approve the scope once as the owner if prompted.
 *   (B) Keep "Execute as: User accessing the web app": each user must authorize the
 *       Sheets scope AND have edit access to the sheet. Push a New version, then the
 *       affected user reopens the web-app URL and completes the Google consent prompt
 *       ("See, edit, create and delete all your Google Sheets"). If the consent screen
 *       doesn't request that scope, force it by ADDING (not replacing) it to the
 *       project's appsscript.json manifest oauthScopes:
 *           "https://www.googleapis.com/auth/spreadsheets"
 */

/**
 * Find the calendar tab by its CONTENT, not its name: the header row (row 1) must contain
 * both a "Row ID" and a "Status" column. Returns {sheet, idCol, stCol} (0-based columns)
 * or null. This avoids reading/writing the wrong (or an empty) tab.
 */
function findCalendarTab_(ss) {
  var sheets = ss.getSheets();
  for (var i = 0; i < sheets.length; i++) {
    var lastCol = sheets[i].getLastColumn();
    if (lastCol < 1) continue;
    var hdr = sheets[i].getRange(1, 1, 1, lastCol).getValues()[0].map(function (h) {
      return String(h == null ? '' : h).trim().toLowerCase();
    });
    var idCol = hdr.indexOf('row id');
    var stCol = hdr.indexOf('status');
    if (idCol >= 0 && stCol >= 0) return { sheet: sheets[i], idCol: idCol, stCol: stCol };
  }
  return null;
}

/**
 * Who is viewing the preview, and can they reach the calendar sheet. Powers the page's
 * "Signed in as …" badge — so when a teammate hits access errors they can confirm WHICH
 * Google account the browser is using, and whether that account can open the sheet (the
 * usual cause: the browser is signed into the wrong account, or the sheet isn't shared
 * with them). ``email`` is the ACTIVE (viewing) user; it can be blank for accounts
 * outside the owner's Workspace domain — that blank is itself a useful signal.
 */
function getViewerInfo(sheetId) {
  var info = { email: '', effective: '' };
  try { info.email = Session.getActiveUser().getEmail() || ''; } catch (e) {}
  try { info.effective = Session.getEffectiveUser().getEmail() || ''; } catch (e) {}
  if (sheetId) {
    try {
      info.sheetName = SpreadsheetApp.openById(sheetId).getName();
      info.canOpenSheet = true;
    } catch (e) {
      info.canOpenSheet = false;
      info.sheetError = String(e && e.message ? e.message : e);
    }
  }
  return info;
}

function setPostStatus(sheetId, rowId, status) {
  if (!sheetId) throw new Error('Missing spreadsheet id (this preview is read-only).');
  var ss = SpreadsheetApp.openById(sheetId);
  var tab = findCalendarTab_(ss);
  if (!tab) {
    throw new Error('No tab with both "Row ID" and "Status" header columns was found in "'
      + ss.getName() + '".');
  }
  var sheet = tab.sheet, idCol = tab.idCol, stCol = tab.stCol;

  var want = String(rowId).trim();
  var ids = sheet.getRange(1, idCol + 1, sheet.getLastRow(), 1).getValues();
  for (var r = 1; r < ids.length; r++) {  // r = 0 is the header row
    if (String(ids[r][0]).trim() === want) {
      var cell = sheet.getRange(r + 1, stCol + 1);  // +1: getRange is 1-based
      var oldValue = cell.getValue();
      cell.setValue(status);
      // No colour is set here: the Status column is conditionally formatted (see
      // applyStatusFormatting_), so the cell recolours itself from the new value.
      SpreadsheetApp.flush();
      return {
        ok: true,
        spreadsheetName: ss.getName(),
        spreadsheetUrl: ss.getUrl(),
        sheetName: sheet.getName(),
        row: r + 1,
        rowId: want,
        oldValue: oldValue,
        newValue: cell.getValue()  // read back AFTER flush to prove the write landed
      };
    }
  }
  throw new Error('Row ID "' + want + '" not found on tab "' + sheet.getName()
    + '" of "' + ss.getName() + '".');
}

/**
 * Status text -> conditional-format fill colour, matching the preview palette (see the
 * per-status tokens in content_hub/social/preview.py). Blank has no rule (no fill).
 */
var STATUS_FILLS = {
  'Draft': '#FDECB0',           // yellow
  'Approved': '#BCE8C8',        // green
  'Awaiting Asset': '#DDE0E4',  // gray
  'Wiah Review': '#E0D2F7'      // purple
};
var STATUS_OTHER_FILL = '#F7C7C2';  // any other non-blank status -> red

function columnLetter_(col) {  // 1-based column index -> A1 letter(s)
  var s = '';
  while (col > 0) { var m = (col - 1) % 26; s = String.fromCharCode(65 + m) + s; col = (col - m - 1) / 26; }
  return s;
}

/**
 * Install (or refresh) conditional-formatting rules that colour the Status column by
 * value — so a status set ANY way (this preview, or a direct edit in the sheet) is
 * coloured automatically, and nothing goes stale. Idempotent: prior rules targeting the
 * Status column are dropped before the current set is re-added. Run ONCE per sheet from
 * the Apps Script editor (pass the living sheet's id); re-run after changing the palette.
 */
function applyStatusFormatting_(sheetId) {
  if (!sheetId) throw new Error('Missing spreadsheet id.');
  var ss = SpreadsheetApp.openById(sheetId);
  var tab = findCalendarTab_(ss);
  if (!tab) throw new Error('No tab with both "Row ID" and "Status" headers was found.');
  var sheet = tab.sheet, stCol = tab.stCol + 1;  // 1-based
  var col = columnLetter_(stCol);
  var range = sheet.getRange(2, stCol, Math.max(sheet.getMaxRows() - 1, 1), 1);

  // Keep every rule that isn't one of ours (i.e. doesn't solely target the Status column).
  var keep = sheet.getConditionalFormatRules().filter(function (rule) {
    return !rule.getRanges().some(function (rg) {
      return rg.getColumn() === stCol && rg.getNumColumns() === 1;
    });
  });

  Object.keys(STATUS_FILLS).forEach(function (status) {
    keep.push(SpreadsheetApp.newConditionalFormatRule()
      .whenTextEqualTo(status).setBackground(STATUS_FILLS[status])
      .setRanges([range]).build());
  });
  var known = Object.keys(STATUS_FILLS).map(function (s) {
    return '$' + col + '2<>"' + s + '"';
  }).join(',');
  keep.push(SpreadsheetApp.newConditionalFormatRule()
    .whenFormulaSatisfied('=AND($' + col + '2<>"",' + known + ')')
    .setBackground(STATUS_OTHER_FILL).setRanges([range]).build());

  sheet.setConditionalFormatRules(keep);
  SpreadsheetApp.flush();
  return { ok: true, sheet: sheet.getName(), statusColumn: col,
           rules: Object.keys(STATUS_FILLS).length + 1 };
}

function getPostStatuses(sheetId) {
  if (!sheetId) return {};
  var ss = SpreadsheetApp.openById(sheetId);
  var tab = findCalendarTab_(ss);
  if (!tab) return {};
  var sheet = tab.sheet, idCol = tab.idCol, stCol = tab.stCol;

  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return {};
  var width = Math.max(idCol, stCol) + 1;
  var rows = sheet.getRange(2, 1, lastRow - 1, width).getValues();
  var out = {};
  for (var r = 0; r < rows.length; r++) {
    var id = String(rows[r][idCol] == null ? '' : rows[r][idCol]).trim();
    if (!id) continue;
    out[id] = String(rows[r][stCol] == null ? '' : rows[r][stCol]).trim();
  }
  return out;
}
