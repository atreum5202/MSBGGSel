export function registerShutdownRoutes({ app, shutdownController, logger }) {
  app.post('/api/shutdown', async (_req, reply) => {
    if (shutdownController.isShuttingDown()) {
      return { status: 'already-shutting-down' };
    }

    reply.send({ status: 'shutting-down' });

    setImmediate(() => {
      shutdownController.performShutdown({ exit: true }).catch((err) => {
        logger.error({ err: err.message }, 'graceful shutdown via REST failed');
      });
    });
  });
}
