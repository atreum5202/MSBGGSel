export function registerNetworkRoutes({ app, profileManager, logger }) {
  // Get network settings for a profile
  app.get('/profiles/:id/network', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    const networkSettings = profile.network || {
      webRTC: {
        enabled: true,
        ipHandlingPolicy: 'default_public_interface_only',
        multicastHandling: 'disable',
        nonProxiedUdpEnabled: false,
      },
      dns: {
        leakProtection: true,
        customServers: [],
      },
      headers: {
        custom: [],
        spoofOrder: true,
      },
      tls: {
        fingerprint: 'chrome',
      },
    };
    
    return { ok: true, data: networkSettings };
  });

  // Update network settings for a profile
  app.patch('/profiles/:id/network', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    const networkSettings = req.body || {};
    const updated = await profileManager.update(req.params.id, {
      network: { ...profile.network, ...networkSettings }
    });
    
    logger?.info({ profileId: req.params.id, via: 'rest' }, 'network settings updated via API');
    return { ok: true, data: updated };
  });

  // Test DNS leak protection
  app.post('/profiles/:id/network/test-dns', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    // Simulate DNS leak test
    const dnsLeakTest = {
      status: 'protected',
      leakDetected: false,
      testedServers: ['8.8.8.8', '1.1.1.1'],
      results: [
        { server: '8.8.8.8', leaked: false, responseTime: 15 },
        { server: '1.1.1.1', leaked: false, responseTime: 12 },
      ],
    };
    
    return { ok: true, data: dnsLeakTest };
  });

  // Test WebRTC leak
  app.post('/profiles/:id/network/test-webrtc', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    // Simulate WebRTC leak test
    const webRTCTest = {
      status: 'protected',
      leakDetected: false,
      localIPs: [],
      publicIP: profile.proxy ? 'proxy_detected' : 'direct',
      iceCandidates: [],
    };
    
    return { ok: true, data: webRTCTest };
  });

  // Add custom HTTP header
  app.post('/profiles/:id/network/headers', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    const { name, value } = req.body || {};
    if (!name || !value) {
      return reply.code(400).send({ ok: false, error: 'name and value required' });
    }
    
    const network = profile.network || { headers: { custom: [] } };
    network.headers = network.headers || { custom: [] };
    network.headers.custom.push({ name, value });
    
    await profileManager.update(req.params.id, { network });
    
    logger?.info({ profileId: req.params.id, header: name, via: 'rest' }, 'custom header added via API');
    return { ok: true, data: network.headers };
  });

  // Remove custom HTTP header
  app.delete('/profiles/:id/network/headers/:headerName', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    const network = profile.network || { headers: { custom: [] } };
    network.headers = network.headers || { custom: [] };
    network.headers.custom = network.headers.custom.filter(h => h.name !== req.params.headerName);
    
    await profileManager.update(req.params.id, { network });
    
    logger?.info({ profileId: req.params.id, header: req.params.headerName, via: 'rest' }, 'custom header removed via API');
    return { ok: true, data: network.headers };
  });

  // Set DNS servers
  app.post('/profiles/:id/network/dns', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    const { servers } = req.body || {};
    if (!Array.isArray(servers)) {
      return reply.code(400).send({ ok: false, error: 'servers array required' });
    }
    
    const network = profile.network || { dns: { customServers: [] } };
    network.dns = { ...network.dns, customServers: servers };
    
    await profileManager.update(req.params.id, { network });
    
    logger?.info({ profileId: req.params.id, servers, via: 'rest' }, 'DNS servers updated via API');
    return { ok: true, data: network.dns };
  });
}
