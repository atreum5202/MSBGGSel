export function registerStatsRoutes({ app, statistics }) {
  app.get('/profiles/:id/stats', async (req) => statistics.get(req.params.id));
  app.get('/stats', async () => statistics.summary());
}
