// Mapping of human-friendly operator display names → SIRI operator codes.
// This mirrors backend BusLive.OPERATOR_NAMES and is used as a defensive
// fallback when live feed records lack `operator_ref` but include a
// display `operator` string.

const NAME_TO_CODE = {
  'Archway Travel': 'ARCT',
  'Blackpool Transport': 'BLAC',
  'Kirkby Lonsdale Coach Hire': 'KLCO',
  'Stagecoach Cumbria & North Lancashire': 'SCCU',
  'Stagecoach Merseyside & South Lancashire': 'SCMY',
  'Transpora North West': 'NUTT',
};

function _normalizeName(name) {
  if (!name || typeof name !== 'string') return '';
  // collapse whitespace and lower-case for robust matching
  return name.trim().replace(/\s+/g, ' ').toLowerCase();
}

const NORMALIZED_TO_CODE = Object.fromEntries(
  Object.entries(NAME_TO_CODE).map(([k, v]) => [_normalizeName(k), v])
);

/**
 * Resolve an operator identifier to a canonical SIRI operator code.
 * - If `operator` already looks like a short code (2-5 uppercase letters),
 *   return it unchanged.
 * - Otherwise try to match known display names (case-insensitive).
 * - Returns null when no resolution is possible.
 */
export function resolveOperatorCode(operator) {
  if (!operator) return null;
  const s = String(operator).trim();
  // Heuristic: codes are short uppercase tokens without spaces (e.g. SCCU)
  if (/^[A-Z]{2,6}$/.test(s)) return s;
  const norm = _normalizeName(s);
  return NORMALIZED_TO_CODE[norm] || null;
}

export default { resolveOperatorCode };
