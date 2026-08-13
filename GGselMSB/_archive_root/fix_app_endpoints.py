import sys
import re

js_path = "C:/Users/Atreum/Desktop/MySoft/GgsellerMoreLogin/static/app.js"
with open(js_path, "r", encoding="utf-8") as f:
    content = f.read()

# Update loadProfile to fetch notifications
old_profile = """    const d = await api('/api/profile');
    const data = d.data || {};
    
    document.getElementById('profile-email').value = data.email || '';
    document.getElementById('profile-nickname').value = data.name || '';
    document.getElementById('profile-wmz').value = data.wmz || '';
    
    if (d.stub) {
      showToast('Раздел недоступен в API-key режиме', 'warning');
    }"""

new_profile = """    const d = await api('/api/profile');
    const data = d.data || {};
    
    document.getElementById('profile-email').value = data.email || '';
    document.getElementById('profile-nickname').value = data.name || '';
    document.getElementById('profile-wmz').value = data.wmz || '';
    
    if (d.stub) {
      showToast('Профиль: недоступен в API-key режиме', 'warning');
    }
    
    // Load notifications for tab 2
    try {
      const notifData = await api('/api/notifications');
      if (notifData.stub) {
        console.warn('Уведомления: stub mode');
      } else {
        // Here we could parse and render notifications if needed
        // For now just load the data to ensure endpoint works
      }
    } catch(e) {
      console.error('Failed to load notifications', e);
    }"""

if old_profile in content:
    content = content.replace(old_profile, new_profile)

# Update switchSettingsTab to fetch whitelisted IPs
old_switch = """function switchSettingsTab(tab) {
  document.querySelectorAll('.stab-content').forEach(el => el.style.display = 'none');
  const target = document.getElementById('stab-' + tab);
  if(target) target.style.display = 'block';"""

new_switch = """async function switchSettingsTab(tab) {
  document.querySelectorAll('.stab-content').forEach(el => el.style.display = 'none');
  const target = document.getElementById('stab-' + tab);
  if(target) target.style.display = 'block';
  
  if (tab == 2) {
    try {
      const ipData = await api('/api/whitelisted_ips');
      if (!ipData.stub && ipData.items) {
        const textarea = document.getElementById('settings-ips');
        if (textarea) {
          // Extract IPs if format matches list of strings or objects
          let ipList = [];
          if (Array.isArray(ipData.items)) {
            ipList = ipData.items.map(i => typeof i === 'string' ? i : (i.ip || i.address || JSON.stringify(i)));
          }
          textarea.value = ipList.join('\\n');
        }
      }
    } catch (e) {
      console.error('Failed to load whitelisted IPs', e);
    }
  }"""

if old_switch in content:
    content = content.replace(old_switch, new_switch)

with open(js_path, "w", encoding="utf-8") as f:
    f.write(content)
print("Updated app.js")
