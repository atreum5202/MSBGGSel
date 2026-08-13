export function registerMonitoringRoutes({ app, profileManager, browserLauncher, statistics, logger }) {
  // Get system metrics
  app.get('/monitoring/system', async (req, reply) => {
    const memUsage = process.memoryUsage();
    const uptime = process.uptime();
    
    const profiles = profileManager.list();
    const running = browserLauncher.status();
    
    return {
      ok: true,
      data: {
        memory: {
          rss: Math.round(memUsage.rss / 1024 / 1024),
          heapTotal: Math.round(memUsage.heapTotal / 1024 / 1024),
          heapUsed: Math.round(memUsage.heapUsed / 1024 / 1024),
          external: Math.round(memUsage.external / 1024 / 1024),
        },
        uptime: {
          seconds: Math.floor(uptime),
          formatted: formatUptime(uptime),
        },
        profiles: {
          total: profiles.length,
          running: running.length,
          stopped: profiles.length - running.length,
        },
        cpu: {
          usage: process.cpuUsage(),
        },
      }
    };
  });

  // Get performance metrics
  app.get('/monitoring/performance', async (req, reply) => {
    const profiles = profileManager.list();
    const running = browserLauncher.status();
    
    const performanceData = {
      averageStartupTime: 0,
      averageMemoryPerProfile: 0,
      totalMemoryUsed: 0,
      profilesByEngine: {},
      profilesByAccountType: {},
    };
    
    let totalStartupTime = 0;
    let startupCount = 0;
    
    profiles.forEach(profile => {
      const engine = profile.engine || 'unknown';
      performanceData.profilesByEngine[engine] = (performanceData.profilesByEngine[engine] || 0) + 1;
      
      const accType = profile.account?.type || 'other';
      performanceData.profilesByAccountType[accType] = (performanceData.profilesByAccountType[accType] || 0) + 1;
    });
    
    running.forEach(r => {
      if (r.startTime) {
        totalStartupTime += (Date.now() - new Date(r.startTime).getTime());
        startupCount++;
      }
    });
    
    if (startupCount > 0) {
      performanceData.averageStartupTime = Math.round(totalStartupTime / startupCount);
    }
    
    const memUsage = process.memoryUsage();
    performanceData.totalMemoryUsed = Math.round(memUsage.heapUsed / 1024 / 1024);
    
    if (profiles.length > 0) {
      performanceData.averageMemoryPerProfile = Math.round(performanceData.totalMemoryUsed / profiles.length);
    }
    
    return { ok: true, data: performanceData };
  });

  // Get usage statistics
  app.get('/monitoring/usage', async (req, reply) => {
    const stats = await statistics.summary();
    const profiles = profileManager.list();
    
    const usageData = {
      totalProfiles: profiles.length,
      totalStarts: stats.totalStarts || 0,
      totalStops: stats.totalStops || 0,
      averageSessionDuration: stats.averageSessionDuration || 0,
      mostUsedProfiles: [],
      recentlyActive: [],
    };
    
    // Calculate most used profiles
    const profileUsage = new Map();
    profiles.forEach(profile => {
      const profileStats = statistics.get(profile.id);
      if (profileStats) {
        profileUsage.set(profile.id, {
          name: profile.name,
          starts: profileStats.starts || 0,
          duration: profileStats.totalDuration || 0,
        });
      }
    });
    
    usageData.mostUsedProfiles = Array.from(profileUsage.values())
      .sort((a, b) => b.starts - a.starts)
      .slice(0, 10);
    
    // Get recently active profiles
    const now = Date.now();
    const oneHourAgo = now - (60 * 60 * 1000);
    
    usageData.recentlyActive = profiles
      .filter(profile => {
        const profileStats = statistics.get(profile.id);
        return profileStats && profileStats.lastActivity && new Date(profileStats.lastActivity).getTime() > oneHourAgo;
      })
      .map(profile => ({
        id: profile.id,
        name: profile.name,
        lastActivity: statistics.get(profile.id)?.lastActivity,
      }))
      .slice(0, 10);
    
    return { ok: true, data: usageData };
  });

  // Get alerts
  app.get('/monitoring/alerts', async (req, reply) => {
    const alerts = [];
    const profiles = profileManager.list();
    const running = browserLauncher.status();
    const memUsage = process.memoryUsage();
    
    // Memory alert
    const memPercent = (memUsage.heapUsed / memUsage.heapTotal) * 100;
    if (memPercent > 80) {
      alerts.push({
        type: 'warning',
        severity: 'high',
        message: `High memory usage: ${memPercent.toFixed(1)}%`,
        timestamp: new Date().toISOString(),
      });
    }
    
    // Long-running profiles alert
    running.forEach(r => {
      if (r.startTime) {
        const runningTime = Date.now() - new Date(r.startTime).getTime();
        const hours = runningTime / (1000 * 60 * 60);
        
        if (hours > 24) {
          alerts.push({
            type: 'warning',
            severity: 'medium',
            message: `Profile ${r.id} has been running for ${hours.toFixed(1)} hours`,
            timestamp: new Date().toISOString(),
            profileId: r.id,
          });
        }
      }
    });
    
    // Profile count alert
    if (profiles.length > 100) {
      alerts.push({
        type: 'info',
        severity: 'low',
        message: `High profile count: ${profiles.length} profiles`,
        timestamp: new Date().toISOString(),
      });
    }
    
    return { ok: true, data: { alerts, count: alerts.length } };
  });

  // Get real-time activity
  app.get('/monitoring/activity', async (req, reply) => {
    const limit = req.query?.limit ? parseInt(req.query.limit) : 50;
    
    const activity = {
      runningProfiles: browserLauncher.status(),
      recentActions: [], // Would need to track actions in a real implementation
      systemLoad: {
        memory: process.memoryUsage(),
        cpu: process.cpuUsage(),
      },
    };
    
    return { ok: true, data: activity };
  });

  // Get health check
  app.get('/monitoring/health', async (req, reply) => {
    const checks = {
      api: { status: 'healthy', responseTime: 0 },
      profiles: { status: 'healthy', count: 0 },
      browser: { status: 'healthy', running: 0 },
      memory: { status: 'healthy', usage: 0 },
    };
    
    const start = Date.now();
    
    try {
      const profiles = profileManager.list();
      checks.profiles.count = profiles.length;
      checks.profiles.status = profiles.length > 0 ? 'healthy' : 'warning';
      
      const running = browserLauncher.status();
      checks.browser.running = running.length;
      
      const memUsage = process.memoryUsage();
      const memPercent = (memUsage.heapUsed / memUsage.heapTotal) * 100;
      checks.memory.usage = memPercent;
      checks.memory.status = memPercent < 90 ? 'healthy' : 'critical';
      
      checks.api.responseTime = Date.now() - start;
    } catch (e) {
      checks.api.status = 'unhealthy';
      checks.api.error = e.message;
    }
    
    const overallStatus = Object.values(checks).every(c => c.status === 'healthy') ? 'healthy' : 'degraded';
    
    return {
      ok: true,
      data: {
        status: overallStatus,
        checks,
        timestamp: new Date().toISOString(),
      }
    };
  });
}

function formatUptime(seconds) {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  
  if (days > 0) {
    return `${days}d ${hours}h ${minutes}m`;
  } else if (hours > 0) {
    return `${hours}h ${minutes}m`;
  } else {
    return `${minutes}m`;
  }
}
