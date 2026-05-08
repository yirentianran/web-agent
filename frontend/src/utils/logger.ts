/**
 * Unified logger with configurable level.
 *
 * Level hierarchy matches Python logging (backend):
 *   DEBUG < INFO < WARNING < ERROR < CRITICAL < OFF
 *
 * Usage:
 *   import { logger } from '@/utils/logger';
 *   logger.debug('WebSocket connected', { url });
 *   logger.info('Session started', { sessionId });
 *   logger.warning('Retry limit reached');
 *   logger.error('Upload failed', err);
 *   logger.critical('Server unreachable');
 */

type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL' | 'OFF';

const LOG_LEVEL_PRIORITY: Record<LogLevel, number> = {
  DEBUG: 0,
  INFO: 1,
  WARNING: 2,
  ERROR: 3,
  CRITICAL: 4,
  OFF: 5,
};

// Get level from localStorage or default
function getLogLevel(): LogLevel {
  // 1. localStorage (runtime override)
  const storedLevel = localStorage.getItem('LOG_LEVEL');
  if (storedLevel && storedLevel.toUpperCase() in LOG_LEVEL_PRIORITY) {
    return storedLevel.toUpperCase() as LogLevel;
  }

  // 2. VITE_LOG_LEVEL from .env (build-time default)
  const envLevel = import.meta.env.VITE_LOG_LEVEL;
  if (envLevel && envLevel.toUpperCase() in LOG_LEVEL_PRIORITY) {
    return envLevel.toUpperCase() as LogLevel;
  }

  // 3. Default: DEBUG in dev, WARNING in prod
  const isDev = typeof window !== 'undefined' && (
    window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1'
  );
  return isDev ? 'DEBUG' : 'WARNING';
}

class Logger {
  private level: LogLevel;
  private prefix: string;

  constructor(prefix: string = '[App]') {
    this.level = getLogLevel();
    this.prefix = prefix;
  }

  setLevel(level: LogLevel) {
    this.level = level;
    localStorage.setItem('LOG_LEVEL', level);
  }

  getLevel(): LogLevel {
    return this.level;
  }

  private shouldLog(level: LogLevel): boolean {
    return LOG_LEVEL_PRIORITY[level] >= LOG_LEVEL_PRIORITY[this.level];
  }

  debug(message: string, ...args: unknown[]) {
    if (this.shouldLog('DEBUG')) {
      console.debug(`${this.prefix} ${message}`, ...args);
    }
  }

  info(message: string, ...args: unknown[]) {
    if (this.shouldLog('INFO')) {
      console.info(`${this.prefix} ${message}`, ...args);
    }
  }

  warning(message: string, ...args: unknown[]) {
    if (this.shouldLog('WARNING')) {
      console.warn(`${this.prefix} ${message}`, ...args);
    }
  }

  // Alias for warning (backward compatibility)
  warn(message: string, ...args: unknown[]) {
    this.warning(message, ...args);
  }

  error(message: string, ...args: unknown[]) {
    if (this.shouldLog('ERROR')) {
      console.error(`${this.prefix} ${message}`, ...args);
    }
  }

  critical(message: string, ...args: unknown[]) {
    if (this.shouldLog('CRITICAL')) {
      console.error(`${this.prefix} [CRITICAL] ${message}`, ...args);
    }
  }
}

export const logger = new Logger();

// Factory for context-specific loggers
export function createLogger(prefix: string): Logger {
  return new Logger(prefix);
}