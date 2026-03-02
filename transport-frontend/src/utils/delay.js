// Small utility helpers for formatting and classifying delays.
export function formatDelay(seconds) {
  if (seconds === null || seconds === undefined) return null;
  const s = Number(seconds) || 0;
  if (s === 0) return "On time";
  if (Math.abs(s) >= 60) return `+${Math.round(s / 60)} min`;
  return `+${s} sec`;
}

export function isDelayed(seconds) {
  if (seconds === null || seconds === undefined) return false;
  return Number(seconds) > 0;
}

export function hasDelay(seconds) {
  return seconds !== null && seconds !== undefined && Number(seconds) !== 0;
}

export default { formatDelay, isDelayed, hasDelay };
