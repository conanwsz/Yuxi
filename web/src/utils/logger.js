/**
 * 统一日志工具
 *
 * 设计目标:
 * 1. 新代码统一通过本工具输出日志,避免直接调用 console.* 散落在各文件
 * 2. dev 环境输出全部 level;production 仅输出 warn / error
 * 3. 生产环境的 console.log / debug / info / warn / trace 已被 vite.config.js
 *    的 esbuild.drop 编译时剥离,此处再加运行时门控是双保险
 * 4. 后续接入 Sentry / LogRocket 等远程日志时,只需在本文件统一接入,
 *    无需逐文件改造
 *
 * 用法:
 *   import { logger } from '@/utils/logger'
 *   logger.debug('组件挂载', { props })
 *   logger.info('用户登录', userId)
 *   logger.warn('接口返回异常字段', response)
 *   logger.error('上传失败', error)
 */

const isDev = import.meta.env.DEV

export const logger = {
  /**
   * 调试日志,仅 dev 环境输出
   * @param  {...any} args
   */
  debug(...args) {
    if (isDev) console.log('[DEBUG]', ...args)
  },

  /**
   * 一般信息,仅 dev 环境输出
   * @param  {...any} args
   */
  info(...args) {
    if (isDev) console.info('[INFO]', ...args)
  },

  /**
   * 警告,所有环境都输出
   * @param  {...any} args
   */
  warn(...args) {
    console.warn('[WARN]', ...args)
  },

  /**
   * 错误,所有环境都输出
   *
   * 注: console.error 是当前生产环境唯一的客户端调试信号,构建时不会被剥离;
   * 接入 Sentry 后,应在此处同步上报后,再调用 console.error
   * @param  {...any} args
   */
  error(...args) {
    console.error('[ERROR]', ...args)
  }
}

export default logger
