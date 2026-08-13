export function trySend(socket, obj) {

  try { socket.send(JSON.stringify(obj)); } catch {}

}
