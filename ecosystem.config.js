module.exports = {
  apps: [
    {
      name: 'stock-signal-api',
      script: 'apps/api/dist/main.js',
      env_production: {
        NODE_ENV: 'production',
      },
      instances: 1,
      autorestart: true,
      watch: false,
      max_memory_restart: '512M',
    },
    {
      name: 'stock-signal-analysis',
      script: 'uvicorn',
      args: 'main:app --host 0.0.0.0 --port 8000',
      cwd: 'apps/analysis-service',
      interpreter: 'python3',
      interpreter_args: '-m',
      env_production: {
        PYTHONUNBUFFERED: '1',
      },
      autorestart: true,
      watch: false,
      max_memory_restart: '512M',
    },
  ],
};
