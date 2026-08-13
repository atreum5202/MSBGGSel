const RING_DEFAULT = 500;

export function createConsoleLogBuffer(maxSize = RING_DEFAULT) {
  const entries = [];

  return {
    push(entry) {
      entries.push(entry);
      if (entries.length > maxSize) entries.shift();
    },
    list(limit) {
      if (!limit || limit >= entries.length) return entries.slice();
      return entries.slice(entries.length - limit);
    },
    get raw() {
      return entries;
    },
  };
}

export const DEFAULTS = { RING_SIZE: RING_DEFAULT };
