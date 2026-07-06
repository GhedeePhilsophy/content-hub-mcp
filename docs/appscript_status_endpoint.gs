/**
 * Server-side endpoints for the Social Calendar preview page.
 *
 * Add these to the SAME Apps Script project as the existing doGet() that serves the
 * preview HTML. The preview page (built by content_hub/social/preview.py) calls them
 * from the browser via google.script.run:
 *
 *   - setPostStatus(sheetId, rowId, status)  writes one row's Status cell.
 *   - getPostStatuses(sheetId)               returns {rowId: status} for every row, so
 *                                            the page can show the sheet's CURRENT
 *                                            statuses on load (the served HTML is a
 *                                            static snapshot and would otherwise be stale).
 *
 * sheetId is baked into the page at build time and is the living Google Sheet's Drive id
 * (a Google Sheet's file id IS its spreadsheetId). It is only present for the live view —
 * snapshots render read-only.
 *
 * IMPORTANT: after adding or changing these functions you must push a NEW VERSION of the
 * web-app deployment (Deploy > Manage deployments > edit > Version: New version),
 * otherwise the browser keeps calling the old code.
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
