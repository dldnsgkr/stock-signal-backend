module.exports = {
  apps: [
    {
      name: 'stock-signal-api',
      script: 'apps/api/dist/main.js',
      exec_mode: 'fork',
      instances: 1,
      wait_ready: true,
      listen_timeout: 10000,
      kill_timeout: 5000,
      env_production: {
        NODE_ENV: 'production',
      },
      autorestart: true,
      watch: false,
      max_memory_restart: '1500M',
    },
    {
      name: 'stock-signal-analysis',
      script: 'apps/analysis-service/.venv/bin/uvicorn',
      args: 'main:app --host 0.0.0.0 --port 8000',
      cwd: 'apps/analysis-service',
      interpreter: 'none',
      env_production: {
        PYTHONUNBUFFERED: '1',
      },
      autorestart: true,
      watch: false,
      max_memory_restart: '512M',
    },
  ],
};
