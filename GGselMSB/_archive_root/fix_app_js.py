import sys
import re

js_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/static/app.js"
with open(js_path, "r", encoding="utf-8") as f:
    content = f.read()

# Update loadChats to use WebSocket
old_loadchats = """async function loadChats() {
  const d = await api('/api/chats');"""

new_loadchats = """let chatWs = null;

async function loadChats() {
  const d = await api('/api/chats');
  
  // Try to connect to WebSocket for realtime updates
  if (!chatWs) {
    try {
      chatWs = new WebSocket(`ws://${location.host}/ws/chats`);
      chatWs.onopen = () => {
        // Send a dummy token or real one if available
        chatWs.send("hello");
      };
      chatWs.onmessage = (e) => {
        console.log("WebSocket message:", e.data);
        // Could parse JSON and append to messages if it's a new_message event
        // For now, just reload chats or ignore
      };
      chatWs.onerror = () => {
        console.log("WebSocket error, falling back to polling");
      };
    } catch(err) {
      console.log("WebSocket failed", err);
    }
  }"""

if old_loadchats in content:
    content = content.replace(old_loadchats, new_loadchats)

with open(js_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated app.js")
