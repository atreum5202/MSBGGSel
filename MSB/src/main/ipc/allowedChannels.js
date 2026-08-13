import { IPC, IPC_EXTENSIONS } from '../core/constants.js';

function flattenChannelNames(node) {
  return Object.values(node).flatMap((value) =>
    typeof value === 'string' ? [value] : flattenChannelNames(value)
  );
}

export const ALLOWED_CHANNELS = [
  ...flattenChannelNames(IPC),
  ...flattenChannelNames(IPC_EXTENSIONS),
];

export const ALLOWED_EVENT_CHANNELS = [];
